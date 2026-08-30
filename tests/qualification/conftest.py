from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from urllib.parse import urlparse

import pytest

from activegraph_surrealdb import SurrealConfig


@dataclass(frozen=True)
class QualificationEnvironment:
    config: SurrealConfig
    container: str
    volume: str
    host_port: int
    image: str


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "qualification: controls a pinned Docker qualification server"
    )


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
    assert parsed.scheme == "ws"
    assert parsed.hostname in {"127.0.0.1", "localhost"}
    assert parsed.port == host_port

    data_mounts = [mount for mount in inspection["Mounts"] if mount["Destination"] == "/data"]
    assert len(data_mounts) == 1
    assert data_mounts[0]["Type"] == "volume"
    assert data_mounts[0]["Name"] == volume
    assert any("rocksdb:///data/" in item for item in inspection["Config"]["Cmd"])

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
    )
