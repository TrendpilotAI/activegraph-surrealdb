from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from decimal import Decimal
from threading import Barrier
from typing import Any
from uuid import uuid4

import pytest
from activegraph.core.event import Event
from activegraph.core.graph import Object, Relation
from activegraph.store.serde import NonSerializableEventError
from surrealdb import BlockingSurrealTransaction, RecordID

from activegraph_surrealdb import SurrealEventStore, SurrealGraphStore
from activegraph_surrealdb.errors import ConcurrentWriterError


def _event(event_id: str) -> Event:
    return Event(
        id=event_id,
        type="object.created",
        payload={"id": "person#1", "nullable": None},
        actor="test",
        frame_id=None,
        caused_by=None,
        timestamp="2026-08-30T12:00:00Z",
    )


@pytest.mark.surrealdb
def test_data_survives_close_and_reopen(surreal_config) -> None:
    scope = f"restart_{uuid4().hex}"
    store = SurrealEventStore(surreal_config, run_id="run", scope=scope)
    store.append(_event("evt_1"))
    store.close()

    reopened = SurrealEventStore(surreal_config, run_id="run", scope=scope)
    assert reopened.count() == 1
    assert reopened.get_event("evt_1") == _event("evt_1")
    reopened.close()


@pytest.mark.surrealdb
def test_supported_path_uses_a_websocket_transaction_handle(surreal_config) -> None:
    store = SurrealEventStore(
        surreal_config,
        run_id="run",
        scope=f"transaction_probe_{uuid4().hex}",
    )

    observed: list[BlockingSurrealTransaction] = []

    def inspect_transaction(transaction, session_id, head) -> None:
        del session_id, head
        observed.append(transaction)

    store._test_after_head_read = inspect_transaction
    store.append(_event("evt_transaction"))

    assert len(observed) == 1
    assert isinstance(observed[0], BlockingSurrealTransaction)
    store.close()


@pytest.mark.surrealdb
def test_stale_writer_fails_without_silently_rebasing(surreal_config) -> None:
    scope = f"writers_{uuid4().hex}"
    first = SurrealEventStore(surreal_config, run_id="run", scope=scope)
    second = SurrealEventStore(surreal_config, run_id="run", scope=scope)
    barrier = Barrier(2)
    session_ids: set[object] = set()

    def hold_after_read(transaction, session_id, head) -> None:
        assert isinstance(transaction, BlockingSurrealTransaction)
        assert head["next_seq"] == 0
        session_ids.add(session_id)
        barrier.wait()

    first._test_after_head_read = hold_after_read
    second._test_after_head_read = hold_after_read

    def append(store: SurrealEventStore, event_id: str) -> str:
        try:
            store.append(_event(event_id))
            return "committed"
        except ConcurrentWriterError:
            return "stale"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(
            pool.map(
                lambda pair: append(*pair),
                [(first, "evt_a"), (second, "evt_b")],
            )
        )

    assert outcomes == ["committed", "stale"]
    assert len(session_ids) == 2
    assert first.count() == 1
    verification = first.verify_integrity()
    assert verification.event_count == 1
    assert verification.next_seq == 1
    assert verification.head_hash is not None
    first.close()
    second.close()


@pytest.mark.surrealdb
def test_transaction_failure_rolls_back_event_and_head(surreal_config) -> None:
    store = SurrealEventStore(
        surreal_config,
        run_id="run",
        scope=f"rollback_{uuid4().hex}",
    )
    before = store.verify_integrity()
    staged: dict[str, Any] = {}

    def fail_after_staging(transaction, session_id, run_record, event_record) -> None:
        del session_id
        staged["event"] = transaction.query("SELECT * FROM ONLY $event;", {"event": event_record})
        staged["head"] = transaction.query("SELECT * FROM ONLY $run;", {"run": run_record})
        assert staged["event"]["event_id"] == "evt_rollback"
        assert staged["head"]["next_seq"] == 1
        raise RuntimeError("injected before commit")

    store._test_before_commit = fail_after_staging

    with pytest.raises(RuntimeError, match="injected before commit"):
        store.append(_event("evt_rollback"))

    after = store.verify_integrity()
    assert staged
    assert after == before
    assert store.get_event("evt_rollback") is None
    store.close()


