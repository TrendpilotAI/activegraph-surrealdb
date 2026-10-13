"""Export Modal OpenCode history without printing transcript or credential content."""

import base64
import datetime
import hashlib
import io
import json
import os
import shutil
import tarfile
from pathlib import Path
from urllib.parse import unquote

import modal

REMOTE = r"""

import base64, io, json, os, pathlib, shutil, sqlite3, tarfile, tempfile, datetime

os.umask(0o077)
root = pathlib.Path(tempfile.mkdtemp(prefix="mercor-session-export-"))
home = pathlib.Path("/home/mercor")
data = home / ".local/share/opencode"
sources = [p for p in data.rglob("opencode.db") if p.is_file()] if data.exists() else []
sessions = []
for index, source in enumerate(sources):
    folder = root / ("database-" + str(index))
    folder.mkdir()
    db = folder / "opencode-history.db"
    src = sqlite3.connect("file:" + str(source) + "?mode=ro", uri=True)
    dst = sqlite3.connect(db)
    src.backup(dst)
    src.close()
    # Remove provider/account credential stores from the exported copy only.
    for table in ["account_state", "account", "control_account", "credential", "kv"]:
        dst.execute('DROP TABLE IF EXISTS "' + table + '"')
    dst.commit()
    dst.execute("VACUUM")
    dst.row_factory = sqlite3.Row
    tables = {r[0] for r in dst.execute("select name from sqlite_master where type='table'")}
    if "session_v2" in tables:
        for row in dst.execute("select * from session_v2 order by time_created").fetchall():
            session = dict(row)
            sid = session["id"]
            target = folder / "sessions" / sid
            target.mkdir(parents=True)
            messages = []
            for row in dst.execute(
                "select * from session_message where session_id=? order by seq,time_created", (sid,)
            ):
                message = dict(row)
                message["data"] = json.loads(message["data"])
                messages.append(message)
            payload = {"session": session, "messages": messages}
            (target / "transcript.json").write_text(
                json.dumps(payload, indent=2, ensure_ascii=False)
            )
            md = [
                "# " + (session.get("title") or sid),
                "",
                "Session: " + sid,
                "",
                "Directory: " + session["directory"],
                "",
            ]
            for message in messages:
                d = message["data"]
                md += ["## " + message["type"] + " — " + message["id"], ""]
                if d.get("text"):
                    md += [d["text"], ""]
                for part in d.get("content", []):
                    if part.get("text"):
                        md += [part["text"], ""]
                    else:
                        md += ["```json", json.dumps(part, indent=2, ensure_ascii=False), "```", ""]
                if d.get("files"):
                    md += ["Attachments:", "```json", json.dumps(d["files"], indent=2), "```", ""]
            (target / "transcript.md").write_text("\n".join(md))
            # Preserve supporting records, including tool execution events.
            for table, key in [
                ("event", "aggregate_id"),
                ("session_inbox", "session_id"),
                ("session_pending", "session_id"),
                ("instruction_entry", "session_id"),
                ("instruction_state", "session_id"),
            ]:
                if table in tables:
                    records = [
                        dict(r)
                        for r in dst.execute(
                            "select * from " + table + " where " + key + "=?", (sid,)
                        )
                    ]
                    (target / (table + ".json")).write_text(
                        json.dumps(records, indent=2, ensure_ascii=False)
                    )
            sessions.append(
                {
                    "id": sid,
                    "title": session.get("title"),
                    "directory": session["directory"],
                    "messages": len(messages),
                    "transcript": str((target / "transcript.md").relative_to(root)),
                }
            )
    dst.close()
for name, path in [
    ("logs", data / "log"),
    ("shell-output", data / "shell"),
    ("state", home / ".local/state/opencode"),
]:
    if path.exists():
        shutil.copytree(path, root / name)
history = home / ".orca/terminal-history"
if history.exists():
    shutil.copytree(history, root / "orca-terminal-history")
search = []
for directory, dirs, files in os.walk("/work"):
    dirs[:] = [
        d for d in dirs if d not in ["node_modules", ".venv", ".git", "references", "snapshot"]
    ]
    for name in files:
        if name == "opencode.db" or (
            "transcript" in name.lower() and name.endswith((".json", ".md", ".jsonl", ".txt"))
        ):
            search.append(str(pathlib.Path(directory) / name))
manifest = {
    "capturedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "sessions": sessions,
    "persistentHistoryCandidates": search,
    "databaseCredentialTablesExcluded": [
        "account_state",
        "account",
        "control_account",
        "credential",
        "kv",
    ],
}
(root / "manifest.json").write_text(json.dumps(manifest, indent=2))
buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:gz") as archive:
    for p in root.iterdir():
        archive.add(p, arcname=p.name)
print(base64.b64encode(buf.getvalue()).decode())
shutil.rmtree(root)
"""

