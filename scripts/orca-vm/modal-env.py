"""Modal lifecycle operations. stdout is reserved for machine-readable results."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE_PATH = Path(os.environ.get("MERCOR_STATE_FILE", str(HERE / "modal-state.json")))
STATE = json.loads(STATE_PATH.read_text())
LOCAL = HERE / ".local"


def save_state(**updates):
    STATE.update(updates)
    STATE_PATH.write_text(json.dumps(STATE, indent=2) + "\n")


def prepare():
    """Copy only installed Orca program files and the audited Mercor installer."""
    root = Path(
        os.environ.get(
            "ORCA_TEMPLATE_PATH", "/Applications/Orca.app/Contents/Resources/orcad-template"
        )
    )
    manifest = json.loads((root / "orcad-template.json").read_text())
    target = "linux-x64-glibc"
    bundle = LOCAL / "orca"
    bundle.mkdir(parents=True, exist_ok=True)
    for base, files in [
        (root, manifest["commonSha256"]),
        (root / "targets" / target, manifest["targets"][target]["files"]),
    ]:
        for rel, digest in files.items():
            source = base / rel
            if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
                raise RuntimeError(f"Installed Orca program checksum mismatch: {rel}")
            dest = bundle / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
    version = json.loads((bundle / "out/package.json").read_text())["version"]
    if version != STATE["orcaVersion"]:
        raise RuntimeError(f"Expected Orca {STATE['orcaVersion']}, found {version}")
    # Orca's managed runtime verifies an ordered artifact hash, not its app version.
    # This order matches the pinned 1.4.224 runtime's zVt/aQs implementation.
    ordered = list(manifest["commonSha256"])
    index = ordered.index("session-scanner-service-entry.js") + 1
    ordered[index:index] = [".server-target", ".runtime-node"]
    index = ordered.index("node_modules/@parcel/watcher/index.js") + 1
    ordered[index:index] = ["node_modules/@parcel/watcher/watcher.node"]
    ordered += ["node_modules/node-pty/build/Release/pty.node", "ripgrep/linux-x64/rg"]
    artifact_hash = hashlib.sha256()
    for rel in ordered:
        artifact_hash.update((bundle / rel).read_bytes())
    artifact_version = "0.1.0+" + artifact_hash.hexdigest()[:12]
    (bundle / ".version").write_text(artifact_version + "\n")
    installer = Path(os.environ.get("MERCOR_INIT_PATH", "/Users/winner/Downloads/init.sh"))
    shutil.copy2(installer, LOCAL / "mercor-init.sh")
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    save_state(mercorInstallerSha256=digest, orcaArtifactVersion=artifact_version)
    return {"prepared": True, "orcaVersion": version, "installerSha256": digest}


def image_definition(modal):
    if not (LOCAL / "orca/orcad.js").exists():
        raise RuntimeError("Run modal-env.py prepare first")
    refs = STATE["references"]
    commands = [
        "useradd --create-home --uid 1000 --shell /bin/bash mercor",
        "mkdir -p /opt/orca/runtimes /opt/orca-node /opt/references /opt/mercor-tools",
        (
            "curl -fsSL https://nodejs.org/dist/v24.21.0/n"
            "ode-v24.21.0-linux-x64.tar.gz -o /tmp/node.ta"
            "r.gz"
        ),
        (
            "echo '6e1db87ef58b8819e5d5402eff1536491b18edd"
            "8eb7bee5ef7897876e88dc5ff  /tmp/node.tar.gz' "
            "| sha256sum -c -"
        ),
        "tar -xzf /tmp/node.tar.gz --strip-components=1 -C /opt/orca-node",
        (
            "ln -s /opt/orca-node /opt/orca/runtimes/node-"
            "7fde7b8afa198da66257f42ee2001d874c7355631e6d1"
            "579a5fb5ef1f246df4c"
        ),
        f"/opt/orca-node/bin/npm install --global @opencode/cli@{STATE['opencodeVersion']}",
        "git clone https://github.com/Arize-ai/coding-harness-tracing.git /opt/arize-source && "
        f"git -C /opt/arize-source checkout --detach {STATE['arizeCommit']}",
        "python3 -m venv /opt/arize-venv && /opt/arize-venv/bin/pip install /opt/arize-source",
    ]
    for name, ref in refs.items():
        commands.append(
            f"git clone --filter=blob:none --no-checkout {ref['url']} /opt/references/{name} && "
            f"git -C /opt/references/{name} checkout --detach {ref['commit']}"
        )
    image = (
        modal.Image.debian_slim(python_version="3.12")
        .apt_install("git", "curl", "ca-certificates", "build-essential", "util-linux", "ripgrep")
        .env(
            {
                "PATH": (
                    "/opt/orca-node/bin:/usr/local/sbin:/usr/local"
                    "/bin:/usr/sbin:/usr/bin:/sbin:/bin"
                ),
                "SHELL": "/bin/bash",
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        .run_commands(*commands)
        .add_local_dir(LOCAL / "orca", "/opt/orca/current", copy=True)
        .add_local_file(LOCAL / "mercor-init.sh", "/opt/mercor-tools/mercor-init.sh", copy=True)
    )
    for name in ["modal-state.json", "configure-runtime.py", "arize-v2.ts", "package.json"]:
        image = image.add_local_file(HERE / name, f"/opt/mercor-tools/{name}", copy=True)
    # Use the lockfile created during local validation, when present.
    if (HERE / "package-lock.json").exists():
        image = image.add_local_file(
            HERE / "package-lock.json", "/opt/mercor-tools/package-lock.json", copy=True
        )
    image = image.run_commands(
        "cd /opt/mercor-tools && npm install --omit=dev --ignore-scripts --no-audit --no-fund",
        # npm 11 can skip unapproved postinstall scripts. This package's native
        # binary selector is required and is run explicitly inside the build.
        "node /opt/orca-node/lib/node_modules/@opencode/cli/postinstall.mjs",
        (
            "printf '%s\\n' '#!/usr/bin/env bash' 'set -euo"
            " pipefail' 'if [ \"${1:-}\" = serve ]; then shi"
            "ft; exec /opt/orca-node/bin/node /opt/orca/cu"
            "rrent/orcad.js \"$@\"; fi' 'exec /opt/orca-node"
            "/bin/node /opt/orca/current/out/cli/index.js "
            '"$@"\' > /usr/local/bin/orca'
        ),
        "chmod 755 /usr/local/bin/orca /opt/orca/current/ripgrep/linux-x64/rg",
        "opencode --version",
    )
    return image


def app(modal):
    return modal.App.lookup(
        STATE["appName"], environment_name=STATE["environment"], create_if_missing=True
    )


def build(modal):
    # The reusable snapshot never runs Orca or contains runtime secrets.
    sandbox = modal.Sandbox.create(
        "sleep",
        "infinity",
        app=app(modal),
        image=image_definition(modal),
        timeout=STATE["timeoutSeconds"],
        cpu=(STATE["cpu"], STATE["cpu"]),
        memory=(STATE["memoryMiB"], STATE["memoryMiB"]),
        region=STATE["region"],
        runtime=STATE["runtime"],
    )
    try:
        snapshot = sandbox.snapshot_filesystem(timeout=120, ttl=30 * 24 * 3600)
        save_state(
            snapshotId=snapshot.object_id,
            dockerSourceSnapshotId=snapshot.object_id,
            dockerEnabled=False,
        )
        return {"snapshotId": snapshot.object_id, "containsCredentials": False}
    finally:
        sandbox.terminate(wait=True)


def remote(sandbox, *args, timeout=1200, env=None):
    process = sandbox.exec(*args, timeout=timeout, workdir="/home/mercor", env=env)
    # Installation output may be large; stream without including secrets.
    for line in process.stdout:
        print(line, end="", file=sys.stderr)
    error = process.stderr.read()
    code = process.wait()
    if error:
        print(error, end="", file=sys.stderr)
    if code != 0:
        raise RuntimeError(f"Remote command failed with exit code {code}")


def create(modal, *, volume_name=None):
    if not STATE["snapshotId"]:
        raise RuntimeError("Base snapshot is missing; build it after approving the paid build step")
    instance = os.environ.get("ORCA_VM_INSTANCE_ID") or str(uuid.uuid4())
    if os.environ.get("MERCOR_VERIFY_RUNTIME") == "1" and not volume_name:
        volume_name = os.environ.get("MERCOR_TEST_VOLUME")
    if not volume_name:
        volume_name = "mercor-ag-" + hashlib.sha256(instance.encode()).hexdigest()[:20]
    if not re.fullmatch(r"mercor-ag-[a-f0-9]{20}", volume_name):
        raise ValueError("Invalid Mercor workspace volume name")
    volume = modal.Volume.from_name(
        volume_name, environment_name=STATE["environment"], create_if_missing=True, version=2
    )
    secrets = [
        modal.Secret.from_name(
            STATE["arizeSecret"],
            environment_name=STATE["environment"],
            required_keys=["ARIZE_API_KEY", "ARIZE_SPACE_ID"],
        ),
        modal.Secret.from_name(
            STATE["mercorSecret"],
            environment_name=STATE["environment"],
            required_keys=["DEVTRAFFIC_ACCESS_ID"],
        ),
    ]
    if STATE.get("githubSecret"):
        secrets.append(
            modal.Secret.from_name(STATE["githubSecret"], environment_name=STATE["environment"])
        )
    runtime_image = modal.Image.from_id(STATE["snapshotId"])
    for name in ["configure-runtime.py", "arize-v2.ts", "arize-reconcile.py"]:
        runtime_image = runtime_image.add_local_file(HERE / name, f"/opt/mercor-tools/{name}")
    runtime_image = runtime_image.add_local_file(STATE_PATH, "/opt/mercor-tools/modal-state.json")
    docker_enabled = STATE.get("dockerEnabled", False)
    entrypoint = (
        ["dockerd", "--config-file=/etc/docker/daemon.json"]
        if docker_enabled
        else ["sleep", "infinity"]
    )
    sandbox = modal.Sandbox.create(
        *entrypoint,
        app=app(modal),
        image=runtime_image,
        workdir="/home/mercor",
        cpu=(STATE["cpu"], STATE["cpu"]),
        memory=(STATE["memoryMiB"], STATE["memoryMiB"]),
        timeout=STATE["timeoutSeconds"],
        runtime=STATE["runtime"],
        experimental_options={"enable_exit_snapshot": STATE.get("enableExitSnapshot", True)},
        region=STATE["region"],
        volumes={"/work": volume},
        secrets=secrets,
        encrypted_ports=[STATE["port"]],
        outbound_domain_allowlist=STATE["outboundDomains"],
        readiness_probe=modal.Probe.with_exec("docker", "info", interval_ms=500)
        if docker_enabled
        else None,
        env={
            "ARIZE_TRACE_ENABLED": "true",
            "MERCOR_SANDBOX": "1",
            "PIP_INDEX_URL": "https://pypi.org/simple",
            "PYTHONPATH": "",
            **(
                {"DOCKER_HOST": "unix:///var/run/docker.sock", "DOCKER_CONTEXT": ""}
                if docker_enabled
                else {}
            ),
        },
    )
    try:
        if docker_enabled:
            sandbox.wait_until_ready(timeout=90)
        remote(
            sandbox,
            "bash",
            "-c",
            (
                "for name in node opencode; do test -e /usr/lo"
                "cal/bin/$name || ln -s /opt/orca-node/bin/$na"
                "me /usr/local/bin/$name; done; test -e /usr/l"
                "ocal/bin/opencode2 || ln -s /opt/orca-node/bi"
                "n/opencode /usr/local/bin/opencode2"
            ),
            timeout=30,
        )
        remote(sandbox, "chown", "1000:1000", "/work", timeout=30)
        if STATE.get("contactKillerTools"):
            remote(
                sandbox,
                "npm",
                "install",
                "--global",
                "--ignore-scripts",
                "bun@1.4.0",
                "@beads/bd@1.3.1",
                timeout=240,
            )
            remote(sandbox, "node", "/opt/orca-node/lib/node_modules/bun/install.js", timeout=120)
            remote(
                sandbox,
                "node",
                "/opt/orca-node/lib/node_modules/@beads/bd/scripts/postinstall.js",
                timeout=120,
            )
            remote(
                sandbox,
                "bash",
                "-c",
                (
                    "for name in bun bd; do test -e /usr/local/bin"
                    "/$name || ln -s /opt/orca-node/bin/$name /usr"
                    "/local/bin/$name; done"
                ),
                timeout=30,
            )
        if STATE.get("persistentHome"):
            remote(
                sandbox,
                "python3",
                "-c",
                (
                    "from pathlib import Path; import shutil,os; h"
                    "ome=Path('/home/mercor'); target=Path('/work/"
                    ".mercor-home'); fresh=not target.exists(); sh"
                    "util.copytree(home,target) if fresh else None"
                    "; shutil.rmtree(home) if not home.is_symlink("
                    ") else None; home.symlink_to(target) if not h"
                    "ome.is_symlink() else None; os.chown(target,1"
                    "000,1000); [os.chown(p,1000,1000) for p in ta"
                    "rget.rglob('*') if not p.is_symlink()] if fre"
                    "sh else None"
                ),
                timeout=30,
            )
        if docker_enabled:
            remote(
                sandbox,
                "/usr/sbin/runuser",
                "-u",
                "mercor",
                "--",
                "env",
                "HOME=/home/mercor",
                "PYTHONPATH=",
                "python3",
                "-c",
                (HERE / "configure-docker-shell.py").read_text(),
                timeout=30,
            )
        remote(
            sandbox,
            "bash",
            "-c",
            "if ! command -v bd >/dev/null && test -x /home/mercor/.local/bin/bd; "
            "then ln -s /home/mercor/.local/bin/bd /usr/local/bin/bd; fi",
            timeout=10,
        )
        for project in STATE.get("projects", []):
            if not project.get("private"):
                continue
            probe = sandbox.exec("test", "-d", project["path"] + "/.git", timeout=10)
            if probe.wait() == 0:
                continue
            import subprocess

            token = subprocess.run(
                ["gh", "auth", "token"], check=True, text=True, capture_output=True
            ).stdout.strip()
            clone_code = r"""

