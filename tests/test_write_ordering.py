from __future__ import annotations

import pytest
from activegraph import Graph
from activegraph.core.event import Event
from activegraph.core.graph_store import InMemoryGraphStore


class RejectingEventStore:
    run_id = "run"

    def append(self, event: Event) -> None:
        raise RuntimeError("simulated durable append failure")


def test_append_failure_does_not_leave_projected_state() -> None:
    """ActiveGraph 1.12 persists before projecting (CONTRACT v1.11 #2).

    A rejected durable append must leave no projected object behind. This was
    a strict expected failure on ActiveGraph 1.10, which projected a live event
    before ``EventStore.append()``; the append-first ordering in 1.12 closes
    that gap, so the provider now asserts the stronger invariant directly.
    """

    projection = InMemoryGraphStore()
    graph = Graph(run_id="run", graph_store=projection)
    graph.attach_store(RejectingEventStore())

    with pytest.raises(RuntimeError, match="simulated"):
        graph.add_object("person", {"name": "must not survive"})

    assert projection.all_objects() == []
