from __future__ import annotations

from uuid import uuid4

import pytest
from activegraph import Runtime
from activegraph.core.graph import Object

from activegraph_surrealdb import (
    SurrealEventStore,
    SurrealGraphStore,
    fork_event_store,
    replay_graph,
)


@pytest.mark.surrealdb
def test_fork_diff_and_replay_prove_projection_is_disposable(surreal_config) -> None:
    scope = f"experiment_{uuid4().hex}"
    parent_events = SurrealEventStore(surreal_config, run_id="parent", scope=scope)
    parent_projection = SurrealGraphStore(surreal_config, run_id="parent", scope=scope)
    parent_graph = replay_graph(parent_events, parent_projection)
    parent = Runtime(parent_graph, store=parent_events)

    person = parent.graph.add_object(
        "person",
        {"display_name": "Ada Lovelace", "resolution": "unreviewed"},
    )
    source = parent.graph.add_object("source_record", {"source": "crm", "name": "A. King"})
    fork_point = parent.graph.events[-1].id
    suffix = parent.graph.add_object("source_record", {"source": "mail", "name": "Ada L."})
    suffix_event_id = parent.graph.events[-1].id
    parent_prefix_ids = [event.id for event in parent.graph.events[:2]]
    parent_before_fork = [event.id for event in parent.graph.events]

    expected_prefix = list(parent_events.iter_events(until=fork_point))
    parent_events_before_fork = list(parent_events.iter_events())
    parent_integrity_before_fork = parent_events.verify_integrity()
    child_events = fork_event_store(
        parent_events,
        new_run_id="alternate",
        at_event_id=fork_point,
        label="alternate identity resolution",
    )
    actual_prefix = list(child_events.iter_events())
    assert actual_prefix == expected_prefix
    assert list(parent_events.iter_events()) == parent_events_before_fork
    assert parent_events.verify_integrity() == parent_integrity_before_fork
    child_integrity = child_events.verify_integrity()
    assert child_integrity.event_count == len(expected_prefix)
    assert child_integrity.next_seq == len(expected_prefix)
    assert child_integrity.head_hash is not None
    child_projection = SurrealGraphStore(surreal_config, run_id="alternate", scope=scope)
    child_graph = replay_graph(child_events, child_projection)
    child = Runtime(child_graph, store=child_events)

    parent.graph.patch_object(person.id, {"resolution": "merge"})
    parent_patch_event_id = parent.graph.events[-1].id
    child.graph.patch_object(person.id, {"resolution": "keep_separate"})
    child_patch_event_id = child.graph.events[-1].id

    difference = parent.diff(child)
    before = sorted((obj.id, obj.type, obj.data) for obj in parent.graph.all_objects())

    ghost = parent_projection.get_object(person.id)
    assert ghost is not None
    parent_projection.put_object(
        type(ghost)(
            id="ghost#1",
            type="ghost",
            data={"must": "be removed"},
            version=1,
            provenance={},
        )
    )
    rebuilt_projection = SurrealGraphStore(surreal_config, run_id="parent", scope=scope)
    rebuilt_graph = replay_graph(parent_events, rebuilt_projection)
    after = sorted((obj.id, obj.type, obj.data) for obj in rebuilt_graph.all_objects())

    assert difference.is_identical is False
    assert [event.id for event in difference.shared_events] == parent_prefix_ids
    assert [event.id for event in difference.parent_only_events] == [
        suffix_event_id,
        parent_patch_event_id,
    ]
    assert [event.id for event in difference.fork_only_events] == [child_patch_event_id]
    assert [item.id for item in difference.divergent_objects] == [person.id, suffix.id]
    assert before == after
    assert rebuilt_graph.get_object("ghost#1") is None
    assert rebuilt_projection.projection_status() == "ready"
    assert parent_events.count() == len(parent.graph.events)
    assert [event.id for event in child_events.iter_events()] == parent_prefix_ids + [
        child_patch_event_id
    ]
    assert [event.id for event in parent_events.iter_events()][:3] == parent_before_fork
    parent_suffix_event = parent_events.get_event(suffix_event_id)
    assert parent_suffix_event is not None
    assert parent_suffix_event not in list(child_events.iter_events())
    assert child.graph.get_object(suffix.id) is None
    assert child.graph.get_object(source.id) is not None
    child_run = child_events.get_run()
    assert child_run is not None
    assert child_run.parent_run_id == "parent"
    assert child_run.forked_at_event_id == fork_point
    assert child_run.label == "alternate identity resolution"

    parent_events.close()
    child_events.close()
    parent_projection.close()
    child_projection.close()
    rebuilt_projection.close()


@pytest.mark.surrealdb
def test_failed_replay_marks_projection_incomplete(surreal_config) -> None:
    scope = f"failed_replay_{uuid4().hex}"
    events = SurrealEventStore(surreal_config, run_id="run", scope=scope)
    source_projection = SurrealGraphStore(surreal_config, run_id="run", scope=scope)
    graph = replay_graph(events, source_projection)
    runtime = Runtime(graph, store=events)
    runtime.graph.add_object("person", {"name": "Ada"})

    class FailingHistory:
        def __init__(self) -> None:
            self.run_id = "run"
            self.scope = scope

        def iter_events(self):
            yield next(events.iter_events())
            raise RuntimeError("injected replay failure")

    target = SurrealGraphStore(surreal_config, run_id="run", scope=scope)
    with pytest.raises(RuntimeError, match="injected replay failure"):
        replay_graph(FailingHistory(), target)

    assert target.projection_status() == "failed"
    with pytest.raises(Exception, match="incomplete|failed"):
        target.all_objects()

    target.close()
    reopened = SurrealGraphStore(surreal_config, run_id="run", scope=scope)
    assert reopened.projection_status() == "failed"
    with pytest.raises(Exception, match="incomplete|failed"):
        reopened.all_objects()

    events.close()
    source_projection.close()
    reopened.close()


@pytest.mark.surrealdb
@pytest.mark.parametrize(
    ("event_run", "event_scope", "projection_run", "projection_scope"),
    [
        ("source", "scope_a", "target", "scope_a"),
        ("source", "scope_a", "source", "scope_b"),
    ],
)
def test_replay_rejects_run_or_scope_mismatch_before_clearing_target(
    surreal_config,
    event_run,
    event_scope,
    projection_run,
    projection_scope,
) -> None:
    suffix = uuid4().hex
    events = SurrealEventStore(
        surreal_config,
        run_id=f"{event_run}_{suffix}",
        scope=f"{event_scope}_{suffix}",
    )
    projection = SurrealGraphStore(
        surreal_config,
        run_id=f"{projection_run}_{suffix}",
        scope=f"{projection_scope}_{suffix}",
    )
    projection.mark_ready()
    projection.put_object(Object("sentinel#1", "sentinel", {"preserve": True}, 1, {}))

    with pytest.raises(ValueError, match="scope|run"):
        replay_graph(events, projection)

    assert projection.get_object("sentinel#1") is not None
    events.close()
    projection.close()
