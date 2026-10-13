"""Validate Docker in an isolated VM before updating the shared runtime image."""

import datetime
import json
import re
from pathlib import Path

import modal

here = Path(__file__).resolve().parent
state = json.loads((here / "modal-state.json").read_text())
result = json.loads((here / ".local/docker-image-build.json").read_text())
app = modal.App.lookup(
    state["appName"], environment_name=state["environment"], create_if_missing=True
)
allowed = set(state["outboundDomains"])
sandbox = modal.Sandbox.create(
    "dockerd",
    "--config-file=/etc/docker/daemon.json",
    app=app,
    image=modal.Image.from_id(result["snapshotId"]),
    runtime="vm",
    timeout=900,
    cpu=2,
    memory=4096,
    readiness_probe=modal.Probe.with_exec("docker", "info", interval_ms=500),
    outbound_domain_allowlist=sorted(allowed),
)
checks = []
try:
    sandbox.wait_until_ready(timeout=90)

    def execute(*args, timeout=120):
        process = sandbox.exec(*args, timeout=timeout)
        output = process.stdout.read()
        errors = process.stderr.read()
        code = process.wait()
        checks.append({"command": list(args), "exitCode": code, "stdout": output, "stderr": errors})
        return code, output

    code, output = execute("docker", "info", "--format", "{{json .}}")
    if code:
        raise RuntimeError("Docker info failed")
    info = json.loads(output)
    if info["Driver"] != "overlay2":
        raise RuntimeError("Unexpected Docker storage driver")
    code, _ = execute(
        "/usr/sbin/runuser",
        "-u",
        "mercor",
        "--",
        "docker",
        "info",
        "--format",
        "{{.ServerVersion}}",
    )
    if code:
        raise RuntimeError("Mercor user cannot access the VM Docker socket")
    for image, command in [
        ("busybox:1.37.0", ["echo", "MODAL_DOCKER_OK"]),
        ("surrealdb/surrealdb:v3.3.0", ["version"]),
    ]:
        code, _ = execute("docker", "run", "--rm", image, *command, timeout=120)
        if code:
            blocked = []
            for entry in sandbox.logs.fetch(
                since=datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=20)
            ):
                blocked += re.findall(
                    r"blocking all outbound connections to ([a-zA-Z0-9.-]+)", entry.message
                )
            if blocked:
                allowed.update(blocked)
                sandbox._experimental_set_outbound_network_policy(
                    outbound_domain_allowlist=sorted(allowed)
                )
                code, _ = execute("docker", "run", "--rm", image, *command, timeout=120)
        if code:
            raise RuntimeError("Docker container execution failed")
    result.update(
        validated=True,
        validationSandboxId=sandbox.object_id,
        validationChecks=checks,
        outboundDomains=sorted(allowed),
        validatedAt=datetime.datetime.now(datetime.UTC).isoformat(),
    )
    (here / ".local/docker-image-build.json").write_text(json.dumps(result, indent=2))
    for path in [here / "modal-state.json", here / ".local/contactkiller-state.json"]:
        config = json.loads(path.read_text())
        config.update(
            snapshotId=result["snapshotId"],
            dockerSourceSnapshotId=result["sourceSnapshotId"],
            dockerEnabled=True,
            dockerPackages=result["dockerPackages"],
            outboundDomains=sorted(allowed),
        )
        path.write_text(json.dumps(config, indent=2) + "\n")
    print(
        json.dumps(
            {
                "validated": True,
                "snapshotId": result["snapshotId"],
                "serverVersion": info["ServerVersion"],
                "storageDriver": info["Driver"],
                "checks": [{"command": c["command"], "exitCode": c["exitCode"]} for c in checks],
            },
            indent=2,
        )
    )
except Exception:
    (here / ".local/docker-image-validation-failure.json").write_text(
        json.dumps({"sandboxId": sandbox.object_id, "checks": checks}, indent=2)
    )
    raise
finally:
    sandbox.terminate(wait=True)
