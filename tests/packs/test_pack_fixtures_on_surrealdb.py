"""Every pack fixture suite runs against SurrealDB-backed stores.

The pack library's own suite runs each pack's ``fixtures/run_fixtures.py`` on
the default in-memory / SQLite backends. This lane runs the same, unmodified
fixture scripts with the runtime's store seams swapped to SurrealDB (see
``surreal_bootstrap.py``): the authoritative event log and the materialized
graph projection both live in one SurrealDB database while the pack behaviors
execute and their assertions run.

Each fixture is a separate interpreter, so the patch never leaks between packs.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from activegraph_surrealdb.harness import PackHarnessError, packs_path

from .conftest import surreal_test_config

pytestmark = [pytest.mark.surrealdb, pytest.mark.packs]

BOOTSTRAP = Path(__file__).with_name("surreal_bootstrap.py")
_HERMETIC_KEYS = {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "COMPOSIO_API_KEY"}


def _fixture_scripts() -> list[Path]:
    """Discover ``<pack>/fixtures/run_fixtures.py`` scripts in the checkout."""

    try:
        base = packs_path() / "packs"
    except PackHarnessError:
        return []
    scripts = sorted(base.glob("*/fixtures/run_fixtures.py"))
    scripts += sorted(base.glob("importers/*/fixtures/run_fixtures.py"))
    # Skip scaffolding (``_template``) and other underscore-prefixed helpers.
    return [path for path in scripts if not path.parents[1].name.startswith("_")]


_FIXTURES = _fixture_scripts()

# Fixture suites that depend on backends the pack library assumes, not on
# anything this provider can supply. They are marked xfail (not hidden) so the
# divergence stays visible and the rest of the lane stays green.
#
#  * communication: one sub-fixture reads the ``Object`` returned by
#    ``add_object`` *after* a behavior patched it. That only works when the
#    projection is the in-process object graph (Python object identity); an
#    external store returns a fresh instance per read. The patch itself
#    persists correctly — a fresh ``get_object`` sees the new value.
#  * evolution: its fork trials spawn fresh interpreters and read run metadata
#    through ``SQLiteEventStore.list_runs``, so it requires a SQLite-backed
#    runtime by construction, independent of the projection backend.
_EXPECTED_DIVERGENCES = {
    "packs/communication": (
        "relies on in-memory object identity: a behavior patches an object and "
        "the fixture reads the stale add_object() return value"
    ),
    "packs/evolution": (
        "requires a SQLite-backed runtime for subprocess fork trials and "
        "SQLiteEventStore.list_runs retention"
    ),
}


def _params() -> list[pytest.param]:
    if not _FIXTURES:
        return [pytest.param(None, id="unavailable")]
    params: list[pytest.param] = []
    for path in _FIXTURES:
        fixture_id = _fixture_id(path)
        reason = _EXPECTED_DIVERGENCES.get(fixture_id)
        marks = [pytest.mark.xfail(reason=reason, strict=False)] if reason else []
        params.append(pytest.param(path, id=fixture_id, marks=marks))
    return params


def _fixture_id(path: Path | None) -> str:
    if path is None:
        return "unavailable"
    return "/".join(path.parts[-4:-2])


_PARAMS = _params()


@pytest.fixture(scope="session")
def packs_root() -> Path:
    try:
        return packs_path()
    except PackHarnessError as exc:
        pytest.skip(str(exc))


@pytest.mark.parametrize("fixture", _PARAMS)
def test_pack_fixture_suite_runs_on_surrealdb(fixture: Path | None, packs_root: Path) -> None:
    if fixture is None:
        pytest.skip("no pack library checkout to run")

    config = surreal_test_config("pack_fixtures")
    env = {key: value for key, value in os.environ.items() if key not in _HERMETIC_KEYS}
    env.update(
        {
            "ACTIVEGRAPH_SURREALDB_TEST_URL": config.endpoint,
            "ACTIVEGRAPH_SURREALDB_TEST_NAMESPACE": config.namespace,
            "ACTIVEGRAPH_SURREALDB_TEST_DATABASE": config.database,
            "ACTIVEGRAPH_SURREALDB_TEST_USERNAME": str(config.username),
            "ACTIVEGRAPH_SURREALDB_TEST_PASSWORD": str(config.password),
            "ACTIVEGRAPH_FIXTURE_SCOPE_PREFIX": f"packfix_{_fixture_id(fixture).replace('/', '_')}",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(part for part in (str(packs_root), existing) if part)

    result = subprocess.run(
        [sys.executable, str(BOOTSTRAP), str(fixture)],
        cwd=str(packs_root),
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"{_fixture_id(fixture)} fixture suite failed on SurrealDB "
            f"(exit {result.returncode}).\n"
            f"--- stdout (last 4000 chars) ---\n{result.stdout[-4000:]}\n"
            f"--- stderr (last 2000 chars) ---\n{result.stderr[-2000:]}"
        )
