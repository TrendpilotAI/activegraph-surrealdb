"""Refresh both current pairings without removing their stable environment IDs."""

import datetime
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

os.umask(0o077)
here = Path(__file__).resolve().parent
source = Path.home() / "Library/Application Support/Orca/orca-environments.json"
targets = [
    ("mercor-modal-vm", "manual-vm.json", "13b18308-ff44-44a6-ac81-c0b0f482cc37"),
    ("mercor-contactkiller-vm", "contactkiller-vm.json", "b8e818ad-25d2-4bc6-9af2-fd47d15ff6f8"),
]
results = []
for name, filename, stable in targets:
    vm = json.loads((here / ".local" / filename).read_text())

    def call(*args, pairing_code=vm["pairingCode"]):
        p = subprocess.run(["orca", *args, "--json"], capture_output=True, text=True, timeout=60)
        data = json.loads(p.stdout)
        if not data.get("ok"):
            raise RuntimeError(str(data.get("error")).replace(pairing_code, "[REDACTED]"))
        return data

    temporary_name = name + "-refresh-" + uuid.uuid4().hex[:8]
    call("environment", "add", "--name", temporary_name, "--pairing-code", vm["pairingCode"])
    original = source.read_bytes()
    store = json.loads(original)
    current = next(e for e in store["environments"] if e["name"] == name)
    fresh = dict(next(e for e in store["environments"] if e["name"] == temporary_name))
    fresh.update(
        id=stable, name=name, createdAt=current["createdAt"], updatedAt=int(time.time() * 1000)
    )
    store["environments"] = [
        e for e in store["environments"] if e["name"] not in [temporary_name, name]
    ] + [fresh]
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%S%fZ")
    (here / ".local" / ("orca-environments-before-reconnect-" + stamp + ".json")).write_bytes(
        original
    )
    pending = source.with_name(source.name + ".reconnect.tmp")
    pending.write_text(json.dumps(store, indent=2) + "\n")
    pending.chmod(0o600)
    pending.replace(source)
    status = call("status", "--environment", stable)["result"]["runtime"]
    inventory = call("terminal", "list", "--environment", stable)["result"]["terminals"]
    results.append(
        {
            "name": name,
            "environmentId": stable,
            "sandboxId": vm["userData"]["resourceId"],
            "runtimeState": status["state"],
            "connectionState": status["connectionState"],
            "sessions": [
                {
                    "handle": t["handle"],
                    "branch": t.get("branch"),
                    "connected": t.get("connected"),
                    "agent": t.get("agentIdentity"),
                }
                for t in inventory
            ],
        }
    )
(here / ".local/reconnected-vms.json").write_text(json.dumps(results, indent=2))
print(json.dumps(results, indent=2))
