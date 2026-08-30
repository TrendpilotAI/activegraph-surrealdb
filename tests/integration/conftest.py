from __future__ import annotations

import os
from uuid import uuid4

import pytest

from activegraph_surrealdb import SurrealConfig


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "surrealdb: requires a live SurrealDB test server")


def config_from_env() -> SurrealConfig:
    endpoint = os.getenv("ACTIVEGRAPH_SURREALDB_TEST_URL")
    if not endpoint:
        pytest.skip("set ACTIVEGRAPH_SURREALDB_TEST_URL for integration tests")
    return SurrealConfig(
        endpoint=endpoint,
        namespace=os.getenv("ACTIVEGRAPH_SURREALDB_TEST_NAMESPACE", "activegraph"),
        database=os.getenv("ACTIVEGRAPH_SURREALDB_TEST_DATABASE", f"test_{uuid4().hex}"),
        username=os.getenv("ACTIVEGRAPH_SURREALDB_TEST_USERNAME", "root"),
        password=os.getenv("ACTIVEGRAPH_SURREALDB_TEST_PASSWORD", "root"),
    )


@pytest.fixture(scope="session")
def surreal_config() -> SurrealConfig:
    return config_from_env()
