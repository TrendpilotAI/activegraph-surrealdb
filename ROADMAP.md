# Roadmap

This roadmap describes evidence gates, not release dates. Work moves forward
only when the previous boundary is repeatably demonstrated.

## Phase 0 — Contract-first research preview

- [x] Separate authoritative event and disposable graph responsibilities.
- [x] Pin ActiveGraph `1.12.0`, SurrealDB `3.3.0`, and Python SDK `2.0.0` lanes.
- [x] Define serialization, isolation, replay, fork/diff, and qualification contracts.
- [x] Record upstream lifecycle limitations without private runtime patches.

## Phase 1 — Generic provider implementation

- [x] Pass unit serialization/configuration contracts.
- [x] Pass released EventStore and GraphStore conformance.
- [x] Implement transactional append/head checks and deterministic stale-writer tests.
- [x] Implement native relation storage without an in-memory mirror.
- [x] Push object, relation, existence, and neighbourhood queries into native SurrealQL; `match_chain` composes those primitives.
- [x] Implement fail-closed replay readiness and uncompacted prefix forks.
- [x] Load the published pack library onto one SurrealDB-backed Runtime.
- [x] Publish reproducible fork/diff/replay trace evidence.

## Phase 2 — Stable qualification

- [x] Pass live isolation, rollback, native-edge, and exact-JSON tests.
- [ ] Pass pinned RocksDB graceful restart and abrupt container termination tests.
- [x] Add an in-place, data-preserving schema upgrade lane and reject a newer schema.
- [x] Add a schema rollback lane before changing support.
- [x] Exercise backup and clean-environment restore, then verify event integrity and replay.
- [ ] Record bounded performance observations on controlled, representative data.

## Phase 3 — Upstream lifecycle alignment

- [x] Re-evaluate append/projection ordering after ActiveGraph 1.12 incorporated it
  (CONTRACT v1.11 #2); see
  [ADR 0003](docs/adr/0003-latest-stable-pins-and-exploration-lanes.md).
- [ ] Adopt a public store-driver/registry contract if upstream accepts one.
- [ ] Adopt store-neutral load/resume without phantom-run or stale-projection behavior.
- [ ] Design snapshot/compaction hydration only against an explicit upstream contract.
- [ ] Evaluate an optional runtime-owned cross-store transaction coordinator.

## Phase 4 — Production evaluation

- [ ] Qualify `wss://`, certificate validation, least-privilege identities, and rotation.
- [ ] Define monitoring and operator response for failed/incomplete projections.
- [ ] Document data retention, restore objectives, and incident response.
- [ ] Decide whether single-node RocksDB meets availability requirements or a different
  deployment architecture is necessary.
- [ ] Complete an independent security and operational review.

## Continuing non-goals

- claiming cross-store atomicity without an upstream coordinator;
- hiding upstream gaps behind copied private runtime code;
- managing application contacts, auth, CRM records, or identity ontologies;
- qualifying every SurrealDB transport, engine, cloud, or prerelease; and
- calling the preview production-ready based only on CI.
