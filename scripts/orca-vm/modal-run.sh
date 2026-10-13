#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -n "${MODAL_PYTHON:-}" ]; then
  modal_python="$MODAL_PYTHON"
else
  modal_cli="$(command -v modal)" || { echo "Install and authenticate the Modal CLI first" >&2; exit 1; }
  modal_python="$(head -n 1 "$modal_cli")"
  modal_python="${modal_python#\#!}"
  case "$modal_python" in /*/python|/*/python3|/*/python3.*) ;; *)
    echo "Set MODAL_PYTHON to a Python interpreter with the modal package installed" >&2; exit 1 ;;
  esac
fi
exec "$modal_python" "$script_dir/modal-env.py" "$@"
