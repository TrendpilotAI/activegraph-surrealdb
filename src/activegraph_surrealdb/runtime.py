"""Explicit uncompacted replay and prefix-fork helpers."""

from __future__ import annotations

import time
from contextlib import suppress
from typing import Any

from activegraph.core.graph import Graph

from .config import SurrealConfig
from .errors import UnsupportedCompactedRunError
from .event_store import SurrealEventStore


def replay_graph(event_store: Any, graph_store: Any) -> Graph:
    """Replace one isolated projection from an uncompacted event log."""

    event_run = getattr(event_store, "run_id", None)
    event_scope = getattr(event_store, "scope", None)
    if event_run != getattr(graph_store, "run_id", None):
        raise ValueError("event and projection run_id values must match")
    if event_scope != getattr(graph_store, "scope", None):
        raise ValueError("event and projection scope values must match")

    try:
        graph_store.begin_rebuild()
        events = list(event_store.iter_events())
        if events and events[0].type == "runtime.snapshot":
            raise UnsupportedCompactedRunError(
                "runtime.snapshot replay is outside the uncompacted preview contract"
            )
        graph = Graph(run_id=str(event_run), graph_store=graph_store)
        for event in events:
            graph._replay_event(event)  # noqa: SLF001 - ActiveGraph replay seam
        graph.ids.reseed_from_events(events)
        graph_store.mark_ready()
        return graph
    except BaseException as exc:
        # Preserve the causal replay/clear failure when the projection
        # connection is itself unavailable and cannot record status.
        with suppress(BaseException):
            graph_store.mark_failed(exc)
        raise


def fork_event_store(
    parent: SurrealEventStore,
    *,
    new_run_id: str,
    at_event_id: str,
    label: str | None = None,
) -> SurrealEventStore:
    """Copy an inclusive uncompacted prefix into a fresh scoped run."""

    return parent.fork_prefix(
        new_run_id=new_run_id,
        at_event_id=at_event_id,
        label=label,
    )


def wait_until_ready(config: SurrealConfig, *, timeout_seconds: float) -> None:
    """Wait until a configured server accepts an authenticated query."""

    deadline = time.monotonic() + timeout_seconds
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        connection = None
        try:
            connection = SurrealEventStore._new_connection(config)
            connection.server_version()
            return
        except BaseException as exc:
            last_error = exc
        finally:
            if connection is not None:
                connection.close()
        time.sleep(0.2)
    raise TimeoutError("SurrealDB did not become ready before the deadline") from last_error


def wait_until_unavailable(config: SurrealConfig, *, timeout_seconds: float) -> None:
    """Wait until a configured server stops accepting connections."""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        connection = None
        try:
            connection = SurrealEventStore._new_connection(config)
            connection.server_version()
        except BaseException:
            return
        finally:
            if connection is not None:
                connection.close()
        time.sleep(0.2)
    raise TimeoutError("SurrealDB remained available past the deadline")
