"""Preserve original Orca environment IDs while retaining current endpoints."""

import argparse
import datetime
import hashlib
import json
import os
import time
from pathlib import Path

IDENTITIES = {
    "mercor-modal-vm": ("13b18308-ff44-44a6-ac81-c0b0f482cc37", "connection.json"),
    "mercor-contactkiller-vm": (
        "b8e818ad-25d2-4bc6-9af2-fd47d15ff6f8",
        "contactkiller-connection.json",
    ),
}


def stabilize(apply=False):
    os.umask(0o077)
    local = Path(__file__).resolve().parent / ".local"
    source = Path.home() / "Library/Application Support/Orca/orca-environments.json"
    original = source.read_bytes()
    data = json.loads(original)
    changes = []
    for row in data["environments"]:
        if row["name"] not in IDENTITIES:
            continue
        stable, filename = IDENTITIES[row["name"]]
        if row["id"] == stable:
            continue
        if any(other["id"] == stable for other in data["environments"]):
            raise RuntimeError("Stable environment ID already belongs to another connection")
        changes.append({"name": row["name"], "previousId": row["id"], "stableId": stable})
        row["id"] = stable
        row["updatedAt"] = int(time.time() * 1000)
    if changes:
        stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
        (local / ("orca-environments-before-stable-" + stamp + ".json")).write_bytes(original)
        prepared = local / "orca-environments-stable.json"
        prepared.write_text(json.dumps(data, indent=2) + "\n")
        if apply:
            if hashlib.sha256(source.read_bytes()).digest() != hashlib.sha256(original).digest():
                raise RuntimeError("Orca connection store changed during preparation; retry")
            temporary = source.with_name(source.name + ".mercor-repair.tmp")
            temporary.write_bytes(prepared.read_bytes())
            temporary.chmod(0o600)
            temporary.replace(source)
    if apply:
        for stable, filename in IDENTITIES.values():
            path = local / filename
            if path.exists():
                connection = json.loads(path.read_text())
                connection["environmentId"] = stable
                path.write_text(json.dumps(connection, indent=2))
    return {"applied": apply, "changes": changes}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    print(json.dumps(stabilize(parser.parse_args().apply), indent=2))
