"""Offline guarantees for the Arize AX conformance mirror.

The mirror must stay in lockstep with the suites it represents: the registry
counts, the pytest node ids it points at, and the scoring semantics are all
contract, so each is pinned here without needing a live store.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.telemetry.case_recorder import RECORD_ENV
from tests.telemetry.conformance_cases import (
    DATASET_EVENT_STORE,
    DATASET_GRAPH_STORE,
    DATASET_KNOWN_ISSUES,
    LAYER_KNOWN_ISSUES,
    OUTCOME_SCORES,
    SUITE_EVENT_STORE,
    SUITE_GRAPH_STORE,
    SUITE_KNOWN_ISSUES,
    cases_by_dataset,
    enumerate_cases,
)
from tests.telemetry.scoring import contract_scorer

REPO_ROOT = Path(__file__).resolve().parents[2]


def _suite(suite: str) -> list:
    return [case for case in enumerate_cases() if case.suite == suite]


def test_event_store_registry_mirrors_the_released_suite() -> None:
    cases = _suite(SUITE_EVENT_STORE)
    assert len(cases) == 11
    assert all(case.dataset == DATASET_EVENT_STORE for case in cases)
    assert all("test_event_store_conformance.py" in case.nodeid for case in cases)
    assert all(case.expected == "pass" for case in cases)
    assert all(case.contract_ref.startswith("CONTRACT") for case in cases)
    # The conformance base class is inherited, so every registry case must
    # come from a real method of the suite, not a registry typo.
    assert {case.case_id.split(".", 1)[1] for case in cases} == {
        case.nodeid.rsplit("::", 1)[1] for case in cases
    }


def test_graph_store_registry_mirrors_the_released_suite() -> None:
    cases = _suite(SUITE_GRAPH_STORE)
    assert len(cases) == 25
    assert all(case.dataset == DATASET_GRAPH_STORE for case in cases)
    assert all("test_graph_store_conformance.py" in case.nodeid for case in cases)
    assert all(case.expected == "pass" for case in cases)


def test_known_issues_mirror_the_strict_xfail_tests() -> None:
    cases = _suite(SUITE_KNOWN_ISSUES)
    assert {case.case_id for case in cases} == {
        "known-issues.test_unknown_run_load_does_not_create_phantom_run",
        "known-issues.test_runtime_load_replaces_stale_projection",
    }
    assert all(case.expected == "expected-failure" for case in cases)
    assert all(case.layer == LAYER_KNOWN_ISSUES for case in cases)
    # Each known issue must cite its upstream tracker so a score of 1.0 on a
    # rerun is traceable to the fix that closed it.
    joined = " ".join(case.contract_ref for case in cases)
    assert "yoheinakajima/activegraph#81" in joined
    assert "yoheinakajima/activegraph#82" in joined


def test_registry_case_ids_and_datasets_are_well_formed() -> None:
    cases = enumerate_cases()
    assert len({case.case_id for case in cases}) == len(cases)
    grouped = cases_by_dataset(cases)
    assert set(grouped) == {
        DATASET_EVENT_STORE,
        DATASET_GRAPH_STORE,
        DATASET_KNOWN_ISSUES,
    }
    for dataset, group in grouped.items():
        assert all(case.dataset == dataset for case in group)
    examples = [case.as_example() for case in cases]
    assert all(example["input"]["nodeid"] and example["input"]["case_id"] for example in examples)
    assert all(
        example["metadata"]["suite"] and example["metadata"]["layer"] for example in examples
    )


def test_every_registry_nodeid_is_collectable() -> None:
    nodeids = [case.nodeid for case in enumerate_cases()]
    files = sorted({nodeid.split("::", 1)[0] for nodeid in nodeids})
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "--no-header",
            *files,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    collected = set(proc.stdout.splitlines())
    missing = [nodeid for nodeid in nodeids if nodeid not in collected]
    assert not missing, f"registry nodeids pytest cannot collect: {missing}"


def test_outcome_scores_match_the_telemetry_plugin_contract() -> None:
    assert OUTCOME_SCORES == {
        "passed": 1.0,
        "expected-failure": 1.0,
        "failed": 0.0,
        "xpassed": 0.0,
        "xpass-strict": 0.0,
    }
    # A skip decides nothing: it is label-only, so it must stay out of the
    # score table by design.
    assert "skipped" not in OUTCOME_SCORES


def test_contract_scorer_scores_the_shared_vocabulary() -> None:
    case_input = {
        "contract_ref": "CONTRACT v0.8 #18",
        "expected": "pass",
    }
    for outcome, score in (
        ("passed", 1.0),
        ("expected-failure", 1.0),
        ("failed", 0.0),
        ("xpass-strict", 0.0),
    ):
        result = contract_scorer({"outcome": outcome, "explanation": "detail"}, case_input)
        assert result.score == score
        assert result.label == outcome
        assert "CONTRACT v0.8 #18" in result.explanation
        assert "detail" in result.explanation
    skipped = contract_scorer({"outcome": "skipped"}, case_input)
    assert skipped.score is None
    assert skipped.label == "skipped"
    unknown = contract_scorer({"outcome": "mystery"}, case_input)
    assert unknown.score == 0.0  # unknown outcomes fail loudly


def test_case_recorder_captures_the_shared_vocabulary(
    tmp_path: Path,
) -> None:
    source = tmp_path / "mirror_probe.py"
    source.write_text(
        "import pytest\n"
        "\n"
        "def test_ok():\n"
        "    assert True\n"
        "\n"
        "def test_bad():\n"
        '    assert False, "deliberate"\n'
        "\n"
        '@pytest.mark.xfail(strict=True, reason="mirror")\n'
        "def test_known_gap():\n"
        "    assert False\n",
        encoding="utf-8",
    )
    record = tmp_path / "outcomes.json"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(source),
            "-q",
            "--no-header",
            "-p",
            "tests.telemetry.case_recorder",
        ],
        cwd=REPO_ROOT,
        env={**os.environ, RECORD_ENV: str(record)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert record.exists(), proc.stdout
    outcomes = json.loads(record.read_text(encoding="utf-8"))
    assert len(outcomes) == 3
    assert {entry["outcome"] for entry in outcomes.values()} == {
        "passed",
        "failed",
        "expected-failure",
    }
    assert all(entry["duration_ms"] >= 0.0 for entry in outcomes.values())
    failed = next(entry for entry in outcomes.values() if entry["outcome"] == "failed")
    assert "deliberate" in failed["explanation"]


def test_recorder_without_record_path_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without AG_CASE_RECORD the plugin must stay inert."""
    monkeypatch.delenv(RECORD_ENV, raising=False)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "--no-header",
            "tests/test_write_ordering.py",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0
    assert not list(tmp_path.glob("outcomes.json"))
