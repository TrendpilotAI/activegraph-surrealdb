"""Persistent-backend run metadata used by ``persist_to=`` and ``Runtime.load``.

``SurrealEventStore`` implements the runtime's persistent-backend extra
(``upsert_run``) and a scope-aware ``most_recent_run_id`` so the runtime's
persistence sugar works against SurrealDB exactly as it does against SQLite:
``Runtime(graph, persist_to=...)`` records run metadata, and ``Runtime.load``
can pick the run that most recently made progress.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from activegraph.core.event import Event

from activegraph_surrealdb import SurrealConfig, SurrealEventStore

pytestmark = pytest.mark.surrealdb


def _fresh_config() -> SurrealConfig:
    return SurrealConfig(
        endpoint=os.environ["ACTIVEGRAPH_SURREALDB_TEST_URL"],
        namespace=os.environ.get("ACTIVEGRAPH_SURREALDB_TEST_NAMESPACE", "activegraph"),
        database=f"run_metadata_{uuid4().hex}",
        username=os.environ.get("ACTIVEGRAPH_SURREALDB_TEST_USERNAME", "root"),
        password=os.environ.get("ACTIVEGRAPH_SURREALDB_TEST_PASSWORD", "root"),
    )


def _event(event_id: str) -> Event:
    return Event(
        id=event_id,
        type="object.created",
        payload={"k": "v"},
        actor="test",
        timestamp="2026-01-01T00:00:00Z",
    )


def test_upsert_run_inserts_then_updates_without_clearing() -> None:
    store = SurrealEventStore(_fresh_config(), run_id="run", scope="scope")
    try:
        store.upsert_run(created_at="2026-01-01T00:00:00Z", goal="ship", frame_id="frame_1")
        record = store.get_run()
        assert record is not None
        assert (record.goal, record.frame_id) == ("ship", "frame_1")
        created_at = record.created_at
        assert created_at

        # A reload upserts with only created_at; None must never clear a value.
        store.upsert_run(created_at="2026-01-02T00:00:00Z", label="child")
        record = store.get_run()
        assert record is not None
        assert (record.goal, record.frame_id, record.label) == ("ship", "frame_1", "child")
        # created_at is written on insert only.
        assert record.created_at == created_at
    finally:
        store.close()


def test_upsert_run_records_fork_lineage() -> None:
    config = _fresh_config()
    parent = SurrealEventStore(config, run_id="parent", scope="scope")
    child = SurrealEventStore(config, run_id="child", scope="scope")
    try:
        child.upsert_run(
            created_at="2026-01-01T00:00:00Z",
            parent_run_id="parent",
            forked_at_event_id="evt_1",
        )
        record = child.get_run()
        assert record is not None
        assert (record.parent_run_id, record.forked_at_event_id) == ("parent", "evt_1")
    finally:
        parent.close()
        child.close()


def test_most_recent_run_id_prefers_progress_then_recency() -> None:
    config = _fresh_config()
    idle = SurrealEventStore(config, run_id="idle", scope="scope")
    active = SurrealEventStore(config, run_id="active", scope="scope")
    try:
        idle.upsert_run(created_at="2026-01-01T00:00:00Z")
        active.upsert_run(created_at="2026-01-01T00:00:00Z")
        # Before any events, both are empty; a run id is still returned.
        assert SurrealEventStore.most_recent_run_id(config, scope="scope") in {"idle", "active"}

        # A run with events sorts ahead of an empty one.
        idle.append(_event("evt_1"))
        assert SurrealEventStore.most_recent_run_id(config, scope="scope") == "idle"

        # More events wins.
        active.append(_event("evt_2"))
        active.append(_event("evt_3"))
        assert SurrealEventStore.most_recent_run_id(config, scope="scope") == "active"
    finally:
        idle.close()
        active.close()


def test_most_recent_run_id_is_scope_isolated() -> None:
    config = _fresh_config()
    store = SurrealEventStore(config, run_id="run", scope="here")
    try:
        assert SurrealEventStore.most_recent_run_id(config, scope="elsewhere") is None
        assert SurrealEventStore.most_recent_run_id(config, scope="here") == "run"
    finally:
        store.close()
