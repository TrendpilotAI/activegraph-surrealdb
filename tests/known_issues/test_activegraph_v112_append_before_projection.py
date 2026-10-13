"""Append-before-projection contract for ActiveGraph v1.12.

Converted from the v1.10 known-issue xfail in
``test_activegraph_v110_ordering.py`` (upstream PR #78, released in 1.12.0).
``Graph.emit`` appends to the event store before projecting, so a failed
append must leave no accepted event, graph, or projection state behind.
"""

from __future__ import annotations

import pytest
from activegraph import Graph, Object
from activegraph.core.event import Event
from activegraph.core.graph_store import InMemoryGraphStore


class RejectingEventStore:
    run_id = "run"

    def append(self, event: Event) -> None:
        raise RuntimeError("simulated durable append failure")


class RecordingEventStore:
    run_id = "run"

    def __init__(self, timeline: list[str]) -> None:
        self.timeline = timeline
        self.events: list[Event] = []

    def append(self, event: Event) -> None:
        self.timeline.append("store:append")
        self.events.append(event)


class RecordingGraphStore(InMemoryGraphStore):
    def __init__(self, timeline: list[str]) -> None:
        super().__init__()
        self.timeline = timeline

    def put_object(self, obj: Object) -> None:
        self.timeline.append("projection:put_object")
        super().put_object(obj)


def test_failed_append_changes_no_projection_or_log_state() -> None:
    projection = InMemoryGraphStore()
    graph = Graph(run_id="run", graph_store=projection)
    graph.attach_store(RejectingEventStore())

    with pytest.raises(RuntimeError, match="simulated"):
        graph.add_object("person", {"name": "must not survive"})

    assert projection.all_objects() == []
    assert graph.all_objects() == []
    assert graph.events == []


def test_emit_appends_to_the_store_before_projecting() -> None:
    timeline: list[str] = []
    projection = RecordingGraphStore(timeline)
    store = RecordingEventStore(timeline)
    graph = Graph(run_id="run", graph_store=projection)
    graph.attach_store(store)

    graph.add_object("person", {"name": "Ada"})

    assert timeline == ["store:append", "projection:put_object"]
    assert [event.type for event in store.events] == ["object.created"]
    assert [obj.type for obj in projection.all_objects()] == ["person"]
