from __future__ import annotations

from uuid import uuid4

import pytest
from activegraph.core.event import Event
from activegraph.core.graph import Relation

from activegraph_surrealdb import (
    SurrealEventStore,
    SurrealGraphStore,
    fork_event_store,
    replay_graph,
)


def _object_event(event_id: str, object_id: str, name: str) -> Event:
    return Event(
        id=event_id,
        type="object.created",
        payload={
            "id": object_id,
            "object": {
                "id": object_id,
                "type": "person",
                "data": {"name": name, "nullable": None},
                "version": 1,
                "provenance": {
                    "created_by": "test",
                    "caused_by_event": None,
                    "frame_id": None,
                    "timestamp": "2026-08-30T12:00:00Z",
                    "evidence": [],
                    "run_id": "logical-collision",
                },
            },
        },
        actor="test",
        frame_id=None,
        caused_by=None,
        timestamp="2026-08-30T12:00:00Z",
    )


@pytest.mark.surrealdb
def test_identical_logical_ids_cannot_cross_scope_or_run(surreal_config) -> None:
    nonce = uuid4().hex
    scope_a = f"scope_a_{nonce}'; DELETE ag_event; --"
    scope_b = f"scope_b_{nonce}"
    run_a = f"run_{nonce}:same"
    run_b = f"run_{nonce}:other"
    shared_event_id = "evt_'same'; DELETE ag_run; --"
    shared_object_id = "person#'same'; DELETE ag_graph_object; --"
    shared_relation_id = "rel#'same'; DELETE ag_relation; --"

    a = SurrealEventStore(surreal_config, run_id=run_a, scope=scope_a)
    b = SurrealEventStore(surreal_config, run_id=run_a, scope=scope_b)
    c = SurrealEventStore(surreal_config, run_id=run_b, scope=scope_a)
    a.append(_object_event(shared_event_id, shared_object_id, "scope A"))
    b.append(_object_event(shared_event_id, shared_object_id, "scope B"))
    c.append(_object_event(shared_event_id, shared_object_id, "run B"))
    a.append(_object_event("evt_tail", "person#tail", "tail"))

    child = fork_event_store(
        a,
        new_run_id="child_'quoted",
        at_event_id=shared_event_id,
        label="scope A child",
    )
    b_child = SurrealEventStore(
        surreal_config,
        run_id="child_'quoted",
        scope=scope_b,
    )
    assert b_child.count() == 0

    a.truncate_after(shared_event_id)

    assert a.count() == b.count() == c.count() == child.count() == 1
    assert a.get_event(shared_event_id).payload["object"]["data"]["name"] == "scope A"
    assert b.get_event(shared_event_id).payload["object"]["data"]["name"] == "scope B"
    assert c.get_event(shared_event_id).payload["object"]["data"]["name"] == "run B"
    assert child.get_event(shared_event_id).payload["object"]["data"]["name"] == "scope A"

    projections = {
        "a": SurrealGraphStore(surreal_config, run_id=run_a, scope=scope_a),
        "b": SurrealGraphStore(surreal_config, run_id=run_a, scope=scope_b),
        "c": SurrealGraphStore(surreal_config, run_id=run_b, scope=scope_a),
        "child": SurrealGraphStore(surreal_config, run_id="child_'quoted", scope=scope_a),
    }
    graphs = {
        "a": replay_graph(a, projections["a"]),
        "b": replay_graph(b, projections["b"]),
        "c": replay_graph(c, projections["c"]),
        "child": replay_graph(child, projections["child"]),
    }

    assert graphs["a"].get_object(shared_object_id).data["name"] == "scope A"
    assert graphs["b"].get_object(shared_object_id).data["name"] == "scope B"
    assert graphs["c"].get_object(shared_object_id).data["name"] == "run B"
    assert graphs["child"].get_object(shared_object_id).data["name"] == "scope A"

    for label, confidence in (("a", 0.1), ("b", 0.2), ("c", 0.3), ("child", 0.4)):
        projections[label].put_relation(
            Relation(
                shared_relation_id,
                shared_object_id,
                "person#missing_'quoted",
                "observed_with_'quoted",
                {"confidence": confidence},
                {"source": label},
            )
        )
    assert projections["a"].get_relation(shared_relation_id).data == {"confidence": 0.1}
    assert projections["b"].get_relation(shared_relation_id).data == {"confidence": 0.2}
    assert projections["c"].get_relation(shared_relation_id).data == {"confidence": 0.3}
    assert projections["child"].get_relation(shared_relation_id).data == {"confidence": 0.4}

    projections["a"].clear()
    assert projections["a"].all_objects() == []
    assert projections["b"].get_object(shared_object_id).data["name"] == "scope B"
    assert projections["c"].get_object(shared_object_id).data["name"] == "run B"
    assert projections["child"].get_object(shared_object_id).data["name"] == "scope A"
    assert projections["b"].get_relation(shared_relation_id) is not None
    assert projections["c"].get_relation(shared_relation_id) is not None
    assert projections["child"].get_relation(shared_relation_id) is not None

    for store in (a, b, c, child, b_child):
        store.close()
    for projection in projections.values():
        projection.close()
