"""Lane D (embedded engine) evidence for ADR 0003: transactions are refused.

Non-gating exploration-lane evidence, selected only with ``-m embedded``
(ladder step V8). The embedded engine ships as the ``surrealdb_embedded``
extension, so this module skips wherever that extension is absent — including
the default development venv, whose ``surrealdb==2.0.0`` pin has no embedded
engine at all. Ladder step V8 runs it from the isolated ``.venv-embedded``
holding ``surrealdb[embedded]==3.0.0b10``.

``attach``, ``begin``, ``commit``, and ``cancel`` raising
``UnsupportedFeatureError`` is the expected rejection evidence recorded by
ADR 0003: multi-session and client-side transactions exist only on WebSocket
connections.
"""

from __future__ import annotations

import pytest

pytest.importorskip("surrealdb_embedded")

from surrealdb import Surreal  # noqa: E402
from surrealdb.errors import UnsupportedFeatureError  # noqa: E402

_REFUSAL = "only supported for WebSocket connections"


def _assert_transactions_are_refused(connection) -> None:
    with pytest.raises(UnsupportedFeatureError, match=_REFUSAL):
        connection.attach()
    with pytest.raises(UnsupportedFeatureError, match=_REFUSAL):
        connection.begin()
    with pytest.raises(UnsupportedFeatureError, match=_REFUSAL):
        connection.commit("unused-transaction")
    with pytest.raises(UnsupportedFeatureError, match=_REFUSAL):
        connection.cancel("unused-transaction")


@pytest.mark.embedded
def test_memory_engine_refuses_client_side_transactions() -> None:
    with Surreal("mem://") as connection:
        connection.use("embedded", "embedded")
        _assert_transactions_are_refused(connection)


@pytest.mark.embedded
def test_surrealkv_engine_refuses_client_side_transactions(tmp_path) -> None:
    with Surreal(f"surrealkv://{tmp_path}/embedded-engine.skv") as connection:
        connection.use("embedded", "embedded")
        _assert_transactions_are_refused(connection)
