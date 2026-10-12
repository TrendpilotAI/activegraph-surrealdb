"""The projection returns objects in the reference store's insertion order.

ActiveGraph's in-memory projection yields objects in insertion order, and object
IDs come from one monotonic per-graph counter (``task#1``, ``claim#2``, ...).
The SurrealDB projection persists that counter (``ag_vertex.object_seq``) so
reads return the same order instead of sorting opaque IDs lexicographically —
which would put ``#10`` before ``#2`` and reorder across object types.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from activegraph.core.graph import Object

from activegraph_surrealdb import SurrealConfig, SurrealGraphStore

pytestmark = pytest.mark.surrealdb

# Deliberately interleaves types and crosses the single-digit boundary so a
# lexicographic sort of the logical IDs cannot accidentally match.
_INSERTION = [
    ("task", 1),
    ("claim", 2),
    ("task", 3),
    ("claim", 4),
    ("task", 5),
    ("task", 10),
    ("claim", 11),
    ("task", 12),
]
_EXPECTED = [f"{type_}#{n}" for type_, n in _INSERTION]


def _fresh_config() -> SurrealConfig:
    return SurrealConfig(
        endpoint=os.environ["ACTIVEGRAPH_SURREALDB_TEST_URL"],
        namespace=os.environ.get("ACTIVEGRAPH_SURREALDB_TEST_NAMESPACE", "activegraph"),
        database=f"projection_order_{uuid4().hex}",
        username=os.environ.get("ACTIVEGRAPH_SURREALDB_TEST_USERNAME", "root"),
        password=os.environ.get("ACTIVEGRAPH_SURREALDB_TEST_PASSWORD", "root"),
    )


def _object(object_id: str, type_: str) -> Object:
    return Object(id=object_id, type=type_, data={"id": object_id}, version=1, provenance={})


def _seed(store: SurrealGraphStore) -> None:
    for type_, n in _INSERTION:
        store.put_object(_object(f"{type_}#{n}", type_))


def test_all_objects_preserve_insertion_order() -> None:
    store = SurrealGraphStore(_fresh_config(), run_id="run", scope="scope")
    try:
        _seed(store)
        assert [o.id for o in store.all_objects()] == _EXPECTED
    finally:
        store.close()


def test_find_objects_preserve_insertion_order_within_type() -> None:
    store = SurrealGraphStore(_fresh_config(), run_id="run", scope="scope")
    try:
        _seed(store)
        assert [o.id for o in store.find_objects("task")] == [
            "task#1",
            "task#3",
            "task#5",
            "task#10",
            "task#12",
        ]
        assert [o.id for o in store.find_objects_in_types(["claim", "task"])] == _EXPECTED
    finally:
        store.close()


def test_order_is_stable_across_reopen_and_replay() -> None:
    config = _fresh_config()
    store = SurrealGraphStore(config, run_id="run", scope="scope")
    try:
        _seed(store)
    finally:
        store.close()

    # A fresh handle replaying the same objects (the ``Runtime.load`` shape)
    # must reproduce the original order, not a re-sorted one.
    reopened = SurrealGraphStore(config, run_id="run", scope="scope")
    try:
        _seed(reopened)
        assert [o.id for o in reopened.all_objects()] == _EXPECTED
    finally:
        reopened.close()
