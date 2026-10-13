"""Attach a Modal GitHub Secret to existing VMs; never print credential values."""

import json
from pathlib import Path

import modal

HERE = Path(__file__).resolve().parent
HOSTS = [
    (name, json.loads((HERE / ".local" / filename).read_text())["sandboxId"])
    for name, filename in [
        ("ActiveGraph", "connection.json"),
        ("ContactKiller", "contactkiller-connection.json"),
    ]
]
secret_name = json.loads((HERE / "modal-state.json").read_text())["githubSecret"]
secret = modal.Secret.from_name(secret_name, environment_name="Development")
secret_keys = sorted(secret._get_keys(refresh=True))

code = r"""


import os, subprocess, pathlib, json

candidates = json.loads(os.environ["CHECK_SECRET_KEYS"])
token = next((os.environ[k].strip() for k in candidates if os.environ.get(k)), None)
if not token:
    token = next(
        (
            os.environ[k].strip()
            for k in candidates
            if os.environ.get(k, "").startswith(("github_pat_", "ghp_", "gho_"))
        ),
        None,
    )
if not token:
    raise RuntimeError("The github-secret Secret has no recognized GitHub token field")
env = dict(os.environ)
for key in ["GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"]:
    env.pop(key, None)
os.umask(0o077)
result = subprocess.run(
    ["gh", "auth", "login", "--hostname", "github.com", "--git-protocol", "https", "--with-token"],
    input=token + "\n",
    text=True,
    capture_output=True,
    env=env,
)
if result.returncode:
    raise RuntimeError("GitHub CLI authentication failed: " + result.stderr)
subprocess.run(
    ["gh", "auth", "setup-git", "--hostname", "github.com"],
    env=env,
    check=True,
    capture_output=True,
)
hosts = pathlib.Path.home() / ".config/gh/hosts.yml"
if hosts.exists():
    hosts.chmod(0o600)
startup = pathlib.Path.home() / ".config/mercor-github/env.sh"
startup.parent.mkdir(parents=True, exist_ok=True)
startup.write_text(
    'if [ -z "${GITHUB_TOKEN:-}" ]; then\n'
    '  GITHUB_TOKEN="$(/usr/local/bin/gh auth token '
    '--hostname github.com 2>/dev/null)"\n'
    '  export GITHUB_TOKEN\nfi\n'
)
startup.chmod(0o600)
marker = "# Mercor GitHub credential environment"
for filename in [".profile", ".bashrc", ".bash_profile"]:
    profile = pathlib.Path.home() / filename
    if not profile.exists() and filename == ".bash_profile":
        continue
    content = profile.read_text() if profile.exists() else ""
    if marker not in content:
        profile.write_text(content + "\n" + marker + '\n. "$HOME/.config/mercor-github/env.sh"\n')
result = subprocess.run(
    ["gh", "api", "repos/TrendpilotAI/ContactKiller", "--jq", ".private"],
    text=True,
    capture_output=True,
    env=env,
)
account = subprocess.run(
    ["gh", "api", "user", "--jq", ".login"], text=True, capture_output=True, env=env
)
print(
    json.dumps(
        {
            "githubCredentialConfigured": True,
            "privateRepositoryAccess": result.returncode == 0,
            "account": account.stdout.strip() if account.returncode == 0 else None,
            "accessError": result.stderr.strip() if result.returncode else None,
        }
    )
)
"""

for name, sandbox_id in HOSTS:
    sandbox = modal.Sandbox.from_id(sandbox_id)
    if sandbox.poll() is not None:
        raise RuntimeError(f"{name} VM is no longer running")
    install = sandbox.exec(
        "bash",
        "-c",
        (
            "if ! command -v gh >/dev/null; then curl -fsS"
            "L https://github.com/cli/cli/releases/downloa"
            "d/v2.102.0/gh_2.102.0_linux_amd64.tar.gz -o /"
            "tmp/mercor-gh.tar.gz && echo 'bb766f710eef8ed"
            "e859c18578c72c327597cd4c8a85b06001b1f3843c601"
            "9386  /tmp/mercor-gh.tar.gz' | sha256sum -c -"
            " && tar -xzf /tmp/mercor-gh.tar.gz -C /tmp &&"
            " install -m 755 /tmp/gh_2.102.0_linux_amd64/b"
            "in/gh /usr/local/bin/gh; fi"
        ),
        workdir="/home/mercor",
        timeout=120,
    )
    install.stdout.read()
    errors = install.stderr.read()
    if install.wait() != 0:
        raise RuntimeError("GitHub CLI installation failed: " + errors)
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
        secrets=[secret],
        env={"CHECK_SECRET_KEYS": json.dumps(secret_keys)},
        workdir="/home/mercor",
        timeout=60,
    )
    output = process.stdout.read()
    errors = process.stderr.read()
    print(name + ": " + output.strip())
    if process.wait() != 0:
        raise RuntimeError(name + " GitHub configuration failed: " + errors)
