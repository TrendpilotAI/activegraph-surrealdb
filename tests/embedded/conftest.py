from __future__ import annotations

import os

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "embedded: runs inside the isolated surrealdb[embedded] lane venv",
    )


@pytest.fixture(scope="session", autouse=True)
def require_embedded_mode() -> None:
    if os.getenv("ACTIVEGRAPH_SURREALDB_EMBEDDED") != "1":
        pytest.skip("set ACTIVEGRAPH_SURREALDB_EMBEDDED=1 for embedded lane tests")
