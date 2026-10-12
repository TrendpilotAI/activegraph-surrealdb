"""Optional Arize AX export for test telemetry and conformance evaluations.

This is evidence reporting, not application telemetry. When explicitly
enabled, the plugin exports one OpenTelemetry span per test plus a session
span to an Arize AX OTLP endpoint. Released-conformance cases additionally
carry OpenInference ``evaluation.*`` attributes so Arize AX renders them as
evaluations.

The export is metadata only: outcomes, durations, layers, and environment
versions. Event payloads, graph objects, relations, queries, and credentials
are never exported. Enablement requires credentials and an explicit flag,
mirroring the qualification gate in tests/qualification/conftest.py. Export
problems are reported but never fail a test run: telemetry stays fail-open
while the provider contracts stay fail-closed.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from dataclasses import dataclass, field
from importlib import metadata
from typing import Any

import pytest

from tests.telemetry.conformance_cases import (
    LAYER_CONFORMANCE as CONFORMANCE_LAYER,
)
from tests.telemetry.conformance_cases import OUTCOME_SCORES

ENV_ENABLE = "ACTIVEGRAPH_SURREALDB_ARIZE"
ENV_SPACE_ID = "ARIZE_SPACE_ID"
ENV_API_KEY = "ARIZE_API_KEY"
ENV_ENDPOINT = "ARIZE_OTLP_ENDPOINT"
ENV_PROJECT = "ARIZE_PROJECT_NAME"

DEFAULT_ENDPOINT = "https://otlp.arize.com/v1"
DEFAULT_PROJECT = "activegraph-surrealdb"
EXPLANATION_LIMIT = 512
FLUSH_TIMEOUT_MS = 15_000

# Session exit statuses that do not indicate failed assertions:
# 0 (all passed) and 5 (no tests collected).
benign = (0, 5)

# Outcome labels that decide a case even when the call phase never runs.
_deciding = {"failed", "skipped", "xpass-strict"}


@dataclass
class Settings:
    space_id: str
    api_key: str
    endpoint: str
    project: str


@dataclass
class Case:
    nodeid: str
    layer: str
    duration: float = 0.0
    outcome: str = "passed"
    explanation: str | None = None


@dataclass
class State:
    problem: str | None = None
    tracer: Any = None
    provider: Any = None
    session_span: Any = None
    cases: dict[str, Case] = field(default_factory=dict)


_STATE = State()


def _settings() -> Settings | None:
    if os.getenv(ENV_ENABLE) != "1":
        return None
    space_id = os.getenv(ENV_SPACE_ID)
    api_key = os.getenv(ENV_API_KEY)
    if not space_id or not api_key:
        return None
    return Settings(
        space_id=space_id,
        api_key=api_key,
        endpoint=os.getenv(ENV_ENDPOINT, DEFAULT_ENDPOINT),
        project=os.getenv(ENV_PROJECT, DEFAULT_PROJECT),
    )


def _layer(item: pytest.Item) -> str:
    nodeid = item.nodeid
    if "tests/qualification/" in nodeid or item.get_closest_marker("qualification"):
        return "qualification"
    if "tests/known_issues/" in nodeid:
        return "upstream known issues"
    if item.get_closest_marker("surrealdb"):
        if "conformance" in nodeid:
            return CONFORMANCE_LAYER
        return "provider invariants"
    return "serialization and configuration"


def outcome(report: pytest.TestReport) -> str:
    wasxfail = bool(getattr(report, "wasxfail", False))
    if wasxfail and report.passed:
        return "xpassed"
    if wasxfail and report.failed:
        # Strict xfail promotes an unexpected pass to a run failure.
        return "xpass-strict"
    if wasxfail:
        return "expected-failure"
    if report.passed:
        return "passed"
    if report.failed:
        return "failed"
    return "skipped"


def _explanation(report: pytest.TestReport) -> str | None:
    if report.passed:
        return None
    text = str(report.longrepr) if report.longrepr is not None else report.outcome
    if len(text) > EXPLANATION_LIMIT:
        text = text[:EXPLANATION_LIMIT] + "..."
    return text


def _git_evidence() -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        evidence["git.commit"] = sha
        evidence["git.dirty"] = bool(dirty)
    except (OSError, subprocess.SubprocessError):
        evidence["git.commit"] = "unknown"
        evidence["git.dirty"] = True
    return evidence


def _package_version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return "unknown"


def _build(settings: Settings) -> tuple[Any, Any, str | None]:
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        return (
            None,
            None,
            (
                "Arize AX export is enabled but opentelemetry is not installed. "
                "Install it with: python -m pip install -e '.[dev,telemetry]'"
            ),
        )
    resource = Resource(
        attributes={
            "service.name": settings.project,
            "arize.project.name": settings.project,
        }
    )
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(
        endpoint=settings.endpoint,
        headers={
            "space_id": settings.space_id,
            "api_key": settings.api_key,
            "Arize-Project": settings.project,
        },
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    tracer = provider.get_tracer("activegraph-surrealdb.conformance")
    return tracer, provider, None


def pytest_configure(config: pytest.Config) -> None:
    settings = _settings()
    if settings is None:
        return
    tracer, provider, problem = _build(settings)
    if problem is not None:
        _STATE.problem = problem
        return
    _STATE.tracer = tracer
    _STATE.provider = provider
    _STATE.session_span = tracer.start_span(
        "pytest::session",
        attributes={
            "pytest.command": " ".join(sys.argv),
            **_git_evidence(),
            "activegraph.version": _package_version("activegraph"),
            "activegraph_surrealdb.version": _package_version("activegraph-surrealdb"),
            "surrealdb.version": _package_version("surrealdb"),
            "python.version": platform.python_version(),
            "os.platform": f"{platform.system()} {platform.release()} {platform.machine()}",
        },
    )


def pytest_collection_modifyitems(
    session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
) -> None:
    for item in items:
        _STATE.cases.setdefault(item.nodeid, Case(nodeid=item.nodeid, layer=_layer(item)))


def pytest_sessionstart(session: pytest.Session) -> None:
    if _STATE.problem is None:
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(f"warning: {_STATE.problem}", yellow=True)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if _STATE.tracer is None:
        return
    case = _STATE.cases.get(report.nodeid)
    if case is None:
        case = Case(nodeid=report.nodeid, layer="unknown")
        _STATE.cases[report.nodeid] = case
    case.duration += report.duration
    decided = outcome(report)
    if report.when == "call" or decided in _deciding:
        case.outcome = decided
        case.explanation = _explanation(report)
    if report.when != "teardown":
        return
    _STATE.cases.pop(report.nodeid, None)
    _emit_case(case)


def _evaluation_attributes(case: Case) -> dict[str, Any]:
    if case.layer != CONFORMANCE_LAYER:
        return {}
    attributes: dict[str, Any] = {
        "evaluation.name": "conformance",
        "evaluation.label": case.outcome,
    }
    score = OUTCOME_SCORES.get(case.outcome)
    if score is not None:
        attributes["evaluation.score"] = score
    if case.explanation is not None:
        attributes["evaluation.explanation"] = case.explanation
    return attributes


def _emit_case(case: Case) -> None:
    from opentelemetry.trace import Status, StatusCode

    assert _STATE.tracer is not None
    span = _STATE.tracer.start_span(
        f"pytest::{case.nodeid}",
        attributes={
            "pytest.nodeid": case.nodeid,
            "pytest.layer": case.layer,
            "pytest.outcome": case.outcome,
            "pytest.duration_ms": case.duration * 1000.0,
            **_evaluation_attributes(case),
        },
    )
    if case.outcome in {"failed", "xpass-strict"}:
        span.set_status(Status(StatusCode.ERROR, case.outcome))
    span.end()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if _STATE.session_span is None:
        return
    from opentelemetry.trace import Status, StatusCode

    for category, count in _terminal_counts(session).items():
        _STATE.session_span.set_attribute(f"pytest.{category}", count)
    _STATE.session_span.set_attribute("pytest.exitstatus", int(exitstatus))
    errored = exitstatus not in benign
    _STATE.session_span.set_status(
        Status(StatusCode.ERROR, "session had failures") if errored else Status(StatusCode.OK)
    )
    _STATE.session_span.end()
    _flush()


def _terminal_counts(session: pytest.Session) -> dict[str, int]:
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        return {}
    stats = getattr(reporter, "stats", {})
    return {
        category: len(entries)
        for category, entries in stats.items()
        if category and isinstance(entries, list) and entries
    }


def _flush() -> None:
    if _STATE.provider is None:
        return
    try:
        _STATE.provider.force_flush(FLUSH_TIMEOUT_MS)
        _STATE.provider.shutdown()
    except Exception as error:  # telemetry stays fail-open
        print(f"warning: Arize AX export failed: {error}", file=sys.stderr)
    finally:
        _STATE.provider = None
        _STATE.tracer = None
        _STATE.session_span = None
