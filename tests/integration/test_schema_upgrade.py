"""The provider schema upgrade path is additive and in place.

Schema v2 adds the indexes that make the native query pushdown efficient
(``ag_vertex`` presence/type and ``ag_relation`` source/target/type). Schema v3
adds ``ag_vertex.object_seq`` so the projection returns objects in the
reference in-memory store's insertion order. An existing older database must be
upgraded without data loss and without a downtime migration; a database written
by a newer provider must be rejected rather than silently mis-read.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from activegraph_surrealdb import SurrealConfig, SurrealEventStore
from activegraph_surrealdb.errors import LedgerIntegrityError

pytestmark = pytest.mark.surrealdb

_V2_VERTEX_INDEXES = {
    "ag_vertex_scope_run_present",
    "ag_vertex_scope_run_type",
}
_V2_RELATION_INDEXES = {
    "ag_relation_scope_run_source",
    "ag_relation_scope_run_target",
    "ag_relation_scope_run_type",
}
_V3_VERTEX_INDEXES = {"ag_vertex_scope_run_seq"}


def _fresh_config() -> SurrealConfig:
    return SurrealConfig(
        endpoint=os.environ["ACTIVEGRAPH_SURREALDB_TEST_URL"],
        namespace=os.environ.get("ACTIVEGRAPH_SURREALDB_TEST_NAMESPACE", "activegraph"),
        database=f"schema_upgrade_{uuid4().hex}",
        username=os.environ.get("ACTIVEGRAPH_SURREALDB_TEST_USERNAME", "root"),
        password=os.environ.get("ACTIVEGRAPH_SURREALDB_TEST_PASSWORD", "root"),
    )


def _index_names(store: SurrealEventStore, table: str) -> set[str]:
    info = store.raw_query(f"INFO FOR TABLE {table};")
    indexes = info.get("indexes", {}) if isinstance(info, dict) else {}
    return set(indexes)


def test_fresh_database_installs_current_schema() -> None:
    store = SurrealEventStore(_fresh_config(), run_id="run", scope="scope")
    try:
        assert store.schema_version() == "3"
        assert _index_names(store, "ag_vertex") >= _V2_VERTEX_INDEXES | _V3_VERTEX_INDEXES
        assert _index_names(store, "ag_relation") >= _V2_RELATION_INDEXES
    finally:
        store.close()


def test_version_one_database_upgrades_in_place_without_data_loss() -> None:
    config = _fresh_config()

    store = SurrealEventStore(config, run_id="run", scope="scope")
    store.append(_event("evt_1"))
    # Simulate a v1 database: drop the v2/v3 indexes and lower the version.
    for index, table in (
        ("ag_vertex_scope_run_present", "ag_vertex"),
        ("ag_vertex_scope_run_type", "ag_vertex"),
        ("ag_vertex_scope_run_seq", "ag_vertex"),
        ("ag_relation_scope_run_source", "ag_relation"),
        ("ag_relation_scope_run_target", "ag_relation"),
        ("ag_relation_scope_run_type", "ag_relation"),
    ):
        store.raw_query(f"REMOVE INDEX IF EXISTS {index} ON {table};")
    store.raw_query("UPDATE ONLY ag_schema:singleton SET version = 1;")
    store.close()

    upgraded = SurrealEventStore(config, run_id="run", scope="scope")
    try:
        assert upgraded.schema_version() == "3"
        assert _index_names(upgraded, "ag_vertex") >= _V2_VERTEX_INDEXES | _V3_VERTEX_INDEXES
        assert _index_names(upgraded, "ag_relation") >= _V2_RELATION_INDEXES
        # The event written before the upgrade is intact.
        assert [e.id for e in upgraded.iter_events()] == ["evt_1"]
    finally:
        upgraded.close()


def test_version_two_database_upgrades_to_three() -> None:
    config = _fresh_config()

    store = SurrealEventStore(config, run_id="run", scope="scope")
    store.append(_event("evt_1"))
    # Simulate a v2 database: the v3 ordering index is absent.
    store.raw_query("REMOVE INDEX IF EXISTS ag_vertex_scope_run_seq ON ag_vertex;")
    store.raw_query("UPDATE ONLY ag_schema:singleton SET version = 2;")
    store.close()

    upgraded = SurrealEventStore(config, run_id="run", scope="scope")
    try:
        assert upgraded.schema_version() == "3"
        assert _index_names(upgraded, "ag_vertex") >= _V3_VERTEX_INDEXES
        assert [e.id for e in upgraded.iter_events()] == ["evt_1"]
    finally:
        upgraded.close()


def test_newer_schema_version_is_rejected() -> None:
    config = _fresh_config()
    store = SurrealEventStore(config, run_id="run", scope="scope")
    store.raw_query("UPDATE ONLY ag_schema:singleton SET version = 99;")
    store.close()

    with pytest.raises(LedgerIntegrityError, match="newer than"):
        SurrealEventStore(config, run_id="run", scope="scope")


def test_schema_version_below_minimum_is_rejected() -> None:
    config = _fresh_config()
    store = SurrealEventStore(config, run_id="run", scope="scope")
    store.raw_query("UPDATE ONLY ag_schema:singleton SET version = 0;")
    store.close()

    with pytest.raises(LedgerIntegrityError, match="predates"):
        SurrealEventStore(config, run_id="run", scope="scope")


def _event(event_id: str):
    from activegraph.core.event import Event

    return Event(
        id=event_id,
        type="object.created",
        payload={"k": "v"},
        actor="test",
        frame_id=None,
        caused_by=None,
        timestamp="2026-01-01T00:00:00Z",
    )
