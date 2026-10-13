"""Use the VM Docker socket while retaining persistent home-installed tools."""

from pathlib import Path

MARKER = "# Mercor VM Docker daemon"
LEGACY_LITERAL = (
    r"\n# Mercor VM Docker daemon\nexport DOCKER_HOST=unix:///var/run/docker.sock"
    r"\nunset DOCKER_CONTEXT\n"
)
LEGACY_BLOCK = (
    "\n# Mercor VM Docker daemon\nexport DOCKER_HOST=unix:///var/run/docker.sock"
    "\nunset DOCKER_CONTEXT\n"
)
BLOCK = (
    "\n# Mercor VM Docker daemon\n"
    'export PATH="$HOME/.local/bin:$HOME/bin:$PATH"\n'
    "export DOCKER_HOST=unix:///var/run/docker.sock\n"
    "unset DOCKER_CONTEXT\n"
)


def configure(home):
    for name in [".profile", ".bashrc", ".bash_profile"]:
        path = home / name
        if name == ".bash_profile" and not path.exists():
            continue
        original = path.read_text() if path.exists() else ""
        harness_created = name == ".bash_profile" and original.startswith(LEGACY_LITERAL)
        content = original.replace(LEGACY_LITERAL, "\n").replace(LEGACY_BLOCK, "\n")
        if harness_created and '. "$HOME/.profile"' not in content:
            content = '. "$HOME/.profile"\n' + content
        if BLOCK not in content:
            content += BLOCK
        if content != original:
            path.write_text(content)


if __name__ == "__main__":
    configure(Path.home())
