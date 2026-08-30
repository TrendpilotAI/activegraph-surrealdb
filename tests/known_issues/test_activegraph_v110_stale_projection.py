from __future__ import annotations

import pytest
from activegraph import Graph, InMemoryGraphStore, Runtime


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Tracked upstream as yoheinakajima/activegraph#82",
)
def test_runtime_load_replaces_stale_projection(tmp_path) -> None:
    database = str(tmp_path / "runs.sqlite")
    source = Runtime(Graph(), persist_to=database)
    expected = source.graph.add_object("person", {"name": "Ada"})

    projection = InMemoryGraphStore()
    seeded = Graph(graph_store=projection)
    ghost = seeded.add_object("ghost", {"name": "stale"})
    loaded = Runtime.load(database, run_id=source.run_id, graph_store=projection)

    assert sorted(item.id for item in loaded.graph.all_objects()) == [expected.id]
    assert loaded.graph.get_object(ghost.id) is None
