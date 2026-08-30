from __future__ import annotations

from uuid import uuid4

import pytest
from activegraph.core.event import Event

from activegraph_surrealdb import (
    SurrealEventStore,
    SurrealGraphStore,
    fork_event_store,
    replay_graph,
)
from activegraph_surrealdb.errors import (
    ForkPointNotFoundError,
    RunAlreadyExistsError,
    UnsupportedCompactedRunError,
)


@pytest.mark.surrealdb
def test_replay_and_fork_fail_closed_for_compacted_snapshot_logs(surreal_config) -> None:
    scope = f"compacted_{uuid4().hex}"
    events = SurrealEventStore(surreal_config, run_id="parent", scope=scope)
    snapshot = Event(
        id="evt_snapshot",
        type="runtime.snapshot",
        payload={"state_hash": "0" * 64},
        actor="runtime",
        frame_id=None,
        caused_by=None,
        timestamp="2026-08-30T12:00:00Z",
    )
    events.append(snapshot)
    projection = SurrealGraphStore(surreal_config, run_id="parent", scope=scope)

    with pytest.raises(UnsupportedCompactedRunError, match="runtime.snapshot"):
        replay_graph(events, projection)
    with pytest.raises(UnsupportedCompactedRunError, match="runtime.snapshot"):
        fork_event_store(
            events,
            new_run_id="child",
            at_event_id=snapshot.id,
        )

    assert projection.projection_status() == "failed"
    events.close()
    projection.close()


@pytest.mark.surrealdb
def test_missing_cutoff_and_existing_destination_leave_no_partial_child(
    surreal_config,
) -> None:
    scope = f"fork_boundary_{uuid4().hex}"
    parent = SurrealEventStore(surreal_config, run_id="parent", scope=scope)
    parent.append(
        Event(
            id="evt_1",
            type="audit.recorded",
            payload={"value": 1},
            actor="test",
            frame_id="frame_1",
            caused_by=None,
            timestamp="2026-08-30T12:00:00Z",
        )
    )
    parent_run_before = parent.get_run()
    parent_integrity_before = parent.verify_integrity()
    parent_events_before = list(parent.iter_events())

    with pytest.raises(ForkPointNotFoundError, match="evt_missing"):
        fork_event_store(
            parent,
            new_run_id="missing_child",
            at_event_id="evt_missing",
        )
    assert (
        SurrealEventStore.run_exists(surreal_config, run_id="missing_child", scope=scope) is False
    )
    assert SurrealEventStore.inspect_run_artifacts(
        surreal_config, run_id="missing_child", scope=scope
    ) == {"run_records": 0, "event_records": 0}
    assert parent.get_run() == parent_run_before
    assert parent.verify_integrity() == parent_integrity_before
    assert list(parent.iter_events()) == parent_events_before

    existing = SurrealEventStore(surreal_config, run_id="existing_child", scope=scope)
    existing.append(
        Event(
            id="evt_existing",
            type="audit.recorded",
            payload={"value": "preserve"},
            actor="test",
            frame_id=None,
            caused_by=None,
            timestamp="2026-08-30T12:01:00Z",
        )
    )
    before = list(existing.iter_events())
    existing_run_before = existing.get_run()
    existing_integrity_before = existing.verify_integrity()
    with pytest.raises(RunAlreadyExistsError, match="existing_child"):
        fork_event_store(
            parent,
            new_run_id="existing_child",
            at_event_id="evt_1",
        )
    assert list(existing.iter_events()) == before
    assert existing.get_run() == existing_run_before
    assert existing.verify_integrity() == existing_integrity_before
    assert parent.get_run() == parent_run_before
    assert parent.verify_integrity() == parent_integrity_before
    assert list(parent.iter_events()) == parent_events_before

    parent.close()
    existing.close()
