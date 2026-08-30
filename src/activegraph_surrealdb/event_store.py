"""A scoped, transactional ActiveGraph event log backed by SurrealDB."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from activegraph.core.event import Event
from activegraph.store.base import RunRecord
from activegraph.store.errors import DuplicateEventError, EventNotFoundError

from .config import SurrealConfig
from .errors import (
    ClosedStoreError,
    ConcurrentWriterError,
    ForkPointNotFoundError,
    LedgerIntegrityError,
    RunAlreadyExistsError,
    UnsupportedCompactedRunError,
)
from .serde import event_hash, event_to_record, record_to_event

if TYPE_CHECKING:
    from .connection import SurrealConnection


_CONFLICT_WORDS = ("conflict", "concurrent", "transaction", "unique", "already exists")
_VERSION_RE = re.compile(r"(?:surrealdb-)?(?P<version>\d+\.\d+\.\d+)")


@dataclass(frozen=True)
class IntegrityReport:
    """Verified durable state for one scope/run event chain."""

    event_count: int
    next_seq: int
    head_hash: str | None


class SurrealEventStore:
    """Independent EventStore protocol implementation for one scope and run."""

    def __init__(self, config: SurrealConfig, *, run_id: str, scope: str) -> None:
        if not str(run_id):
            raise ValueError("run_id must be non-empty")
        if not str(scope):
            raise ValueError("scope must be non-empty")

        self.config = config
        self.run_id = str(run_id)
        self.scope = str(scope)
        self._closed = False
        self._lock = threading.RLock()
        self._test_after_head_read: Callable[..., None] | None = None
        self._test_before_commit: Callable[..., None] | None = None

        self._connection = self._new_connection(config)
        try:
            self._connection.ensure_schema()
            self._run_record = self._connection.record_id("ag_run", self.scope, self.run_id)
            self._ensure_run()
            report = self.verify_integrity()
            self._expected_next_seq = report.next_seq
            self._expected_head_hash = report.head_hash
        except BaseException:
            self._connection.close()
            self._closed = True
            raise

    @staticmethod
    def _new_connection(config: SurrealConfig) -> SurrealConnection:
        from .connection import SurrealConnection

        return SurrealConnection(config)

    # ------------------------------------------------------------------
    # ActiveGraph EventStore protocol

    def append(self, event: Event) -> None:
        # Validate and adapt before opening a transaction or mutating the head.
        encoded = event_to_record(event)
        with self._lock:
            self._ensure_open()
            transaction = self._connection.begin_transaction()
            try:
                head = self._transaction_run_row(transaction)
                self._assert_expected_head(head)
                if self._test_after_head_read is not None:
                    self._test_after_head_read(
                        transaction,
                        self._connection.session_id,
                        dict(head),
                    )

                event_record = self._event_record(event.id)
                existing = _one(
                    transaction.query("SELECT * FROM ONLY $event;", {"event": event_record})
                )
                if existing is not None:
                    raise DuplicateEventError(
                        f"event {event.id!r} already exists in run {self.run_id!r}"
                    )

                seq = int(head["next_seq"])
                previous_hash = _optional_text(head.get("head_hash"))
                current_hash = event_hash(
                    self.scope,
                    self.run_id,
                    seq,
                    event,
                    previous_hash=previous_hash,
                )
                transaction.create(
                    event_record,
                    {
                        "scope": self.scope,
                        "run_id": self.run_id,
                        **encoded,
                        "seq": seq,
                        "previous_hash": previous_hash,
                        "hash": current_hash,
                    },
                )
                transaction.merge(
                    self._run_record,
                    {"next_seq": seq + 1, "head_hash": current_hash},
                )

                if self._test_before_commit is not None:
                    self._test_before_commit(
                        transaction,
                        self._connection.session_id,
                        self._run_record,
                        event_record,
                    )
                transaction.commit()
            except BaseException as exc:
                _cancel_safely(transaction)
                if isinstance(exc, (DuplicateEventError, ConcurrentWriterError)):
                    raise
                self._raise_if_concurrent(exc)
                raise
            else:
                self._expected_next_seq = seq + 1
                self._expected_head_hash = current_hash

    def iter_events(
        self,
        after: str | None = None,
        until: str | None = None,
    ) -> Iterator[Event]:
        rows, _ = self._verified_rows_and_run()
        boundaries = {str(row["event_id"]): int(row["seq"]) for row in rows}
        after_seq = self._boundary_seq(after, boundaries) if after is not None else None
        until_seq = self._boundary_seq(until, boundaries) if until is not None else None
        for row in rows:
            seq = int(row["seq"])
            if after_seq is not None and seq <= after_seq:
                continue
            if until_seq is not None and seq > until_seq:
                continue
            yield record_to_event(row)

    def get_event(self, event_id: str) -> Event | None:
        self._ensure_open()
        row = _one(
            self._connection.query(
                "SELECT * FROM ONLY $event;",
                {"event": self._event_record(event_id)},
            )
        )
        if row is None:
            return None
        self._assert_row_scope(row)
        return record_to_event(row)

    def count(self) -> int:
        self._ensure_open()
        result = self._connection.query(
            "SELECT count() AS count FROM ag_event "
            "WHERE scope = $scope AND run_id = $run_id GROUP ALL;",
            {"scope": self.scope, "run_id": self.run_id},
        )
        row = _one(result)
        return int(row.get("count", 0)) if row is not None else 0

    def truncate_after(self, event_id: str) -> None:
        with self._lock:
            self._ensure_open()
            transaction = self._connection.begin_transaction()
            try:
                head = self._transaction_run_row(transaction)
                self._assert_expected_head(head)
                event_record = self._event_record(event_id)
                cutoff = _one(
                    transaction.query("SELECT * FROM ONLY $event;", {"event": event_record})
                )
                if cutoff is None:
                    raise EventNotFoundError(f"event {event_id!r} not found in run {self.run_id!r}")
                self._assert_row_scope(cutoff)
                cutoff_seq = int(cutoff["seq"])
                cutoff_hash = str(cutoff["hash"])
                transaction.query(
                    "DELETE ag_event WHERE scope = $scope AND run_id = $run_id "
                    "AND seq > $cutoff_seq;",
                    {
                        "scope": self.scope,
                        "run_id": self.run_id,
                        "cutoff_seq": cutoff_seq,
                    },
                )
                transaction.merge(
                    self._run_record,
                    {"next_seq": cutoff_seq + 1, "head_hash": cutoff_hash},
                )
                transaction.commit()
            except BaseException as exc:
                _cancel_safely(transaction)
                if isinstance(exc, (EventNotFoundError, ConcurrentWriterError)):
                    raise
                self._raise_if_concurrent(exc)
                raise
            else:
                self._expected_next_seq = cutoff_seq + 1
                self._expected_head_hash = cutoff_hash

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._connection.close()

    # ------------------------------------------------------------------
    # Provider diagnostics and run metadata

    def raw_query(self, query: str, variables: Mapping[str, Any] | None = None) -> Any:
        self._ensure_open()
        return self._connection.query(query, dict(variables or {}))

    def server_version(self) -> str:
        self._ensure_open()
        raw = str(self._connection.server_version())
        match = _VERSION_RE.search(raw)
        return match.group("version") if match else raw

    def schema_version(self) -> str:
        self._ensure_open()
        result = self._connection.query("SELECT VALUE version FROM ONLY ag_schema:singleton;")
        value = _scalar(result)
        if value is None:
            raise LedgerIntegrityError("the provider schema version record is missing")
        return str(value)

    def get_run(self) -> RunRecord | None:
        self._ensure_open()
        row = self._read_run_row()
        if row is None:
            return None
        return RunRecord(
            run_id=str(row["run_id"]),
            parent_run_id=_optional_text(row.get("parent_run_id")),
            forked_at_event_id=_optional_text(row.get("forked_at_event_id")),
            label=_optional_text(row.get("label")),
            created_at=str(row["created_at"]),
            goal=_optional_text(row.get("goal")),
            frame_id=_optional_text(row.get("frame_id")),
        )

    def verify_integrity(self) -> IntegrityReport:
        rows, run = self._verified_rows_and_run()
        return IntegrityReport(
            event_count=len(rows),
            next_seq=int(run["next_seq"]),
            head_hash=_optional_text(run.get("head_hash")),
        )

    @classmethod
    def run_exists(cls, config: SurrealConfig, *, run_id: str, scope: str) -> bool:
        connection = cls._new_connection(config)
        try:
            connection.ensure_schema()
            record = connection.record_id("ag_run", str(scope), str(run_id))
            return _one(connection.query("SELECT * FROM ONLY $run;", {"run": record})) is not None
        finally:
            connection.close()

    @classmethod
    def inspect_run_artifacts(
        cls, config: SurrealConfig, *, run_id: str, scope: str
    ) -> dict[str, int]:
        connection = cls._new_connection(config)
        try:
            connection.ensure_schema()
            variables = {"scope": str(scope), "run_id": str(run_id)}
            runs = connection.query(
                "SELECT id FROM ag_run WHERE scope = $scope AND run_id = $run_id;",
                variables,
            )
            events = connection.query(
                "SELECT id FROM ag_event WHERE scope = $scope AND run_id = $run_id;",
                variables,
            )
            return {
                "run_records": len(list(runs or [])),
                "event_records": len(list(events or [])),
            }
        finally:
            connection.close()

    # ------------------------------------------------------------------
    # Store-neutral, uncompacted prefix fork primitive

    def fork_prefix(
        self,
        *,
        new_run_id: str,
        at_event_id: str,
        label: str | None = None,
    ) -> SurrealEventStore:
        with self._lock:
            self._ensure_open()
            rows, parent_row = self._verified_rows_and_run()
            self._assert_expected_head(parent_row)
            if rows and str(rows[0]["event_type"]) == "runtime.snapshot":
                raise UnsupportedCompactedRunError(
                    "runtime.snapshot logs are outside the preview fork contract"
                )
            cutoff_index = next(
                (index for index, row in enumerate(rows) if str(row["event_id"]) == at_event_id),
                None,
            )
            if cutoff_index is None:
                raise ForkPointNotFoundError(
                    f"fork point {at_event_id!r} is absent from run {self.run_id!r}"
                )
            if self._destination_exists(new_run_id):
                raise RunAlreadyExistsError(f"destination run {new_run_id!r} already exists")

            prefix = [record_to_event(row) for row in rows[: cutoff_index + 1]]
            child_record = self._connection.record_id("ag_run", self.scope, str(new_run_id))
            created_at = _now()
            child_events: list[tuple[Any, dict[str, Any]]] = []
            previous_hash: str | None = None
            for seq, event in enumerate(prefix):
                current_hash = event_hash(
                    self.scope,
                    str(new_run_id),
                    seq,
                    event,
                    previous_hash=previous_hash,
                )
                child_events.append(
                    (
                        self._connection.record_id(
                            "ag_event", self.scope, str(new_run_id), event.id
                        ),
                        {
                            "scope": self.scope,
                            "run_id": str(new_run_id),
                            **event_to_record(event),
                            "seq": seq,
                            "previous_hash": previous_hash,
                            "hash": current_hash,
                        },
                    )
                )
                previous_hash = current_hash

            transaction = self._connection.begin_transaction()
            try:
                current_parent = self._transaction_run_row(transaction)
                if int(current_parent["next_seq"]) != len(rows) or _optional_text(
                    current_parent.get("head_hash")
                ) != _optional_text(parent_row.get("head_hash")):
                    raise ConcurrentWriterError(f"parent run {self.run_id!r} changed during fork")
                existing = _one(
                    transaction.query("SELECT * FROM ONLY $run;", {"run": child_record})
                )
                if existing is not None:
                    raise RunAlreadyExistsError(f"destination run {new_run_id!r} already exists")

                # Touching the parent head makes concurrent truncation/append
                # conflict with this fork transaction without changing values.
                transaction.merge(
                    self._run_record,
                    {
                        "next_seq": int(parent_row["next_seq"]),
                        "head_hash": _optional_text(parent_row.get("head_hash")),
                    },
                )
                transaction.create(
                    child_record,
                    {
                        "scope": self.scope,
                        "run_id": str(new_run_id),
                        "parent_run_id": self.run_id,
                        "forked_at_event_id": at_event_id,
                        "label": label,
                        "created_at": created_at,
                        "goal": _optional_text(parent_row.get("goal")),
                        "frame_id": _optional_text(parent_row.get("frame_id")),
                        "next_seq": len(child_events),
                        "head_hash": previous_hash,
                    },
                )
                for event_record, content in child_events:
                    transaction.create(event_record, content)
                transaction.commit()
            except BaseException as exc:
                _cancel_safely(transaction)
                if isinstance(exc, (ConcurrentWriterError, RunAlreadyExistsError)):
                    raise
                if self._destination_exists(new_run_id):
                    raise RunAlreadyExistsError(
                        f"destination run {new_run_id!r} already exists"
                    ) from exc
                self._raise_if_concurrent(exc)
                raise

            return type(self)(
                self.config,
                run_id=str(new_run_id),
                scope=self.scope,
            )

    # ------------------------------------------------------------------
    # Internal helpers

    def _ensure_run(self) -> None:
        if self._read_run_row() is not None:
            return
        content = {
            "scope": self.scope,
            "run_id": self.run_id,
            "parent_run_id": None,
            "forked_at_event_id": None,
            "label": None,
            "created_at": _now(),
            "goal": None,
            "frame_id": None,
            "next_seq": 0,
            "head_hash": None,
        }
        try:
            self._connection.query(
                "CREATE $run CONTENT $content;",
                {"run": self._run_record, "content": content},
            )
        except BaseException:
            # A competing constructor may have created the same canonical run.
            if self._read_run_row() is None:
                raise

    def _read_run_row(self) -> dict[str, Any] | None:
        row = _one(self._connection.query("SELECT * FROM ONLY $run;", {"run": self._run_record}))
        if row is None:
            return None
        if str(row.get("scope")) != self.scope or str(row.get("run_id")) != self.run_id:
            raise LedgerIntegrityError("the canonical run record has mismatched scope")
        return dict(row)

    def _transaction_run_row(self, transaction: Any) -> dict[str, Any]:
        row = _one(transaction.query("SELECT * FROM ONLY $run;", {"run": self._run_record}))
        if row is None:
            raise LedgerIntegrityError(f"run metadata {self.run_id!r} is missing")
        if str(row.get("scope")) != self.scope or str(row.get("run_id")) != self.run_id:
            raise LedgerIntegrityError("the transactional run record is mis-scoped")
        return dict(row)

    def _verified_rows_and_run(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        self._ensure_open()
        rows = [
            dict(row)
            for row in self._connection.query(
                "SELECT * FROM ag_event WHERE scope = $scope AND run_id = $run_id "
                "ORDER BY seq ASC;",
                {"scope": self.scope, "run_id": self.run_id},
            )
        ]
        run = self._read_run_row()
        if run is None:
            raise LedgerIntegrityError(f"run metadata {self.run_id!r} is missing")

        previous_hash: str | None = None
        for expected_seq, row in enumerate(rows):
            self._assert_row_scope(row)
            actual_seq = int(row["seq"])
            if actual_seq != expected_seq:
                raise LedgerIntegrityError(
                    f"run {self.run_id!r} has sequence {actual_seq}; expected {expected_seq}"
                )
            stored_previous = _optional_text(row.get("previous_hash"))
            if stored_previous != previous_hash:
                raise LedgerIntegrityError(
                    f"event {row.get('event_id')!r} has an invalid previous hash"
                )
            event = record_to_event(row)
            calculated = event_hash(
                self.scope,
                self.run_id,
                actual_seq,
                event,
                previous_hash=stored_previous,
            )
            if calculated != str(row.get("hash")):
                raise LedgerIntegrityError(f"event {event.id!r} failed hash-chain verification")
            previous_hash = calculated

        if int(run.get("next_seq", -1)) != len(rows):
            raise LedgerIntegrityError(
                f"run head expects {run.get('next_seq')} events; found {len(rows)}"
            )
        if _optional_text(run.get("head_hash")) != previous_hash:
            raise LedgerIntegrityError("run head hash does not match the event chain")
        return rows, run

    def _assert_expected_head(self, head: Mapping[str, Any]) -> None:
        if (
            int(head.get("next_seq", -1)) != self._expected_next_seq
            or _optional_text(head.get("head_hash")) != self._expected_head_hash
        ):
            raise ConcurrentWriterError(
                f"run {self.run_id!r} changed after this handle observed its head"
            )

    def _raise_if_concurrent(self, cause: BaseException) -> None:
        try:
            current = self._read_run_row()
        except BaseException:
            current = None
        changed = current is not None and (
            int(current.get("next_seq", -1)) != self._expected_next_seq
            or _optional_text(current.get("head_hash")) != self._expected_head_hash
        )
        conflict = any(word in str(cause).lower() for word in _CONFLICT_WORDS)
        if changed or conflict:
            raise ConcurrentWriterError(
                f"run {self.run_id!r} changed during a transactional write"
            ) from cause

    def _destination_exists(self, run_id: str) -> bool:
        record = self._connection.record_id("ag_run", self.scope, str(run_id))
        return _one(self._connection.query("SELECT * FROM ONLY $run;", {"run": record})) is not None

    def _event_record(self, event_id: str) -> Any:
        return self._connection.record_id("ag_event", self.scope, self.run_id, str(event_id))

    def _assert_row_scope(self, row: Mapping[str, Any]) -> None:
        if str(row.get("scope")) != self.scope or str(row.get("run_id")) != self.run_id:
            raise LedgerIntegrityError("an event row crossed its scope/run boundary")

    def _boundary_seq(self, event_id: str, boundaries: Mapping[str, int]) -> int:
        try:
            return boundaries[event_id]
        except KeyError as exc:
            raise EventNotFoundError(
                f"event {event_id!r} not found in run {self.run_id!r}"
            ) from exc

    def _ensure_open(self) -> None:
        if self._closed:
            raise ClosedStoreError("the SurrealEventStore is closed")


def _one(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        if not value:
            return None
        first = value[0]
        return first if isinstance(first, dict) else None
    return None


def _scalar(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _optional_text(value: Any) -> str | None:
    return None if value is None else str(value)


def _cancel_safely(transaction: Any) -> None:
    with suppress(BaseException):
        transaction.cancel()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
