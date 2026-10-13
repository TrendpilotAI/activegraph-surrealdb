"""Run inside a VM: checkpoint SQLite consistently and stream history changes."""

import argparse
import base64
import datetime
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

HOME = Path(os.environ.get("MERCOR_BACKUP_HOME", "/home/mercor"))
DATA = HOME / ".local/share/opencode"
BACKUPS = Path(os.environ.get("MERCOR_BACKUP_ROOT", "/work/.mercor-backups"))
STAGING = Path(os.environ.get("MERCOR_CHECKPOINT_STAGING", "/tmp/mercor-session-checkpoint"))


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temp.replace(path)


def records(database):
    database.row_factory = sqlite3.Row
    tables = {r[0] for r in database.execute("select name from sqlite_master where type='table'")}
    if "session_v2" not in tables:
        return []
    output = []
    # One read transaction produces a consistent set of session/message/event rows.
    database.execute("BEGIN")
    try:
        sessions = [
            dict(r) for r in database.execute("select * from session_v2 order by time_created")
        ]
        ids = {s["id"] for s in sessions}
        for row in sessions:
            output.append({"kind": "session", "key": row["id"], "sessionId": row["id"], "row": row})
        for table, kind, key in [
            ("session_message", "message", "session_id"),
            ("event", "event", "aggregate_id"),
        ]:
            if table not in tables:
                continue
            for result in database.execute("select * from " + table):
                row = dict(result)
                if row[key] in ids:
                    if isinstance(row.get("data"), str):
                        row["data"] = json.loads(row["data"])
                    output.append(
                        {"kind": kind, "key": row["id"], "sessionId": row[key], "row": row}
                    )
    finally:
        database.rollback()
    return output


