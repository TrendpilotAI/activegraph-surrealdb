"""Evidence that the graph query hooks are pushed down into SurrealDB.

The released ``GraphStore`` query hooks default to Python scans over
``all_objects`` / ``all_relations``. This provider overrides them with bound
SurrealQL. To prove the override is real — and that ``match_chain`` composes the
pushed-down primitives rather than falling back to a scan — every test disables
``all_objects`` and ``all_relations`` and then exercises the hook. If any hook
still relied on the base-class scan it would raise instead of returning rows.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from activegraph.core.graph import Object, Relation
from activegraph.core.graph_store import ObjectQuery

from activegraph_surrealdb import SurrealGraphStore
from tests.integration.conftest import config_from_env

pytestmark = pytest.mark.surrealdb


def _obj(oid: str, type_: str = "memo") -> Object:
    return Object(
        id=oid,
        type=type_,
        data={"text": f"hello {oid}", "n": 1},
        version=1,
        provenance={"created_by": "test"},
    )


def _rel(rid: str, src: str, tgt: str, type_: str = "links") -> Relation:
    return Relation(
        id=rid,
        source=src,
        target=tgt,
        type=type_,
        data={"weight": 0.5},
        provenance={"created_by": "test"},
    )


class _ForbidScanStore(SurrealGraphStore):
    """A store that fails loudly if a hook touches the full-scan fallback."""

    def all_objects(self) -> list[Object]:  # noqa: D102 - deliberate trap
        raise AssertionError("find_objects must not fall back to all_objects()")

    def all_relations(self) -> list[Relation]:  # noqa: D102 - deliberate trap
        raise AssertionError("find_relations must not fall back to all_relations()")


@pytest.fixture
def store() -> SurrealGraphStore:
    s = _ForbidScanStore(
        config_from_env(),
        run_id=f"pushdown_{uuid4().hex}",
        scope=f"pushdown_{uuid4().hex}",
    )
    yield s
    s.close()


def test_find_objects_pushes_type_filter(store: SurrealGraphStore) -> None:
    store.put_object(_obj("o1", "memo"))
    store.put_object(_obj("o2", "memo"))
    store.put_object(_obj("o3", "note"))

    assert sorted(o.id for o in store.find_objects()) == ["o1", "o2", "o3"]
    assert sorted(o.id for o in store.find_objects("memo")) == ["o1", "o2"]
    assert [o.id for o in store.find_objects("note")] == ["o3"]
    assert store.find_objects("missing") == []


def test_find_objects_in_types_pushes_or_filter(store: SurrealGraphStore) -> None:
    store.put_object(_obj("o1", "memo"))
    store.put_object(_obj("o2", "note"))
    store.put_object(_obj("o3", "memo"))
    store.put_object(_obj("o4", "task"))

    assert sorted(o.id for o in store.find_objects_in_types(["memo"])) == ["o1", "o3"]
    assert sorted(o.id for o in store.find_objects_in_types(["memo", "task"])) == [
        "o1",
        "o3",
        "o4",
    ]
    assert store.find_objects_in_types([]) == []
    assert store.find_objects_in_types(["nope"]) == []


def test_find_relations_pushes_endpoint_and_type_filters(
    store: SurrealGraphStore,
) -> None:
    store.put_relation(_rel("r1", "a", "b", "links"))
    store.put_relation(_rel("r2", "a", "c", "cites"))
    store.put_relation(_rel("r3", "b", "a", "links"))

    assert sorted(r.id for r in store.find_relations()) == ["r1", "r2", "r3"]
    assert sorted(r.id for r in store.find_relations(source="a")) == ["r1", "r2"]
    assert [r.id for r in store.find_relations(target="a")] == ["r3"]
    assert sorted(r.id for r in store.find_relations(type="links")) == ["r1", "r3"]
    assert [r.id for r in store.find_relations(source="a", type="links")] == ["r1"]
    assert store.find_relations(source="z") == []


def test_query_objects_returns_exact_residual(store: SurrealGraphStore) -> None:
    store.put_object(_obj("o1", "memo"))
    store.put_object(_obj("o2", "memo"))
    store.put_object(_obj("o3", "note"))
    where = {"n": {">": 0}, "text": "hello o2"}

    result = store.query_objects(ObjectQuery(type="memo", where=where))

    assert sorted(o.id for o in result.candidates) == ["o1", "o2"]
    assert result.residual_where == where
    assert result.exists is None


def test_query_objects_pushes_limit_and_existence(store: SurrealGraphStore) -> None:
    store.put_object(_obj("o1", "memo"))
    store.put_object(_obj("o2", "memo"))

    limited = store.query_objects(ObjectQuery(type="memo", limit=1))
    assert len(limited.candidates) == 1
    assert limited.residual_where == {}
    assert limited.exists is None

    assert (
        store.query_objects(ObjectQuery(type="memo", limit=1, result_mode="exists")).exists is True
    )
    assert (
        store.query_objects(ObjectQuery(type="missing", limit=1, result_mode="exists")).exists
        is False
    )


def test_neighborhood_pushes_frontier_queries(store: SurrealGraphStore) -> None:
    for oid in ("o1", "o2", "o3"):
        store.put_object(_obj(oid))
    store.put_relation(_rel("r1", "o1", "p"))  # p is a placeholder, not an object
    store.put_relation(_rel("r2", "p", "o2"))
    store.put_relation(_rel("r3", "o2", "o3"))

    objs, rels = store.neighborhood("o1", depth=2)
    assert sorted(o.id for o in objs) == ["o1", "o2"]
    assert sorted(r.id for r in rels) == ["r1", "r2"]

    objs, rels = store.neighborhood("o1", depth=3)
    assert sorted(o.id for o in objs) == ["o1", "o2", "o3"]
    assert sorted(r.id for r in rels) == ["r1", "r2", "r3"]

    # Unknown start and placeholder-only start are not objects.
    assert store.neighborhood("missing") == ([], [])
    assert store.neighborhood("p") == ([], [])


def test_match_chain_composes_pushed_down_primitives(store: SurrealGraphStore) -> None:
    for oid in ("a", "b", "c", "d"):
        store.put_object(_obj(oid))
    store.put_relation(_rel("ab", "a", "b", "links"))
    store.put_relation(_rel("bc", "b", "c", "links"))
    store.put_relation(_rel("cd", "c", "d", "cites"))

    chains = store.match_chain([None, None, None], [("links", "right"), ("links", "right")])
    assert sorted(
        (tuple(o.id for o in c.objects), tuple(r.id for r in c.relations)) for c in chains
    ) == [(("a", "b", "c"), ("ab", "bc"))]
