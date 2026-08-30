# Compatibility and known issues

## Qualified and preview lanes

| Component | Status | Notes |
| --- | --- | --- |
| ActiveGraph `1.10.0` | Baseline | Public store interfaces and released conformance suites |
| SurrealDB server `3.2.4` | Required stable lane | Pinned Docker image, remote WebSocket, server-side RocksDB |
| SurrealDB Python SDK `2.0.0` | Required | Exact dependency pin for the research preview |
| SurrealDB `3.3.x` prerelease | Preview only | Forward-compatibility signal; never gates the stable lane |
| Python `3.11+` | Package requirement | CI results, not the version declaration alone, determine qualified interpreters |

## Transport and engine matrix

| Path | Accepted by configuration | Qualification status |
| --- | --- | --- |
| `ws://` to pinned local server | Yes | Required qualification lane |
| `wss://` to remote server | Yes | Syntax supported; no TLS qualification claim yet |
| HTTP/HTTPS | No | Stateful SDK transaction handles are WebSocket-only |
| `mem://` embedded | No | Not durable and outside the provider preview |
| embedded `file://`, `rocksdb://`, or `surrealkv://` | No | Persistence and transaction semantics are not qualified |
| server-side RocksDB | Yes | Required stable storage path |
| server-side SurrealKV | No recommendation | SurrealKV remains beta |

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

- [ActiveGraph PR #78](https://github.com/yoheinakajima/activegraph/pull/78)
  proposes append-before-projection ordering and stronger run ownership. It is
  not part of the released `1.10.0` baseline.
- [ActiveGraph issue #81](https://github.com/yoheinakajima/activegraph/issues/81)
  tracks `Runtime.load()` creating an unknown run instead of failing without a
  mutation.
- [ActiveGraph issue #82](https://github.com/yoheinakajima/activegraph/issues/82)
  tracks replay into a stale supplied projection without clearing it first.

Strict expected-failure tests keep these limitations visible. An upstream fix
that makes one unexpectedly pass is a prompt to review and update the provider,
not to delete the test blindly.

## Compaction

Replay and fork helpers support uncompacted event histories only. They reject a
log beginning with `runtime.snapshot` and reject a fork point outside the
retained prefix. Snapshot materialization, compaction horizons, and legacy
migration are not implemented by guessing at private runtime state.

## Claims this matrix does not make

- ActiveGraph `1.11` compatibility before an upstream release is tested.
- Wire or storage compatibility across arbitrary SurrealDB versions.
- Distributed SurrealDB, SurrealDB Cloud, or multi-node high availability.
- TLS certificate, proxy, or workload-identity qualification.
- Atomic event-plus-projection acceptance.