@pytest.mark.surrealdb
def test_different_run_heads_progress_independently(surreal_config) -> None:
    scope = f"independent_{uuid4().hex}"
    barrier = Barrier(2)
    first = SurrealEventStore(surreal_config, run_id="run_a", scope=scope)
    second = SurrealEventStore(surreal_config, run_id="run_b", scope=scope)
    first._test_after_head_read = lambda transaction, session_id, head: barrier.wait()
    second._test_after_head_read = lambda transaction, session_id, head: barrier.wait()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(
                lambda pair: (pair[0].append(_event(pair[1])), "committed")[1],
                [(first, "evt_a"), (second, "evt_b")],
            )
        )

    assert outcomes == ["committed", "committed"]
    assert first.verify_integrity().next_seq == 1
    assert second.verify_integrity().next_seq == 1
    first.close()
    second.close()


@pytest.mark.surrealdb
def test_stale_truncate_fails_and_fresh_handle_can_recover(surreal_config) -> None:
    scope = f"truncate_{uuid4().hex}"
    writer = SurrealEventStore(surreal_config, run_id="run", scope=scope)
    stale = SurrealEventStore(surreal_config, run_id="run", scope=scope)
    writer.append(_event("evt_1"))
    writer.append(_event("evt_2"))

    list(stale.iter_events())
    with pytest.raises(ConcurrentWriterError):
        stale.truncate_after("evt_1")
    with pytest.raises(ConcurrentWriterError):
        stale.append(_event("evt_stale"))

    fresh = SurrealEventStore(surreal_config, run_id="run", scope=scope)
    fresh.truncate_after("evt_1")
    assert [event.id for event in fresh.iter_events()] == ["evt_1"]
    assert fresh.verify_integrity().next_seq == 1
    fresh.append(_event("evt_3"))
    assert [event.id for event in fresh.iter_events()] == ["evt_1", "evt_3"]
    assert fresh.verify_integrity().next_seq == 2

    writer.close()
    stale.close()
    fresh.close()


@pytest.mark.surrealdb
def test_graph_reads_are_shared_across_process_local_store_instances(
    surreal_config,
) -> None:
    scope = f"shared_{uuid4().hex}"
    writer = SurrealGraphStore(surreal_config, run_id="run", scope=scope)
    reader = SurrealGraphStore(surreal_config, run_id="run", scope=scope)
    obj = Object(
        id="person#1",
        type="person",
        data={"display_name": "Ada", "middle_name": None},
        version=1,
        provenance={"event": "evt_1"},
    )

    writer.put_object(obj)

    assert reader.get_object(obj.id) == obj
    writer.close()
    reader.close()


@pytest.mark.surrealdb
def test_relations_are_native_edges_and_allow_dangling_endpoints(
    surreal_config,
) -> None:
    store = SurrealGraphStore(
        surreal_config,
        run_id="run",
        scope=f"edges_{uuid4().hex}",
    )
    relation = Relation(
        id="rel#1",
        source="missing#source",
        target="missing#target",
        type="knows",
        data={"confidence": 0.7},
        provenance={"event": "evt_1"},
    )

    store.put_relation(relation)
    definition = store.raw_query("INFO FOR DB;")
    raw = store.raw_query(
        "SELECT in, out, relation_id FROM ag_relation WHERE scope = $scope AND run_id = $run_id;",
        {"scope": store.scope, "run_id": store.run_id},
    )

    assert store.get_relation("rel#1") == relation
    assert "TYPE RELATION" in str(definition["tables"]["ag_relation"])
    assert len(raw) == 1
    assert isinstance(raw[0]["in"], RecordID)
    assert isinstance(raw[0]["out"], RecordID)
    assert str(raw[0]["in"]).startswith("ag_vertex:")
    assert str(raw[0]["out"]).startswith("ag_vertex:")
    assert raw[0]["relation_id"] == "rel#1"

    source_vertex = store.vertex_record_id("missing#source")
    native_before = store.raw_query(
        "SELECT VALUE ->ag_relation->ag_vertex[WHERE object_present = true] FROM ONLY $source;",
        {"source": source_vertex},
    )
    assert native_before == []

    assert store.neighborhood("missing#source") == ([], [])
    store.put_object(Object("missing#source", "person", {"name": "Ada"}, 1, {}))
    store.put_object(Object("missing#target", "person", {"name": "Charles"}, 1, {}))
    native_after = store.raw_query(
        "SELECT VALUE ->ag_relation->ag_vertex[WHERE object_present = true] FROM ONLY $source;",
        {"source": source_vertex},
    )
    assert native_after == [store.vertex_record_id("missing#target")]
    objects, relations = store.neighborhood("missing#source", depth=1)
    assert sorted(item.id for item in objects) == ["missing#source", "missing#target"]
    assert [item.id for item in relations] == ["rel#1"]
    assert relations[0].data == {"confidence": 0.7}
    store.close()


