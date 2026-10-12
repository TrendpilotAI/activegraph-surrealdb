"""Every claimed pack loads and runs on a SurrealDB-backed Runtime.

These tests are the provider's pack conformance lane. They load the external
ActiveGraph pack library onto a ``Runtime`` whose authoritative event log and
materialized graph projection both live in one SurrealDB database, then assert
that pack behaviors fire and that their outputs persist to the same database.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from activegraph_surrealdb import build_runtime
from activegraph_surrealdb.harness import HARNESS_PACK_MODULES

from .conftest import surreal_test_config

pytestmark = [pytest.mark.surrealdb, pytest.mark.packs]


@pytest.fixture(scope="module")
def config():
    return surreal_test_config()


def _scope(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def test_harness_pack_set_loads_into_one_runtime(config, pack_library_path) -> None:
    handle = build_runtime(
        config,
        run_id="run_all",
        scope=_scope("all"),
        modules=HARNESS_PACK_MODULES,
        root=pack_library_path,
    )
    try:
        assert len(handle.loaded_packs) == len(HARNESS_PACK_MODULES)
        assert "core" in handle.loaded_packs
        assert "composio" in handle.loaded_packs
        assert "diligence_core_bridge" in handle.loaded_packs

        # Every load_pack emits a pack.loaded event into the SurrealDB log.
        loaded_events = [e for e in handle.event_store.iter_events() if e.type == "pack.loaded"]
        assert len(loaded_events) == len(HARNESS_PACK_MODULES)
    finally:
        handle.close()


@pytest.mark.parametrize("module_name", HARNESS_PACK_MODULES)
def test_each_harness_pack_loads_individually(config, pack_library_path, module_name: str) -> None:
    handle = build_runtime(
        config,
        run_id="run_one",
        scope=_scope(f"one_{module_name.replace('.', '_')}"),
        modules=[module_name],
        root=pack_library_path,
    )
    try:
        assert handle.loaded_packs, f"packs.{module_name} registered no pack"
    finally:
        handle.close()


def test_core_pack_behaviors_project_into_surrealdb(config, pack_library_path) -> None:
    handle = build_runtime(
        config,
        run_id="run_core",
        scope=_scope("core"),
        modules=["core"],
        root=pack_library_path,
    )
    try:
        graph = handle.runtime.graph
        graph.add_object(
            "source",
            {
                "kind": "meeting_note",
                "content": "The team decided to ship the release on Friday.",
            },
        )
        handle.runtime.run_until_idle()

        # The event log is authoritative and durable in SurrealDB.
        assert handle.event_store.count() > 1
        types = [e.type for e in handle.event_store.iter_events()]
        assert "object.created" in types
        assert "runtime.idle" in types

        # The projection is materialized in the same database, not just in memory.
        projected = handle.graph_store.find_objects()
        assert projected, "core behaviors should have projected at least one object"
        assert handle.graph_store.find_objects("observation"), (
            "the observation_extractor behavior should have created observations"
        )
        assert all(handle.graph_store.get_object(o.id) is not None for o in projected)
    finally:
        handle.close()


def test_event_and_graph_stores_share_one_database(config, pack_library_path) -> None:
    scope = _scope("shared")
    handle = build_runtime(
        config,
        run_id="run_shared",
        scope=scope,
        modules=["core"],
        root=pack_library_path,
    )
    try:
        # One config, one namespace/database, one scope/run for both stores.
        assert handle.event_store.config is handle.graph_store.config
        assert handle.event_store.scope == handle.graph_store.scope == scope
        assert handle.event_store.run_id == handle.graph_store.run_id == "run_shared"

        handle.runtime.graph.add_object(
            "observation",
            {"text": "an observation that must be durable", "confidence": 0.9},
        )
        handle.runtime.run_until_idle()

        # A read from the graph store and a read from the event store both see
        # the same scope/run in the same database.
        assert handle.graph_store.find_objects("observation")
        assert handle.event_store.count() >= 1
    finally:
        handle.close()
