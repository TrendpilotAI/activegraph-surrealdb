"""Trusted Modal scheduler; no management credentials are placed in work VMs."""

import datetime
import json

import modal

app = modal.App("mercor-session-backup-scheduler")
hosts = modal.Dict.from_name(
    "mercor-session-protected-vms", create_if_missing=True, environment_name="Development"
)
index = modal.Dict.from_name(
    "mercor-session-snapshot-index", create_if_missing=True, environment_name="Development"
)


@app.function(schedule=modal.Period(minutes=15), timeout=600, cpu=0.25, memory=256)
def checkpoint_vms():
    results = []
    for _, host in hosts.items():
        try:
            sandbox = modal.Sandbox.from_id(host["sandboxId"])
            if sandbox.poll() is not None:
                if host.get("exitSnapshotEnabled"):
                    image = sandbox._experimental_get_exit_snapshot(timeout=30)
                    index["exit:" + sandbox.object_id] = {"imageId": image.object_id, **host}
                results.append({"sandboxId": sandbox.object_id, "state": "exited"})
                continue
            process = sandbox.exec(
                "/usr/sbin/runuser",
                "-u",
                "mercor",
                "--",
                "env",
                "HOME=/home/mercor",
                "PYTHONPATH=",
                "python3",
                "/home/mercor/session-backup-agent.py",
                "checkpoint",
                timeout=120,
            )
            output = process.stdout.read()
            if process.wait() != 0:
                raise RuntimeError("Checkpoint worker failed: " + process.stderr.read()[-3000:])
            checkpoint = json.loads(output)
            image = sandbox.snapshot_filesystem(timeout=55, ttl=7 * 24 * 3600)
            result = {
                **host,
                "imageId": image.object_id,
                "checkpoint": checkpoint,
                "createdAt": datetime.datetime.now(datetime.UTC).isoformat(),
                "retentionDays": 7,
            }
            index["latest:" + sandbox.object_id] = result
            history = index.get("history:" + sandbox.object_id, [])
            index["history:" + sandbox.object_id] = (history + [result])[-672:]
            results.append(
                {"sandboxId": sandbox.object_id, "imageId": image.object_id, "state": "snapshotted"}
            )
        except Exception as exc:
            error = {
                "sandboxId": host["sandboxId"],
                "error": str(exc),
                "time": datetime.datetime.now(datetime.UTC).isoformat(),
            }
            index["error:" + host["sandboxId"]] = error
            results.append(error)
    return results


@app.local_entrypoint()
def main():
    print(json.dumps(checkpoint_vms.remote(), indent=2))
