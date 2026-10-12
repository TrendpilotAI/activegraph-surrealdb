"""Contract scoring shared by the Arize experiment runner and its tests.

The scorer turns a case outcome — the same vocabulary the pytest telemetry
plugin reports — into an Arize ``EvaluationResult``:

- ``passed`` / ``expected-failure`` score 1.0 (the store behaved as the
  contract documents, including a known issue behaving as expected),
- ``failed`` / ``xpassed`` / ``xpass-strict`` score 0.0 (an ``xpass-strict``
  means the upstream contract moved and this repository must act),
- ``skipped`` is label-only and carries no score.

Unknown outcomes score 0.0: the vocabulary is closed, so anything else is a
bug and must fail loudly rather than vanish from score aggregates.
"""

from __future__ import annotations

from typing import Any

from tests.telemetry.conformance_cases import EXPECTED_PASS, OUTCOME_SCORES

SKIPPED = "skipped"


def contract_scorer(output: Any, input: Any = None, **_: Any) -> Any:
    """Score one case outcome as an Arize evaluation.

    ``output`` is the task result mapping (``outcome``, optional
    ``explanation``); ``input`` is the dataset example input carrying the
    case's ``contract_ref`` and ``expected`` fields. Returns an Arize
    ``EvaluationResult``; the SDK import is deferred so the mirror's offline
    tests run without the ``telemetry`` extra.
    """
    from arize.experiments import EvaluationResult

    case = dict(input) if isinstance(input, dict) else {}
    observed = output.get("outcome") if isinstance(output, dict) else None
    observed = observed if isinstance(observed, str) else "unknown"
    explanation = output.get("explanation") if isinstance(output, dict) else None
    if observed == SKIPPED:
        return EvaluationResult(
            score=None,
            label=SKIPPED,
            explanation="case did not run against the lane; label only",
            metadata={},
        )
    detail = (
        f"{case.get('contract_ref', 'contract')}: "
        f"expected {case.get('expected', EXPECTED_PASS)}, observed {observed}"
    )
    if explanation:
        detail = f"{detail} -- {explanation}"
    return EvaluationResult(
        score=OUTCOME_SCORES.get(observed, 0.0),
        label=observed,
        explanation=detail,
        metadata={},
    )
