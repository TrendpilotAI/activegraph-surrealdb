# ADR 0002: Stable-first SurrealDB and WebSocket-only support

- Status: accepted for the research preview
- Date: 2026-08-30

## Decision

The production-qualification lane targets:

- SurrealDB server `3.2.4` (current stable on 2026-08-30)
- SurrealDB Python SDK `2.0.x`
- Python `3.11+`, matching ActiveGraph v1.10
- remote WebSocket (`ws://` for the required local qualification lane)
- a server process backed by RocksDB

SurrealDB `3.3.0-beta.3` is a preview lane, not the stable baseline. Its
`SELECT ... FOR UPDATE` behavior may be tested but is not required by this
adapter because every append already writes the per-run head record.

## Why WebSocket only

The Python SDK's stateful transaction handle is WebSocket-only. Although a
multi-statement SurrealQL transaction can be submitted as one query through
other transports, this preview rejects HTTP and embedded modes rather than
claim equivalent crash and session semantics without repeatable proof.

## Storage engines

- Server-side RocksDB is the recommended single-node durable path.
- SurrealKV remains beta and is not a production recommendation here.
- Embedded RocksDB support is contradictory across current Python docs and is
  not advertised until an executable compatibility test proves it.
- Separate SurrealDB processes must never share one RocksDB data directory.

## Required lanes

| Lane | Server | Transport | Purpose |
| --- | --- | --- | --- |
| stable | 3.2.4 | WebSocket | required conformance and persistence proof |
| preview | 3.3 prerelease | WebSocket | non-gating forward compatibility |
| TLS | stable | `wss://` | supported syntax, not qualified until a TLS lane passes |
| HTTP | stable | HTTP | must be rejected by config in this preview |
| memory | SDK embedded | `mem://` | not advertised as durable provider support |
| Docker | pinned stable image | WebSocket + RocksDB | restart and volume proof |

Every supported lane must bind the whole `RecordID` as a query parameter,
never interpolate user-controlled record or relation types into SurrealQL.
