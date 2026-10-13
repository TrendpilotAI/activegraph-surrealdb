from __future__ import annotations

import json
import subprocess
from typing import Any
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


def _inspect_container(container: str) -> dict[str, Any]:
    result = subprocess.run(
        ["docker", "inspect", container],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)[0]


@pytest.mark.qualification
def test_storage_survives_restart_and_abrupt_container_termination(
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

    # Capture the lane's full start configuration before the kill so the
    # re-creation is faithful on every lane (TLS flags and certificate
    # mounts included) instead of approximating the default lane.
    inspection = _inspect_container(container)
    original_command = inspection["Config"]["Cmd"]
    tls_mounts = [
        mount for mount in inspection["Mounts"] if mount["Destination"].startswith("/tls/")
    ]

    _control_container(container, "kill")
    wait_until_unavailable(config, timeout_seconds=15)
    _control_container(container, "rm")
    recreate = [
        "docker",
        "run",
        "--detach",
        "--name",
        container,
        "--publish",
        f"127.0.0.1:{qualification_environment.host_port}:8000",
        "--mount",
        f"type=volume,src={qualification_environment.volume},dst=/data,volume-nocopy",
        "--env",
        f"SURREAL_USER={config.username}",
        "--env",
        f"SURREAL_PASS={config.password}",
    ]
    for mount in tls_mounts:
        recreate += [
            "--mount",
            f"type=bind,src={mount['Source']},dst={mount['Destination']},readonly",
        ]
    recreate.append(qualification_environment.image)
    recreate.extend(original_command)
    subprocess.run(recreate, check=True, capture_output=True, text=True)
    wait_until_ready(config, timeout_seconds=30)

    recovered = SurrealEventStore(config, run_id="run", scope=scope)
    assert [event.id for event in recovered.iter_events()] == ["evt_1", "evt_2"]
    assert recovered.verify_integrity().next_seq == 2
    assert recovered.schema_version() == "3"
    assert recovered.raw_query(
        "SELECT VALUE payload_json FROM ag_event "
        "WHERE scope = $scope AND run_id = $run_id AND event_id = $event_id;",
        {"scope": scope, "run_id": "run", "event_id": "evt_1"},
    ) == [canonical_payload]
    recovered.close()
