"""Backup and clean-environment restore rehearsal (ROADMAP Phase 2).

The event log is the durable artifact: a backup is the exact-JSON export of
the appended events (with the source schema version recorded), and a restore
replays them into a clean environment through the store's own transactional
append path. After a restore the ledger must verify (chain hashes and run
record intact) and the projection must rebuild identically to the original.
A database written by a newer provider must be rejected on reopen rather
than silently mis-read — the rollback-lane guarantee the rehearsal depends
on.
"""

from __future__ import annotations

import json
import os
from typing import Any
from uuid import uuid4

import pytest
from activegraph import Runtime

from activegraph_surrealdb import (
    SurrealConfig,
    SurrealEventStore,
    SurrealGraphStore,
    replay_graph,
)
from activegraph_surrealdb.errors import LedgerIntegrityError
from activegraph_surrealdb.serde import event_to_record

pytestmark = pytest.mark.surrealdb


def _config(database: str) -> SurrealConfig:
    return SurrealConfig(
        endpoint=os.environ["ACTIVEGRAPH_SURREALDB_TEST_URL"],
        namespace=os.environ.get("ACTIVEGRAPH_SURREALDB_TEST_NAMESPACE", "activegraph"),
        database=database,
        username=os.environ.get("ACTIVEGRAPH_SURREALDB_TEST_USERNAME", "root"),
        password=os.environ.get("ACTIVEGRAPH_SURREALDB_TEST_PASSWORD", "root"),
    )


def _build_run(database: str, resolution: str) -> tuple[SurrealEventStore, SurrealGraphStore]:
    """Append a real mutation trace (create + patch) through the Runtime."""
    events = SurrealEventStore(_config(database), run_id="run", scope="scope")
    projection = SurrealGraphStore(_config(database), run_id="run", scope="scope")
    graph = replay_graph(events, projection)
    runtime = Runtime(graph, store=events)
    decision = runtime.graph.add_object(
        "decision",
        {"subject": "restore-rehearsal", "resolution": "unreviewed"},
    )
    runtime.graph.patch_object(decision.id, {"resolution": resolution})
    return events, projection


def _projection_snapshot(projection: SurrealGraphStore) -> list[tuple[str, str, dict[str, Any]]]:
    return sorted((obj.id, obj.type, obj.data) for obj in projection.all_objects())


def test_backup_and_clean_environment_restore_verifies_integrity_and_replay(
    tmp_path,
) -> None:
    original_db = f"backup_orig_{uuid4().hex}"
    restore_db = f"backup_restore_{uuid4().hex}"

    original, original_projection = _build_run(original_db, "accept")
    try:
        original_report = original.verify_integrity()
        assert original_report.event_count == 2
        original_snapshot = _projection_snapshot(original_projection)

        # Export: exact-JSON event records with the source schema version.
        backup = {
            "schema_version": original.schema_version(),
            "events": [event_to_record(e) for e in original.iter_events()],
        }
        export_path = tmp_path / "backup.json"
        export_path.write_text(json.dumps(backup))
    finally:
        original.close()

    # Restore into a clean environment: a fresh database (a fresh RocksDB
    # volume in the containerized flow) replays the exported events through
    # the store's transactional append path.
    restored = SurrealEventStore(_config(restore_db), run_id="run", scope="scope")
    try:
        for record in json.loads(export_path.read_text())["events"]:
            from activegraph_surrealdb.serde import record_to_event

            restored.append(record_to_event(record))

        report = restored.verify_integrity()
        assert report.event_count == original_report.event_count
        assert report.head_hash == original_report.head_hash

        # Replay the restored log and require the same projection.
        restored_projection = SurrealGraphStore(
            _config(restore_db), run_id="run", scope="scope"
        )
        replay_graph(restored, restored_projection)
        assert _projection_snapshot(restored_projection) == original_snapshot
    finally:
        restored.close()


def test_backup_from_newer_provider_is_rejected_instead_of_misread(
    tmp_path,
) -> None:
    source_db = f"backup_newer_{uuid4().hex}"
    restore_db = f"backup_reject_{uuid4().hex}"

    source, _ = _build_run(source_db, "needs_review")
    try:
        backup = {
            "schema_version": source.schema_version(),
            "events": [event_to_record(e) for e in source.iter_events()],
        }
        export_path = tmp_path / "backup.json"
        export_path.write_text(json.dumps(backup))
    finally:
        source.close()

    # Simulate the restore target having been touched by a newer provider.
    target = SurrealEventStore(_config(restore_db), run_id="run", scope="scope")
    target.raw_query("UPDATE ONLY ag_schema:singleton SET version = 99;")
    target.close()

    # The clean-environment restore must fail loudly, not silently mis-read.
    with pytest.raises(LedgerIntegrityError, match="newer than"):
        SurrealEventStore(_config(restore_db), run_id="run", scope="scope")
