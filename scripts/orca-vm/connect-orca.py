"""Pair the retained VM and register its project without printing pairing credentials."""

import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[2]
local = Path(__file__).resolve().parent / ".local"
local.chmod(0o700)
vm_path = local / "manual-vm.json"
vm_path.chmod(0o600)
vm = json.loads(vm_path.read_text())


def call(*args):
    result = subprocess.run(["orca", *args, "--json"], text=True, capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    parsed = json.loads(result.stdout)
    if parsed.get("ok") is False:
        raise RuntimeError(json.dumps(parsed.get("error")))
    return parsed


existing = next(
    (
        e
        for e in call("environment", "list")["result"]["environments"]
        if e["name"] == "mercor-modal-vm"
    ),
    None,
)
if existing:
    expected = json.loads((local / "connection.json").read_text())["environmentId"]
    if existing["id"] != expected:
        raise RuntimeError("Unexpected ActiveGraph pairing; refusing replacement")
    call("environment", "rm", "--environment", existing["id"])
pair = call("environment", "add", "--name", "mercor-modal-vm", "--pairing-code", vm["pairingCode"])
subprocess.run(
    ["python3", str(Path(__file__).with_name("stabilize-environments.py")), "--apply"],
    check=True,
    capture_output=True,
)
pair_path = local / "orca-pair-result.json"
pair_path.write_text(json.dumps(pair))
pair_path.chmod(0o600)
environments = call("environment", "list")["result"]["environments"]
environment = next(e for e in environments if e["name"] == "mercor-modal-vm")
environment_id = environment["id"]
call("repo", "add", "--path", str(root))
setup = call(
    "project",
    "setup-existing-folder",
    "--project",
    "github:trendpilotai/activegraph-surrealdb",
    "--host",
    "runtime:" + environment_id,
    "--path",
    vm["projectRoot"],
    "--kind",
    "git",
    "--display-name",
    "ActiveGraph SurrealDB — Modal VM",
)
connection = {
    "environmentId": environment_id,
    "environmentName": environment["name"],
    "sandboxId": vm["userData"]["resourceId"],
    "volumeName": vm["userData"]["volumeName"],
    "projectId": "github:trendpilotai/activegraph-surrealdb",
    "projectRoot": vm["projectRoot"],
}
(local / "connection.json").write_text(json.dumps(connection, indent=2))
print(json.dumps({"connection": connection, "setup": setup}))
