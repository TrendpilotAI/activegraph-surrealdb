from __future__ import annotations

import subprocess
from uuid import uuid4

import pytest
from activegraph.core.event import Event
from activegraph.core.graph import Object, Relation

from activegraph_surrealdb import (
    SurrealEventStore,
    SurrealGraphStore,
    wait_until_ready,
    wait_until_unavailable,
)


def _event(event_id: str) -> Event:
    return Event(
        id=event_id,
        type="qualification.recorded",
        payload={"explicit_null": None, "unicode": "東京"},
        actor="qualification",
        frame_id=None,
        caused_by=None,
        timestamp="2026-08-30T12:00:00Z",
    )


def _control_container(container: str, action: str) -> None:
    subprocess.run(
        ["docker", action, container],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.qualification
def test_rocksdb_survives_restart_and_abrupt_container_termination(
    qualification_environment,
) -> None:
    config = qualification_environment.config
    container = qualification_environment.container
    scope = f"restart_{uuid4().hex}"
    events = SurrealEventStore(config, run_id="run", scope=scope)
    graph = SurrealGraphStore(config, run_id="run", scope=scope)
    events.append(_event("evt_1"))
    canonical_payload = '{"explicit_null":null,"unicode":"東京"}'
    graph.put_object(Object("person#1", "person", {"name": "Ada"}, 1, {}))
    graph.put_relation(Relation("rel#1", "person#1", "company#1", "works_at", {}, {}))
    before = events.verify_integrity()
    events.close()
    graph.close()

    _control_container(container, "restart")
    wait_until_ready(config, timeout_seconds=30)

    reopened_events = SurrealEventStore(config, run_id="run", scope=scope)
    reopened_graph = SurrealGraphStore(config, run_id="run", scope=scope)
    assert reopened_events.verify_integrity() == before
    assert reopened_events.get_event("evt_1") == _event("evt_1")
    assert reopened_events.raw_query(
        "SELECT VALUE payload_json FROM ag_event "
        "WHERE scope = $scope AND run_id = $run_id AND event_id = $event_id;",
        {"scope": scope, "run_id": "run", "event_id": "evt_1"},
    ) == [canonical_payload]
    assert reopened_graph.get_object("person#1").data == {"name": "Ada"}
    assert reopened_graph.get_relation("rel#1").type == "works_at"
    reopened_events.append(_event("evt_2"))
    reopened_events.close()
    reopened_graph.close()

    _control_container(container, "kill")
    wait_until_unavailable(config, timeout_seconds=15)
    subprocess.run(
        ["docker", "rm", container],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--name",
            container,
            "--publish",
            f"127.0.0.1:{qualification_environment.host_port}:8000",
            "--volume",
            f"{qualification_environment.volume}:/data",
            "--env",
            f"SURREAL_USER={config.username}",
            "--env",
            f"SURREAL_PASS={config.password}",
            qualification_environment.image,
            "start",
            "--bind",
            "0.0.0.0:8000",
            "rocksdb:///data/activegraph.db",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    wait_until_ready(config, timeout_seconds=30)

    recovered = SurrealEventStore(config, run_id="run", scope=scope)
    assert [event.id for event in recovered.iter_events()] == ["evt_1", "evt_2"]
    assert recovered.verify_integrity().next_seq == 2
    assert recovered.schema_version() == "1"
    assert recovered.raw_query(
        "SELECT VALUE payload_json FROM ag_event "
        "WHERE scope = $scope AND run_id = $run_id AND event_id = $event_id;",
        {"scope": scope, "run_id": "run", "event_id": "evt_1"},
    ) == [canonical_payload]
    recovered.close()
