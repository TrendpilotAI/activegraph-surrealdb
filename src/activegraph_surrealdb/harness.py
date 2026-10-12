"""Load the published ActiveGraph pack library onto a SurrealDB-backed Runtime.

The provider's store suites prove the event log and graph projection each match
their released contracts. This module proves the layer above: a real ActiveGraph
``Runtime`` whose *authoritative event log* and *materialized graph projection*
both live in one SurrealDB deployment can load the published pack library and
run its behaviors, with every accepted event and every projected object landing
in the same database.

The pack repository is external. Point the harness at a checkout with
``ACTIVEGRAPH_PACKS_PATH`` (default: the reference checkout baked into this
environment). Nothing here vendors or mutates the packs.
"""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Iterable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from activegraph import Graph, Runtime

from .config import SurrealConfig
from .event_store import SurrealEventStore
from .graph_store import SurrealGraphStore

DEFAULT_PACKS_PATH = "/opt/references/activegraph-packs"
PACKS_PATH_ENV = "ACTIVEGRAPH_PACKS_PATH"

# The harness set this provider claims: core + infrastructure + communication
# (including composio) + bridges + mcp + attention + evolution +
# semantic_extraction + eval_outcome + activity_normalizer + connector_control +
# skills + usage + importers, plus the scaffolding template.
HARNESS_PACK_MODULES: tuple[str, ...] = (
    # core
    "core",
    # infrastructure
    "tool_gateway",
    "secrets",
    "memory_gateway",
    "identity_auth",
    "agent_profile",
    "entity",
    "subject_profile",
    # communication (including composio)
    "communication",
    "chat",
    "email",
    "composio",
    # bridges
    "bridges",
    # runtime capabilities
    "mcp",
    "attention",
    "evolution",
    "semantic_extraction",
    "eval_outcome",
    "activity_normalizer",
    "connector_control",
    "skills",
    "usage",
    "schedule",
    # importers
    "importers.local_files",
    "importers.chatgpt_export",
    "importers.claude_export",
    "importers.assistant_self_summary",
    "importers.assistant_local_sessions",
    "importers.public_presence",
    # scaffolding template
    "_template",
)


class PackHarnessError(RuntimeError):
    """The pack harness could not locate or import the external pack library."""


def packs_path() -> Path:
    """Return the configured pack checkout, or raise if it is absent."""

    root = Path(os.environ.get(PACKS_PATH_ENV, DEFAULT_PACKS_PATH)).expanduser()
    if not (root / "packs").is_dir():
        raise PackHarnessError(
            f"no pack library at {root!s}; set {PACKS_PATH_ENV} to a checkout "
            "containing a 'packs' package"
        )
    return root


def _ensure_importable(root: Path) -> None:
    entry = str(root)
    if entry not in sys.path:
        sys.path.insert(0, entry)
    _evict_foreign_packs(root)


def _evict_foreign_packs(root: Path) -> None:
    """Drop a cached ``packs`` package that came from a different checkout.

    ``sys.path`` only decides the *first* import: once ``packs`` is cached, a
    later call with a different ``root`` would silently reuse the cached
    modules instead of the requested checkout. Evict the cached package (and
    its submodules) so the requested root wins.
    """

    cached = sys.modules.get("packs")
    if cached is None:
        return
    expected = (root / "packs").resolve()
    cached_paths = {Path(entry).resolve() for entry in getattr(cached, "__path__", [])}
    if expected in cached_paths:
        return
    for name in [name for name in sys.modules if name == "packs" or name.startswith("packs.")]:
        del sys.modules[name]


def import_pack(module_name: str, *, root: Path | None = None) -> ModuleType:
    """Import ``packs.<module_name>`` from the configured checkout."""

    _ensure_importable(root or packs_path())
    try:
        return importlib.import_module(f"packs.{module_name}")
    except ImportError as exc:  # pragma: no cover - surfaced as a clear error
        raise PackHarnessError(f"could not import packs.{module_name}: {exc}") from exc


@dataclass
class SurrealPackRuntime:
    """A Runtime, its event store, and its graph store in one SurrealDB database.

    Closing the handle closes both stores. The two stores intentionally keep
    independent SurrealDB transactions (the released runtime has no cross-store
    coordinator); the event log remains authoritative and replay repairs the
    projection, exactly as the provider architecture documents.
    """

    config: SurrealConfig
    run_id: str
    scope: str
    runtime: Runtime
    event_store: SurrealEventStore
    graph_store: SurrealGraphStore
    loaded_packs: list[str] = field(default_factory=list)

    def close(self) -> None:
        try:
            self.event_store.close()
        finally:
            # Even if the event store's teardown raises, the graph store keeps
            # an independent connection that must still be closed.
            self.graph_store.close()


def build_runtime(
    config: SurrealConfig,
    *,
    run_id: str,
    scope: str,
    modules: Sequence[str] = HARNESS_PACK_MODULES,
    settings: Mapping[str, Any] | None = None,
    root: Path | None = None,
) -> SurrealPackRuntime:
    """Open a SurrealDB-backed Runtime and load ``modules`` into it.

    ``settings`` maps a pack module name to the settings instance passed to
    ``Runtime.load_pack``; packs not named there use their declared defaults.
    """

    with ExitStack() as stack:
        event_store = SurrealEventStore(config, run_id=run_id, scope=scope)
        stack.callback(event_store.close)
        graph_store = SurrealGraphStore(config, run_id=run_id, scope=scope)
        stack.callback(graph_store.close)
        graph = Graph(run_id=run_id, graph_store=graph_store)
        runtime = Runtime(graph, store=event_store)

        handle = SurrealPackRuntime(
            config=config,
            run_id=run_id,
            scope=scope,
            runtime=runtime,
            event_store=event_store,
            graph_store=graph_store,
        )
        _load_modules(runtime, handle, modules, settings or {}, root)
        # Success: detach the teardown callbacks so the stores stay open.
        stack.pop_all()
        return handle


def _load_modules(
    runtime: Runtime,
    handle: SurrealPackRuntime,
    modules: Iterable[str],
    settings: Mapping[str, Any],
    root: Path | None,
) -> None:
    for module_name in modules:
        module = import_pack(module_name, root=root)
        pack = getattr(module, "pack", None)
        if pack is None:
            raise PackHarnessError(f"packs.{module_name} does not export a 'pack'")
        pack_settings = settings.get(module_name)
        if pack_settings is None:
            runtime.load_pack(pack)
        else:
            runtime.load_pack(pack, settings=pack_settings)
        if pack.name not in handle.loaded_packs:
            handle.loaded_packs.append(pack.name)


__all__ = [
    "DEFAULT_PACKS_PATH",
    "HARNESS_PACK_MODULES",
    "PACKS_PATH_ENV",
    "PackHarnessError",
    "SurrealPackRuntime",
    "build_runtime",
    "import_pack",
    "packs_path",
]
