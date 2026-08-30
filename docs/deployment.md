# Deployment and operations

This guide creates the disposable, single-node environment used for research
qualification. It is not a production runbook.

## Local pinned server

Copy and edit the environment template:

```bash
cp .env.example .env
```

Replace both password placeholders with the same local-only value. Compose
reads `.env` automatically; export it for Python commands:

```bash
set -a
. ./.env
set +a
docker compose config
docker compose up -d
docker compose ps
```

The service binds only to `127.0.0.1`, uses the pinned
`surrealdb/surrealdb:v3.2.4` image, and stores RocksDB data in the named
volume identified by `SURREALDB_VOLUME`. The qualification fixture verifies
that exact named volume and the absolute `rocksdb:///data/activegraph.db`
storage URL. Do not change the image to `latest` in qualification evidence.

SurrealDB's production image runs as a non-root user. A one-shot, fixed-version
BusyBox service makes the otherwise root-owned empty Docker volume writable,
then exits before SurrealDB starts. It does not remain on the network or run the
database as root.

## Stop and remove

Stop the server without deleting its volume:

```bash
docker compose stop
```

`docker compose down` removes the container and network but retains the named
volume unless an operator explicitly adds `--volumes`. Removing that volume is
destructive and is not part of the normal test workflow.

Never start two SurrealDB processes against the same RocksDB directory or
volume. Filesystem copying while the server is writing is not a qualified
backup procedure.

## Test connection

The integration suite uses the `ACTIVEGRAPH_SURREALDB_TEST_*` variables from
`.env`:

```bash
pytest -m surrealdb tests/integration
```

Tests isolate provider state with generated scopes and runs. Use a disposable
database anyway. Do not point the test variables at production, shared staging,
or customer data.

## Qualification lane

Qualification checks the exact SDK/server versions, image command, RocksDB
engine, transactional WebSocket path, graceful restart, abrupt container kill,
and reopening persisted event/graph state.

It controls the container named by
`ACTIVEGRAPH_SURREALDB_DOCKER_CONTAINER`, including `docker kill`, removes and
recreates it against `ACTIVEGRAPH_SURREALDB_DOCKER_VOLUME`:

```bash
ACTIVEGRAPH_SURREALDB_QUALIFY=1 pytest -m qualification tests/qualification
```

Use only the disposable Compose container. A pass proves the asserted data
survived those test sequences on that host; it does not prove crash consistency
under every failure, storage durability guarantees, or restore readiness.

## Backup and restore

Backup and restore are promotion gates, not completed features of this preview.
A production evaluation must document and exercise:

1. the SurrealDB-supported backup/export mechanism for the selected deployment;
2. credential and encryption handling;
3. restoration into a separate clean environment;
4. event-chain and run-head verification after restore;
5. graph clear/replay after restore; and
6. recovery-time and recovery-point observations.

A copied file, successful export command, or green application health check is
not by itself restore proof.

## Production evaluation checklist

Before any production use, require evidence for all of the following:

- pinned, supported versions and a documented upgrade/rollback test;
- external TLS and least-privilege identities rather than root credentials;
- network isolation and authorization for every application table;
- backup and clean-environment restore;
- monitoring for failed/incomplete projections and hash-chain verification;
- capacity, latency, and failure testing on representative data;
- an explicit high-availability design, if required; and
- operator acceptance of the non-atomic event/projection boundary.
