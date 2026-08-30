from __future__ import annotations

import pytest
from activegraph import Runtime, SQLiteEventStore


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Tracked upstream as yoheinakajima/activegraph#81",
)
def test_unknown_run_load_does_not_create_phantom_run(tmp_path) -> None:
    database = str(tmp_path / "runs.sqlite")
    before = SQLiteEventStore.list_runs(database)

    try:
        Runtime.load(database, run_id="missing")
    except (FileNotFoundError, KeyError):
        return

    assert SQLiteEventStore.list_runs(database) == before
