"""Paid self-test only: run inside the sandbox as mercor, after configuration."""

import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

assert os.getuid() == 1000
assert not Path("/Users/winner").exists()
assert not Path("/var/run/docker.sock").exists()
assert not os.access("/root", os.R_OK)
state = json.loads(Path("/opt/mercor-tools/modal-state.json").read_text())
repo = Path(state["projectRoot"])
for name, ref in state["references"].items():
    actual = subprocess.check_output(
        [
            "git",
            "-c",
            f"safe.directory=/opt/references/{name}",
            "-C",
            f"/opt/references/{name}",
            "rev-parse",
            "HEAD",
        ],
        text=True,
    ).strip()
    assert actual == ref["commit"], name
config = json.loads((Path.home() / ".config/opencode/opencode.json").read_text())
assert config["model"].startswith("mercor/")
assert "/opt/mercor-tools/arize-v2.ts" in config["plugins"]
subprocess.run(
    [
        str(repo / ".venv/bin/python"),
        "-m",
        "pytest",
        "-q",
        "-m",
        "not surrealdb and not qualification",
    ],
    cwd=repo,
    check=True,
    timeout=180,
)
receipt = Path.home() / ".arize/harness/logs/export-receipts.jsonl"
provider = config["providers"]["mercor"]
model_ids = list(provider["models"])
smoke_model = next((m for m in model_ids if m.startswith("mid-")), model_ids[0])
key = Path("/work/mercor-work/.devtraffic/key").read_text()
request = urllib.request.Request(
    provider["settings"]["baseURL"] + "/chat/completions",
    data=json.dumps(
        {
            "model": smoke_model,
            "messages": [{"role": "user", "content": "Say OK."}],
            "max_tokens": 16,
        }
    ).encode(),
    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
)
with urllib.request.urlopen(request, timeout=60) as response:
    response.read()
    print("Direct Mercor model request HTTP status:", response.status)
started = time.time()
# A single harmless request tests the real Mercor provider and the real V2 plugin.
try:
    process = subprocess.run(
        [
            "opencode",
            "run",
            "--standalone",
            "--print-logs",
            "--format",
            "json",
            "--model",
            "mercor/" + smoke_model,
            "Respond with exactly OK. Do not use tools.",
        ],
        cwd=repo,
        text=True,
        capture_output=True,
        timeout=90,
    )
except subprocess.TimeoutExpired as error:
    print((error.stdout or b"").decode(errors="replace"))
    print((error.stderr or b"").decode(errors="replace"))
    raise
print(process.stdout)
print(process.stderr)
process.check_returncode()
deadline = time.monotonic() + 40
while time.monotonic() < deadline:
    records = (
        [json.loads(line) for line in receipt.read_text().splitlines()] if receipt.exists() else []
    )
    recent = [r for r in records if r["time"] >= started]
    if any(r["accepted"] for r in recent):
        print("Live OpenCode trace export acknowledged by Arize AX.")
        break
    time.sleep(1)
else:
    raise RuntimeError("No successful Arize AX acknowledgment for the OpenCode smoke session")
print(
    "Non-root execution, isolated filesystem, five"
    " references, unit tests, Mercor request, and "
    "Arize export verified."
)
