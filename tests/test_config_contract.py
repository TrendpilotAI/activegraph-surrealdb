from __future__ import annotations

import pytest

from activegraph_surrealdb import SurrealConfig
from activegraph_surrealdb.errors import ConfigurationError
from activegraph_surrealdb.keys import record_key


def test_config_normalizes_rpc_suffix_and_redacts_password() -> None:
    config = SurrealConfig(
        endpoint="wss://db.example.test/rpc",
        namespace="activegraph",
        database="provider",
        username="service",
        password="never-print-me",
    )

    assert config.endpoint == "wss://db.example.test"
    assert "never-print-me" not in repr(config)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:8000",
        "https://db.example.test",
        "mem://",
        "file:///tmp/provider.db",
        "surrealkv:///tmp/provider.db",
        "rocksdb:///tmp/provider.db",
    ],
)
def test_preview_rejects_unproven_transports(endpoint: str) -> None:
    with pytest.raises(ConfigurationError, match="ws:// or wss://"):
        SurrealConfig(
            endpoint=endpoint,
            namespace="activegraph",
            database="provider",
            username="service",
            password="secret",
        )


@pytest.mark.parametrize("field", ["namespace", "database", "username", "password"])
def test_config_rejects_empty_required_values(field: str) -> None:
    values = {
        "endpoint": "ws://localhost:8000",
        "namespace": "activegraph",
        "database": "provider",
        "username": "service",
        "password": "secret",
    }
    values[field] = "  "

    with pytest.raises(ConfigurationError, match=field):
        SurrealConfig(**values)


def test_record_keys_are_stable_scoped_and_do_not_embed_plaintext() -> None:
    first = record_key("event", "tenant/a", "run:7", "evt one")
    same = record_key("event", "tenant/a", "run:7", "evt one")
    other_scope = record_key("event", "tenant/b", "run:7", "evt one")

    assert first == same
    assert first != other_scope
    assert len(first) == 64
    assert "tenant" not in first
    assert "evt" not in first


def test_capabilities_do_not_claim_cross_store_atomicity_or_tls_qualification() -> None:
    config = SurrealConfig(
        endpoint="wss://db.example.test",
        namespace="activegraph",
        database="provider",
        username="service",
        password="secret",
    )

    assert config.capabilities.transport == "websocket"
    assert config.capabilities.transactional_append is True
    assert config.capabilities.atomic_event_projection is False
    assert config.capabilities.tls_qualified is False
