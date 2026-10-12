"""Unit contracts for the pack harness that do not need a live server."""

from __future__ import annotations

import pytest

from activegraph_surrealdb.harness import (
    DEFAULT_PACKS_PATH,
    HARNESS_PACK_MODULES,
    PACKS_PATH_ENV,
    PackHarnessError,
    packs_path,
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
