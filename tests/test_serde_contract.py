from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from activegraph.core.event import Event
from activegraph.core.graph import Object, Relation
from activegraph.core.patch import Patch
from activegraph.store.serde import NonSerializableEventError

from activegraph_surrealdb.serde import (
    canonical_json,
    event_hash,
    event_to_record,
    object_from_record,
    object_to_record,
    patch_from_record,
    patch_to_record,
    record_to_event,
    record_to_relation,
    relation_to_record,
)


def test_event_round_trip_preserves_explicit_null_unicode_and_nested_values() -> None:
    event = Event(
        id="evt:001",
        type="source.observed",
        payload={
            "name": "Zoë — 東京 🚀",
            "middle_name": None,
            "nested": {"present_null": None, "items": [1, None, {"ok": True}]},
        },
        actor="connector:test",
        frame_id=None,
        caused_by="evt:000",
        timestamp="2026-08-30T12:00:00Z",
    )

    encoded = event_to_record(event)
    decoded = record_to_event(encoded)

    assert decoded == event
    assert '"middle_name":null' in encoded["payload_json"]
    assert '"present_null":null' in encoded["payload_json"]


def test_hash_chain_is_stable_and_bound_to_run_sequence_and_previous_hash() -> None:
    event = Event(
        id="evt_1",
        type="object.created",
        payload={"id": "person#1"},
        actor="test",
        frame_id=None,
        caused_by=None,
        timestamp="2026-08-30T12:00:00Z",
    )

    first = event_hash("scope", "run", 0, event, previous_hash=None)

    assert first == event_hash("scope", "run", 0, event, previous_hash=None)
    assert first != event_hash("scope", "run", 1, event, previous_hash=None)
    assert first != event_hash("scope", "other", 0, event, previous_hash=None)
    assert first != event_hash("scope", "run", 0, event, previous_hash="0" * 64)


def test_hash_changes_for_every_semantic_event_field_but_not_dict_key_order() -> None:
    base = Event(
        id="evt_1",
        type="object.created",
        payload={"alpha": 1, "nested": {"x": 2, "y": 3}},
        actor="test",
        frame_id="frame_1",
        caused_by="evt_0",
        timestamp="2026-08-30T12:00:00Z",
    )
    baseline = event_hash("scope", "run", 7, base, previous_hash="a" * 64)
    reordered = Event(
        id=base.id,
        type=base.type,
        payload={"nested": {"y": 3, "x": 2}, "alpha": 1},
        actor=base.actor,
        frame_id=base.frame_id,
        caused_by=base.caused_by,
        timestamp=base.timestamp,
    )

    assert event_hash("scope", "run", 7, reordered, previous_hash="a" * 64) == baseline

    variants = [
        Event(
            "evt_2",
            base.type,
            base.payload,
            base.actor,
            base.frame_id,
            base.caused_by,
            base.timestamp,
        ),
        Event(
            base.id,
            "object.updated",
            base.payload,
            base.actor,
            base.frame_id,
            base.caused_by,
            base.timestamp,
        ),
        Event(
            base.id,
            base.type,
            {"alpha": 2},
            base.actor,
            base.frame_id,
            base.caused_by,
            base.timestamp,
        ),
        Event(
            base.id, base.type, base.payload, "other", base.frame_id, base.caused_by, base.timestamp
        ),
        Event(
            base.id, base.type, base.payload, base.actor, "frame_2", base.caused_by, base.timestamp
        ),
        Event(base.id, base.type, base.payload, base.actor, base.frame_id, "evt_x", base.timestamp),
        Event(
            base.id,
            base.type,
            base.payload,
            base.actor,
            base.frame_id,
            base.caused_by,
            "2026-08-30T12:00:01Z",
        ),
    ]
    for variant in variants:
        assert event_hash("scope", "run", 7, variant, previous_hash="a" * 64) != baseline
    assert event_hash("other", "run", 7, base, previous_hash="a" * 64) != baseline
    assert event_hash("scope", "other", 7, base, previous_hash="a" * 64) != baseline
    assert event_hash("scope", "run", 8, base, previous_hash="a" * 64) != baseline
    assert event_hash("scope", "run", 7, base, previous_hash="b" * 64) != baseline


def test_canonical_json_and_hash_have_literal_golden_values() -> None:
    event = Event(
        id="evt_1",
        type="object.created",
        payload={"nullable": None, "a": 1},
        actor="test",
        frame_id=None,
        caused_by=None,
        timestamp="2026-08-30T12:00:00Z",
    )

    assert canonical_json(event.payload) == '{"a":1,"nullable":null}'
    assert event_hash("scope", "run", 0, event, previous_hash=None) == (
        "fefa98758f0ff3290703d2f551e02bbae4013c0ca935d876ee0add4e55f5e55b"
    )


def test_activegraph_json_adapters_are_preserved_in_canonical_form() -> None:
    payload = {
        "decimal": Decimal("123.4500"),
        "date": date(2026, 8, 30),
        "datetime": datetime(2026, 8, 30, 12, 30, tzinfo=UTC),
        "set": {"b", "a"},
        "frozen": frozenset({3, 1, 2}),
        "nested": {"nullable": None},
    }

    assert canonical_json(payload) == (
        '{"date":"2026-08-30","datetime":"2026-08-30T12:30:00+00:00",'
        '"decimal":"123.4500","frozen":[1,2,3],"nested":{"nullable":null},'
        '"set":["a","b"]}'
    )

    event = Event(
        id="evt_adapted",
        type="payload.adapted",
        payload=payload,
        actor="test",
        frame_id=None,
        caused_by=None,
        timestamp="2026-08-30T12:00:00Z",
    )
    decoded = record_to_event(event_to_record(event))
    assert decoded.payload == {
        "date": "2026-08-30",
        "datetime": "2026-08-30T12:30:00+00:00",
        "decimal": "123.4500",
        "frozen": [1, 2, 3],
        "nested": {"nullable": None},
        "set": ["a", "b"],
    }


def test_unsupported_payload_value_fails_instead_of_being_stringified() -> None:
    class Unsupported:
        pass

    with pytest.raises(NonSerializableEventError):
        canonical_json({"nested": {"bad": Unsupported()}})

    with pytest.raises(NonSerializableEventError):
        event_to_record(
            Event(
                id="evt_bad",
                type="payload.bad",
                payload={"nested": {"bad": Unsupported()}},
                actor="test",
                frame_id=None,
                caused_by=None,
                timestamp="2026-08-30T12:00:00Z",
            )
        )


def test_projection_entities_round_trip_without_losing_nulls() -> None:
    obj = Object(
        id="person#1",
        type="person",
        data={"display_name": "Ada", "nickname": None},
        version=3,
        provenance={"source": "crm", "note": None},
    )
    relation = Relation(
        id="rel#1",
        source="person#1",
        target="company#1",
        type="works_at",
        data={"title": None},
        provenance={"event": "evt_1"},
    )
    patch = Patch(
        id="patch#1",
        target="person#1",
        op="update",
        value={"display_name": "Ada L.", "nickname": None},
        expected_version=3,
        proposed_by="agent",
        rationale="new evidence",
        evidence=["evt_2"],
        status="proposed",
        provenance={"source": None},
    )

    assert object_from_record(object_to_record(obj)) == obj
    assert record_to_relation(relation_to_record(relation)) == relation
    assert patch_from_record(patch_to_record(patch)) == patch