@pytest.mark.surrealdb
def test_canonical_payload_string_is_persisted_exactly(surreal_config) -> None:
    store = SurrealEventStore(
        surreal_config,
        run_id="run",
        scope=f"json_{uuid4().hex}",
    )
    event = Event(
        id="evt_json",
        type="source.observed",
        payload={
            "unicode": "Zoë — 東京 🚀",
            "nested": {"absent_peer": "present", "explicit_null": None},
            "large_integer": 9007199254740993,
            "float": 1.25,
        },
        actor="test",
        frame_id=None,
        caused_by=None,
        timestamp="2026-08-30T12:00:00Z",
    )
    expected = (
        '{"float":1.25,"large_integer":9007199254740993,'
        '"nested":{"absent_peer":"present","explicit_null":null},'
        '"unicode":"Zoë — 東京 🚀"}'
    )

    store.append(event)
    rows = store.raw_query(
        "SELECT VALUE payload_json FROM ag_event "
        "WHERE scope = $scope AND run_id = $run_id AND event_id = $event_id;",
        {"scope": store.scope, "run_id": store.run_id, "event_id": event.id},
    )
    store.close()

    reopened = SurrealEventStore(
        surreal_config,
        run_id="run",
        scope=store.scope,
    )
    assert rows == [expected]
    assert reopened.get_event(event.id) == event
    assert reopened.raw_query(
        "SELECT VALUE payload_json FROM ag_event "
        "WHERE scope = $scope AND run_id = $run_id AND event_id = $event_id;",
        {"scope": reopened.scope, "run_id": reopened.run_id, "event_id": event.id},
    ) == [expected]
    reopened.close()


@pytest.mark.surrealdb
def test_activegraph_payload_adapters_round_trip_and_bad_values_mutate_nothing(
    surreal_config,
) -> None:
    store = SurrealEventStore(
        surreal_config,
        run_id="run",
        scope=f"adapted_{uuid4().hex}",
    )
    adapted = Event(
        id="evt_adapted",
        type="payload.adapted",
        payload={
            "decimal": Decimal("4.200"),
            "date": date(2026, 8, 30),
            "datetime": datetime(2026, 8, 30, 12, tzinfo=UTC),
            "set": {"b", "a"},
            "nested": {"null": None},
        },
        actor="test",
        frame_id=None,
        caused_by=None,
        timestamp="2026-08-30T12:00:00Z",
    )
    store.append(adapted)
    before_bad = store.verify_integrity()

    class Unsupported:
        pass

    bad = Event(
        id="evt_bad",
        type="payload.bad",
        payload={"bad": Unsupported()},
        actor="test",
        frame_id=None,
        caused_by=None,
        timestamp="2026-08-30T12:01:00Z",
    )
    with pytest.raises(NonSerializableEventError):
        store.append(bad)
    assert store.verify_integrity() == before_bad
    assert store.get_event("evt_bad") is None
    store.close()

    reopened = SurrealEventStore(
        surreal_config,
        run_id="run",
        scope=store.scope,
    )
    persisted = reopened.get_event("evt_adapted")
    assert persisted is not None
    assert persisted.payload == {
        "date": "2026-08-30",
        "datetime": "2026-08-30T12:00:00+00:00",
        "decimal": "4.200",
        "nested": {"null": None},
        "set": ["a", "b"],
    }
    reopened.close()
