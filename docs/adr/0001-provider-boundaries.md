# ADR 0001: Keep the event log authoritative and the graph disposable

- Status: accepted for the research preview
- Date: 2026-08-30
- Decision owners: TrendpilotAI
- Upstream baseline: ActiveGraph `v1.10.0` (`148e12c2969f18fa12a1a3c2e75f3affd9aa0616`)

## Context

ActiveGraph deliberately separates two jobs:

1. the `EventStore` preserves what happened; and
2. the `GraphStore` answers questions about the current projected state.

SurrealDB can host both jobs, plus application documents and native graph
relations, in one deployment. That does not make the two jobs interchangeable.
If a projection is lost, replay must rebuild it. If the event log is lost, the
story is lost.

An initial application prototype demonstrated a promising co-located shape,
but it was application-specific and did not satisfy the complete released
ActiveGraph store contracts. Those choices are design evidence, not a general
provider implementation.

## Decision

This package must expose two independent, manually constructed provider seams:

- `SurrealEventStore`: a per-scope, per-run ActiveGraph event log that must pass
  the released `EventStoreConformance` suite.
- `SurrealGraphStore`: a per-scope, per-run current-state projection that must
  pass `GraphStoreConformance`, perform reads in SurrealDB, and store edges in
  a fixed `TYPE RELATION` table.

Both use one schema prefix and can share one SurrealDB namespace/database, but
the event log remains authoritative and the graph remains rebuildable.

The provider must also offer explicit helpers for replay and prefix-copy forks.
It does not patch `Runtime.load()`, register a URL scheme, or claim full runtime
resume. Those require upstream lifecycle and driver-registry contracts.

## Invariants

- A logical event ID is unique within a scope and run, but may be reused by a
  forked run.
- Event order is gap-free and protected by a written per-run head record.
- A stale writer fails closed; it never silently rebases on a newer head.
- Exact JSON is retained for replay, including explicit `null` values.
- Graph reads come from SurrealDB, not an in-process mirror.
- Relations are native edges with `in` and `out` record endpoints. A separate
  vertex table permits ActiveGraph's valid dangling-edge semantics.
- Schema migration is explicit, versioned, additive, and fail-closed on an
  unknown version.
- Credentials never enter record IDs, errors, reprs, events, or traces.
- Replay owns an isolated provider projection, marks it incomplete, clears it,
  applies history, and marks it ready only after successful completion.
- A failed rebuild stays explicitly incomplete and must not be served as a
  valid current-state projection.
- Application contacts, auth, CRM documents, source records, and identity
  ontologies are outside the provider schema.

## Atomicity boundary

ActiveGraph v1.10 projects an event before calling `EventStore.append()`. A
provider cannot atomically combine the two writes without depending on that
private order. Pending PR #78 reverses the order, which is safer for an
authoritative log but still does not expose a shared transaction coordinator.

Therefore the research preview does not advertise atomic event-plus-projection
acceptance. A failed live write may require replay to repair the projection.
The proposed future capability is an optional runtime-owned coordinator whose
database-specific transaction implementation remains in this package.

## Consequences

Positive:

- One SurrealDB deployment can serve event, graph, and application workloads
  without collapsing their authority boundaries.
- The resulting adapter can be reused across application domains.
- Native edges and query pushdown can be measured against FalkorDB instead of
  inferred from a write-through memory cache.
- Known upstream gaps stay visible rather than being hidden in copied private
  runtime code.

Negative:

- Applications must wire stores manually on ActiveGraph v1.10.
- Replay is the recovery mechanism for cross-store partial failure.
- Full fork/resume behavior is a provider helper rather than an upstream URL
  driver until ActiveGraph accepts a store-neutral lifecycle contract.
- A single-node RocksDB server is not distributed high availability.

## Promotion gates

The project must not call itself production-ready until all of these are true:

1. Full released EventStore and GraphStore conformance pass against a pinned
   stable SurrealDB server.
2. Restart, two-session stale-writer, fork/replay/diff, exact-JSON, native-edge,
   and run-isolation tests pass.
3. A supported-version upgrade test proves an existing database survives.
4. Backup and restore are exercised, not merely documented.
5. Upstream contracts exist for store-neutral resume and optional atomic
   coordination, or the limitations remain explicit to every caller.

## Replay and compaction boundary

The preview replay and fork helpers operate only on uncompacted event logs.
They fail closed when the first event is `runtime.snapshot` or when a requested
fork point is outside the available prefix. Snapshot materialization, retained
horizons, behavior re-queuing, approvals, caches, and full `Runtime.load()`
resume semantics remain upstream lifecycle work.
