"""SurrealDB providers for ActiveGraph v1.10."""

from .config import ProviderCapabilities, SurrealConfig
from .event_store import IntegrityReport, SurrealEventStore
from .graph_store import ProjectionUnavailableError, SurrealGraphStore
from .runtime import (
    fork_event_store,
    replay_graph,
    wait_until_ready,
    wait_until_unavailable,
)

__all__ = [
    "IntegrityReport",
    "ProjectionUnavailableError",
    "ProviderCapabilities",
    "SurrealConfig",
    "SurrealEventStore",
    "SurrealGraphStore",
    "fork_event_store",
    "replay_graph",
    "wait_until_ready",
    "wait_until_unavailable",
]
