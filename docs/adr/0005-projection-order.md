# ADR 0005: Return projected objects in the reference store's insertion order

- Status: accepted for the research preview
- Date: 2026-10-12
- Decision owners: TrendpilotAI
- Upstream baseline: ActiveGraph `v1.12.0` (`50d8405923b79e4e823a73a3045bb969436a872c`)

## Context

ActiveGraph's `GraphStore.all_objects` contract says only that it returns every
object and that "order is unspecified". The reference `InMemoryGraphStore`
happens to return objects in insertion order, and the runtime leans on that
observable behavior in practice: object IDs come from one monotonic per-graph
counter (`task#1`, `claim#2`, ...), and code such as the published pack
fixtures treats `graph.objects(type=...)[-1]` as "the most recently created
object of this type".

The first SurrealDB projection ordered reads by `object_id ASC`. Because the
logical IDs are strings, that is a lexicographic sort: it puts `comm_intent#10`
before `comm_intent#2`, and it orders across types by type name rather than by
creation. The data was correct, but the returned order was not the order the
in-memory reference exposes, so behavior that depended on "the last object"
diverged.

## Decision

Persist the framework's object counter and order projection reads by it.

- Schema v3 adds `ag_vertex.object_seq` (int) and the
  `(scope, run_id, object_seq)` index.
- `SurrealGraphStore.put_object` records `object_seq` by recovering the numeric
  suffix of the logical ID (`type#n`), which is exactly the global creation
  counter. Re-writing an object (a patch, a replay) recomputes the same value,
  so first-insert order is stable.
- `all_objects`, `find_objects`, `find_objects_in_types`, and the logical-ID
  lookup order by `object_seq ASC, object_id ASC`.
- Placeholder rows (relation endpoints, removed objects) carry the same derived
  value, so stubbing or removing an object never reorders the survivors.

This stays inside the released contract: "unspecified" permits insertion order,
and matching the reference store is what a caller porting from memory expects.
It is pinned by `tests/integration/test_projection_order.py`, which interleaves
types and crosses the `#10` boundary so a lexicographic sort cannot pass by
accident.

## Consequences

- Objects read back in the same order the in-memory store would return them,
  including after a reopen or a `Runtime.load` replay.
- IDs that are not of the form `type#n` fall back to `object_seq = 0` and
  tie-break on the logical ID. The provider does not create such IDs; only a
  caller writing a `GraphStore` directly could.
- Relation IDs (`rel_001`, zero-padded) already sort in creation order for the
  scale the preview targets, so relations keep ordering by `relation_id`. A
  relation-ordering field can be added the same way if a future workload needs
  it past the zero-padding boundary.

## Alternatives considered

- **Sort numerically in the query** (`type::int(string::split(object_id, '#')[1])`).
  Rejected: it makes every read depend on the ID format, cannot use an index,
  and fails on non-framework IDs.
- **Emulate Python object identity** so `add_object()` returns the same instance
  that later patches mutate. Rejected: it requires an in-process mirror of the
  whole projection, which defeats the point of an external store, and it is not
  something any out-of-process backend can provide.
- **Leave the order unspecified and document it.** Rejected: it makes portable
  code that runs on the reference store fail only on this backend, for a reason
  (string sorting) that is an artifact rather than a design choice.
