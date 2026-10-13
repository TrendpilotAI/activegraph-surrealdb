"""Download surviving ActiveGraph worktree files and inspect other Modal volumes."""

import base64
import io
import json
import os
import tarfile
from pathlib import Path

import modal

os.umask(0o077)
out = Path(__file__).resolve().parent / ".local/session-exports/activegraph-recovery"
out.mkdir(parents=True, exist_ok=True)
code = r"""

import base64, io, json, os, pathlib, subprocess, tarfile, tempfile

os.umask(0o077)
repo = "/work/mercor-work/activegraph-surrealdb"
raw = subprocess.check_output(["git", "-C", repo, "worktree", "list", "--porcelain"], text=True)
paths = [line.split(" ", 1)[1] for line in raw.splitlines() if line.startswith("worktree ")]
buf = io.BytesIO()
files = []
excluded = []
with tempfile.TemporaryDirectory() as tmp:
    bundle = pathlib.Path(tmp) / "all-branches.bundle"
    subprocess.run(
        ["git", "-C", repo, "bundle", "create", str(bundle), "--all"],
        check=True,
        capture_output=True,
    )
    with tarfile.open(fileobj=buf, mode="w:gz") as archive:
        archive.add(bundle, arcname="all-branches.bundle")
        for index, path in enumerate(paths):
            prefix = "worktrees/" + ("main" if path == repo else pathlib.Path(path).name)
            for directory, dirs, names in os.walk(path):
                dirs[:] = [
                    d
                    for d in dirs
                    if d
                    not in [
                        ".git",
                        ".venv",
                        "node_modules",
                        ".devtraffic",
                        "__pycache__",
                        ".pytest_cache",
                    ]
                ]
                for name in names:
                    source = pathlib.Path(directory) / name
                    relative = source.relative_to(path)
                    if (
                        name == ".git"
                        or name == "opencode.json"
                        or name == ".env"
                        or name.startswith(".env.")
                    ):
                        excluded.append(str(source))
                        continue
                    archive.add(source, arcname=prefix + "/" + str(relative), recursive=False)
                    files.append(
                        {
                            "source": str(source),
                            "export": prefix + "/" + str(relative),
                            "bytes": source.lstat().st_size,
                        }
                    )
        manifest = json.dumps(
            {
                "worktrees": paths,
                "gitWorktreeInventory": raw,
                "files": files,
                "excludedCredentialConfigs": excluded,
            },
            indent=2,
        ).encode()
        item = tarfile.TarInfo("surviving-files-manifest.json")
        item.size = len(manifest)
        archive.addfile(item, io.BytesIO(manifest))
print(base64.b64encode(buf.getvalue()).decode())
"""
sandbox = modal.Sandbox.from_id("sb-01M4S4RFG38T4ZGCVDWAVCGX56")
process = sandbox.exec(
    "/usr/sbin/runuser",
    "-u",
    "mercor",
    "--",
    "env",
    "HOME=/home/mercor",
    "PYTHONPATH=",
    "python3",
    "-c",
    code,
    timeout=120,
)
encoded = process.stdout.read()
errors = process.stderr.read()
if process.wait() != 0:
    raise RuntimeError(errors)
blob = base64.b64decode(encoded.strip(), validate=True)
(out / "surviving-activegraph-files.tar.gz").write_bytes(blob)
with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
    archive.extractall(out / "surviving-files", filter="data")

volume_names = [
    "mercor-ag-6980ab2f8f2f64a9249a",
    "mercor-ag-7ffb4859d17e9d8352a7",
    "mercor-ag-4bd308eb1f87fd41e525",
    "mercor-ag-8afffb57a06b7627cd3b",
    "mercor-ag-a051b7588978c0db106a",
]
results = {}
for name in volume_names:
    volume = modal.Volume.from_name(name, environment_name="Development")
    queue = ["/"]
    candidates = []
    dirs_checked = 0
    while queue:
        directory = queue.pop()
        dirs_checked += 1
        for entry in volume.listdir(directory):
            path = entry.path
            if str(entry.type) == "2":
                leaf = path.rsplit("/", 1)[-1]
                if leaf not in [
                    ".venv",
                    "node_modules",
                    ".git",
                    "references",
                    "snapshot",
                    "__pycache__",
                    ".cache",
                ]:
                    queue.append("/" + path.lstrip("/"))
            elif any(
                t in path.lower()
                for t in [
                    "transcript",
                    "opencode.db",
                    "opencode/log",
                    "prompt-history",
                    "terminal-history",
                    "arize/harness/logs",
                ]
            ):
                candidates.append({"path": path, "bytes": entry.size})
    results[name] = {"directoriesInspected": dirs_checked, "historyCandidates": candidates}
(out / "modal-volume-history-scan.json").write_text(json.dumps(results, indent=2))
manifest = json.loads((out / "surviving-files/surviving-files-manifest.json").read_text())
print(
    json.dumps(
        {
            "worktrees": manifest["worktrees"],
            "filesCopied": len(manifest["files"]),
            "archiveBytes": len(blob),
            "volumeScan": results,
        },
        indent=2,
    )
)
