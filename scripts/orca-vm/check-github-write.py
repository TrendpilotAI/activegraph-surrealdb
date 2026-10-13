"""Check the current Modal Secret using non-mutating Git push dry runs."""

import json
import os
import sys
from pathlib import Path

import modal

os.umask(0o077)
code = r"""

import json, os, pathlib, subprocess, tempfile, urllib.request, urllib.error, uuid

keys = json.loads(os.environ["CHECK_SECRET_KEYS"])
token = next((os.environ[k].strip() for k in keys if os.environ.get(k)), None)
if not token:
    token = next(
        (
            os.environ[k].strip()
            for k in keys
            if os.environ.get(k, "").startswith(("github_pat_", "ghp_", "gho_"))
        ),
        None,
    )
if not token:
    raise RuntimeError("No recognized GitHub token field in Modal Secret")


def api(path):
    req = urllib.request.Request(
        "https://api.github.com/" + path,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "User-Agent": "Mercor-token-permission-check",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return {
                "status": r.status,
                "data": json.load(r),
                "classicScopes": r.headers.get("X-OAuth-Scopes"),
            }
    except urllib.error.HTTPError as e:
        return {"status": e.code, "data": json.load(e)}


account = api("user")
results = []
for repo, path in json.loads(os.environ["CHECK_REPOS"]):
    metadata = api("repos/TrendpilotAI/" + repo)
    env = {**os.environ, "CHECK_GITHUB_TOKEN": token, "GIT_TERMINAL_PROMPT": "0"}
    with tempfile.TemporaryDirectory(prefix="mercor-github-check-") as tmp:
        helper = pathlib.Path(tmp) / "askpass"
        helper.write_text(
            (
                '#!/bin/sh\ncase "$1" in *Username*) printf "%s'
                '" x-access-token;; *Password*) printf "%s" "$'
                'CHECK_GITHUB_TOKEN";; esac\n'
            )
        )
        helper.chmod(0o700)
        env["GIT_ASKPASS"] = str(helper)
        command = [
            "git",
            "-C",
            path,
            "-c",
            "credential.helper=",
            "push",
            "--dry-run",
            "--no-verify",
            "--porcelain",
            "https://github.com/TrendpilotAI/" + repo + ".git",
            "HEAD:refs/heads/mercor-permission-check-" + uuid.uuid4().hex[:12],
        ]
        try:
            push = subprocess.run(command, env=env, text=True, capture_output=True, timeout=30)
            check = {
                "exitCode": push.returncode,
                "stdout": push.stdout.replace(token, "[REDACTED]"),
                "stderr": push.stderr.replace(token, "[REDACTED]"),
            }
        except subprocess.TimeoutExpired:
            check = {"exitCode": None, "error": "Push dry run timed out"}
    results.append(
        {
            "repo": "TrendpilotAI/" + repo,
            "metadataStatus": metadata["status"],
            "accountRepositoryPermissions": metadata["data"].get("permissions"),
            "apiError": metadata["data"].get("message"),
            "pushDryRun": check,
        }
    )
print(
    json.dumps(
        {
            "account": account["data"].get("login"),
            "authenticationStatus": account["status"],
            "tokenType": "fine-grained" if token.startswith("github_pat_") else "classic-or-other",
            "classicScopes": account.get("classicScopes"),
            "repositories": results,
        },
        indent=2,
    )
)
"""
here = Path(__file__).resolve().parent
active = json.loads((here / ".local/connection.json").read_text())["sandboxId"]
contact = json.loads((here / ".local/contactkiller-connection.json").read_text())["sandboxId"]
hosts = [
    ("ActiveGraph", active, [("activegraph-surrealdb", "/work/mercor-work/activegraph-surrealdb")]),
    (
        "ContactKiller",
        contact,
        [
            ("ContactKiller-OSS", "/work/mercor-work/ContactKiller-OSS"),
            ("ContactKiller", "/work/mercor-work/ContactKiller"),
        ],
    ),
]
secret_name = sys.argv[1] if len(sys.argv) > 1 else "github-secret"
secret = modal.Secret.from_name(secret_name, environment_name="Development")
secret_keys = sorted(secret._get_keys(refresh=True))
results = []
for name, sandbox_id, repos in hosts:
    sandbox = modal.Sandbox.from_id(sandbox_id)
    process = sandbox.exec(
        "/usr/sbin/runuser",
        "-u",
        "mercor",
        "--",
        "env",
        "HOME=/home/mercor",
        "PYTHONPATH=",
        "python3",
        "-c",
        code,
        secrets=[secret],
        env={"CHECK_REPOS": json.dumps(repos), "CHECK_SECRET_KEYS": json.dumps(secret_keys)},
        timeout=120,
    )
    output = process.stdout.read()
    errors = process.stderr.read()
    if process.wait() != 0:
        raise RuntimeError(name + " check failed: " + errors)
    result = {"sandbox": name, "modalSecret": secret_name, **json.loads(output)}
    results.append(result)
    print(json.dumps(result, indent=2))
destination = Path(__file__).resolve().parent / ".local/github-write-check.json"
destination.write_text(json.dumps(results, indent=2))
destination.chmod(0o600)
