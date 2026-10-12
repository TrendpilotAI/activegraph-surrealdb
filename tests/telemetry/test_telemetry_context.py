"""Guarantees for the shared evidence context and adaptor instrumentation.

Evidence collection must never lie (branch, CI, lane host) and never fail a
run (the server probe is bounded and fail-open). The instrumentation spans
must carry operation identity only — no statement text, no payloads — and
must behave as no-ops when no SDK provider is installed.
"""

from __future__ import annotations

import contextlib
import subprocess

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

from tests.telemetry.context import (
    ci_evidence,
    git_evidence,
    lane_host,
    surrealdb_server_version,
)


def test_git_evidence_covers_commit_branch_and_dirty_status() -> None:
    evidence = git_evidence()
    assert set(evidence) == {"git.commit", "git.dirty", "git.branch"}
    assert isinstance(evidence["git.commit"], str)
    assert isinstance(evidence["git.dirty"], bool)
    assert isinstance(evidence["git.branch"], str) and evidence["git.branch"]
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert evidence["git.commit"] == sha


def test_ci_evidence_activates_only_inside_ci(
    monkeypatch,
) -> None:

    for var in (
        "GITHUB_ACTIONS",
        "CI",
        "GITHUB_REPOSITORY",
        "GITHUB_RUN_ID",
        "GITHUB_ACTOR",
        "GITHUB_REF",
    ):
        monkeypatch.delenv(var, raising=False)
    assert ci_evidence() == {}

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", "TrendpilotAI/activegraph-surrealdb")
    monkeypatch.setenv("GITHUB_RUN_ID", "1234")
    monkeypatch.setenv("GITHUB_ACTOR", "TrendpilotAI")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    evidence = ci_evidence()
    assert evidence["ci.provider"] == "github-actions"
    assert evidence["ci.run_id"] == "1234"
    assert evidence["ci.actor"] == "TrendpilotAI"
    assert evidence["git.ref"] == "refs/heads/main"
    assert evidence["ci.run_url"].endswith("/actions/runs/1234")


def test_lane_host_exposes_the_host_only() -> None:
    assert lane_host("ws://localhost:8000") == "localhost:8000"
    assert lane_host("http://user:secret@db.example.com:8000/rpc") == ("db.example.com:8000")
    assert lane_host(None) is None
    assert lane_host("") is None


def test_server_version_probe_fails_open_on_a_dead_lane() -> None:
    version = surrealdb_server_version("http://127.0.0.1:1", timeout_s=1.0)
    assert version == "unknown"


class _Collector(SpanExporter):
    """In-memory exporter for span assertions."""

    def __init__(self) -> None:
        self.spans = []

    def export(self, spans):
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self):
        return None


def test_traced_decorator_records_operation_outcome_and_duration(
    monkeypatch,
) -> None:
    from activegraph_surrealdb import instrument

    collector = _Collector()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(collector))
    monkeypatch.setattr(instrument, "tracer", provider.get_tracer("test"))

    @instrument.traced("event_store.append")
    def works(self):
        return "ok"

    @instrument.traced("event_store.append")
    def breaks(self):
        raise RuntimeError("boom")

    class Store:
        pass

    store = Store()
    assert works(store) == "ok"
    with contextlib.suppress(RuntimeError):
        breaks(store)
    provider.force_flush()

    assert len(collector.spans) == 2
    ok, failed = collector.spans
    # Success is implicit in OpenTelemetry: the status stays UNSET, and only
    # failures carry an explicit ERROR status with the exception recorded.
    assert ok.status.status_code.name == "UNSET"
    assert ok.attributes["activegraph.operation"] == "event_store.append"
    assert failed.status.status_code.name == "ERROR"
    assert failed.attributes["activegraph.operation"] == "event_store.append"
    assert any(
        event.name == "exception" and event.attributes.get("exception.type") == "RuntimeError"
        for event in failed.events
    )


def test_traced_decorator_holds_spans_open_for_generators(
    monkeypatch,
) -> None:
    from activegraph_surrealdb import instrument

    collector = _Collector()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(collector))
    monkeypatch.setattr(instrument, "tracer", provider.get_tracer("test"))

    @instrument.traced("event_store.iter")
    def lazy(self):
        # The deferred fetch happens on first next(), inside the span.
        rows = iter([1, 2, 3])
        yield from rows

    store = object()
    iterator = lazy(store)
    assert list(iterator) == [1, 2, 3]
    provider.force_flush()

    assert len(collector.spans) == 1
    assert collector.spans[0].name == "activegraph.event_store.iter"


def test_statement_kind_classification_stays_metadata_only() -> None:
    from activegraph_surrealdb.instrument import statement_kind

    assert statement_kind("SELECT * FROM $event;") == "SELECT"
    assert statement_kind("  create person CONTENT $x;") == "CREATE"
    assert statement_kind("BEGIN TRANSACTION") == "BEGIN"
    assert statement_kind("RELATE $from->$to;") == "RELATE"
    # Unclassifiable text must not leak into the attribute beyond the class.
    assert statement_kind("/* comment */ SELECT 1;") == "OTHER"
    assert statement_kind("") == "OTHER"


def test_noop_api_keeps_stores_working_without_an_sdk() -> None:
    """Without a configured SDK the API is a no-op; stores must not care."""

    from activegraph_surrealdb.instrument import tracer

    current = trace.get_tracer_provider()
    if type(current).__name__ == "ProxyTracerProvider":
        with tracer.start_as_current_span("probe") as span:
            assert span.is_recording() is False
