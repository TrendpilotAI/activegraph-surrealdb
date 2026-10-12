"""Test-session configuration. Telemetry is optional and off by default."""

from __future__ import annotations

pytest_plugins = ["tests.telemetry.arize_ax"]
