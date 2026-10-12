"""Database-backed ActiveGraph projection using native SurrealDB relations."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from activegraph.core.graph import Object, Relation
from activegraph.core.graph_store import GraphStore, ObjectQuery, ObjectQueryResult
from activegraph.core.patch import Patch
from surrealdb import RecordID, Table

from .config import SurrealConfig
from .connection import SurrealConnection
from .errors import ClosedStoreError, SurrealProviderError
from .serde import (
    object_from_record,
    object_to_record,
    patch_from_record,
    patch_to_record,
    record_to_relation,
    relation_to_record,
)


class ProjectionUnavailableError(SurrealProviderError):
    """The persisted projection is not safe to serve as current state."""


class SurrealGraphStore(GraphStore):
    """Per-scope, per-run materialized graph stored entirely in SurrealDB.

    Objects and dangling endpoints share ``ag_vertex`` records.  Removing an
    object turns its vertex back into a placeholder so SurrealDB does not
    cascade-delete still-valid ActiveGraph relations.  Edges themselves are
    records in the fixed ``ag_relation TYPE RELATION`` table.

    The released query hooks are overridden with bound SurrealQL so object,
    relation, existence, and neighbourhood lookups run in the database rather
    than scanning in Python; ``match_chain`` composes those primitives.  The
    rich ``where`` predicate is returned to ActiveGraph unchanged as the
    residual, per the released contract.
    """

    def __init__(self, config: SurrealConfig, *, run_id: str, scope: str) -> None:
        if not str(run_id):
            raise ValueError("run_id must be non-empty")
        if not str(scope):
            raise ValueError("scope must be non-empty")

        self.config = config
        self.run_id = str(run_id)
        self.scope = str(scope)
        self._closed = False
        self._rebuilding_here = False
        self._connection = SurrealConnection(config)
        try:
            self._connection.ensure_schema()
            self._projection_record = self._connection.record_id(
                "ag_projection", self.scope, self.run_id
            )
            self._ensure_projection_record()
        except BaseException:
            self._connection.close()
            self._closed = True
            raise

    # ------------------------------------------------------------------
    # Object projection

    def put_object(self, obj: Object) -> None:
        self._assert_writable()
        content = {
            "scope": self.scope,
            "run_id": self.run_id,
            "object_present": True,
            "object_seq": _object_seq(obj.id),
            **object_to_record(obj),
        }
        self._connection.session.upsert(self.vertex_record_id(obj.id), content)

    def get_object(self, object_id: str) -> Object | None:
        self._assert_readable()
        row = _one(self._connection.session.select(self.vertex_record_id(object_id)))
        if row is None or not bool(row.get("object_present", False)):
            return None
        self._assert_scoped_row(row, "object_id", object_id)
        return object_from_record(row)

    def remove_object(self, object_id: str) -> None:
        self._assert_writable()
        record = self.vertex_record_id(object_id)
        row = _one(self._connection.session.select(record))
        if row is None:
            return
        self._assert_scoped_row(row, "object_id", object_id)
        self._connection.session.upsert(
            record,
            self._placeholder_content(object_id),
        )

    def all_objects(self) -> list[Object]:
        self._assert_readable()
        rows = _rows(
            self._connection.query(
                "SELECT * FROM ag_vertex WHERE scope = $scope AND run_id = $run_id "
                "AND object_present = true ORDER BY object_seq ASC, object_id ASC;",
                self._scope_variables(),
            )
        )
        return [object_from_record(row) for row in rows]

    # ------------------------------------------------------------------
    # Native relation projection

    def put_relation(self, rel: Relation) -> None:
        self._assert_writable()
        source = self.vertex_record_id(rel.source)
        target = self.vertex_record_id(rel.target)
        relation_record = self._relation_record_id(rel.id)
        transaction = self._connection.begin_transaction()
        try:
            self._ensure_placeholder_in_transaction(transaction, source, rel.source)
            self._ensure_placeholder_in_transaction(transaction, target, rel.target)
            transaction.delete(relation_record)
            transaction.insert_relation(
                Table("ag_relation"),
                {
                    "id": relation_record,
                    "in": source,
                    "out": target,
                    "scope": self.scope,
                    "run_id": self.run_id,
                    **relation_to_record(rel),
                },
            )
            transaction.commit()
        except BaseException:
            with suppress(BaseException):
                transaction.cancel()
            raise

    def get_relation(self, relation_id: str) -> Relation | None:
        self._assert_readable()
        row = _one(self._connection.session.select(self._relation_record_id(relation_id)))
        if row is None:
            return None
        self._assert_scoped_row(row, "relation_id", relation_id)
        return record_to_relation(row)

    def remove_relation(self, relation_id: str) -> None:
        self._assert_writable()
        self._connection.session.delete(self._relation_record_id(relation_id))

    def all_relations(self) -> list[Relation]:
        self._assert_readable()
        rows = _rows(
            self._connection.query(
                "SELECT * FROM ag_relation WHERE scope = $scope AND run_id = $run_id "
                "ORDER BY relation_id ASC;",
                self._scope_variables(),
            )
        )
        return [record_to_relation(row) for row in rows]

    # ------------------------------------------------------------------
    # Native query pushdown
    #
    # ActiveGraph's GraphStore query hooks default to Python scans over
    # ``all_objects`` / ``all_relations``. This provider overrides them so the
    # structural work runs inside SurrealDB: object type filters, relation
    # source/target/type filters, existence probes, and the undirected
    # neighbourhood walk are all expressed as bound SurrealQL. ``match_chain``
    # is inherited from the base class and therefore composes these
    # pushed-down primitives hop by hop rather than scanning the whole graph.
    #
    # The rich ``where`` predicate stays with ActiveGraph: per the released
    # contract a backend may only consume clauses it can prove equivalent, so
    # ``query_objects`` returns the plan's ``where`` unchanged as the residual
    # and lets ``Graph`` evaluate it in Python.

    def query_objects(self, plan: ObjectQuery) -> ObjectQueryResult:
        """Execute the structural portion of an object plan inside SurrealDB."""

        self._assert_readable()
        residual = copy.deepcopy(plan.where or {})
        if plan.result_mode == "exists" and not residual:
            return ObjectQueryResult([], {}, exists=self._object_exists(plan.type))
        limit = plan.limit if not residual else None
        return ObjectQueryResult(self._select_objects(plan.type, limit=limit), residual)

    def find_objects(self, type: str | None = None) -> list[Object]:
        """Return objects filtered by ``type`` in SurrealDB (``None`` = any)."""

        self._assert_readable()
        return self._select_objects(type)

    def find_objects_in_types(self, types: list[str]) -> list[Object]:
        """Return objects whose type is any of ``types`` (OR) in one query."""

        if not types:
            return []
        self._assert_readable()
        rows = _rows(
            self._connection.query(
                "SELECT * FROM ag_vertex WHERE scope = $scope AND run_id = $run_id "
                "AND object_present = true AND object_type IN $types "
                "ORDER BY object_seq ASC, object_id ASC;",
                {**self._scope_variables(), "types": list(dict.fromkeys(types))},
            )
        )
        return [object_from_record(row) for row in rows]

    def find_relations(
        self,
        source: str | None = None,
        target: str | None = None,
        type: str | None = None,
    ) -> list[Relation]:
        """Return relations matching every provided filter inside SurrealDB."""

        self._assert_readable()
        statement = "SELECT * FROM ag_relation WHERE scope = $scope AND run_id = $run_id"
        variables = self._scope_variables()
        if source is not None:
            statement += " AND source = $source"
            variables["source"] = str(source)
        if target is not None:
            statement += " AND target = $target"
            variables["target"] = str(target)
        if type is not None:
            statement += " AND relation_type = $relation_type"
            variables["relation_type"] = str(type)
        statement += " ORDER BY relation_id ASC;"
        return [
            record_to_relation(row) for row in _rows(self._connection.query(statement, variables))
        ]

    def neighborhood(self, object_id: str, depth: int = 1) -> tuple[list[Object], list[Relation]]:
        """Undirected breadth-first walk resolved by scoped SurrealQL per level.

        Each level issues one bound query for the relations incident to the
        current frontier, mirroring the base class's level-by-level frontier
        semantics (so placeholders and cycles resolve identically) without ever
        loading relations that are not reachable from the start.
        """

        self._assert_readable()
        start = str(object_id)
        if self.get_object(start) is None:
            return ([], [])
        seen_objs: set[str] = {start}
        frontier: set[str] = {start}
        seen_rels: set[str] = set()
        for _ in range(max(int(depth), 0)):
            if not frontier:
                break
            rows = _rows(
                self._connection.query(
                    "SELECT * FROM ag_relation WHERE scope = $scope AND run_id = $run_id "
                    "AND (source IN $frontier OR target IN $frontier) "
                    "ORDER BY relation_id ASC;",
                    {**self._scope_variables(), "frontier": sorted(frontier)},
                )
            )
            snapshot = set(seen_objs)
            next_frontier: set[str] = set()
            for row in rows:
                seen_rels.add(str(row["relation_id"]))
                for endpoint in (str(row["source"]), str(row["target"])):
                    if endpoint not in snapshot:
                        next_frontier.add(endpoint)
            seen_objs |= next_frontier
            frontier = next_frontier
        return (
            self._objects_by_logical_ids(seen_objs),
            self._relations_by_logical_ids(seen_rels),
        )

    def _select_objects(self, object_type: str | None, *, limit: int | None = None) -> list[Object]:
        statement = (
            "SELECT * FROM ag_vertex WHERE scope = $scope AND run_id = $run_id "
            "AND object_present = true"
        )
        variables: dict[str, Any] = dict(self._scope_variables())
        if object_type is not None:
            statement += " AND object_type = $object_type"
            variables["object_type"] = str(object_type)
        statement += " ORDER BY object_seq ASC, object_id ASC"
        if limit is not None:
            statement += " LIMIT $limit"
            variables["limit"] = int(limit)
        statement += ";"
        return [
            object_from_record(row) for row in _rows(self._connection.query(statement, variables))
        ]

    def _object_exists(self, object_type: str | None) -> bool:
        statement = (
            "SELECT * FROM ag_vertex WHERE scope = $scope AND run_id = $run_id "
            "AND object_present = true"
        )
        variables = self._scope_variables()
        if object_type is not None:
            statement += " AND object_type = $object_type"
            variables["object_type"] = str(object_type)
        statement += " LIMIT 1;"
        return bool(_rows(self._connection.query(statement, variables)))

    def _objects_by_logical_ids(self, object_ids: set[str]) -> list[Object]:
        if not object_ids:
            return []
        rows = _rows(
            self._connection.query(
                "SELECT * FROM ag_vertex WHERE scope = $scope AND run_id = $run_id "
                "AND object_present = true AND object_id IN $ids "
                "ORDER BY object_seq ASC, object_id ASC;",
                {**self._scope_variables(), "ids": sorted(object_ids)},
            )
        )
        return [object_from_record(row) for row in rows]

    def _relations_by_logical_ids(self, relation_ids: set[str]) -> list[Relation]:
        if not relation_ids:
            return []
        rows = _rows(
            self._connection.query(
                "SELECT * FROM ag_relation WHERE scope = $scope AND run_id = $run_id "
                "AND relation_id IN $ids;",
                {**self._scope_variables(), "ids": sorted(relation_ids)},
            )
        )
        return [record_to_relation(row) for row in rows]

    # ------------------------------------------------------------------
    # Patch projection

    def put_patch(self, patch: Patch) -> None:
        self._assert_writable()
        self._connection.session.upsert(
            self._patch_record_id(patch.id),
            {
                "scope": self.scope,
                "run_id": self.run_id,
                **patch_to_record(patch),
            },
        )

    def get_patch(self, patch_id: str) -> Patch | None:
        self._assert_readable()
        row = _one(self._connection.session.select(self._patch_record_id(patch_id)))
        if row is None:
            return None
        self._assert_scoped_row(row, "patch_id", patch_id)
        return patch_from_record(row)

    def all_patches(self) -> list[Patch]:
        self._assert_readable()
        rows = _rows(
            self._connection.query(
                "SELECT * FROM ag_patch WHERE scope = $scope AND run_id = $run_id "
                "ORDER BY patch_id ASC;",
                self._scope_variables(),
            )
        )
        return [patch_from_record(row) for row in rows]

    def remove_patch(self, patch_id: str) -> None:
        self._assert_writable()
        self._connection.session.delete(self._patch_record_id(patch_id))

    # ------------------------------------------------------------------
    # Projection lifecycle

    def begin_rebuild(self) -> None:
        """Make the projection unreadable, then clear its isolated state."""

        self._ensure_open()
        self._set_projection_status("rebuilding", error=None)
        self._rebuilding_here = True
        self.clear()

    def mark_ready(self) -> None:
        self._ensure_open()
        self._set_projection_status("ready", error=None)
        self._rebuilding_here = False

    def mark_failed(self, error: BaseException | str | None = None) -> None:
        self._ensure_open()
        # Persist a diagnostic class/name, never an SDK exception string that
        # could contain a credential-bearing command or endpoint.
        if error is None:
            detail = "projection rebuild failed"
        elif isinstance(error, BaseException):
            detail = type(error).__name__
        else:
            detail = str(error)
        self._set_projection_status("failed", error=detail)
        self._rebuilding_here = False

    def projection_status(self) -> str:
        self._ensure_open()
        row = self._projection_row()
        if row is None:
            raise ProjectionUnavailableError("projection metadata is incomplete")
        return str(row.get("status", "failed"))

    def clear(self) -> None:
        """Delete only this scope/run's disposable graph projection."""

        self._ensure_open()
        variables = self._scope_variables()
        # Relation records must be removed before their endpoint vertices.
        for table in ("ag_relation", "ag_patch", "ag_vertex"):
            self._connection.query(
                f"DELETE {table} WHERE scope = $scope AND run_id = $run_id;",
                variables,
            )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._connection.close()

    # ------------------------------------------------------------------
    # Provider diagnostics used by the real-engine contract

    def raw_query(self, query: str, variables: Mapping[str, Any] | None = None) -> Any:
        self._ensure_open()
        return self._connection.raw_query(query, variables)

    def vertex_record_id(self, object_id: str) -> RecordID:
        return self._connection.record_id("ag_vertex", self.scope, self.run_id, str(object_id))

    # ------------------------------------------------------------------
    # Internal records and guards

    def _ensure_projection_record(self) -> None:
        if self._projection_row() is not None:
            return
        content = {
            "scope": self.scope,
            "run_id": self.run_id,
            "status": "ready",
            "error": None,
            "updated_at": _now(),
        }
        try:
            self._connection.query(
                "CREATE $projection CONTENT $content;",
                {"projection": self._projection_record, "content": content},
            )
        except BaseException:
            if self._projection_row() is None:
                raise

    def _projection_row(self) -> dict[str, Any] | None:
        row = _one(
            self._connection.query(
                "SELECT * FROM ONLY $projection;",
                {"projection": self._projection_record},
            )
        )
        if row is not None:
            self._assert_scoped_row(row)
        return row

    def _set_projection_status(self, status: str, *, error: str | None) -> None:
        self._connection.session.upsert(
            self._projection_record,
            {
                "scope": self.scope,
                "run_id": self.run_id,
                "status": status,
                "error": error,
                "updated_at": _now(),
            },
        )

    def _assert_readable(self) -> None:
        self._ensure_open()
        status = self.projection_status()
        if status == "ready":
            return
        if status == "rebuilding" and self._rebuilding_here:
            return
        raise ProjectionUnavailableError(
            f"projection is {status}; current-state data is incomplete"
        )

    def _assert_writable(self) -> None:
        self._assert_readable()

    def _ensure_placeholder_in_transaction(
        self, transaction: Any, record: RecordID, object_id: str
    ) -> None:
        row = _one(transaction.select(record))
        if row is None:
            transaction.create(record, self._placeholder_content(object_id))
            return
        self._assert_scoped_row(row, "object_id", object_id)

    def _placeholder_content(self, object_id: str) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "run_id": self.run_id,
            "object_id": str(object_id),
            "object_present": False,
            # Keep the projection order stable when an object is removed or a
            # relation endpoint is stubbed: the counter is derived from the ID.
            "object_seq": _object_seq(object_id),
        }

    def _relation_record_id(self, relation_id: str) -> RecordID:
        return self._connection.record_id("ag_relation", self.scope, self.run_id, str(relation_id))

    def _patch_record_id(self, patch_id: str) -> RecordID:
        return self._connection.record_id("ag_patch", self.scope, self.run_id, str(patch_id))

    def _scope_variables(self) -> dict[str, str]:
        return {"scope": self.scope, "run_id": self.run_id}

    def _assert_scoped_row(
        self,
        row: Mapping[str, Any],
        logical_field: str | None = None,
        expected_logical_id: str | None = None,
    ) -> None:
        if str(row.get("scope")) != self.scope or str(row.get("run_id")) != self.run_id:
            raise SurrealProviderError("a graph record crossed its scope/run boundary")
        if logical_field is not None and str(row.get(logical_field)) != str(expected_logical_id):
            raise SurrealProviderError("an opaque graph record ID resolved incorrectly")

    def _ensure_open(self) -> None:
        if self._closed:
            raise ClosedStoreError("the SurrealGraphStore is closed")


_OBJECT_SEQ_RE = re.compile(r"#(?P<seq>\d+)$")


def _object_seq(object_id: str) -> int:
    """Recover the framework's global object counter from a ``type#n`` ID.

    ActiveGraph assigns object IDs from one monotonic per-graph counter
    (``task#1``, ``claim#2``, ...). That counter *is* the insertion order the
    reference in-memory store exposes, so persisting it lets SurrealDB return
    objects in the same order rather than sorting opaque IDs lexicographically.
    Non-framework IDs fall back to 0 and tie-break on the logical ID.
    """

    match = _OBJECT_SEQ_RE.search(str(object_id))
    return int(match.group("seq")) if match else 0


def _one(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return dict(value[0])
    return None


def _rows(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [dict(value)]
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    return []


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


__all__ = [
    "ProjectionUnavailableError",
    "SurrealGraphStore",
]
