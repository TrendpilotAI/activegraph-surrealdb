"""Lane B (in-place upgrade) evidence for ADR 0003: 3.2.4 -> 3.3.0 on one volume.

Non-gating exploration-lane evidence, selected only with ``-m upgrade``
(ladder step V5); the default selection never runs it. The lane provisions its
own RocksDB volume and containers from the existing qualification environment
variables, so it never shares storage with any other backend or lane.

``surreal export`` is a client command that needs a live endpoint, so the
verified export is captured via ``docker exec`` while 3.2.4 is still serving,
immediately before the graceful stop — matching the ADR 0003 evidence order:
data written under 3.2.4, a verified pre-upgrade export, an in-place 3.3.0
start. A final phase restores that export into a fresh 3.2.4 datastore and
re-verifies the hash chain, rehearsing the only documented route back to 3.2.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from activegraph import Runtime

from activegraph_surrealdb import (
    SurrealConfig,
    SurrealEventStore,
    SurrealGraphStore,
    replay_graph,
    wait_until_ready,
)

LEGACY_IMAGE = "surrealdb/surrealdb:v3.2.4"
UPGRADE_IMAGE = "surrealdb/surrealdb:v3.3.0"
VOLUME_INIT_IMAGE = "busybox:1.37.0"
# ADR 0003 makes the upgrade lane a RocksDB lane; the lane always provisions
# its own volume, so this location is never shared with another engine.
STORAGE_URL = "rocksdb:///data/activegraph.db"
LEGACY_START_TIMEOUT_SECONDS = 60
UPGRADE_START_TIMEOUT_SECONDS = 120


def _docker(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *arguments],
        check=check,
        capture_output=True,
        text=True,
    )


def _create_fresh_volume(volume: str) -> None:
    """Create an empty volume and pre-permit the datastore directory."""
    _docker("volume", "create", volume)
    _docker(
        "run",
        "--rm",
        "--mount",
        f"type=volume,src={volume},dst=/data,volume-nocopy",
        VOLUME_INIT_IMAGE,
        "chmod",
        "0777",
        "/data",
    )


def _published_port(container: str) -> int:
    listing = _docker("port", container, "8000").stdout.strip()
    assert listing != ""
    assert "\n" not in listing
    return int(listing.rsplit(":", 1)[1])


def _start_server(
    image: str,
    container: str,
    volume: str,
    basis: SurrealConfig,
    *,
    timeout_seconds: float,
) -> SurrealConfig:
    """Start one lane server on an ephemeral port and wait until it serves."""
    _docker(
        "run",
        "--detach",
        "--name",
        container,
        "--publish",
        "127.0.0.1::8000",
        "--mount",
        f"type=volume,src={volume},dst=/data,volume-nocopy",
        "--env",
        f"SURREAL_USER={basis.username}",
        "--env",
        f"SURREAL_PASS={basis.password}",
        image,
        "start",
        "--bind",
        "0.0.0.0:8000",
        STORAGE_URL,
    )
    config = SurrealConfig(
        endpoint=f"ws://127.0.0.1:{_published_port(container)}",
        namespace=basis.namespace,
        database=basis.database,
        username=basis.username,
        password=basis.password,
    )
    wait_until_ready(config, timeout_seconds=timeout_seconds)
    return config


def _export_database(container: str, config: SurrealConfig) -> str:
    """Dump the namespace/database as a SurrealQL script from inside the container.

    ``--log none`` is required: the CLI writes its own INFO logs to stdout, so
    without it the captured "export" is prefixed and suffixed with timestamped
    log lines that ``surreal import`` rejects as a parse error.
    """
    return _docker(
        "exec",
        container,
        "/surreal",
        "export",
        "-e",
        "http://127.0.0.1:8000",
        "-u",
        config.username,
        "-p",
        config.password,
        "--namespace",
        config.namespace,
        "--database",
        config.database,
        "--log",
        "none",
    ).stdout


def _import_export(container: str, export_path: Path, config: SurrealConfig) -> None:
    """Restore a SurrealQL script; import reads a file from inside the container."""
    container_path = f"/tmp/{export_path.name}"
    _docker("cp", str(export_path), f"{container}:{container_path}")
    _docker(
        "exec",
        container,
        "/surreal",
        "import",
        container_path,
        "-e",
        "http://127.0.0.1:8000",
        "-u",
        config.username,
        "-p",
        config.password,
        "--namespace",
        config.namespace,
        "--database",
        config.database,
    )


def _verify_export(export_text: str, expected_event_ids: list[str], scope: str) -> None:
    assert export_text.strip() != ""
    for table in ("ag_run", "ag_event", "ag_vertex", "ag_projection"):
        assert table in export_text
    assert scope in export_text
    assert "Ada King" in export_text
    for event_id in expected_event_ids:
        assert event_id in export_text


def _stop_and_remove(container: str) -> None:
    """Stop one container gracefully, then free the name for re-creation."""
    _docker("stop", container)
    _docker("rm", container)


def _discard(container: str, volume: str) -> None:
    """Best-effort teardown so a failed lane never leaks containers or volumes."""
    _docker("rm", "--force", container, check=False)
    _docker("volume", "rm", volume, check=False)


@pytest.mark.upgrade
def test_in_place_upgrade_from_3_2_4_to_3_3_0(
    qualification_environment,
    tmp_path,
) -> None:
    environment = qualification_environment
    suffix = uuid4().hex[:8]
    scope = f"upgrade_{suffix}"
    run_id = "upgrade"
    container = f"{environment.container}_upgrade_{suffix}"
    volume = f"{environment.volume}_upgrade_{suffix}"
    restore_container = f"{environment.container}_restore_{suffix}"
    restore_volume = f"{environment.volume}_restore_{suffix}"
    export_path = tmp_path / "upgrade-lane-export.surql"

    try:
        # Phase 1: 3.2.4 on a fresh volume writes a replayable event history.
        _create_fresh_volume(volume)
        legacy_config = _start_server(
            LEGACY_IMAGE,
            container,
            volume,
            environment.config,
            timeout_seconds=LEGACY_START_TIMEOUT_SECONDS,
        )
        events = SurrealEventStore(legacy_config, run_id=run_id, scope=scope)
        projection = SurrealGraphStore(legacy_config, run_id=run_id, scope=scope)
        graph = replay_graph(events, projection)
        runtime = Runtime(graph, store=events)
        person = runtime.graph.add_object(
            "person",
            {"display_name": "Ada Lovelace", "resolution": "unreviewed"},
        )
        runtime.graph.add_object("source_record", {"source": "crm", "name": "Ada King"})
        runtime.graph.patch_object(person.id, {"resolution": "merge"})
        expected_event_ids = [event.id for event in events.iter_events()]
        expected_integrity = events.verify_integrity()
        expected_objects = sorted((obj.id, obj.type, obj.data) for obj in graph.all_objects())
        assert len(expected_event_ids) == 3
        assert expected_integrity.event_count == 3
        assert expected_integrity.next_seq == 3
        assert expected_integrity.head_hash is not None
        assert events.server_version() == "3.2.4"
        events.close()
        projection.close()

        # Phase 2: verified export while 3.2.4 still serves, then graceful stop.
        export_text = _export_database(container, legacy_config)
        export_path.write_text(export_text, encoding="utf-8")
        _verify_export(export_text, expected_event_ids, scope)
        _stop_and_remove(container)

        # Phase 3: 3.3.0 starts in place on the same volume.
        upgraded_config = _start_server(
            UPGRADE_IMAGE,
            container,
            volume,
            environment.config,
            timeout_seconds=UPGRADE_START_TIMEOUT_SECONDS,
        )
        upgraded_events = SurrealEventStore(upgraded_config, run_id=run_id, scope=scope)
        upgraded_projection = SurrealGraphStore(upgraded_config, run_id=run_id, scope=scope)
        assert upgraded_events.server_version() == "3.3.0"
        assert [event.id for event in upgraded_events.iter_events()] == expected_event_ids
        assert upgraded_events.verify_integrity() == expected_integrity
        assert upgraded_events.schema_version() == "3"
        surviving = sorted(
            (obj.id, obj.type, obj.data) for obj in upgraded_projection.all_objects()
        )
        assert surviving == expected_objects

        # The projection is disposable: clear it, then replay the migrated log.
        upgraded_projection.clear()
        assert upgraded_projection.all_objects() == []
        replayed = replay_graph(upgraded_events, upgraded_projection)
        replayed_objects = sorted((obj.id, obj.type, obj.data) for obj in replayed.all_objects())
        assert replayed_objects == expected_objects
        assert upgraded_projection.projection_status() == "ready"
        upgraded_events.close()
        upgraded_projection.close()

        # Phase 4: restore the export into a fresh 3.2.4 datastore (rollback rehearsal).
        _create_fresh_volume(restore_volume)
        restore_config = _start_server(
            LEGACY_IMAGE,
            restore_container,
            restore_volume,
            environment.config,
            timeout_seconds=LEGACY_START_TIMEOUT_SECONDS,
        )
        _import_export(restore_container, export_path, restore_config)
        restored_events = SurrealEventStore(restore_config, run_id=run_id, scope=scope)
        assert restored_events.server_version() == "3.2.4"
        assert [event.id for event in restored_events.iter_events()] == expected_event_ids
        assert restored_events.verify_integrity() == expected_integrity
        assert restored_events.schema_version() == "3"
        restored_events.close()
    finally:
        _discard(container, volume)
        _discard(restore_container, restore_volume)
