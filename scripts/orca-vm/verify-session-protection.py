"""Verify current sessions, backup freshness, and allowed network endpoints."""

import hashlib
import json
import subprocess
import tarfile
from pathlib import Path

import modal

here = Path(__file__).resolve().parent
hosts = json.loads((here / ".local/protected-vms.json").read_text())
results = []
network_code = r"""


import concurrent.futures, json, subprocess

urls = [
    "https://models.opencode.ai/",
    "https://registry-1.docker.io/v2/",
    "https://download.surrealdb.com/",
    "https://mcp.context7.com/mcp",
    "https://mcp.exa.ai/mcp",
    "https://docs.arize.com/",
]


def check(url):
    p = subprocess.run(
        [
            "curl",
            "--head",
            "--location",
            "--max-time",
            "10",
            "--silent",
            "--show-error",
            "--output",
            "/dev/null",
            "--write-out",
            "%{http_code}",
            url,
        ],
        capture_output=True,
        text=True,
    )
    return {"url": url, "httpStatus": p.stdout, "exitCode": p.returncode, "error": p.stderr}


with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
    print(json.dumps(list(pool.map(check, urls))))
"""
snapshot_index = modal.Dict.from_name(
    "mercor-session-snapshot-index", environment_name="Development"
)
for host in hosts.values():
    sandbox = modal.Sandbox.from_id(host["sandboxId"])
    root = here / ".local/session-backups" / host["name"] / host["sandboxId"]
    stream = json.loads((root / "stream-status.json").read_text())
    latest = json.loads(sandbox.filesystem.read_text("/work/.mercor-backups/latest.json"))
    local_archive = root / "remote-checkpoints" / Path(latest["archive"]).name
    local_archive.parent.mkdir(parents=True, exist_ok=True)
    sandbox.filesystem.copy_to_local(latest["archive"], str(local_archive))
    assert hashlib.sha256(local_archive.read_bytes()).hexdigest() == latest["sha256"]
    with tarfile.open(local_archive) as archive:
        manifest = json.load(archive.extractfile("manifest.json"))
    snapshot = snapshot_index.get("latest:" + sandbox.object_id)
    process = sandbox.exec("python3", "-c", network_code, timeout=30)
    network = json.loads(process.stdout.read())
    # Preserve CLI terminal evidence without printing conversation content.
    environment = "mercor-modal-vm" if host["name"] == "activegraph" else "mercor-contactkiller-vm"
    call = subprocess.run(
        ["orca", "terminal", "list", "--environment", environment, "--json"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    inventory = json.loads(call.stdout)
    if not inventory.get("ok"):
        raise RuntimeError("Orca terminal inventory failed")
    terminals = inventory["result"]["terminals"]
    (root / "orca-terminal-inventory.json").write_text(json.dumps(inventory, indent=2))
    summaries = []
    for terminal in terminals:
        call = subprocess.run(
            [
                "orca",
                "terminal",
                "read",
                "--environment",
                environment,
                "--terminal",
                terminal["handle"],
                "--limit",
                "1000",
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        read = json.loads(call.stdout)
        (root / (terminal["handle"] + "-read.json")).write_text(json.dumps(read, indent=2))
        text = "\n".join(read.get("result", {}).get("terminal", {}).get("tail", []))
        summaries.append(
            {
                "handle": terminal["handle"],
                "branch": terminal.get("branch"),
                "connected": terminal.get("connected"),
                "agent": terminal.get("agentIdentity"),
                "mercorModelVisible": ("Mercor gateway" in text),
                "worktree": terminal.get("worktreePath"),
            }
        )
    results.append(
        {
            "name": host["name"],
            "sandboxId": sandbox.object_id,
            "stream": stream,
            "checkpoint": latest,
            "checkpointManifest": manifest,
            "snapshot": snapshot,
            "terminals": summaries,
            "network": network,
        }
    )
(here / ".local/session-protection-verification.json").write_text(json.dumps(results, indent=2))
print(json.dumps(results, indent=2))
