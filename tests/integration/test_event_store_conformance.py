from __future__ import annotations

from uuid import uuid4

import pytest
from activegraph.store.conformance import EventStoreConformance

from activegraph_surrealdb import SurrealEventStore
from tests.integration.conftest import config_from_env


@pytest.mark.surrealdb
class TestSurrealEventStoreConformance(EventStoreConformance):
    __test__ = True

    def setup_method(self) -> None:
        self._store: SurrealEventStore | None = None
        self._scope = f"conformance_{uuid4().hex}"

    def make_store(self, run_id: str) -> SurrealEventStore:
        self._store = SurrealEventStore(config_from_env(), run_id=run_id, scope=self._scope)
        return self._store

    def cleanup(self) -> None:
        if self._store is not None:
            self._store.close()
