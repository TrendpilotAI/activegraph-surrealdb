"""Subprocess recorder that captures test outcomes as JSON.

``scripts/arize_conformance.py`` runs each conformance case as its own pytest
invocation (live execution against the pinned SurrealDB lane). This plugin is
loaded in that subprocess via ``-p tests.telemetry.case_recorder`` and writes
one JSON document — node id → outcome, duration, explanation — when the
session finishes. The outcome vocabulary is shared with the Arize plugin, so
an experiment run and a CI run label cases identically.

Enable by setting ``AG_CASE_RECORD`` to the output path; without it the plugin
does nothing.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any

import pytest

from tests.telemetry.arize_ax import EXPLANATION_LIMIT, outcome

RECORD_ENV = "AG_CASE_RECORD"

# Same decision rule as the Arize plugin: the call phase decides, except for
# the setup/teardown statuses that decide a case on their own.
_deciding = {"failed", "skipped", "xpass-strict"}


@dataclass
class _Case:
    outcome: str = "passed"
    duration: float = 0.0
    setup: float = 0.0
    call: float = 0.0
    teardown: float = 0.0
    explanation: str | None = None


_CASES: dict[str, _Case] = {}


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    case = _CASES.setdefault(report.nodeid, _Case())
    case.duration += report.duration
    if report.when == "setup":
        case.setup += report.duration
    elif report.when == "call":
        case.call += report.duration
    elif report.when == "teardown":
        case.teardown += report.duration
    decided = outcome(report)
    if report.when == "call" or decided in _deciding:
        case.outcome = decided
        explanation = None
        if not report.passed:
            explanation = str(report.longrepr) if report.longrepr is not None else decided
            if len(explanation) > EXPLANATION_LIMIT:
                explanation = explanation[:EXPLANATION_LIMIT] + "..."
        case.explanation = explanation


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    path = os.getenv(RECORD_ENV)
    if not path:
        return
    payload: dict[str, Any] = {
        nodeid: {
            "outcome": case.outcome,
            "duration_ms": round(case.duration * 1000.0, 3),
            "setup_ms": round(case.setup * 1000.0, 3),
            "call_ms": round(case.call * 1000.0, 3),
            "teardown_ms": round(case.teardown * 1000.0, 3),
            "explanation": case.explanation,
        }
        for nodeid, case in _CASES.items()
    }
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
    except OSError as error:
        print(f"warning: case recording failed: {error}", file=sys.stderr)
