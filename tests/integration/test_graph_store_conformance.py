from __future__ import annotations

from uuid import uuid4

import pytest
from activegraph.store.graph_conformance import GraphStoreConformance

from activegraph_surrealdb import SurrealGraphStore
from tests.integration.conftest import config_from_env


@pytest.mark.surrealdb
class TestSurrealGraphStoreConformance(GraphStoreConformance):
    __test__ = True

    def setup_method(self) -> None:
        self._store: SurrealGraphStore | None = None

    def make_store(self) -> SurrealGraphStore:
        self._store = SurrealGraphStore(
            config_from_env(),
            run_id=f"graph_{uuid4().hex}",
            scope=f"conformance_{uuid4().hex}",
        )
        return self._store

    def cleanup(self) -> None:
        if self._store is not None:
            self._store.close()
