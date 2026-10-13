"""Lane C (surrealkv engine) evidence for ADR 0003: beta engine qualification.

Non-gating exploration-lane evidence behind the existing ``qualification``
marker. Ladder step V7 runs it with ``ACTIVEGRAPH_SURREALDB_TEST_ENGINE=
surrealkv`` on the engine's own volume; the test skips whenever that variable
is not ``surrealkv``, so the stable RocksDB qualification run (V3) never
attempts it.
"""

from __future__ import annotations

import pytest

from activegraph_surrealdb import SurrealEventStore


@pytest.mark.qualification
def test_surrealkv_engine_serves_the_provider(qualification_environment) -> None:
    if qualification_environment.engine != "surrealkv":
        pytest.skip("set ACTIVEGRAPH_SURREALDB_TEST_ENGINE=surrealkv for the lane C evidence")

    assert qualification_environment.storage_url == "surrealkv:///data/activegraph.db"
    events = SurrealEventStore(
        qualification_environment.config,
        run_id="environment",
        scope="qualification",
    )
    assert events.server_version() == "3.3.0"
    events.close()
