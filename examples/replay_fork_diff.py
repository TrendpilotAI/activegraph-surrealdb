"""Run a small fork/diff/replay experiment against a disposable SurrealDB."""

from __future__ import annotations

import json
import os
from typing import Any
from uuid import uuid4

from activegraph import Runtime
from activegraph.core.graph import Graph, Object

from activegraph_surrealdb import (
    SurrealConfig,
    SurrealEventStore,
    SurrealGraphStore,
    fork_event_store,
    replay_graph,
)


def required_environment(name: str) -> str:
    """Return a non-empty environment value or explain how to provide it."""
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is required; export the values from .env first")
    return value


def graph_snapshot(graph: Graph) -> list[tuple[str, str, dict[str, Any]]]:
    """Normalize projected objects into a deterministic comparison value."""
    return sorted((obj.id, obj.type, obj.data) for obj in graph.all_objects())


def main() -> int:
    config = SurrealConfig(
        endpoint=os.getenv("ACTIVEGRAPH_SURREALDB_TEST_URL", "ws://127.0.0.1:8000"),
        namespace=os.getenv("ACTIVEGRAPH_SURREALDB_TEST_NAMESPACE", "activegraph"),
        database=os.getenv("ACTIVEGRAPH_SURREALDB_TEST_DATABASE", "provider_test"),
        username=os.getenv("ACTIVEGRAPH_SURREALDB_TEST_USERNAME", "root"),
        password=required_environment("ACTIVEGRAPH_SURREALDB_TEST_PASSWORD"),
    )
    scope = f"experiment_{uuid4().hex}"
    resources: list[SurrealEventStore | SurrealGraphStore] = []

    try:
        parent_events = SurrealEventStore(config, run_id="parent", scope=scope)
        resources.append(parent_events)
        parent_projection = SurrealGraphStore(config, run_id="parent", scope=scope)
        resources.append(parent_projection)
        parent_graph = replay_graph(parent_events, parent_projection)
        parent = Runtime(parent_graph, store=parent_events)

        decision = parent.graph.add_object(
            "decision",
            {"subject": "synthetic-case", "resolution": "unreviewed"},
        )
        fork_point = parent.graph.events[-1].id

        child_events = fork_event_store(
            parent_events,
            new_run_id="alternate",
            at_event_id=fork_point,
            label="alternate review outcome",
        )
        resources.append(child_events)
        child_projection = SurrealGraphStore(config, run_id="alternate", scope=scope)
        resources.append(child_projection)
        child_graph = replay_graph(child_events, child_projection)
        child = Runtime(child_graph, store=child_events)

        parent.graph.patch_object(decision.id, {"resolution": "accept"})
        child.graph.patch_object(decision.id, {"resolution": "needs_review"})
        difference = parent.diff(child)
        before = graph_snapshot(parent.graph)

        parent_projection.put_object(
            Object(
                id="ghost#not-in-events",
                type="ghost",
                data={"should_survive": False},
                version=1,
                provenance={},
            )
        )
        rebuilt_graph = replay_graph(parent_events, parent_projection)
        after = graph_snapshot(rebuilt_graph)
        integrity = parent_events.verify_integrity()

        trace: dict[str, Any] = {
            "scope": scope,
            "fork": {
                "shared_event_ids": [event.id for event in difference.shared_events],
                "parent_only_event_ids": [event.id for event in difference.parent_only_events],
                "fork_only_event_ids": [event.id for event in difference.fork_only_events],
                "divergent_object_ids": [item.id for item in difference.divergent_objects],
            },
            "projection": {
                "rebuilt_exactly": before == after,
                "ghost_removed": rebuilt_graph.get_object("ghost#not-in-events") is None,
                "status": parent_projection.projection_status(),
            },
            "event_log": {
                "event_count": integrity.event_count,
                "next_sequence": integrity.next_seq,
                "head_hash": integrity.head_hash,
            },
        }

        assert trace["fork"]["shared_event_ids"] == [fork_point]
        assert trace["fork"]["parent_only_event_ids"]
        assert trace["fork"]["fork_only_event_ids"]
        assert trace["fork"]["divergent_object_ids"] == [decision.id]
        assert trace["projection"] == {
            "rebuilt_exactly": True,
            "ghost_removed": True,
            "status": "ready",
        }

        print(json.dumps(trace, indent=2, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        for resource in reversed(resources):
            resource.close()


if __name__ == "__main__":
    raise SystemExit(main())
