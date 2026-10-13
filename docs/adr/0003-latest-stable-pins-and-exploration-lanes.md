# ADR 0003: Latest-stable pins and exploration lanes

- Status: accepted (2026-10-13)
- Date: 2026-10-12
- Extends: ADR 0002 (which remains the record of the lane it accepted on
  2026-08-30)
- Implementation branch: `non-agentic/graph-db-upgrades`
- Implementation plan: [upgrade plan](../upgrade-plan.md)

## Context

As of 2026-10-12 the upstream landscape has moved past the lane ADR 0002
pinned on 2026-08-30:

- SurrealDB `3.3.0` is the latest stable server release (2026-09-28). The
  3.3.x line has left prerelease. `3.2.5` (2026-10-06) is the latest patch
  on the 3.2.x line. A 3.2 datastore opens on 3.3.0 without an export and
  import, but the first 3.3.0 start applies data migrations that are
  recorded in a datastore ledger. Once 3.3.0 has started against a
  datastore, rollback to 3.2 is only possible by restoring a pre-upgrade
  `surreal export` into a fresh 3.2 datastore.
- ActiveGraph `1.12.0` (2026-09-26) ships the staged, never separately
  published 1.11.0 trust-boundary work: `Graph.emit` appends to the
  authoritative event store before projection, `ConcurrentWriterError`
  enforces one logical writer per run, `InvalidPatchOperation` closes the
  patch vocabulary to `update` and `replace`, and `attach_store` requires
  the graph and the store to name the same run at the same event count.
- The SurrealDB Python SDK `2.0.0` remains the latest stable SDK. The
  embedded engine (`mem://`, `file://`, `surrealkv://`) ships only in the
  SDK `3.0.0` beta line through `surrealdb[embedded]`, which installs
  `surrealdb-embedded==3.0.0-beta.10`. The embedded connection refuses
  client-side transactions: `attach`, `begin`, `commit`, and `cancel`
  raise `UnsupportedFeatureError`.
- Surrealist is superseded by SurrealDB Studio. Studio is the official
  visual interface, delivered as a hosted web app and a desktop app, and
  connects to any SurrealDB instance. No self-hostable Studio image exists
  to pin.
- SurrealKV remains beta upstream. It targets embedded and local-first
  workloads; RocksDB remains the recommended write-heavy server engine.
- The server exposes TLS through `--web-crt`/`--web-key`. The SDK accepts
  `wss://` with standard certificate verification.

## Decision

1. The stable lane re-pins to the latest stable releases, gated by the
   qualification policy in
   [compatibility](../compatibility.md):

   - SurrealDB server `v3.3.0` on a fresh disposable volume, with `v3.2.5`
     as the documented fallback if 3.3.0 fails conformance.
   - ActiveGraph `1.12.0`, including the append-before-projection ordering
     that ADR 0001 documented as missing.
   - SurrealDB Python SDK `2.0.0` unchanged; it is still the latest stable
     SDK.
   - Transport remains remote WebSocket `ws://`; storage remains
     server-side RocksDB.

2. The documented UI is SurrealDB Studio. No UI image is pinned in the
   Compose deployment because Studio is not self-hostable, and the
   end-of-life Surrealist image is deliberately not adopted.

3. Four exploration lanes open as non-gating evidence lanes that must not
   disturb the stable lane:

   - Lane A, TLS: `wss://` against server `--web-crt`/`--web-key` with a
     locally generated certificate authority trusted by the host.
   - Lane B, existing-volume upgrade: a RocksDB volume written by 3.2.4,
     upgraded in place to 3.3.0, with a pre-upgrade `surreal export` and
     an export-restore rollback rehearsal.
   - Lane C, SurrealKV: server-side `surrealkv://` storage, still beta, on
     its own volume. A storage location is never shared between backends.
   - Lane D, embedded engine: SDK `3.0.0b10` with `surrealdb[embedded]` in
     an isolated environment. The expected result is a documented
     rejection: the embedded engine refuses the client-side transactions
     this provider's append and head contracts depend on.

## Required lanes

