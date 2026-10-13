"""Embedded SDK lane: expected-rejection evidence.

The preview's proven transport contract is WebSocket only: the provider pins
``surrealdb==2.0.0`` (plain ``ws://`` / ``wss://``) and ``SurrealConfig``
rejects every embedded/local transport scheme up front. This lane installs
the upstream embedded SDK variant (``surrealdb[embedded]==3.0.0b10``) in an
isolated venv and asserts that the provider still rejects embedded
transports — the contract-first posture: upstream limitations are recorded,
not patched, and an unproven SDK variant cannot silently widen the accepted
transports.
"""

from __future__ import annotations

import os

import pytest

from activegraph_surrealdb import SurrealConfig
from activegraph_surrealdb.errors import ConfigurationError

pytestmark = pytest.mark.embedded


def _gate_enabled() -> bool:
    return os.getenv("ACTIVEGRAPH_SURREALDB_EMBEDDED") == "1"


def test_embedded_sdk_variant_is_installed() -> None:
    """The isolated lane venv really carries the embedded SDK variant."""
    try:
        from importlib.metadata import version

        import surrealdb  # noqa: F401

        sdk_version = version("surrealdb")
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.fail(f"embedded venv must have the surrealdb SDK importable: {exc}")
    assert sdk_version.startswith("3.0.0"), (
        f"expected the surrealdb[embedded] 3.0.0b10 variant, got {sdk_version}"
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "mem://",
        "file:///tmp/provider.db",
        "surrealkv:///tmp/provider.db",
        "rocksdb:///tmp/provider.db",
    ],
)
def test_embedded_transports_stay_rejected(endpoint: str) -> None:
    """Embedded transports are rejected even with the embedded SDK installed."""
    with pytest.raises(ConfigurationError, match="ws:// or wss://"):
        SurrealConfig(
            endpoint=endpoint,
            namespace="activegraph",
            database="provider",
            username="service",
            password="secret",
        )
