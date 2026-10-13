# Compatibility and known issues

## Qualified and preview lanes

| Component | Status | Notes |
| --- | --- | --- |
| ActiveGraph `1.12.0` | Baseline | Public store interfaces and released conformance suites |
| SurrealDB server `3.3.0` | Required stable lane | Pinned Docker image, remote WebSocket, server-side RocksDB |
| SurrealDB server `3.2.5` | Documented fallback | Re-pinned only if `3.3.0` fails conformance |
| SurrealDB Python SDK `2.0.0` | Required | Exact dependency pin for the research preview |
| SurrealDB prereleases beyond `3.3.0` | Preview only | Forward-compatibility signal; never gates the stable lane |
| Python `3.11+` | Package requirement | CI results, not the version declaration alone, determine qualified interpreters |

## Transport and engine matrix

| Path | Accepted by configuration | Qualification status |
| --- | --- | --- |
| `ws://` to pinned local server | Yes | Required qualification lane |
| `wss://` to pinned local server | Yes | Preview TLS lane demonstrated: local CA via openssl, certificate verification enforced, live suite green apart from an intermittent client handshake timeout under CPU contention (`agvm-wn2`). No rotation or workload-identity claim. |
| HTTP/HTTPS | No | Stateful SDK transaction handles are WebSocket-only |
| `mem://` embedded | No | Not durable and outside the provider preview; rejection pinned by `tests/embedded/test_embedded_lane.py` |
| embedded `file://`, `rocksdb://`, or `surrealkv://` | No | Persistence and transaction semantics are not qualified; rejection pinned by `tests/embedded/test_embedded_lane.py` |
| server-side RocksDB | Yes | Required stable storage path |
| server-side SurrealKV | No recommendation | SurrealKV remains beta; full live integration suite green on a dedicated SurrealKV volume (V7 lane, 2026-10-13) |

### Evidence recording note (2026-10-13)

The V2–V8 lane runs were executed against the pinned
`surrealdb/surrealdb:v3.3.0` image with the Docker Engine available in the
execution sandbox, on fresh per-lane RocksDB / SurrealKV volumes: the live
integration suite over `ws://` and `wss://`, graceful-restart and abrupt-kill
qualification, the in-place `3.2.4` → `3.3.0` upgrade rehearsal, the TLS lane,
the SurrealKV lane, the pack-fixture lane, and the embedded expected-rejection
lane. This supersedes the earlier native `surreal v3.2.4` binary runs recorded
while the sandbox could not run containers (`agvm-8ii`); containerized
qualification is now the recorded evidence.

The Compose and qualification files intentionally pin complete versions. A new
server or SDK release is unsupported until its own compatibility branch passes
the same conformance, restart, concurrency, isolation, and exact-serialization
tests.

The SDK `2.0.0` blocking WebSocket implementation currently emits a
`DeprecationWarning` with `websockets 17.1` because the SDK opens its internal
socket through a legacy direct-connect path. The stable contract suite passes;
the warning is tracked as dependency-compatibility work and is not suppressed
by this provider.

## ActiveGraph lifecycle limitations

The provider does not patch private ActiveGraph runtime methods or register a
database URL scheme. Stores are constructed explicitly.

- ActiveGraph `1.12.0` ships the append-before-projection ordering proposed by
  [PR #78](https://github.com/yoheinakajima/activegraph/pull/78) as CONTRACT
  v1.11 #2: a rejected durable append changes no projected state. The provider
  asserts that invariant directly in `tests/test_write_ordering.py`.
- [ActiveGraph issue #81](https://github.com/yoheinakajima/activegraph/issues/81)
  tracks `Runtime.load()` creating an unknown run instead of failing without a
  mutation. Still open in `1.12.0`.
- [ActiveGraph issue #82](https://github.com/yoheinakajima/activegraph/issues/82)
  tracks replay into a stale supplied projection without clearing it first.
  Still open in `1.12.0`.

Strict expected-failure tests keep the remaining limitations visible. An
upstream fix that makes one unexpectedly pass is a prompt to review and update
the provider, not to delete the test blindly.

`1.12.0` also closes the patch vocabulary to `update` and `replace`:
`Patch` construction and replay both reject any other op with
`InvalidPatchOperation`. Provider-written stores are unaffected, because
every stored patch passed framework validation at write time. A store
written by another `1.10`-era producer can hold `create` or `remove` ops
(which `1.10` accepted without validation and silently mis-applied); under
`1.12.0` such rows fail loudly at replay and at projection reads, with an
upstream error type rather than `SurrealProviderError`. That data requires
migration or discard, not silent reads.

## Compaction

Replay and fork helpers support uncompacted event histories only. They reject a
log beginning with `runtime.snapshot` and reject a fork point outside the
retained prefix. Snapshot materialization, compaction horizons, and legacy
migration are not implemented by guessing at private runtime state.

## Claims this matrix does not make

- ActiveGraph `1.13` compatibility before an upstream release is tested.
- Wire or storage compatibility across arbitrary SurrealDB versions.
- Distributed SurrealDB, SurrealDB Cloud, or multi-node high availability.
- TLS qualification beyond the pinned preview lane: public CAs, proxy
  termination, least-privilege identities, and certificate rotation.
- Atomic event-plus-projection acceptance.

## Provider schema

The provider schema is versioned and additive. Schema v1 introduced the
event/graph tables; schema v2 adds the indexes that back the native query
pushdown; schema v3 records the object counter that gives projection reads the
reference store's insertion order. Opening an older database upgrades it in
place without data loss; a database written by a newer provider is rejected
rather than mis-read. The upgrade path is pinned by
`tests/integration/test_schema_upgrade.py`, and the backup / clean-environment
restore rehearsal (export, restore into a fresh database, integrity and
projection parity, newer-provider rollback rejection) by
`tests/integration/test_backup_restore.py`.
