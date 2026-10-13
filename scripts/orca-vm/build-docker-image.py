"""Build and validate a clean Docker-enabled Modal VM base from the clean base."""

import datetime
import gzip
import json
import os
import re
import shlex
import urllib.request
from pathlib import Path

import modal

os.environ.setdefault("MODAL_PROFILE", "mercor-work")
here = Path(__file__).resolve().parent
state_path = here / "modal-state.json"
state = json.loads(state_path.read_text())
base = state.get("dockerSourceSnapshotId", state["snapshotId"])

# Pin to versions actually present in Docker's official Bookworm repository.
url = "https://download.docker.com/linux/debian/dists/bookworm/stable/binary-amd64/Packages.gz"
with urllib.request.urlopen(url, timeout=30) as response:
    packages = gzip.decompress(response.read()).decode()
wanted = {
    "docker-ce",
    "docker-ce-cli",
    "containerd.io",
    "docker-buildx-plugin",
    "docker-compose-plugin",
}
versions = {}
for block in packages.split("\n\n"):
    fields = dict(
        line.split(": ", 1)
        for line in block.splitlines()
        if ": " in line and not line.startswith(" ")
    )
    name = fields.get("Package")
    if name not in wanted:
        continue
    version = fields["Version"]
    key = tuple(int(n) for n in re.findall(r"\d+", version))
    if name not in versions or key > tuple(int(n) for n in re.findall(r"\d+", versions[name])):
        versions[name] = version
if set(versions) != wanted:
    raise RuntimeError("Official Docker repository is missing required packages")
daemon = {
    "storage-driver": "overlay2",
    "features": {"containerd-snapshotter": False},
    "registry-mirrors": ["https://mirror.gcr.io"],
    "bip": "10.200.0.1/24",
    "default-address-pools": [{"base": "10.201.0.0/16", "size": 24}],
    "hosts": ["unix:///var/run/docker.sock"],
}
docker_packages = [name + "=" + versions[name] for name in sorted(wanted)]
image = (
    modal.Image.from_id(base)
    .env({"DEBIAN_FRONTEND": "noninteractive"})
    .apt_install("iptables", "ca-certificates", "curl")
    .run_commands(
        "install -d -m 0755 /etc/apt/keyrings /etc/docker",
        "curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc",
        "chmod a+r /etc/apt/keyrings/docker.asc",
        (
            "printf '%s\\n' 'Types: deb' 'URIs: https://dow"
            "nload.docker.com/linux/debian' 'Suites: bookw"
            "orm' 'Components: stable' 'Architectures: amd"
            "64' 'Signed-By: /etc/apt/keyrings/docker.asc'"
            " > /etc/apt/sources.list.d/docker.sources"
        ),
        (
            "printf '%s\\n' '#!/bin/sh' 'exit 101' > /usr/s"
            "bin/policy-rc.d && chmod 755 /usr/sbin/policy"
            "-rc.d"
        ),
    )
    .apt_install(*docker_packages)
    .run_commands(
        "python3 -c "
        + shlex.quote(
            'import json,pathlib; pathlib.Path("/etc/docker/daemon.json").write_text(json.dumps('
            + repr(daemon)
            + "))"
        ),
        "usermod -aG docker mercor",
        "docker --version",
        "docker compose version",
    )
)
app = modal.App.lookup(
    state["appName"], environment_name=state["environment"], create_if_missing=True
)
build_vm = None
try:
    with modal.enable_output():
        build_vm = modal.Sandbox.create(
            "sleep", "infinity", app=app, image=image, runtime="vm", timeout=900, cpu=2, memory=4096
        )
        snapshot = build_vm.snapshot_filesystem(timeout=120, ttl=30 * 24 * 3600)
    result = {
        "sourceSnapshotId": base,
        "snapshotId": snapshot.object_id,
        "dockerPackages": versions,
        "daemonConfig": daemon,
        "builtAt": datetime.datetime.now(datetime.UTC).isoformat(),
        "containsCredentials": False,
        "validated": False,
    }
    (here / ".local/docker-image-build.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
finally:
    if build_vm is not None:
        build_vm.terminate(wait=True)
