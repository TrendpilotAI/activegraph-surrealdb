"""Connection configuration and accurately bounded capabilities."""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import ConfigurationError


@dataclass(frozen=True)
class ProviderCapabilities:
    """Capabilities proven by the preview's required qualification lane."""

    transport: str = "websocket"
    transactional_append: bool = True
    atomic_event_projection: bool = False
    tls_qualified: bool = False


@dataclass(frozen=True)
class SurrealConfig:
    """Secret-safe settings for a remote SurrealDB connection."""

    endpoint: str
    namespace: str
    database: str
    username: str
    password: str = field(repr=False)
    capabilities: ProviderCapabilities = field(
        default_factory=ProviderCapabilities,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        endpoint = str(self.endpoint).strip().rstrip("/")
        if endpoint.endswith("/rpc"):
            endpoint = endpoint[:-4].rstrip("/")
        if not endpoint.startswith(("ws://", "wss://")):
            raise ConfigurationError(
                "endpoint must use ws:// or wss://; HTTP and embedded "
                "transports are not supported by this preview"
            )

        object.__setattr__(self, "endpoint", endpoint)
        for name in ("namespace", "database", "username", "password"):
            value = str(getattr(self, name))
            if not value.strip():
                raise ConfigurationError(f"{name} must be non-empty")
            object.__setattr__(self, name, value.strip())
