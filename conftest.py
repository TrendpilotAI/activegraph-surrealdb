"""Test-session configuration. Telemetry is optional and off by default."""

from __future__ import annotations

import sys
from pathlib import Path

# Run the suite against this checkout's provider source, ahead of any
# editable install that may point elsewhere (shared virtualenvs do).
_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

pytest_plugins = ["tests.telemetry.arize_ax"]
