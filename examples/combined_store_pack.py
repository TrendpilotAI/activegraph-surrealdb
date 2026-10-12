"""Load a pack onto one SurrealDB database that holds the events *and* the graph.

This is the smallest end-to-end demonstration of the provider's central claim:
a real ActiveGraph ``Runtime`` runs with its authoritative event log and its
materialized graph projection in a single SurrealDB deployment, so a pack
behavior's event and the objects it projects can be read back from the same
database with native SurrealQL queries.
"""

from __future__ import annotations

import json
import os
from typing import Any
from uuid import uuid4

from activegraph_surrealdb import SurrealConfig, build_runtime
from activegraph_surrealdb.harness import PackHarnessError, packs_path


def required_environment(name: str) -> str:
    """Return a non-empty environment value or explain how to provide it."""
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is required; export the values from .env first")
    return value


def main() -> int:
    try:
        root = packs_path()
    except PackHarnessError as exc:
        raise SystemExit(str(exc)) from exc

    config = SurrealConfig(
        endpoint=os.getenv("ACTIVEGRAPH_SURREALDB_TEST_URL", "ws://127.0.0.1:8000"),
        namespace=os.getenv("ACTIVEGRAPH_SURREALDB_TEST_NAMESPACE", "activegraph"),
        database=os.getenv("ACTIVEGRAPH_SURREALDB_TEST_DATABASE", "provider_test"),
        username=os.getenv("ACTIVEGRAPH_SURREALDB_TEST_USERNAME", "root"),
        password=required_environment("ACTIVEGRAPH_SURREALDB_TEST_PASSWORD"),
    )
    scope = f"combined_{uuid4().hex}"

    handle = build_runtime(config, run_id="run", scope=scope, modules=["core"], root=root)
    try:
        source = handle.runtime.graph.add_object(
            "source",
            {
                "kind": "meeting_note",
                "content": (
                    "The team decided to ship the release on Friday. "
                    "We prefer dark mode in every editor."
                ),
            },
        )
        handle.runtime.run_until_idle()

        # The event log and the projection are two tables in the same database.
        events = list(handle.event_store.iter_events())
        observations = handle.graph_store.find_objects("observation")
        memory_candidates = handle.graph_store.find_objects("memory_candidate")

        # A pushed-down traversal from the source, resolved in SurrealQL.
        reachable_objects, reachable_relations = handle.graph_store.neighborhood(source.id, depth=2)
        grounds = handle.graph_store.find_relations(source=source.id, type="grounds")
        chains = handle.graph_store.match_chain([None, None], [("grounds", "right")])

        trace: dict[str, Any] = {
            "scope": scope,
            "packs_loaded": handle.loaded_packs,
            "event_log": {
                "count": len(events),
                "types": sorted({event.type for event in events}),
            },
            "projection": {
                "objects": {
                    "source": len(handle.graph_store.find_objects("source")),
                    "observation": len(observations),
                    "memory_candidate": len(memory_candidates),
                }
            },
            "pushdown": {
                "neighborhood_objects": sorted(o.id for o in reachable_objects),
                "neighborhood_relations": sorted(r.id for r in reachable_relations),
                "grounds_relations": sorted(r.id for r in grounds),
                "grounds_chains": sorted(
                    (tuple(o.id for o in chain.objects), tuple(r.id for r in chain.relations))
                    for chain in chains
                ),
            },
        }

        # The behavior cascade produced events, objects, and native edges.
        assert events, "the runtime should have appended events"
        assert "object.created" in trace["event_log"]["types"]
        assert observations, "observation_extractor should create observations"
        assert grounds, "observations should be grounded in the source"
        assert any(chain.objects[0].id == source.id for chain in chains)
        assert source.id in {o.id for o in reachable_objects}

        print(json.dumps(trace, indent=2, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
