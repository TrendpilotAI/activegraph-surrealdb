from __future__ import annotations

import importlib.metadata

import pytest

from activegraph_surrealdb import SurrealEventStore


@pytest.mark.qualification
def test_qualification_lane_pins_stable_versions(
    qualification_environment,
) -> None:
    assert importlib.metadata.version("surrealdb") == "2.0.0"
    config = qualification_environment.config
    assert config.endpoint.startswith(("ws://", "wss://"))
    assert qualification_environment.image == "surrealdb/surrealdb:v3.3.0"

    store = SurrealEventStore(config, run_id="environment", scope="qualification")
    assert store.server_version() == "3.3.0"
    store.close()
