"""Canonical, strict serialization shared by both SurrealDB stores."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from activegraph.core.event import Event
from activegraph.core.graph import Object, Relation
from activegraph.core.patch import Patch
from activegraph.store.serde import encode_payload


def canonical_json(value: Any) -> str:
    """Encode with ActiveGraph's strict adapters and canonical key order."""

    # Reuse ActiveGraph's released adapter and its NonSerializableEventError.
    adapted = json.loads(encode_payload(value))
    return json.dumps(
        adapted,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def event_to_record(event: Event) -> dict[str, Any]:
    """Convert an event to the provider's detached row representation."""

    return {
        "event_id": event.id,
        "event_type": event.type,
        "payload_json": canonical_json(event.payload),
        "actor": event.actor,
        "frame_id": event.frame_id,
        "caused_by": event.caused_by,
        "timestamp": event.timestamp,
    }


def record_to_event(record: Mapping[str, Any]) -> Event:
    """Decode a provider row without retaining database-owned containers."""

    return Event(
        id=str(record["event_id"]),
        type=str(record["event_type"]),
        payload=json.loads(str(record["payload_json"])),
        actor=_optional_text(record.get("actor")),
        frame_id=_optional_text(record.get("frame_id")),
        caused_by=_optional_text(record.get("caused_by")),
        timestamp=str(record.get("timestamp", "")),
    )


def event_hash(
    scope: str,
    run_id: str,
    seq: int,
    event: Event,
    *,
    previous_hash: str | None,
) -> str:
    """Hash every semantic event field plus its scoped chain position."""

    preimage = {
        "scope": scope,
        "run_id": run_id,
        "seq": seq,
        "id": event.id,
        "type": event.type,
        "payload": event.payload,
        "actor": event.actor,
        "frame_id": event.frame_id,
        "caused_by": event.caused_by,
        "timestamp": event.timestamp,
        "previous_hash": previous_hash,
    }
    return hashlib.sha256(canonical_json(preimage).encode("utf-8")).hexdigest()


def object_to_record(obj: Object) -> dict[str, Any]:
    return {
        "object_id": obj.id,
        "object_type": obj.type,
        "data_json": canonical_json(obj.data),
        "version": obj.version,
        "provenance_json": canonical_json(obj.provenance),
    }


def object_from_record(record: Mapping[str, Any]) -> Object:
    return Object(
        id=str(record["object_id"]),
        type=str(record["object_type"]),
        data=json.loads(str(record["data_json"])),
        version=int(record["version"]),
        provenance=json.loads(str(record["provenance_json"])),
    )


def relation_to_record(relation: Relation) -> dict[str, Any]:
    return {
        "relation_id": relation.id,
        "source": relation.source,
        "target": relation.target,
        "relation_type": relation.type,
        "data_json": canonical_json(relation.data),
        "provenance_json": canonical_json(relation.provenance),
    }


def record_to_relation(record: Mapping[str, Any]) -> Relation:
    return Relation(
        id=str(record["relation_id"]),
        source=str(record["source"]),
        target=str(record["target"]),
        type=str(record["relation_type"]),
        data=json.loads(str(record["data_json"])),
        provenance=json.loads(str(record["provenance_json"])),
    )


def patch_to_record(patch: Patch) -> dict[str, Any]:
    return {
        "patch_id": patch.id,
        "target": patch.target,
        "op": patch.op,
        "value_json": canonical_json(patch.value),
        "expected_version": patch.expected_version,
        "proposed_by": patch.proposed_by,
        "rationale": patch.rationale,
        "evidence_json": canonical_json(patch.evidence),
        "status": patch.status,
        "rejection_reason": patch.rejection_reason,
        "provenance_json": canonical_json(patch.provenance),
    }


def patch_from_record(record: Mapping[str, Any]) -> Patch:
    return Patch(
        id=str(record["patch_id"]),
        target=str(record["target"]),
        op=str(record["op"]),
        value=json.loads(str(record["value_json"])),
        expected_version=int(record["expected_version"]),
        proposed_by=str(record["proposed_by"]),
        rationale=_optional_text(record.get("rationale")),
        evidence=list(json.loads(str(record["evidence_json"]))),
        status=str(record["status"]),
        rejection_reason=_optional_text(record.get("rejection_reason")),
        provenance=json.loads(str(record["provenance_json"])),
    )


def _optional_text(value: Any) -> str | None:
    return None if value is None else str(value)
