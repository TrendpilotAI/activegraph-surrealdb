"""Shared fixtures for the SurrealDB pack harness tests."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from activegraph_surrealdb import SurrealConfig
from activegraph_surrealdb.harness import PackHarnessError, packs_path


def surreal_test_config(database_prefix: str = "pack_harness") -> SurrealConfig:
    endpoint = os.getenv("ACTIVEGRAPH_SURREALDB_TEST_URL")
    if not endpoint:
        pytest.skip("set ACTIVEGRAPH_SURREALDB_TEST_URL for pack harness tests")
    return SurrealConfig(
        endpoint=endpoint,
        namespace=os.getenv("ACTIVEGRAPH_SURREALDB_TEST_NAMESPACE", "activegraph"),
        database=f"{database_prefix}_{uuid4().hex}",
        username=os.getenv("ACTIVEGRAPH_SURREALDB_TEST_USERNAME", "root"),
        password=os.getenv("ACTIVEGRAPH_SURREALDB_TEST_PASSWORD", "root"),
    )


@pytest.fixture(scope="session")
def pack_library_path():
    try:
        return packs_path()
    except PackHarnessError as exc:
        pytest.skip(str(exc))
