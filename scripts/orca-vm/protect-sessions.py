"""Mac-side receiver for streaming transcripts and periodic Modal snapshots."""

import argparse
import base64
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

os.environ.setdefault("MODAL_PROFILE", "mercor-work")
import modal

HERE = Path(__file__).resolve().parent
ROOT = HERE / ".local/session-backups"
REGISTRY = HERE / ".local/protected-vms.json"
spec = importlib.util.spec_from_file_location("backup_agent", HERE / "session-backup-agent.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False))
    temp.replace(path)


def register(name, sandbox_id, volume_name, exit_enabled=False):
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    lock = REGISTRY.with_suffix(".lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX)
    data = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {}
    data[volume_name] = {
        "name": name,
        "sandboxId": sandbox_id,
        "volumeName": volume_name,
        "exitSnapshotEnabled": exit_enabled,
    }
    atomic(REGISTRY, data)
    hosts = modal.Dict.from_name(
        "mercor-session-protected-vms", create_if_missing=True, environment_name="Development"
    )
    hosts[volume_name] = data[volume_name]


def refresh_networks():
    domains = set()
    for path in [HERE / "modal-state.json", HERE / ".local/contactkiller-state.json"]:
        if path.exists():
            domains.update(json.loads(path.read_text())["outboundDomains"])
    hosts = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {}
    live = []
    for host in hosts.values():
        sandbox = modal.Sandbox.from_id(host["sandboxId"])
        if sandbox.poll() is not None:
            continue
        domains.add(urlparse(sandbox.tunnels()[7331].url).hostname)
        live.append((host, sandbox))
    result = []
    for host, sandbox in live:
        sandbox._experimental_set_outbound_network_policy(outbound_domain_allowlist=sorted(domains))
        result.append(
            {
                "name": host["name"],
                "sandboxId": sandbox.object_id,
                "allowedDomains": sorted(domains),
            }
        )
    return result


def install_remote(sandbox):
    sandbox.filesystem.write_text(
        (HERE / "session-backup-agent.py").read_text(), "/tmp/mercor-session-backup-agent.py"
    )
    process = sandbox.exec(
        "bash",
        "-c",
        (
            "install -o mercor -g mercor -m 700 /tmp/merco"
            "r-session-backup-agent.py /home/mercor/sessio"
            "n-backup-agent.py; python3 -c 'import os,path"
            "lib,signal,time; [(os.kill(int(p.name),signal"
            '.SIGTERM)) for p in pathlib.Path("/proc").ite'
            'rdir() if p.name.isdigit() and (p/"cmdline").'
            'exists() and b"/home/mercor/session-backup-ag'
            'ent.py\\x00" in (p/"cmdline").read_bytes() and'
            ' any(x in (p/"cmdline").read_bytes().split(b"'
            '\\x00") for x in [b"watch",b"stream"])]; time.'
            "sleep(0.25)'; nohup /usr/sbin/runuser -u merc"
            "or -- env HOME=/home/mercor PYTHONPATH= pytho"
            "n3 /home/mercor/session-backup-agent.py watch"
            " --interval 300 --sessions-only >/tmp/mercor-"
            "session-backup-watch.log 2>&1 </dev/null &"
        ),
        timeout=30,
    )
    if process.wait() != 0:
        raise RuntimeError(process.stderr.read())


def checkpoint_remote(sandbox):
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
        "--sessions-only",
        timeout=120,
    )
    output = process.stdout.read()
    errors = process.stderr.read()
    if process.wait() != 0:
        raise RuntimeError(errors)
    return json.loads(output)


def receive_frame(root, frame):
    for record in frame["records"]:
        sid = record["sessionId"]
        if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}", sid):
            raise ValueError("Unsafe session identifier")
    for log in frame["logs"]:
        relative = Path(log["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe log path")
    database = sqlite3.connect(root / "history.sqlite")
    database.execute(
        "create table if not exists records(kind text,"
        "key text,session_id text,row_json text,hash t"
        "ext,primary key(kind,key))"
    )
    changed = []
    for record in frame["records"]:
        payload = json.dumps(record["row"], sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        old = database.execute(
            "select hash from records where kind=? and key=?", (record["kind"], record["key"])
        ).fetchone()
        if old and old[0] == digest:
            continue
        database.execute(
            "insert or replace into records values(?,?,?,?,?)",
            (record["kind"], record["key"], record["sessionId"], payload, digest),
        )
        changed.append(record)
    database.commit()
    cursors_path = root / "log-cursors.json"
    cursors = json.loads(cursors_path.read_text()) if cursors_path.exists() else {}
    for log in frame["logs"]:
        relative = Path(log["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe log path")
        target = root / "logs" / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        content = base64.b64decode(log["base64"], validate=True)
        with target.open("r+b" if target.exists() else "w+b") as output:
            if log["offset"] == 0:
                output.truncate(0)
            output.seek(log["offset"])
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        cursors[log["path"]] = log["offset"] + len(content)
    atomic(cursors_path, cursors)
    if changed:
        with (root / "changes.jsonl").open("a") as journal:
            for record in changed:
                journal.write(
                    json.dumps({"capturedAt": frame["capturedAt"], **record}, ensure_ascii=False)
                    + "\n"
                )
            journal.flush()
            os.fsync(journal.fileno())
    items = [
        {"kind": kind, "key": key, "sessionId": sid, "row": json.loads(payload)}
        for kind, key, sid, payload in database.execute(
            "select kind,key,session_id,row_json from records"
        )
    ]
    counts = (
        agent.write_transcripts(root, items)
        if changed
        else {
            row["sessionId"]: sum(
                1 for r in items if r["kind"] == "message" and r["sessionId"] == row["sessionId"]
            )
            for row in items
            if row["kind"] == "session"
        }
    )
    database.close()
    atomic(
        root / "stream-status.json",
        {
            "ok": True,
            "lastReceivedAt": frame["capturedAt"],
            "sessions": counts,
            "changedRecords": len(changed),
            "logChunks": len(frame["logs"]),
        },
    )


def stream_host(host, stop):
    root = ROOT / host["name"] / host["sandboxId"]
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    while not stop.is_set():
        process = None
        try:
            sandbox = modal.Sandbox.from_id(host["sandboxId"])
            if sandbox.poll() is not None:
                atomic(
                    root / "stream-status.json",
                    {"ok": False, "state": "vm-exited", "time": time.time()},
                )
                return
            cursors = root / "log-cursors.json"
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
                "stream",
                "--interval",
                "5",
                timeout=1200,
                env={"MERCOR_LOG_CURSORS": cursors.read_text() if cursors.exists() else "{}"},
            )
            buffer = ""
            for chunk in process.stdout:
                if stop.is_set():
                    return
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if line.strip():
                        receive_frame(root, json.loads(line))
            if process.wait() != 0:
                raise RuntimeError(process.stderr.read())
        except Exception as exc:
            atomic(
                root / "stream-status.json", {"ok": False, "error": str(exc), "time": time.time()}
            )
        stop.wait(5)


def preserve_exit(host, sandbox, root):
    try:
        image = sandbox._experimental_get_exit_snapshot(timeout=30)
        atomic(root / "exit-snapshot.json", {"imageId": image.object_id, "time": time.time()})
    except Exception as exc:
        atomic(root / "exit-snapshot.json", {"error": str(exc), "time": time.time()})
    # Volumes remain readable after the VM has exited, even while the Mac was offline.
    try:
        volume = modal.Volume.from_name(host["volumeName"], environment_name="Development")
        latest = json.loads(b"".join(volume.read_file("/.mercor-backups/latest.json")))
        path = "/" + latest["archive"].removeprefix("/work/")
        destination = root / "remote-checkpoints"
        destination.mkdir(exist_ok=True)
        target = destination / Path(path).name
        with target.open("wb") as output:
            volume.read_file_into_fileobj(path, output)
        if hashlib.sha256(target.read_bytes()).hexdigest() != latest["sha256"]:
            raise RuntimeError("Checkpoint checksum mismatch")
        atomic(root / "recovered-volume-checkpoint.json", latest)
    except Exception as exc:
        atomic(root / "recovered-volume-checkpoint.json", {"error": str(exc)})


def maintain_host(host, stop):
    root = ROOT / host["name"] / host["sandboxId"]
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    next_snapshot = 0
    snapshot_status = root / "snapshot-status.json"
    if snapshot_status.exists():
        previous = json.loads(snapshot_status.read_text())
        if previous.get("ok"):
            next_snapshot = datetime.datetime.fromisoformat(previous["createdAt"]).timestamp() + 900
    while not stop.is_set():
        try:
            sandbox = modal.Sandbox.from_id(host["sandboxId"])
            if sandbox.poll() is not None:
                preserve_exit(host, sandbox, root)
                return
            result = checkpoint_remote(sandbox)
            destination = root / "remote-checkpoints"
            destination.mkdir(exist_ok=True)
            archive = destination / Path(result["archive"]).name
            sandbox.filesystem.copy_to_local(result["archive"], str(archive))
            if hashlib.sha256(archive.read_bytes()).hexdigest() != result["sha256"]:
                raise RuntimeError("Downloaded checkpoint checksum mismatch")
            atomic(
                root / "checkpoint-status.json",
                {"ok": True, **result, "localArchive": str(archive)},
            )
            cloud_schedule = HERE / ".local/cloud-backup-schedule.json"
            if cloud_schedule.exists() and json.loads(cloud_schedule.read_text()).get("enabled"):
                index = modal.Dict.from_name(
                    "mercor-session-snapshot-index", environment_name="Development"
                )
                latest = index.get("latest:" + sandbox.object_id)
                if latest:
                    atomic(
                        root / "snapshot-status.json", {"ok": True, "scheduler": "Modal", **latest}
                    )
            elif time.time() >= next_snapshot:
                image = sandbox.snapshot_filesystem(timeout=55, ttl=7 * 24 * 3600)
                record = {
                    "imageId": image.object_id,
                    "sandboxId": host["sandboxId"],
                    "volumeName": host["volumeName"],
                    "createdAt": datetime.datetime.now(datetime.UTC).isoformat(),
                    "retentionDays": 7,
                    "checkpoint": result,
                }
                with (root / "filesystem-snapshots.jsonl").open("a") as output:
                    output.write(json.dumps(record) + "\n")
                    output.flush()
                    os.fsync(output.fileno())
                atomic(root / "snapshot-status.json", {"ok": True, **record})
                next_snapshot = time.time() + 900
        except Exception as exc:
            atomic(root / "maintenance-error.json", {"error": str(exc), "time": time.time()})
        stop.wait(300)


def daemon():
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (ROOT / "receiver.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    jobs = {}
    while True:
        registry = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {}
        wanted = {host["sandboxId"]: host for host in registry.values()}
        for sid in set(jobs) - set(wanted):
            jobs.pop(sid)[0].set()
        for sid, host in wanted.items():
            if sid in jobs:
                continue
            stop = threading.Event()
            threads = [
                threading.Thread(target=fn, args=(host, stop), daemon=True)
                for fn in [stream_host, maintain_host]
            ]
            jobs[sid] = (stop, threads)
            for thread in threads:
                thread.start()
        atomic(
            ROOT / "receiver-status.json",
            {"running": True, "pid": os.getpid(), "time": time.time(), "sandboxes": list(wanted)},
        )
        time.sleep(10)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["install-current", "daemon", "status"])
    args = parser.parse_args()
    if args.mode == "install-current":
        for filename, name in [
            ("connection.json", "activegraph"),
            ("contactkiller-connection.json", "contactkiller"),
        ]:
            host = json.loads((HERE / ".local" / filename).read_text())
            sandbox = modal.Sandbox.from_id(host["sandboxId"])
            if sandbox.poll() is not None:
                raise RuntimeError(name + " VM has expired")
            install_remote(sandbox)
            result = checkpoint_remote(sandbox)
            register(name, host["sandboxId"], host["volumeName"])
            print(json.dumps({"name": name, "checkpoint": result}))
    elif args.mode == "daemon":
        daemon()
    else:
        for file in ROOT.rglob("*status.json"):
            print(str(file.relative_to(ROOT)), file.read_text())


if __name__ == "__main__":
    main()
