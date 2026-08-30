from __future__ import annotations

import importlib.metadata

import pytest

from activegraph_surrealdb import SurrealEventStore


@pytest.mark.qualification
def test_qualification_lane_is_exact_stable_ws_rocksdb(
    qualification_environment,
) -> None:
    assert importlib.metadata.version("surrealdb") == "2.0.0"
    config = qualification_environment.config
    assert config.endpoint.startswith("ws://")
    assert qualification_environment.image == "surrealdb/surrealdb:v3.2.4"

    store = SurrealEventStore(config, run_id="environment", scope="qualification")
    assert store.server_version() == "3.2.4"
    store.close()