def markdown(session, messages):
    lines = [
        "# " + (session.get("title") or session["id"]),
        "",
        "Session: " + session["id"],
        "",
        "Directory: " + session.get("directory", ""),
        "",
    ]
    for message in sorted(messages, key=lambda m: (m.get("seq", 0), m.get("time_created", 0))):
        data = message["data"]
        lines += ["## " + message["type"] + " — " + message["id"], ""]
        if data.get("text"):
            lines += [data["text"], ""]
        for part in data.get("content", []):
            lines += (
                [part["text"], ""]
                if part.get("text")
                else ["```json", json.dumps(part, ensure_ascii=False, indent=2), "```", ""]
            )
        if data.get("files"):
            lines += ["```json", json.dumps(data["files"], ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(lines)


def write_transcripts(root, items):
    sessions = {r["key"]: r["row"] for r in items if r["kind"] == "session"}
    counts = {}
    for sid, session in sessions.items():
        if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}", sid):
            raise ValueError("Unsafe session identifier")
        messages = [r["row"] for r in items if r["kind"] == "message" and r["sessionId"] == sid]
        events = [r["row"] for r in items if r["kind"] == "event" and r["sessionId"] == sid]
        directory = root / "sessions" / sid
        atomic(
            directory / "transcript.json",
            {"session": session, "messages": messages, "events": events},
        )
        temp = directory / "transcript.md.tmp"
        temp.write_text(markdown(session, messages))
        temp.replace(directory / "transcript.md")
        counts[sid] = len(messages)
    return counts


def checkpoint(*, include_repositories=True):
    BACKUPS.mkdir(parents=True, exist_ok=True)
    with (BACKUPS / "checkpoint.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return checkpoint_locked(include_repositories=include_repositories)


def checkpoint_locked(*, include_repositories):
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%S%fZ")
    with tempfile.TemporaryDirectory(prefix="mercor-checkpoint-") as temp:
        root = Path(temp)
        db_path = DATA / "opencode.db"
        counts = {}
        if db_path.exists():
            source = sqlite3.connect("file:" + str(db_path) + "?mode=ro", uri=True, timeout=15)
            target = sqlite3.connect(root / "opencode-history.db")
            source.backup(target)
            source.close()
            for table in ["account_state", "account", "control_account", "credential", "kv"]:
                target.execute('DROP TABLE IF EXISTS "' + table + '"')
            target.commit()
            target.execute("VACUUM")
            if target.execute("pragma integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Checkpoint SQLite integrity check failed")
            counts = write_transcripts(root, records(target))
            target.close()
        for name, path in [
            ("logs", DATA / "log"),
            ("shell-output", DATA / "shell"),
            ("state", HOME / ".local/state/opencode"),
            ("orca-terminal-history", HOME / ".orca/terminal-history"),
        ]:
            if path.exists():
                shutil.copytree(path, root / name)
        state_path = Path("/opt/mercor-tools/modal-state.json")
        repositories = []
        warnings = []
        if include_repositories and state_path.exists():
            state = json.loads(state_path.read_text())
            paths = sorted({state["projectRoot"], *[p["path"] for p in state.get("projects", [])]})
            for path in paths:
                project = root / "repositories" / Path(path).name
                project.mkdir(parents=True)
                refs = subprocess.check_output(
                    [
                        "git",
                        "-C",
                        path,
                        "for-each-ref",
                        "--format=%(refname)",
                        "refs/heads",
                        "refs/tags",
                    ],
                    text=True,
                ).splitlines()
                refs = [ref for ref in refs if not ref.startswith("refs/heads/__dolt_")]
                bundle = project / "all-branches.bundle"
                try:
                    subprocess.run(
                        [
                            "git",
                            "-C",
                            path,
                            "-c",
                            "pack.threads=1",
                            "-c",
                            "pack.compression=1",
                            "bundle",
                            "create",
                            str(bundle),
                            *refs,
                        ],
                        check=True,
                        capture_output=True,
                        timeout=20,
                    )
                except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
                    bundle.unlink(missing_ok=True)
                    warnings.append(
                        {"repository": path, "step": "git-bundle", "error": type(exc).__name__}
                    )
                inventory = subprocess.check_output(
                    ["git", "-C", path, "worktree", "list", "--porcelain"], text=True
                )
                (project / "git-worktrees.txt").write_text(inventory)
                worktrees = [
                    line.split(" ", 1)[1]
                    for line in inventory.splitlines()
                    if line.startswith("worktree ")
                ]
                for number, worktree in enumerate(worktrees):
                    if not Path(worktree).exists():
                        continue
                    destination = project / "worktrees" / (str(number) + "-" + Path(worktree).name)
                    shutil.copytree(
                        worktree,
                        destination,
                        symlinks=True,
                        ignore=shutil.ignore_patterns(
                            ".git",
                            ".venv",
                            "node_modules",
                            ".devtraffic",
                            ".env",
                            ".env.*",
                            "opencode.json",
                            "__pycache__",
                            ".pytest_cache",
                            ".mypy_cache",
                            ".ruff_cache",
                            "dist",
                            ".next",
                            "git-remote-cache",
                        ),
                    )
                repositories.append({"root": path, "worktrees": worktrees})
        atomic(
            root / "manifest.json",
            {
                "capturedAt": stamp,
                "sessions": counts,
                "repositories": repositories,
                "warnings": warnings,
                "sourceFileConsistency": (
                    "Files copied while agents run; SQLite uses a consistent online backup."
                ),
            },
        )
        archive_dir = BACKUPS / "checkpoints"
        archive_dir.mkdir(exist_ok=True)
        archive = archive_dir / (stamp + ".tar.gz")
        pending = archive.with_suffix(".pending")
        with tarfile.open(pending, "w:gz") as output:
            for path in root.iterdir():
                output.add(path, arcname=path.name)
        pending.replace(archive)
        # Copy outside the mounted Volume so filesystem/exit snapshots contain
        # a consistent history checkpoint even though mounts are not captured.
        STAGING.mkdir(parents=True, exist_ok=True)
        pending = STAGING / "checkpoint.tar.gz.pending"
        shutil.copyfile(archive, pending)
        pending.replace(STAGING / "checkpoint.tar.gz")
        result = {
            "capturedAt": stamp,
            "sessions": counts,
            "archive": str(archive),
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        }
        atomic(BACKUPS / "latest.json", result)
        atomic(STAGING / "latest.json", result)
        for old in sorted(archive_dir.glob("*.tar.gz"))[:-288]:
            old.unlink()
        os.sync()
        return result


def stream(interval):
    hashes = {}
    cursors = json.loads(os.environ.get("MERCOR_LOG_CURSORS", "{}"))
    while True:
        changes = []
        db_path = DATA / "opencode.db"
        if db_path.exists():
            database = sqlite3.connect("file:" + str(db_path) + "?mode=ro", uri=True, timeout=10)
            try:
                for record in records(database):
                    digest = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
                    key = record["kind"] + ":" + record["key"]
                    if hashes.get(key) != digest:
                        hashes[key] = digest
                        changes.append(record)
            finally:
                database.close()
        logs = []
        for prefix, directory in [
            ("opencode-log", DATA / "log"),
            ("shell-output", DATA / "shell"),
            ("opencode-state", HOME / ".local/state/opencode"),
            ("orca-terminal-history", HOME / ".orca/terminal-history"),
        ]:
            if not directory.exists():
                continue
            for path in directory.rglob("*"):
                if not path.is_file():
                    continue
                key = prefix + "/" + str(path.relative_to(directory))
                offset = cursors.get(key, 0)
                size = path.stat().st_size
                if size < offset:
                    offset = 0
                with path.open("rb") as source:
                    source.seek(offset)
                    content = source.read(65536)
                if content:
                    logs.append(
                        {
                            "path": key,
                            "offset": offset,
                            "base64": base64.b64encode(content).decode(),
                        }
                    )
                    cursors[key] = offset + len(content)
        print(
            json.dumps(
                {
                    "capturedAt": datetime.datetime.now(datetime.UTC).isoformat(),
                    "records": changes,
                    "logs": logs,
                }
            ),
            flush=True,
        )
        time.sleep(interval)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["checkpoint", "watch", "stream"])
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--sessions-only", action="store_true")
    args = parser.parse_args()
    if args.mode == "stream":
        stream(args.interval)
    elif args.mode == "checkpoint":
        print(json.dumps(checkpoint(include_repositories=not args.sessions_only)))
    else:
        BACKUPS.mkdir(parents=True, exist_ok=True)
        lock = (BACKUPS / "watch.lock").open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        while True:
            try:
                atomic(
                    BACKUPS / "watch-status.json",
                    {
                        "ok": True,
                        "checkpoint": checkpoint(include_repositories=not args.sessions_only),
                    },
                )
            except Exception as exc:
                atomic(
                    BACKUPS / "watch-status.json",
                    {"ok": False, "error": str(exc), "time": time.time()},
                )
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
