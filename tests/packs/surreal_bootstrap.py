#!/usr/bin/env python3
"""Run one ``activegraph-packs`` fixture script against a SurrealDB-backed runtime.

The pack fixture scripts build ``Graph()`` / ``Runtime(graph)`` directly and
occasionally use ``Runtime(..., persist_to=...)`` or ``Runtime.load(...)``.
Those constructors reach for the in-memory / SQLite backends by default. This
bootstrap swaps the runtime's store seams so the same, unmodified fixture runs
on SurrealDB: every event log and every materialized projection lands in the
one SurrealDB database named by the ``ACTIVEGRAPH_SURREALDB_TEST_*`` env vars.

Usage:
    python tests/packs/surreal_bootstrap.py packs/core/fixtures/run_fixtures.py

The fixture is executed as ``__main__`` and its exit code is propagated, so
callers assert on it exactly like the pack library's own runner does.

The patch is deliberately scoped to this process (a fresh interpreter per
fixture), so it never leaks into the rest of a test session.
"""

from __future__ import annotations

import itertools
import os
import runpy
import sys
from pathlib import Path
from typing import Any

from activegraph_surrealdb import SurrealConfig, SurrealEventStore, SurrealGraphStore


def _config_from_env() -> SurrealConfig:
    endpoint = os.environ.get("ACTIVEGRAPH_SURREALDB_TEST_URL")
    if not endpoint:
        raise SystemExit(
            "set ACTIVEGRAPH_SURREALDB_TEST_URL to run pack fixtures against SurrealDB"
        )
    return SurrealConfig(
        endpoint=endpoint,
        namespace=os.environ.get("ACTIVEGRAPH_SURREALDB_TEST_NAMESPACE", "activegraph"),
        database=os.environ.get("ACTIVEGRAPH_SURREALDB_TEST_DATABASE", "pack_fixtures"),
        username=os.environ.get("ACTIVEGRAPH_SURREALDB_TEST_USERNAME", "root"),
        password=os.environ.get("ACTIVEGRAPH_SURREALDB_TEST_PASSWORD", "root"),
    )


def install(config: SurrealConfig, *, prefix: str) -> None:
    """Patch the ActiveGraph runtime so its default stores are SurrealDB.

    Kept import-light and side-effect-free until called, so importing this
    module does not disturb a normal interpreter.
    """

    import activegraph
    import activegraph.runtime.runtime as runtime_module
    from activegraph import Graph as CoreGraph
    from activegraph import IDGen

    counter = itertools.count()

    # A fresh scope per graph/run keeps each fixture scenario isolated inside
    # the one shared database, and mirrors the runtime's run-scoped semantics.
    scope_by_run: dict[str, str] = {}
    scope_by_path: dict[str, str] = {}
    run_by_path: dict[str, str] = {}

    def _new_scope() -> str:
        return f"{prefix}_{next(counter):05d}_{os.getpid()}"

    class SurrealGraph(CoreGraph):
        def __init__(
            self,
            ids: Any = None,
            clock: Any = None,
            run_id: str | None = None,
            graph_store: Any = None,
        ) -> None:
            if graph_store is None:
                ids = ids or IDGen()
                run_id = run_id or ids.run()
                scope = scope_by_run.get(run_id) or _new_scope()
                scope_by_run[run_id] = scope
                graph_store = SurrealGraphStore(config, run_id=run_id, scope=scope)
                super().__init__(ids=ids, clock=clock, run_id=run_id, graph_store=graph_store)
                self._surrealdb_scope = scope
            else:
                super().__init__(ids=ids, clock=clock, run_id=run_id, graph_store=graph_store)

    class SurrealRuntime(runtime_module.Runtime):
        def __init__(self, graph: Any, *args: Any, **kwargs: Any) -> None:
            already_attached = getattr(graph, "_store", None) is not None
            if kwargs.get("store") is None and not already_attached:
                scope = getattr(graph, "_surrealdb_scope", None) or scope_by_run.get(graph.run_id)
                persist_to = kwargs.pop("persist_to", None)
                if persist_to is not None:
                    scope = scope or _new_scope()
                    scope_by_path[persist_to] = scope
                    run_by_path[persist_to] = graph.run_id
                if scope is not None:
                    scope_by_run[graph.run_id] = scope
                    kwargs["store"] = SurrealEventStore(config, run_id=graph.run_id, scope=scope)
            super().__init__(graph, *args, **kwargs)

    real_open = runtime_module._open_sqlite_store
    real_recent = runtime_module._most_recent_run_id

    def _open_store(path_or_url: str, run_id: str) -> Any:
        scope = scope_by_path.get(path_or_url) or scope_by_run.get(run_id)
        if scope is not None:
            scope_by_run[run_id] = scope
            return SurrealEventStore(config, run_id=run_id, scope=scope)
        return real_open(path_or_url, run_id)

    def _most_recent_run_id(path_or_url: str) -> str | None:
        if path_or_url in run_by_path:
            return run_by_path[path_or_url]
        if path_or_url in scope_by_path:
            return SurrealEventStore.most_recent_run_id(config, scope=scope_by_path[path_or_url])
        return real_recent(path_or_url)

    activegraph.Graph = SurrealGraph
    activegraph.Runtime = SurrealRuntime
    runtime_module.Graph = SurrealGraph
    runtime_module._open_sqlite_store = _open_store
    runtime_module._most_recent_run_id = _most_recent_run_id


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        raise SystemExit(f"usage: {Path(argv[0]).name} <fixture_script>")
    fixture = Path(argv[1]).resolve()
    if not fixture.is_file():
        raise SystemExit(f"no such fixture script: {fixture}")

    config = _config_from_env()
    prefix = (
        os.environ.get("ACTIVEGRAPH_FIXTURE_SCOPE_PREFIX") or f"packs_{fixture.parent.parent.name}"
    )
    install(config, prefix=prefix)

    # The fixture scripts insert the pack checkout on sys.path themselves, but
    # make it explicit so a fixture that relies on cwd also resolves `packs`.
    root = fixture
    while root != root.parent and not (root / "packs").is_dir():
        root = root.parent
    if (root / "packs").is_dir() and str(root) not in sys.path:
        sys.path.insert(0, str(root))

    runpy.run_path(str(fixture), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
