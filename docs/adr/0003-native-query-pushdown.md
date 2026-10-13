# ADR 0003: Push the graph query hooks into SurrealQL

- Status: accepted for the research preview
- Date: 2026-10-12
- Decision owners: TrendpilotAI
- Upstream baseline: ActiveGraph `v1.12.0` (`50d8405923b79e4e823a73a3045bb969436a872c`)

## Context

ActiveGraph `1.12` added `GraphStore.query_objects` and kept the earlier
optional hooks (`find_objects`, `find_objects_in_types`, `find_relations`,
`neighborhood`, `match_chain`). Every hook has a working Python default that
scans `all_objects` / `all_relations`, so a backend that overrides nothing is
still correct — it simply does all the work in the client process.

A provider that stores the projection in SurrealDB but never overrides these
hooks leaves the database's graph and indexing capabilities unused. Every pack
read then transfers the whole scope/run graph into Python and filters there.

## Decision

`SurrealGraphStore` overrides the structural hooks with bound SurrealQL:

- `query_objects` consumes the object type, pushes an optional `LIMIT` when no
  residual predicate remains, and answers `result_mode="exists"` with a
  `LIMIT 1` probe. It returns the plan's `where` **unchanged** as the residual,
  because the released contract forbids a backend from claiming a predicate
  clause it cannot prove equivalent to the canonical Python evaluator.
- `find_objects`, `find_objects_in_types`, and `find_relations` filter by type,
  source, and target inside SurrealDB.
- `neighborhood` performs the undirected BFS one frontier at a time, querying
  only the relations incident to the current frontier. It reproduces the base
  class's level-by-level semantics exactly, including placeholder endpoints and
  cycles, without loading unreachable relations.
- `match_chain` is intentionally left to the base class so it composes the
  pushed-down primitives hop by hop. Re-implementing homomorphic chain matching
  (node and relation reuse, self-loops) as a single graph query would risk
  silently changing semantics the conformance suite pins.

Schema v2 adds the `(scope, run_id, ...)` indexes these queries use. The schema
upgrade is additive and in place; a database written by a newer provider is
rejected rather than mis-read.

## Consequences

Positive:

- Pack reads and behaviour reactions touch only the objects and relations they
  actually need, rather than the whole scope/run graph.
- The pushed-down hooks are the same primitives `match_chain` already used, so
  the override improves traversal without a second code path.
- The residual-predicate contract keeps `where` semantics owned by ActiveGraph,
  so the provider never disagrees with the canonical evaluator.

Negative:

- The provider now depends on the `1.12` `ObjectQuery` / `ObjectQueryResult`
  surface, so the `1.12.0` pin is load-bearing.
- The pushdown must track any future change to the base-class hook semantics;
  the conformance suite and `test_graph_pushdown.py` are the guard.

## Verification

`tests/integration/test_graph_pushdown.py` disables `all_objects` and
`all_relations` and then exercises every overridden hook. A regression to the
Python fallback raises instead of returning rows. The released
`GraphStoreConformance` suite runs unchanged against the same store, so the
pushed-down behavior is pinned to the canonical semantics.

## Evidence

Accepted on the strength of the 2026-10-13 evidence-ladder runs (SurrealDB
`3.2.4`, native server lane per the recorded sandbox deviation, `agvm-8ii`):

- `pytest -m surrealdb tests/integration` — 74 passed on server-side RocksDB
  (`ws://`) and again over the TLS lane (`wss://` with enforced certificate
  verification), including `test_graph_pushdown.py` with the Python fallback
  disabled.
- 74 passed on a dedicated SurrealKV volume (V7 lane, beta evidence).
- Unit/contract ladder: 28 passed, 2 xfail (upstream #81/#82).

The load-bearing `1.12.0` pin and the residual-predicate contract are re-proven
on every one of those runs by the released conformance suites.
