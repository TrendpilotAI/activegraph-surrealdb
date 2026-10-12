"""Environment evidence shared by the telemetry plugin and the mirror runner.

Everything here is provenance metadata: where a run happened (git, CI), what
it ran against (the SurrealDB lane's host and server version), and nothing
about the data. Credentials are never collected; the lane is identified by
host only, and the server-version probe is bounded and fail-open so it can
never break or hang a test run.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
from typing import Any
from urllib.parse import urlparse

# Mirrors the server-version normalizer in
# src/activegraph_surrealdb/connection.py; imported from there when the
# provider is installed so both call sites normalize identically.
try:
    from activegraph_surrealdb.connection import _VERSION_RE as _SERVER_VERSION_RE
except ImportError:  # pragma: no cover - only hit without the provider
    _SERVER_VERSION_RE = re.compile(r"(?:surrealdb[- ]v?)?(?P<version>\d+\.\d+\.\d+)", re.I)

ENV_LANE_URL = "ACTIVEGRAPH_SURREALDB_TEST_URL"
SERVER_PROBE_TIMEOUT_S = 3.0


def git_evidence() -> dict[str, Any]:
    """Commit, dirty status, and branch; fail-open to explicit unknowns."""

    def _git(*args: str) -> str:
        return subprocess.run(
            ["git", *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    evidence: dict[str, Any] = {}
    try:
        evidence["git.commit"] = _git("rev-parse", "HEAD")
        dirty = _git("status", "--porcelain")
        evidence["git.dirty"] = bool(dirty)
        evidence["git.branch"] = _git("rev-parse", "--abbrev-ref", "HEAD")
    except (OSError, subprocess.SubprocessError):
        evidence["git.commit"] = "unknown"
        evidence["git.dirty"] = True
        evidence["git.branch"] = "unknown"
    return evidence


def ci_evidence() -> dict[str, Any]:
    """CI provenance when the run happens inside a recognized provider."""

    evidence: dict[str, Any] = {}
    if os.getenv("GITHUB_ACTIONS") == "true":
        evidence["ci.provider"] = "github-actions"
        evidence["ci.run_id"] = os.getenv("GITHUB_RUN_ID", "unknown")
        evidence["ci.run_url"] = (
            "https://github.com/"
            f"{os.getenv('GITHUB_REPOSITORY', 'unknown')}/actions/runs/"
            f"{os.getenv('GITHUB_RUN_ID', 'unknown')}"
        )
        evidence["ci.actor"] = os.getenv("GITHUB_ACTOR", "unknown")
        evidence["git.ref"] = os.getenv("GITHUB_REF", "unknown")
    elif os.getenv("CI"):
        evidence["ci.provider"] = "unknown-ci"
    return evidence


def lane_host(endpoint: str | None) -> str | None:
    """The lane's host and port, or None when no lane is configured.

    Built from ``hostname``/``port`` so userinfo can never leak: an endpoint
    with embedded credentials reports the host alone, and scheme, path, and
    query are dropped. The host is enough to tell lanes apart on a dashboard.
    """

    if not endpoint:
        return None
    parsed = urlparse(endpoint)
    if not parsed.hostname:
        return None
    if parsed.port:
        return f"{parsed.hostname}:{parsed.port}"
    return parsed.hostname


def surrealdb_server_version(endpoint: str, timeout_s: float = SERVER_PROBE_TIMEOUT_S) -> str:
    """Probe the lane's server version, bounded and fail-open.

    Opens a throwaway connection (the same SDK transport the provider uses)
    and asks the server for its version. Returns ``"unknown"`` — never
    raises — when the lane is unreachable, slow, or reports something
    unparseable, because evidence collection must never fail a test run.
    """

    result: dict[str, str] = {}

    def _probe() -> None:
        try:
            from surrealdb import Surreal

            connection = Surreal(endpoint)
            try:
                raw = str(connection.version())
            finally:
                connection.close()
            match = _SERVER_VERSION_RE.search(raw)
            result["version"] = match.group("version") if match else raw
        except Exception:  # noqa: BLE001 - fail-open by contract
            result["version"] = "unknown"

    probe = threading.Thread(target=_probe, daemon=True)
    probe.start()
    probe.join(timeout_s)
    return result.get("version", "unknown")
