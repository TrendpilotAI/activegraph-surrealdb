"""Runs as the unprivileged mercor user, inside Modal only."""

import json
import os
import subprocess
import tempfile
from pathlib import Path


def run(*args, **kwargs):
    subprocess.run(args, check=True, **kwargs)


def main():
    os.umask(0o077)
    home = Path.home()
    tools = Path("/opt/mercor-tools")
    state = json.loads((tools / "modal-state.json").read_text())
    work = Path("/work/mercor-work")
    repo = Path(state["projectRoot"])
    work.mkdir(parents=True, exist_ok=True)
    projects = state.get(
        "projects",
        [{"url": state["repoUrl"], "path": str(repo), "pythonSubdir": ".", "pythonExtras": "dev"}],
    )
    for project in projects:
        path = Path(project["path"])
        if not (path / ".git").is_dir():
            run("git", "clone", "--branch", state["repoRef"], project["url"], str(path))
    # Existing persistent working directories are never reset or pulled automatically.
    for project in projects:
        path = Path(project["path"])
        package = path / project.get("pythonSubdir", ".")
        if (package / "pyproject.toml").exists():
            if not (path / ".venv/bin/python").exists():
                run("python3", "-m", "venv", str(path / ".venv"))
            if not (path / ".venv/.mercor-dependencies-ready").exists():
                extras = project.get("pythonExtras", "dev")
                run(
                    str(path / ".venv/bin/python"),
                    "-m",
                    "pip",
                    "install",
                    "-e",
                    f"{package}[{extras}]",
                )
                (path / ".venv/.mercor-dependencies-ready").touch()
        if project.get("webSubdir"):
            run("bun", "install", "--frozen-lockfile", cwd=path / project["webSubdir"])

    harness = home / ".arize/harness"
    harness.mkdir(parents=True, exist_ok=True)
    if not (harness / "venv").exists():
        (harness / "venv").symlink_to("/opt/arize-venv", target_is_directory=True)
    # The installer reads project naming from an explicitly named dotenv file.
    credentials = {
        "ARIZE_BACKEND": "arize",
        "ARIZE_API_KEY": os.environ["ARIZE_API_KEY"],
        "ARIZE_SPACE_ID": os.environ["ARIZE_SPACE_ID"],
        "ARIZE_PROJECT_NAME": state.get("arizeProjectName", "activegraph-surrealdb-opencode2"),
        "ARIZE_OTLP_ENDPOINT": "otlp.arize.com:443",
        "ARIZE_LOG_PROMPTS": str(state["logContent"]).lower(),
        "ARIZE_LOG_TOOL_DETAILS": str(state["logContent"]).lower(),
        "ARIZE_LOG_TOOL_CONTENT": str(state["logContent"]).lower(),
    }
    with tempfile.NamedTemporaryFile(mode="w", prefix="mercor-arize-", suffix=".env") as env_file:
        for key, value in credentials.items():
            env_file.write(f"{key}={json.dumps(value)}\n")
        env_file.flush()
        env = {**os.environ, "ARIZE_ENV_FILE": env_file.name, "ARIZE_NONINTERACTIVE": "1"}
        run(
            "/opt/arize-venv/bin/python",
            "/opt/arize-source/tracing/opencode/install.py",
            "install",
            env=env,
            stdout=subprocess.DEVNULL,
        )
    # Replace only the freshly installed Arize V1 shim with our native V2 adapter.
    old = home / ".config/opencode/plugin/arize-tracing.ts"
    if old.exists() and old.read_text().startswith("// Arize opencode tracing plugin (shim)."):
        old.unlink()
    config_dir = home / ".config/opencode"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "opencode.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    config.setdefault("$schema", "https://opencode.ai/config.json")
    config.setdefault("update", "disable")
    config.setdefault("plugins", ["/opt/mercor-tools/arize-v2.ts"])
    references = config.setdefault("references", {})
    for name in state["references"]:
        references.setdefault(name, {"type": "local", "path": f"/opt/references/{name}"})

    # OpenCode 2 is already installed: the inspected installer won't install V1.
    # Re-run init.sh in a fresh, non-Git folder and copy its result to the persistent tree.
    # This avoids rewriting existing repositories' config or accumulating duplicate excludes.
    key = os.environ["DEVTRAFFIC_ACCESS_ID"]
    if not key or "\n" in key or "\r" in key:
        raise ValueError("Mercor access ID must be a nonempty single line")
    with tempfile.TemporaryDirectory(prefix="mercor-init-") as temp:
        run("bash", str(tools / "mercor-init.sh"), cwd=temp, input=(key + "\n").encode())
        result = json.loads((Path(temp) / "opencode.json").read_text())
        key_dir = work / ".devtraffic"
        key_dir.mkdir(mode=0o700, exist_ok=True)
        key_dir.chmod(0o700)
        (key_dir / "key").write_text(key)
        (key_dir / "key").chmod(0o600)
        (key_dir / ".gitignore").write_text("*\n")
    # Convert installer output to V2 shape, preserving its actual model list and defaults.
    provider = result["provider"]["mercor"]
    config.setdefault("providers", {})["mercor"] = {
        "name": "Mercor gateway",
        "package": "@opencode/ai/providers/openai-compatible",
        "settings": {
            "baseURL": provider["options"]["baseURL"],
            "apiKey": "{file:/work/mercor-work/.devtraffic/key}",
        },
        "models": provider["models"],
    }
    config.setdefault("model", result["model"])
    config.setdefault("experimental", {}).setdefault(
        "policies",
        [
            {"action": "provider.use", "resource": "*", "effect": "deny"},
            {"action": "provider.use", "resource": "mercor", "effect": "allow"},
        ],
    )
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    # Policy is configuration, not a tamper-proof boundary against code running as this user.
    # Modal's network policy is the outer enforcement boundary.
    run("opencode", "--version")
    print("OpenCode 2 configured with Mercor provider, Arize V2 adapter, and five references.")


if __name__ == "__main__":
    main()