import os, subprocess, tempfile, pathlib

url = os.environ["CLONE_URL"]
dest = pathlib.Path(os.environ["CLONE_DEST"])
dest.parent.mkdir(parents=True, exist_ok=True)
os.chown(dest.parent, 1000, 1000)
if not (dest / ".git").exists():
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mercor-askpass-") as temp:
        helper = pathlib.Path(temp) / "askpass.sh"
        helper.write_text(
            (
                '#!/bin/sh\ncase "$1" in *Username*) echo x-acc'
                'ess-token;; *Password*) printf "%s" "$GH_TOKE'
                'N";; esac\n'
            )
        )
        helper.chmod(0o700)
        subprocess.run(
            ["git", "clone", "--branch", "main", url, str(dest)],
            check=True,
            env={**os.environ, "GIT_ASKPASS": str(helper), "GIT_TERMINAL_PROMPT": "0"},
        )
    for path in [dest, *dest.rglob("*")]:
        if not path.is_symlink():
            os.chown(path, 1000, 1000)
"""
            remote(
                sandbox,
                "python3",
                "-c",
                clone_code,
                env={"GH_TOKEN": token, "CLONE_URL": project["url"], "CLONE_DEST": project["path"]},
            )
        remote(
            sandbox,
            "/usr/sbin/runuser",
            "-u",
            "mercor",
            "--",
            "env",
            "HOME=/home/mercor",
            "PYTHONPATH=",
            "python3",
            "/opt/mercor-tools/configure-runtime.py",
        )
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "session_protection", HERE / "protect-sessions.py"
        )
        protection = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(protection)
        protection.install_remote(sandbox)
        protection.checkpoint_remote(sandbox)
        url = sandbox.tunnels()[STATE["port"]].url
        address = url.replace("https://", "wss://", 1)
        # The serving process has no Modal management token or host credentials.
        # Recovery snapshots belong only to this instance; never reuse them as a base image.
        remote(
            sandbox,
            "bash",
            "-c",
            "nohup /usr/sbin/runuser -u mercor -- env HOME=/home/mercor PYTHONPATH= "
            'orca serve --bind 0.0.0.0 --port "$1" --project-root "$2" --pairing-address "$3" '
            "--recipe-json >/tmp/mercor-orca-result.json 2>/tmp/mercor-orca.log </dev/null &",
            "serve",
            str(STATE["port"]),
            STATE["projectRoot"],
            address,
            timeout=30,
        )
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            process = sandbox.exec("cat", "/tmp/mercor-orca-result.json", timeout=10)
            output = process.stdout.read()
            process.wait()
            try:
                result = json.loads(output)
                if result.get("pairingCode") and result.get("projectRoot"):
                    if os.environ.get("MERCOR_VERIFY_RUNTIME") == "1":
                        remote(
                            sandbox,
                            "/usr/sbin/runuser",
                            "-u",
                            "mercor",
                            "--",
                            "env",
                            "HOME=/home/mercor",
                            "PYTHONPATH=",
                            "python3",
                            "-c",
                            (HERE / "verify-runtime.py").read_text(),
                            timeout=420,
                        )
                    result["schemaVersion"] = 1
                    result["userData"] = {
                        "provider": "modal",
                        "resourceId": sandbox.object_id,
                        "volumeName": volume_name,
                        "exitSnapshotEnabled": STATE.get("enableExitSnapshot", True),
                    }
                    protection.register(
                        "contactkiller"
                        if "contactkiller" in STATE["projectRoot"].lower()
                        else "activegraph",
                        sandbox.object_id,
                        volume_name,
                        STATE.get("enableExitSnapshot", True),
                    )
                    protection.refresh_networks()
                    return result
            except json.JSONDecodeError:
                pass
            time.sleep(0.5)
        process = sandbox.exec("cat", "/tmp/mercor-orca.log")
        print(process.stdout.read(), file=sys.stderr)
        raise RuntimeError("Orca did not emit pairing JSON within 90 seconds")
    except BaseException:
        sandbox.terminate(wait=True)
        print(
            f"Failed sandbox terminated; project files retained in volume {volume_name}",
            file=sys.stderr,
        )
        raise


def lifecycle(modal, action):
    payload = json.load(sys.stdin)
    data = payload.get("recipeResult", {}).get("userData", {})
    if data.get("provider") != "modal":
        raise ValueError("Lifecycle payload is not a Modal recipe result")
    if action == "resume":
        return create(modal, volume_name=data["volumeName"])
    resource_id = data["resourceId"]
    if not re.fullmatch(r"sb-[A-Za-z0-9]+", resource_id):
        raise ValueError("Invalid Modal Sandbox ID")
    sandbox = modal.Sandbox.from_id(resource_id)
    if sandbox.poll() is None:
        remote(
            sandbox,
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
        remote(sandbox, "sync", "/work", timeout=60)
        sandbox.terminate(wait=True)
    # Retain project data on suspend and destroy; deletion must be separately requested.
    return {"terminated": True, "volumeRetained": data["volumeName"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=["prepare", "build", "create", "suspend", "resume", "destroy"]
    )
    args = parser.parse_args()
    if args.action == "prepare":
        result = prepare()
    else:
        os.environ["MODAL_PROFILE"] = STATE["workspace"]
        import modal

        with modal.enable_output():
            # Modal build output also belongs on stderr, never in recipe stdout.
            from contextlib import redirect_stdout

            with redirect_stdout(sys.stderr):
                if args.action == "build":
                    result = build(modal)
                elif args.action == "create":
                    result = create(modal, volume_name=os.environ.get("MERCOR_MANUAL_VOLUME"))
                else:
                    result = lifecycle(modal, args.action)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
