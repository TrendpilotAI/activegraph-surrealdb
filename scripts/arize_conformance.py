#!/usr/bin/env python3
"""Rerun the conformance contract as Arize AX datasets and experiments.

The mirror is built from tests/telemetry/conformance_cases.py, which
enumerates every case this repository tracks: the 11 released event-store
conformance cases, the 25 released graph-store conformance cases, and the
upstream known issues.

Commands:

  cases     Print the mirrored case registry (offline, no credentials).
  sync      Create one Arize AX dataset per suite (idempotent).
  run       Rerun every case live against the pinned SurrealDB lane and
            record the outcomes as an Arize AX experiment.
  register  Register the outcome evaluator in Arize AX so the platform
            renders the same scoring the local evaluators apply.

Required environment:

  ARIZE_API_KEY, ARIZE_SPACE_ID    Arize AX target (sync, run, register)
  ACTIVEGRAPH_SURREALDB_TEST_URL   live SurrealDB lane (run; see
                                   docs/deployment.md)

Every experiment task executes the real pytest case in a subprocess; outcomes
are scored with the mapping shared with the pytest telemetry plugin. The
mirror is metadata only: no event payloads, graph objects, relations, or
query text leaves the machine.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from arize import ArizeClient  # noqa: E402
from arize.evaluators import CustomCodeConfig  # noqa: E402
from arize.experiments.functions import create_evaluator  # noqa: E402
from tests.telemetry.arize_ax import EXPLANATION_LIMIT  # noqa: E402
from tests.telemetry.case_recorder import RECORD_ENV  # noqa: E402
from tests.telemetry.conformance_cases import (  # noqa: E402
    SUITE_EVENT_STORE,
    SUITE_GRAPH_STORE,
    SUITE_KNOWN_ISSUES,
    ConformanceCase,
    cases_by_dataset,
    enumerate_cases,
)
from tests.telemetry.context import (  # noqa: E402
    ci_evidence,
    git_evidence,
    lane_host,
)
from tests.telemetry.scoring import contract_scorer  # noqa: E402

CASE_TIMEOUT_S = 300
EVALUATOR_NAME = "activegraph-contract-outcome"

# Custom code evaluator registered in Arize AX. Mirrors OUTCOME_SCORES so the
# platform scores reruns identically to local runs; the class + evaluate()
# method is the harness AX documents for user-supplied Python evaluators.
EVALUATOR_CODE = """\
# Score a conformance case outcome (see OUTCOME_SCORES in the mirror repo).
class Evaluator:
    def evaluate(self, output=None, **_):
        observed = output.get("outcome") if isinstance(output, dict) else None
        if not isinstance(observed, str):
            observed = "unknown"
        if observed == "skipped":
            return {
                "score": None,
                "label": "skipped",
                "explanation": "case did not run; label only",
            }
        scores = {
            "passed": 1.0,
            "expected-failure": 1.0,
            "failed": 0.0,
            "xpassed": 0.0,
            "xpass-strict": 0.0,
        }
        return {
            "score": scores.get(observed, 0.0),
            "label": observed,
            "explanation": "contract outcome mapping (OUTCOME_SCORES)",
        }
