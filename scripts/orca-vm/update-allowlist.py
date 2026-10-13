"""Apply explicit observed domains and current Orca peer tunnel addresses."""

import datetime
import importlib.util
import json
import re
from pathlib import Path

import modal

here = Path(__file__).resolve().parent
paths = [here / "modal-state.json", here / ".local/contactkiller-state.json"]
observed = set()
saved = here / ".local/session-exports/activegraph-recovery/modal-entrypoint-logs.json"
if saved.exists():
    for row in json.loads(saved.read_text()):
        observed.update(
            re.findall(r"blocking all outbound connections to ([a-zA-Z0-9.-]+)", row["message"])
        )
hosts = json.loads((here / ".local/protected-vms.json").read_text())
for host in hosts.values():
    sandbox = modal.Sandbox.from_id(host["sandboxId"])
    for entry in sandbox.logs.fetch(since=datetime.datetime(2026, 10, 12, tzinfo=datetime.UTC)):
        observed.update(
            re.findall(r"blocking all outbound connections to ([a-zA-Z0-9.-]+)", entry.message)
        )
# VM tunnel hosts are ephemeral; resolve only the current peers below.
observed = {d for d in observed if not d.endswith(".modal.host")}
for path in paths:
    state = json.loads(path.read_text())
    state["outboundDomains"] = sorted(set(state["outboundDomains"]) | observed)
    path.write_text(json.dumps(state, indent=2) + "\n")
spec = importlib.util.spec_from_file_location("protection", here / "protect-sessions.py")
protection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(protection)
result = protection.refresh_networks()
(here / ".local/allowlist-update.json").write_text(
    json.dumps({"addedObservedDomains": sorted(observed), "vms": result}, indent=2)
)
print(json.dumps({"addedObservedDomains": sorted(observed), "vms": result}, indent=2))