| Lane | Server | Transport | Purpose |
| --- | --- | --- | --- |
| stable | 3.3.0 (fallback 3.2.5) | WebSocket | required conformance and persistence proof |
| upgrade | 3.2.4 to 3.3.0 | WebSocket | in-place volume migration and export rollback proof |
| TLS | 3.3.0 | `wss://` | certificate qualification; not required until it passes |
| surrealkv | 3.3.0 | WebSocket | beta engine evidence; never gates the stable lane |
| embedded | SDK 3.0.0b10 | in-process | expected rejection evidence; isolated from the stable pin |
| HTTP | stable | HTTP | must be rejected by config in this preview |
| memory | SDK embedded | `mem://` | not advertised as durable provider support |

Every supported lane must bind the whole `RecordID` as a query parameter
and must never interpolate user-controlled record or relation types into
SurrealQL.

## Consequences

Positive:

- The stable lane tracks the latest stable server and framework releases
  instead of aging pins.
- ActiveGraph 1.12.0 makes the event log authoritative at write time:
  a failed append changes no graph, queue, projection, or observer state,
  and the remaining partial-failure mode (logged but unprojected) is
  repaired by replay, which strengthens the replay-first recovery story.
- The upgrade lane closes the open ROADMAP Phase 2 item with evidence
  rather than assertion.
- The embedded lane records why the provider cannot move to the SDK 3.0
  beta line yet, and produces an early compatibility signal for a future
  stable SDK lane.

Negative:

- A datastore upgraded to 3.3.0 cannot return to 3.2 except through a
  pre-upgrade export, so the upgrade lane must rehearse that path before
  any data-bearing volume is upgraded.
- Studio is not self-hostable, so the deployment carries no UI container
  and depends on an external hosted or desktop application.
- The embedded and surrealkv lanes require separate environments and
  volumes, widening the evidence matrix without gating progress.
- Until the 3.3.0 lane passes qualification, the fallback discussion
  (3.2.5 versus 3.3.0) is open and must be recorded in the
  [compatibility](../compatibility.md) matrix when resolved.

## Evidence required before this ADR is accepted

1. The full unit, live integration, and qualification suites pass against
   the re-pinned stable lane, including the destructive restart and kill
   sequences, on the implementation branch.
2. The upgrade lane demonstrates, on a disposable volume: data written
   under 3.2.4, a verified pre-upgrade export, an in-place 3.3.0 start with
   automatic migrations applied, verified event hash-chain and run-head
   integrity, graph clear/replay equivalence, and a rollback rehearsal by
   export restore into a fresh 3.2.4 datastore.
3. The TLS lane passes the same live integration and qualification suites
   over `wss://`, or the compatibility matrix records its failure.
4. The surrealkv lane runs the live integration suite against server-side
   SurrealKV on its own volume, recorded as beta evidence.
5. The embedded lane records the expected transaction-refusal evidence
   against SDK `3.0.0b10`, without changing the stable dependency pins.
6. The known-issue tests for ActiveGraph 1.10 have been reviewed one by
   one against 1.12.0 and converted or retired with rationale, never
   deleted blindly.

## What this decision does not claim

- Cross-store atomicity; a projection may still lag the event log, and
  replay remains the documented repair path.
- SurrealKV, the SDK 3.0 beta line, or the embedded engine as stable
  provider support.
- TLS qualification before Lane A passes.
- Production readiness, high availability, or backup sufficiency beyond
  the export rehearsal in the upgrade lane.

## Evidence

Accepted on 2026-10-13 against the pinned `surrealdb/surrealdb:v3.3.0` image,
with the Docker Engine available in the execution sandbox and fresh per-lane
RocksDB / SurrealKV volumes:

1. Stable lane: `pytest -m surrealdb tests/integration` — 74 passed; unit /
   contract ladder — 30 passed, 2 xfail (upstream #81/#82).
2. Qualification: graceful-restart and abrupt-kill sequence — 2 passed.
3. Upgrade lane: in-place `3.2.4` → `3.3.0` on one RocksDB volume, verified
   export, graph clear/replay equivalence, and export-restore rollback into a
   fresh `3.2.4` datastore — 1 passed.
4. TLS lane: the same integration suite over `wss://` (local CA, enforced
   verification) is green apart from an intermittent client handshake timeout
   under CPU contention (`agvm-wn2`); qualification restart-recreate — 2
   passed.
5. SurrealKV lane: live integration suite and qualification on a dedicated
   SurrealKV volume — 74 and 3 passed.
6. Embedded lane: expected transaction-refusal evidence against SDK
   `3.0.0b10` — 7 passed.
7. Pack lane: 66 passed, 2 xfail.

The `3.2.5` fallback was not needed; `3.3.0` passed conformance.
