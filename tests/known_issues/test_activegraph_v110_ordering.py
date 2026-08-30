from __future__ import annotations

import pytest
from activegraph import Graph
from activegraph.core.event import Event
from activegraph.core.graph_store import InMemoryGraphStore


class RejectingEventStore:
    run_id = "run"

    def append(self, event: Event) -> None:
        raise RuntimeError("simulated durable append failure")


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "ActiveGraph v1.10 projects before EventStore.append; PR #78 changes "
        "the baseline order but an optional shared transaction contract is still open"
    ),
)
def test_v110_append_failure_does_not_leave_projected_state() -> None:
    projection = InMemoryGraphStore()
    graph = Graph(run_id="run", graph_store=projection)
    graph.attach_store(RejectingEventStore())

    with pytest.raises(RuntimeError, match="simulated"):
        graph.add_object("person", {"name": "must not survive"})

    assert projection.all_objects() == []
