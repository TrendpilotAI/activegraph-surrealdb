"""Unit contracts for the pack harness that do not need a live server."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from activegraph_surrealdb import SurrealConfig, harness
from activegraph_surrealdb.harness import (
    DEFAULT_PACKS_PATH,
    HARNESS_PACK_MODULES,
    PACKS_PATH_ENV,
    PackHarnessError,
    SurrealPackRuntime,
    import_pack,
    packs_path,
)


def _config() -> SurrealConfig:
    return SurrealConfig(
        endpoint="ws://127.0.0.1:8000",
        namespace="activegraph",
        database="unit",
        username="root",
        password="root",
    )


def test_packs_path_reports_missing_library(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(PACKS_PATH_ENV, str(tmp_path / "does-not-exist"))
    with pytest.raises(PackHarnessError, match="no pack library"):
        packs_path()


def test_packs_path_accepts_a_checkout(monkeypatch, tmp_path) -> None:
    (tmp_path / "packs").mkdir()
    monkeypatch.setenv(PACKS_PATH_ENV, str(tmp_path))
    assert packs_path() == tmp_path


def test_default_packs_path_is_documented() -> None:
    assert DEFAULT_PACKS_PATH


def test_harness_pack_set_is_unique_and_ordered() -> None:
    assert len(HARNESS_PACK_MODULES) == len(set(HARNESS_PACK_MODULES))
    # Core must load first: every layered pack declares requires=["core"].
    assert HARNESS_PACK_MODULES[0] == "core"
    for required in ("composio", "mcp", "bridges", "attention", "evolution", "usage"):
        assert required in HARNESS_PACK_MODULES


def _write_checkout(root: Path, marker: str) -> None:
    package = root / "packs"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "core.py").write_text(f"marker = {marker!r}\n", encoding="utf-8")


def test_import_pack_honors_the_requested_checkout(tmp_path) -> None:
    """A second checkout must win even after ``packs`` is cached in sys.modules."""

    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_checkout(first, "first")
    _write_checkout(second, "second")

    saved_modules = {
        name: module
        for name, module in sys.modules.items()
        if name == "packs" or name.startswith("packs.")
    }
    saved_path = list(sys.path)
    for name in list(saved_modules):
        del sys.modules[name]
    try:
        assert import_pack("core", root=first).marker == "first"
        assert import_pack("core", root=second).marker == "second"
    finally:
        for name in [n for n in sys.modules if n == "packs" or n.startswith("packs.")]:
            del sys.modules[name]
        sys.modules.update(saved_modules)
        sys.path[:] = saved_path


def test_build_runtime_closes_event_store_when_graph_store_fails(monkeypatch) -> None:
    closed: list[str] = []

    class FakeEventStore:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def close(self) -> None:
            closed.append("event")

    def failing_graph_store(*args: object, **kwargs: object) -> None:
        raise RuntimeError("graph store boom")

    monkeypatch.setattr(harness, "SurrealEventStore", FakeEventStore)
    monkeypatch.setattr(harness, "SurrealGraphStore", failing_graph_store)

    with pytest.raises(RuntimeError, match="graph store boom"):
        harness.build_runtime(_config(), run_id="run", scope="scope")

    assert closed == ["event"]


def test_pack_runtime_close_attempts_both_stores() -> None:
    calls: list[str] = []

    class BoomEventStore:
        def close(self) -> None:
            raise RuntimeError("event close boom")

    class RecordingGraphStore:
        def close(self) -> None:
            calls.append("graph")

    handle = SurrealPackRuntime(
        config=_config(),
        run_id="run",
        scope="scope",
        runtime=None,  # type: ignore[arg-type]
        event_store=BoomEventStore(),  # type: ignore[arg-type]
        graph_store=RecordingGraphStore(),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="event close boom"):
        handle.close()

    assert calls == ["graph"]
