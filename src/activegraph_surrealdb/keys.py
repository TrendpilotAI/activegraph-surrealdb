"""Opaque deterministic record keys for scoped provider data."""

from __future__ import annotations

import hashlib


def record_key(*parts: str) -> str:
    """Hash length-delimited parts without exposing tenant or logical IDs."""

    digest = hashlib.sha256()
    for part in parts:
        encoded = str(part).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()
