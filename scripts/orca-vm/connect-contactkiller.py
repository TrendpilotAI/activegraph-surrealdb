"""Pair the shared ContactKiller VM and register both remote repositories."""

import json
import subprocess
from pathlib import Path

local = Path(__file__).resolve().parent / ".local"
vm = json.loads((local / "contactkiller-vm.json").read_text())


def call(*args):
    result = subprocess.run(["orca", *args, "--json"], capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    data = json.loads(result.stdout)
    if data.get("ok") is False:
        raise RuntimeError(json.dumps(data.get("error")))
    return data


existing = next(
    (
        e
        for e in call("environment", "list")["result"]["environments"]
        if e["name"] == "mercor-contactkiller-vm"
    ),
    None,
)
if existing:
    expected = json.loads((local / "contactkiller-connection.json").read_text())["environmentId"]
    if existing["id"] != expected:
        raise RuntimeError("Unexpected ContactKiller pairing; refusing replacement")
    call("environment", "rm", "--environment", existing["id"])
call("environment", "add", "--name", "mercor-contactkiller-vm", "--pairing-code", vm["pairingCode"])
subprocess.run(
    ["python3", str(Path(__file__).with_name("stabilize-environments.py")), "--apply"],
    check=True,
    capture_output=True,
)
env = next(
    e
    for e in call("environment", "list")["result"]["environments"]
    if e["name"] == "mercor-contactkiller-vm"
)
repos = []
for name in ["ContactKiller-OSS", "ContactKiller"]:
    result = call("repo", "add", "--environment", env["id"], "--path", "/work/mercor-work/" + name)
    repo = result["result"].get("repo", result["result"])
    repos.append({"name": name, "repoId": repo["id"], "path": repo["path"]})
connection = {
    "environmentId": env["id"],
    "environmentName": env["name"],
    "sandboxId": vm["userData"]["resourceId"],
    "volumeName": vm["userData"]["volumeName"],
    "repos": repos,
}
(local / "contactkiller-connection.json").write_text(json.dumps(connection, indent=2))
print(json.dumps(connection))