"""

CONTRACT_EVALUATOR = create_evaluator(name="contract-outcome", scorer=contract_scorer)


def _selected_cases(suite: str | None) -> list[ConformanceCase]:
    cases = enumerate_cases()
    if suite is None or suite == "all":
        return cases
    return [case for case in cases if case.suite == suite]


def _ax_client() -> tuple[ArizeClient, str]:
    api_key = os.getenv("ARIZE_API_KEY")
    space = os.getenv("ARIZE_SPACE_ID")
    if not api_key or not space:
        raise SystemExit(
            "Arize AX access needs ARIZE_API_KEY and ARIZE_SPACE_ID in the "
            "environment (see docs/telemetry.md)."
        )
    return ArizeClient(api_key=api_key), space


def _run_metadata() -> dict[str, str]:
    # Experiment runs carry the same provenance the session span does:
    # commit, branch, dirty status, CI context, and the lane host only.
    metadata = {key: str(value) for key, value in {**git_evidence(), **ci_evidence()}.items()}
    host = lane_host(os.getenv("ACTIVEGRAPH_SURREALDB_TEST_URL"))
    if host:
        metadata["lane.host"] = host
    return metadata


def make_task() -> Callable[[Any], dict[str, Any]]:
    """Task factory: run one case live in a pytest subprocess."""

    def run_case(example: Any) -> dict[str, Any]:
        raw = getattr(example, "input", example)
        case = dict(raw) if isinstance(raw, dict) else {}
        nodeid = str(case.get("nodeid", ""))
        case_id = str(case.get("case_id", nodeid))
        started = time.monotonic()
        with tempfile.TemporaryDirectory() as tmp:
            record = Path(tmp) / "outcomes.json"
            env = {**os.environ, RECORD_ENV: str(record)}
            try:
                proc = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "pytest",
                        nodeid,
                        "-q",
                        "--tb=short",
                        "--no-header",
                        "-p",
                        "tests.telemetry.case_recorder",
                    ],
                    cwd=REPO_ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=CASE_TIMEOUT_S,
                    check=False,
                )
                outcomes: Any = {}
                if record.exists():
                    try:
                        outcomes = json.loads(record.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError) as error:
                        print(f"warning: {case_id}: unreadable record: {error}")
            except subprocess.TimeoutExpired:
                return {
                    "case_id": case_id,
                    "nodeid": nodeid,
                    "outcome": "failed",
                    "explanation": f"case exceeded {CASE_TIMEOUT_S}s",
                    "duration_ms": CASE_TIMEOUT_S * 1000.0,
                }
        wall_ms = round((time.monotonic() - started) * 1000.0, 3)
        entry = outcomes.get(nodeid) if isinstance(outcomes, dict) else None
        if not isinstance(entry, dict):
            tail = (proc.stdout or proc.stderr or "no outcome recorded")[
                -EXPLANATION_LIMIT:
            ].strip()
            return {
                "case_id": case_id,
                "nodeid": nodeid,
                "outcome": "failed",
                "explanation": tail,
                "duration_ms": wall_ms,
            }
        return {
            "case_id": case_id,
            "nodeid": nodeid,
            "outcome": str(entry.get("outcome", "failed")),
            "explanation": entry.get("explanation"),
            "duration_ms": entry.get("duration_ms") or wall_ms,
            "setup_ms": entry.get("setup_ms"),
            "call_ms": entry.get("call_ms"),
            "teardown_ms": entry.get("teardown_ms"),
        }

    return run_case


def cmd_cases(args: argparse.Namespace) -> int:
    selected = _selected_cases(args.suite)
    for case in selected:
        print(
            f"{case.case_id}\n"
            f"    node: {case.nodeid}\n"
            f"    layer: {case.layer} | contract: {case.contract_ref} | "
            f"expected: {case.expected}\n"
            f"    what: {case.description}"
        )
    print(f"\n{len(selected)} cases")
    return 0


def _find_dataset(response: Any, name: str) -> Any | None:
    items = getattr(response, "datasets", None) or getattr(response, "data", None) or []
    for item in items:
        if isinstance(item, dict):
            if item.get("name") == name:
                return item
        elif getattr(item, "name", None) == name:
            return item
    return None


def cmd_sync(args: argparse.Namespace) -> int:
    client, space = _ax_client()
    failed = False
    for dataset, suite_cases in cases_by_dataset(_selected_cases(args.suite)).items():
        try:
            listing = client.datasets.list(name=dataset, space=space)
            if _find_dataset(listing, dataset) is not None:
                print(f"= {dataset}: dataset exists; cases unchanged")
                continue
            created = client.datasets.create(
                name=dataset,
                space=space,
                examples=[case.as_example() for case in suite_cases],
            )
            print(
                f"+ {dataset}: created with {len(suite_cases)} cases "
                f"({getattr(created, 'id', '?')})"
            )
        except Exception as error:  # keep syncing the remaining suites
            failed = True
            print(f"! {dataset}: {error}")
    return 1 if failed else 0


def cmd_run(args: argparse.Namespace) -> int:
    if not os.getenv("ACTIVEGRAPH_SURREALDB_TEST_URL"):
        raise SystemExit(
            "Live reruns execute the real cases and need a SurrealDB lane: "
            "set ACTIVEGRAPH_SURREALDB_TEST_URL (docs/deployment.md)."
        )
    client, space = _ax_client()
    task = make_task()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    failed = False
    for dataset, suite_cases in cases_by_dataset(_selected_cases(args.suite)).items():
        name = f"{dataset}-{stamp}"
        print(f"--> {name}: {len(suite_cases)} live cases")
        try:
            result = client.experiments.run(
                name=name,
                dataset=dataset,
                space=space,
                task=task,
                evaluators=[CONTRACT_EVALUATOR],
                concurrency=args.concurrency,
                timeout=args.timeout,
                metadata=_run_metadata(),
                exit_on_error=False,
            )
        except Exception as error:  # keep running the remaining suites
            failed = True
            print(f"! {dataset}: experiment failed: {error}")
            continue
        experiment = result[0] if isinstance(result, tuple) else result
        print(f"    experiment id: {getattr(experiment, 'id', 'unknown')}")
    return 1 if failed else 0


def cmd_register(args: argparse.Namespace) -> int:
    config = CustomCodeConfig(
        type="custom_code",
        name=EVALUATOR_NAME,
        code=EVALUATOR_CODE,
        variables=["output"],
    )
    if args.dry_run:
        print(f"--dry-run: would register {EVALUATOR_NAME!r}:\n{config}")
        return 0
    client, space = _ax_client()
    created = client.evaluators.create_code_evaluator(
        name=EVALUATOR_NAME,
        space=space,
        commit_message="Mirror OUTCOME_SCORES from the ActiveGraph SurrealDB conformance mirror",
        code_config=config,
        description=(
            "Scores conformance cases rerun from the activegraph-surrealdb "
            "mirror: passed/expected-failure 1.0, failed/xpassed/"
            "xpass-strict 0.0, skipped label-only."
        ),
    )
    print(f"+ registered {EVALUATOR_NAME}: {getattr(created, 'id', '?')}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="arize_conformance",
        description="Rerun the conformance contract in Arize AX.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def with_suite(target: argparse.ArgumentParser) -> None:
        target.add_argument(
            "--suite",
            choices=[SUITE_EVENT_STORE, SUITE_GRAPH_STORE, SUITE_KNOWN_ISSUES],
            help="limit to one suite (default: all)",
        )

    cases_parser = commands.add_parser("cases", help="print the case registry (offline)")
    with_suite(cases_parser)
    cases_parser.set_defaults(func=cmd_cases)

    sync_parser = commands.add_parser("sync", help="create the per-suite datasets in Arize AX")
    with_suite(sync_parser)
    sync_parser.set_defaults(func=cmd_sync)

    run_parser = commands.add_parser("run", help="live-rerun every case as an Arize AX experiment")
    with_suite(run_parser)
    run_parser.add_argument("--concurrency", type=int, default=3)
    run_parser.add_argument("--timeout", type=int, default=600)
    run_parser.set_defaults(func=cmd_run)

    register_parser = commands.add_parser(
        "register", help="register the outcome evaluator in Arize AX"
    )
    register_parser.add_argument(
        "--dry-run", action="store_true", help="print the config, do not call"
    )
    register_parser.set_defaults(func=cmd_register)

    args = parser.parse_args()
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
