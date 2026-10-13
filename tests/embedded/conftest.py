"""Lane D (embedded engine) session harness.

Registers the ``embedded`` marker for ``--strict-markers``. The evidence tests
are non-gating: they are selected only with ``-m embedded`` (ladder step V8) and
skip unless the isolated ``surrealdb[embedded]`` lane venv is active and the
``surrealdb_embedded`` extension is importable.
"""

from __future__ import annotations

import os

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "embedded: lane D embedded-engine evidence from the isolated SDK venv (non-gating)",
    )


@pytest.fixture(scope="session", autouse=True)
def require_embedded_mode() -> None:
    if os.getenv("ACTIVEGRAPH_SURREALDB_EMBEDDED") != "1":
        pytest.skip("set ACTIVEGRAPH_SURREALDB_EMBEDDED=1 for embedded lane tests")
