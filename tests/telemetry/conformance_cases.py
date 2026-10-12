"""Machine-readable mirror of the conformance contract for Arize AX reruns.

This module enumerates every conformance case this repository tracks — the
released ActiveGraph suites (11 event-store and 25 graph-store cases) plus the
upstream known issues — as plain data. ``scripts/arize_conformance.py`` turns
the registry into Arize AX datasets (one per suite), runs each case live as an
experiment task, and scores it with the same outcome mapping the pytest plugin
uses, so a rerun in Arize is directly comparable with a CI run.

The module is import-safe without a live store: the ActiveGraph conformance
base classes are only inspected lazily, and no store or connection is created.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from typing import Any

SUITE_EVENT_STORE = "event-store"
SUITE_GRAPH_STORE = "graph-store"
SUITE_KNOWN_ISSUES = "known-issues"

DATASET_EVENT_STORE = "event-store-conformance"
DATASET_GRAPH_STORE = "graph-store-conformance"
DATASET_KNOWN_ISSUES = "activegraph-known-issues"

LAYER_CONFORMANCE = "released conformance"
LAYER_KNOWN_ISSUES = "upstream known issues"

EXPECTED_PASS = "pass"
EXPECTED_FAILURE = "expected-failure"

# Outcome -> evaluation score, shared with tests/telemetry/arize_ax.py.
# "skipped" is intentionally absent: it is label-only and carries no score.
OUTCOME_SCORES = {
    "passed": 1.0,
    "expected-failure": 1.0,
    "failed": 0.0,
    "xpassed": 0.0,
    "xpass-strict": 0.0,
}

_CONTRACT_PATTERN = re.compile(r"CONTRACT \S+ #\d+")

_EVENT_MODULE = "activegraph.store.conformance"
_EVENT_BASE = "EventStoreConformance"
_EVENT_FILE = "tests/integration/test_event_store_conformance.py"
_EVENT_CLASS = "TestSurrealEventStoreConformance"

_GRAPH_MODULE = "activegraph.store.graph_conformance"
_GRAPH_BASE = "GraphStoreConformance"
_GRAPH_FILE = "tests/integration/test_graph_store_conformance.py"
_GRAPH_CLASS = "TestSurrealGraphStoreConformance"

_KNOWN_ISSUES: tuple[tuple[str, str, str, str], ...] = (
    # (file, test name, upstream issue, one-line description)
    (
        "tests/known_issues/test_activegraph_v112_load.py",
        "test_unknown_run_load_does_not_create_phantom_run",
        "yoheinakajima/activegraph#81",
        "Runtime.load with an unknown run id must not create a phantom run",
    ),
    (
        "tests/known_issues/test_activegraph_v112_stale_projection.py",
        "test_runtime_load_replaces_stale_projection",
        "yoheinakajima/activegraph#82",
        "Runtime.load must replace a stale graph projection",
    ),
)


@dataclass(frozen=True)
class ConformanceCase:
    """One rerunnable conformance case, mirroring a pytest node."""

    case_id: str
    suite: str
    dataset: str
    layer: str
    nodeid: str
    contract_ref: str
    description: str
    expected: str

    def as_example(self) -> dict[str, Any]:
        """Arize dataset example: task input plus suite/layer metadata."""
        return {
            "input": {
                "case_id": self.case_id,
                "nodeid": self.nodeid,
                "contract_ref": self.contract_ref,
                "description": self.description,
                "expected": self.expected,
            },
            "metadata": {"suite": self.suite, "layer": self.layer},
        }


def _contract_ref(module: Any) -> str:
    doc = inspect.getdoc(module) or ""
    found = _CONTRACT_PATTERN.search(doc)
    return found.group(0) if found else "unversioned contract"


def _description(base: Any, method: str) -> str:
    doc = inspect.getdoc(getattr(base, method)) or ""
    first = doc.strip().splitlines()[0].strip() if doc.strip() else ""
    return first or method.removeprefix("test_").replace("_", " ")


def _suite_cases(
    suite: str,
    dataset: str,
    module_name: str,
    base_name: str,
    test_file: str,
    test_class: str,
) -> list[ConformanceCase]:
    """Enumerate the inherited conformance cases of one released suite."""
    import importlib

    module = importlib.import_module(module_name)
    base = getattr(module, base_name)
    methods = sorted(
        name
        for name, member in inspect.getmembers(base, inspect.isfunction)
        if name.startswith("test_")
    )
    contract = _contract_ref(module)
    layer = LAYER_CONFORMANCE
    return [
        ConformanceCase(
            case_id=f"{suite}.{method}",
            suite=suite,
            dataset=dataset,
            layer=layer,
            nodeid=f"{test_file}::{test_class}::{method}",
            contract_ref=contract,
            description=_description(base, method),
            expected=EXPECTED_PASS,
        )
        for method in methods
    ]


def _known_issue_cases() -> list[ConformanceCase]:
    return [
        ConformanceCase(
            case_id=f"{SUITE_KNOWN_ISSUES}.{test}",
            suite=SUITE_KNOWN_ISSUES,
            dataset=DATASET_KNOWN_ISSUES,
            layer=LAYER_KNOWN_ISSUES,
            nodeid=f"{file}::{test}",
            contract_ref=issue,
            description=description,
            expected=EXPECTED_FAILURE,
        )
        for file, test, issue, description in _KNOWN_ISSUES
    ]


def enumerate_cases() -> list[ConformanceCase]:
    """Every rerunnable case: 11 event-store, 25 graph-store, known issues."""
    return [
        *_suite_cases(
            SUITE_EVENT_STORE,
            DATASET_EVENT_STORE,
            _EVENT_MODULE,
            _EVENT_BASE,
            _EVENT_FILE,
            _EVENT_CLASS,
        ),
        *_suite_cases(
            SUITE_GRAPH_STORE,
            DATASET_GRAPH_STORE,
            _GRAPH_MODULE,
            _GRAPH_BASE,
            _GRAPH_FILE,
            _GRAPH_CLASS,
        ),
        *_known_issue_cases(),
    ]


def cases_by_dataset(cases: list[ConformanceCase]) -> dict[str, list[ConformanceCase]]:
    grouped: dict[str, list[ConformanceCase]] = {}
    for case in cases:
        grouped.setdefault(case.dataset, []).append(case)
    return grouped
