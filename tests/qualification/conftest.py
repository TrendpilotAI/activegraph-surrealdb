"""Session harness for the pinned Docker qualification server.

The endpoint scheme may be ``ws`` or ``wss`` (Lane A TLS reuses these suites),
and the asserted storage engine derives from
``ACTIVEGRAPH_SURREALDB_TEST_ENGINE`` (default ``rocksdb``; Lane C sets
``surrealkv``). Exploration-lane evidence tests gate behind their own markers
and never run in the default selection.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import pytest

from activegraph_surrealdb import SurrealConfig

TEST_ENGINE_VARIABLE = "ACTIVEGRAPH_SURREALDB_TEST_ENGINE"
DEFAULT_TEST_ENGINE = "rocksdb"
SUPPORTED_TEST_ENGINES = ("rocksdb", "surrealkv")


@dataclass(frozen=True)
class QualificationEnvironment:
    config: SurrealConfig
    container: str
    volume: str
    host_port: int
    image: str
    engine: str
    storage_url: str


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "qualification: controls a pinned Docker qualification server"
    )
    config.addinivalue_line(
        "markers", "upgrade: lane B in-place volume upgrade evidence (non-gating)"
    )


def _test_engine() -> str:
    engine = os.getenv(TEST_ENGINE_VARIABLE, DEFAULT_TEST_ENGINE)
    if engine not in SUPPORTED_TEST_ENGINES:
        pytest.fail(
            f"{TEST_ENGINE_VARIABLE}={engine!r} is not one of: " + ", ".join(SUPPORTED_TEST_ENGINES)
        )
    return engine


def _storage_url(inspection: dict[str, Any], engine: str) -> str:
    matches = [item for item in inspection["Config"]["Cmd"] if f"{engine}:///data/" in item]
    assert len(matches) == 1
    return str(matches[0])


@pytest.fixture(scope="session", autouse=True)
def require_qualification_mode() -> None:
    if os.getenv("ACTIVEGRAPH_SURREALDB_QUALIFY") != "1":
        pytest.skip("set ACTIVEGRAPH_SURREALDB_QUALIFY=1 for qualification tests")


@pytest.fixture(scope="session")
def qualification_environment() -> QualificationEnvironment:
    required = [
        "ACTIVEGRAPH_SURREALDB_TEST_URL",
        "ACTIVEGRAPH_SURREALDB_TEST_NAMESPACE",
        "ACTIVEGRAPH_SURREALDB_TEST_DATABASE",
        "ACTIVEGRAPH_SURREALDB_TEST_USERNAME",
        "ACTIVEGRAPH_SURREALDB_TEST_PASSWORD",
        "ACTIVEGRAPH_SURREALDB_DOCKER_CONTAINER",
        "ACTIVEGRAPH_SURREALDB_DOCKER_VOLUME",
    ]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        pytest.fail("qualification environment is missing: " + ", ".join(missing))

    container = os.environ["ACTIVEGRAPH_SURREALDB_DOCKER_CONTAINER"]
    volume = os.environ["ACTIVEGRAPH_SURREALDB_DOCKER_VOLUME"]
    inspection = json.loads(
        subprocess.run(
            ["docker", "inspect", container],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )[0]
    binding = inspection["NetworkSettings"]["Ports"]["8000/tcp"][0]
    host_port = int(binding["HostPort"])
    endpoint = os.environ["ACTIVEGRAPH_SURREALDB_TEST_URL"]
    parsed = urlparse(endpoint)
    assert parsed.scheme in {"ws", "wss"}
    assert parsed.hostname in {"127.0.0.1", "localhost"}
    assert parsed.port == host_port

    engine = _test_engine()
    data_mounts = [mount for mount in inspection["Mounts"] if mount["Destination"] == "/data"]
    assert len(data_mounts) == 1
    assert data_mounts[0]["Type"] == "volume"
    assert data_mounts[0]["Name"] == volume
    storage_url = _storage_url(inspection, engine)

    config = SurrealConfig(
        endpoint=endpoint,
        namespace=os.environ["ACTIVEGRAPH_SURREALDB_TEST_NAMESPACE"],
        database=os.environ["ACTIVEGRAPH_SURREALDB_TEST_DATABASE"],
        username=os.environ["ACTIVEGRAPH_SURREALDB_TEST_USERNAME"],
        password=os.environ["ACTIVEGRAPH_SURREALDB_TEST_PASSWORD"],
    )
    return QualificationEnvironment(
        config=config,
        container=container,
        volume=volume,
        host_port=host_port,
        image=inspection["Config"]["Image"],
        engine=engine,
        storage_url=storage_url,
    )
