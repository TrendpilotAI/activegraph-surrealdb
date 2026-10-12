"""Fail-open OpenTelemetry instrumentation for the provider.

The adaptor emits spans through the OpenTelemetry API only. Without an
installed SDK the API is a no-op, so instrumentation is effectively free and
can never alter store behavior; with an SDK provider configured (for example
by the test telemetry plugin), operation latency and outcomes flow to the
exporter.

The telemetry contract from docs/telemetry.md applies to every span here:
operation identity, statement class, outcome, and duration only. SurrealQL
statement text, bound variables, event payloads, graph objects, relation
data, and credentials are never placed on spans.
"""

from __future__ import annotations

import functools
import inspect
import re
from collections.abc import Callable
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

tracer = trace.get_tracer("activegraph_surrealdb")

_STATEMENT_KIND = re.compile(
    r"^\s*(BEGIN|CANCEL|COMMIT|SELECT|INSERT|CREATE|UPDATE|DELETE|RELATE|"
    r"DEFINE|REMOVE|INFO|LET|RETURN|KILL|REBUILD|THROW)\b",
    re.IGNORECASE,
)


def statement_kind(statement: str) -> str:
    """Classify a statement without recording its text."""

    match = _STATEMENT_KIND.match(statement)
    return match.group(1).upper() if match else "OTHER"


def _fail(span: trace.Span, error: BaseException) -> None:
    span.record_exception(error)
    span.set_status(Status(StatusCode.ERROR, type(error).__name__))


def traced(operation: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Span per store operation, named for the operation.

    Generator methods keep their span open across iteration, so the first
    ``next()`` pays the deferred queries inside the span that observes them.
    Spans carry the operation name and outcome only.
    """

    def decorate(method: Callable[..., Any]) -> Callable[..., Any]:
        name = f"activegraph.{operation}"

        if inspect.isgeneratorfunction(method):

            @functools.wraps(method)
            def gen_wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
                with tracer.start_as_current_span(name, kind=SpanKind.CLIENT) as span:
                    span.set_attribute("activegraph.operation", operation)
                    try:
                        yield from method(self, *args, **kwargs)
                    except Exception as error:
                        _fail(span, error)
                        raise

            return gen_wrapper

        @functools.wraps(method)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            with tracer.start_as_current_span(name, kind=SpanKind.CLIENT) as span:
                span.set_attribute("activegraph.operation", operation)
                try:
                    return method(self, *args, **kwargs)
                except Exception as error:
                    _fail(span, error)
                    raise

        return wrapper

    return decorate


__all__ = ["statement_kind", "traced", "tracer"]