os.umask(0o077)
stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
output = Path(__file__).resolve().parent / ".local" / "session-exports" / stamp
output.mkdir(parents=True, mode=0o700)
hosts = [
    ("activegraph", "sb-01M4S4RFG38T4ZGCVDWAVCGX56"),
    ("contactkiller", "sb-01M4RVRWYEXQSYEQS40V5MR6KR"),
]
summary = {"capturedAt": stamp, "hosts": []}
for name, sandbox_id in hosts:
    sandbox = modal.Sandbox.from_id(sandbox_id)
    process = sandbox.exec("python3", "-c", REMOTE, timeout=120)
    encoded = process.stdout.read()
    errors = process.stderr.read()
    if process.wait() != 0:
        raise RuntimeError(name + " export failed: " + errors)
    blob = base64.b64decode(encoded.strip(), validate=True)
    destination = output / name
    destination.mkdir(mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
        archive.extractall(destination, filter="data")
    manifest = json.loads((destination / "manifest.json").read_text())
    summary["hosts"].append(
        {
            "name": name,
            "sandboxId": sandbox_id,
            "sessions": manifest["sessions"],
            "persistentHistoryCandidates": manifest["persistentHistoryCandidates"],
        }
    )

# Desktop terminal history may retain partial output from the expired VM.
history = Path.home() / "Library/Application Support/Orca/terminal-history"
cached = output / "desktop-terminal-cache"
copied = []
if history.exists():
    for directory in history.iterdir():
        decoded = unquote(directory.name).lower()
        if directory.is_dir() and any(
            term in decoded
            for term in ["activegraph", "contactkiller", "14b15061-0663-44d3-b2f1-40cab8ef39c6"]
        ):
            shutil.copytree(directory, cached / directory.name)
            copied.append(directory.name)
summary["desktopTerminalCaches"] = copied
summary["limitations"] = [
    (
        "Original ActiveGraph VM home was ephemeral; i"
        "ts OpenCode database and full transcripts wer"
        "e lost when it expired."
    ),
    "Desktop terminal output is partial display history, not a complete conversation transcript.",
    "This is a point-in-time export; running sessions may add messages afterward.",
]
(output / "manifest.json").write_text(json.dumps(summary, indent=2))
checksums = []
for p in sorted(output.rglob("*")):
    if p.is_file():
        p.chmod(0o600)
        checksums.append(
            hashlib.sha256(p.read_bytes()).hexdigest() + "  " + str(p.relative_to(output))
        )
(output / "SHA256SUMS").write_text("\n".join(checksums) + "\n")
bundle = output.parent / (stamp + ".tar.gz")
with tarfile.open(bundle, "w:gz") as archive:
    archive.add(output, arcname=stamp)
print(
    json.dumps(
        {
            "directory": str(output),
            "archive": str(bundle),
            "sessionCounts": {h["name"]: len(h["sessions"]) for h in summary["hosts"]},
            "desktopTerminalCaches": len(copied),
        },
        indent=2,
    )
)
