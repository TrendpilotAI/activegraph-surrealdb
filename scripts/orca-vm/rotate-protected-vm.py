"""Checkpoint, then replace one VM on its existing persistent Volume."""

import datetime
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import modal

os.umask(0o077)
here = Path(__file__).resolve().parent
name = sys.argv[1]
if name not in ["activegraph", "contactkiller"]:
    raise ValueError("Unknown VM")
connection_file = "connection.json" if name == "activegraph" else "contactkiller-connection.json"
vm_file = "manual-vm.json" if name == "activegraph" else "contactkiller-vm.json"
state = (
    here / "modal-state.json" if name == "activegraph" else here / ".local/contactkiller-state.json"
)
connection = json.loads((here / ".local" / connection_file).read_text())
old = modal.Sandbox.from_id(connection["sandboxId"])
spec = importlib.util.spec_from_file_location("protection", here / "protect-sessions.py")
protection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(protection)
if old.poll() is None:
    protection.install_remote(old)
    checkpoint = protection.checkpoint_remote(old)
    dest = (
        protection.ROOT
        / name
        / old.object_id
        / "remote-checkpoints"
        / Path(checkpoint["archive"]).name
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    old.filesystem.copy_to_local(checkpoint["archive"], str(dest))
    import hashlib

    if hashlib.sha256(dest.read_bytes()).hexdigest() != checkpoint["sha256"]:
        raise RuntimeError("Final checkpoint checksum mismatch")
    sync = old.exec("sync", "/work", timeout=30)
    if sync.wait() != 0:
        raise RuntimeError("Volume sync failed")
    old.terminate(wait=True)
    protection.preserve_exit(connection, old, dest.parent.parent)
stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
shutil.copy2(here / ".local" / vm_file, here / ".local" / (name + "-before-" + stamp + ".json"))
env = {
    **os.environ,
    "MERCOR_STATE_FILE": str(state),
    "MERCOR_MANUAL_VOLUME": connection["volumeName"],
    "MODAL_PROFILE": "mercor-work",
}
result = subprocess.run(
    [sys.executable, str(here / "modal-env.py"), "create"],
    env=env,
    text=True,
    capture_output=True,
    timeout=1200,
)
(here / ".local" / (name + "-rotation.log")).write_text(result.stderr)
if result.returncode != 0:
    raise RuntimeError(name + " replacement failed; details in protected rotation log")
vm = json.loads(result.stdout)
if not vm.get("pairingCode") or not vm["userData"].get("exitSnapshotEnabled"):
    raise RuntimeError("Unexpected replacement result")
(here / ".local" / vm_file).write_text(json.dumps(vm, indent=2))
connection["sandboxId"] = vm["userData"]["resourceId"]
(here / ".local" / connection_file).write_text(json.dumps(connection, indent=2))
sandbox = modal.Sandbox.from_id(connection["sandboxId"])
try:
    sandbox._experimental_get_exit_snapshot(timeout=0)
    exit_status = "snapshot already available"
except modal.exception.TimeoutError:
    exit_status = "enabled; pending VM exit"
except modal.exception.InvalidError as exc:
    raise RuntimeError("Exit snapshot verification failed: " + str(exc)) from exc
print(
    json.dumps(
        {
            "name": name,
            "sandboxId": connection["sandboxId"],
            "volumeName": connection["volumeName"],
            "exitSnapshot": exit_status,
            "timeoutSeconds": 86400,
        }
    )
)
