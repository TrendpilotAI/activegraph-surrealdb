"""Read-only recovery audit for the expired ActiveGraph Modal VM."""

import asyncio
import json
import os
import shutil
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import modal

os.umask(0o077)
OUT = Path(__file__).resolve().parent / ".local/session-exports/activegraph-recovery"
OUT.mkdir(parents=True, exist_ok=True)
OLD = "sb-01M4RP3HQMPFH5Y3NAZX8EE0JR"
report = {"expiredSandbox": OLD, "checkedAt": datetime.now(UTC).isoformat()}


async def check(name, operation):
    try:
        value = await asyncio.wait_for(operation(), timeout=45)
        report[name] = {"ok": True, "result": value}
    except Exception as exc:
        report[name] = {"ok": False, "errorType": type(exc).__name__, "error": str(exc)}


async def audit():
    sandbox = await modal.Sandbox.from_id.aio(OLD)
    report["exitStatus"] = await sandbox.poll.aio()

    async def logs():
        entries = []
        async for entry in sandbox.logs.fetch.aio(since=datetime(2026, 10, 12, tzinfo=UTC)):
            row = asdict(entry)
            row["timestamp"] = entry.timestamp.isoformat()
            entries.append(row)
        (OUT / "modal-entrypoint-logs.json").write_text(json.dumps(entries, indent=2))
        (OUT / "modal-entrypoint.log").write_text("".join(r["message"] for r in entries))
        return {"entries": len(entries)}

    async def snapshot():
        image = await sandbox._experimental_get_exit_snapshot.aio(timeout=10)
        return {"imageId": image.object_id}

    async def directory():
        files = await sandbox.filesystem.list_files.aio("/home/mercor/.local/share/opencode")
        return [str(f) for f in files]

    async def database():
        content = await sandbox.filesystem.read_bytes.aio(
            "/home/mercor/.local/share/opencode/opencode.db"
        )
        (OUT / "recovered-expired-opencode.db").write_bytes(content)
        return {"bytes": len(content)}

    await asyncio.gather(
        check("entrypointLogs", logs),
        check("exitSnapshot", snapshot),
        check("expiredHomeDirectory", directory),
        check("expiredOpenCodeDatabase", database),
    )
    names = [
        "mercor-ag-6980ab2f8f2f64a9249a",
        "mercor-ag-7ffb4859d17e9d8352a7",
        "mercor-ag-4bd308eb1f87fd41e525",
        "mercor-ag-8afffb57a06b7627cd3b",
        "mercor-ag-a051b7588978c0db106a",
    ]
    report["volumeRoots"] = {}
    for name in names:
        try:
            volume = modal.Volume.from_name(name, environment_name="Development")
            roots = await volume.listdir.aio("/")
            report["volumeRoots"][name] = [
                {"path": r.path, "type": str(r.type), "size": r.size} for r in roots
            ]
        except Exception as exc:
            report["volumeRoots"][name] = {"error": str(exc)}


asyncio.run(audit())

# Recover only ActiveGraph records from local Orca stores; leave originals unchanged.
app = Path.home() / "Library/Application Support/Orca"
journal = sqlite3.connect("file:" + str(app / "agent-session-journal.db") + "?mode=ro", uri=True)
journal.row_factory = sqlite3.Row
records = [
    dict(r)
    for r in journal.execute(
        "select * from agent_session_records where low"
        "er(record_json) like '%activegraph%' or recor"
        "d_json like '%14b15061-0663-44d3-b2f1-40cab8e"
        "f39c6%'"
    )
]
sessions = [
    dict(r)
    for r in journal.execute(
        "select * from journal_sessions where lower(wo"
        "rkspace_id) like '%activegraph%' or workspace"
        "_id like '%14b15061-0663-44d3-b2f1-40cab8ef39"
        "c6%'"
    )
]
ids = {r["session_id"] for r in records + sessions}
rows = [
    dict(r)
    for sid in ids
    for r in journal.execute("select * from journal_rows where session_id=? order by seq", (sid,))
]
(OUT / "orca-desktop-journal.json").write_text(
    json.dumps({"records": records, "sessions": sessions, "rows": rows}, indent=2)
)
report["desktopJournal"] = {"records": len(records), "sessions": len(sessions), "rows": len(rows)}
index = sqlite3.connect(
    "file:" + str(app / "ai-vault/session-search.sqlite") + "?mode=ro", uri=True
)
index.row_factory = sqlite3.Row
indexed = [
    dict(r)
    for r in index.execute(
        "select * from sessions where agent like 'open"
        "code%' and (lower(cwd) like '%activegraph%' o"
        "r lower(file_path) like '%activegraph%')"
    )
]
(OUT / "orca-desktop-index.json").write_text(json.dumps(indexed, indent=2))
report["desktopIndexedOpenCodeSessions"] = len(indexed)
history = app / "terminal-history"
cached = []
for directory in history.iterdir():
    if not directory.is_dir():
        continue
    meta = directory / "meta.json"
    text = directory.name + (meta.read_text() if meta.exists() else "")
    if any(
        t in text.lower()
        for t in [
            "activegraph",
            "14b15061-0663-44d3-b2f1-40cab8ef39c6",
            "arize-ax-telemetry",
            "graph-db-upgrades",
        ]
    ):
        destination = OUT / "desktop-terminal-history" / directory.name
        shutil.copytree(directory, destination, dirs_exist_ok=True)
        cached.append(
            {
                "directory": directory.name,
                "outputBytes": (directory / "output.log").stat().st_size
                if (directory / "output.log").exists()
                else 0,
            }
        )
report["desktopTerminalCaches"] = cached
(OUT / "recovery-report.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
