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
`surrealdb/surrealdb:v3.3.0` image, and stores RocksDB data in the named
volume identified by `SURREALDB_VOLUME`. The qualification fixture verifies
that exact named volume and the absolute `rocksdb:///data/activegraph.db`
storage URL of the default lane. Do not change the image to `latest` in
qualification evidence.

The Compose service parameterizes the optional lanes without changing
the default lane:

- `SURREALDB_ENGINE` selects the storage engine: the default `rocksdb`
  renders `rocksdb:///data/activegraph.db`, and `surrealkv` renders
  `surrealkv:///data/activegraph.db`.
- `SURREALDB_WEB_CRT` and `SURREALDB_WEB_KEY` supply the certificate and
  key for the TLS lane. Compose renders the TLS server only under the
  `tls` profile: a separate `surrealdb-tls` service mounts the
  certificate and key read-only and appends `--web-crt`/`--web-key` to
  its own start command. The default service renders unchanged, and
  `SURREALDB_TLS_CONTAINER_NAME` names the TLS container
  (`activegraph-surrealdb-tls` by default).

A lane uses its own `SURREALDB_VOLUME` value rather than the stable lane's;
a storage location must never be shared between backends.

SurrealDB's production image runs as a non-root user. A one-shot, fixed-version
BusyBox service makes the otherwise root-owned empty Docker volume writable,
then exits before SurrealDB starts. It does not remain on the network or run the
database as root. Both mounts set Docker's `volume-nocopy` option. Without it,
Docker treats the still-empty volume as eligible for image copy-up when the
SurrealDB container starts; that operation restores a root-owned volume root
and the non-root database process cannot create its RocksDB directory.

## The 3.2 to 3.3 one-way door

A datastore written by 3.2 opens on `v3.3.0` directly: the first `v3.3.0`
start applies automatic data migrations and records them in the datastore
ledger. That first start is a one-way door. Once `v3.3.0` has started
against a datastore, rollback to 3.2 is possible only by restoring a
pre-upgrade `surreal export` into a fresh 3.2 datastore; there is no
in-place downgrade.

`v3.3.0` performs its startup work in the background and reports readiness
at `/ready` (not `/health`), bounded by `--startup-operation-timeout` (60
seconds by default). Take and verify a `surreal export` before starting
`v3.3.0` against any volume that already holds 3.2 data; a volume created
fresh on `v3.3.0` is unaffected. The upgrade lane rehearses the full
procedure on a disposable volume.

The documented fallback if `v3.3.0` fails conformance is `v3.2.5`, the
latest 3.2.x patch. Applying the fallback is a recorded decision, and it
does not reopen a datastore that `v3.3.0` has already migrated.

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

## Studio

The documented visual interface is SurrealDB Studio at
`studio.surrealdb.com`, delivered as a hosted web application and a
desktop application. No UI image is pinned in the Compose deployment
because Studio is not self-hostable; Surrealist, now end-of-life, is not
adopted.

The desktop application is the clean path to the local Compose service:
connect to `ws://127.0.0.1:8000` with the `SURREALDB_USER` and
`SURREALDB_PASSWORD` values from `.env`, and treat that root identity as
local-only. A hosted web page reaching a local endpoint is a verification
note, not a claim: whether the hosted page can reach `ws://127.0.0.1:8000`
is not verified here, and an operator who needs that path must verify it
in their own environment before relying on it.

Studio is an operator convenience. Qualification evidence comes from the
test suites, never from a UI session.

## Pack harness lane

The pack lane loads the external ActiveGraph pack library onto a Runtime whose
event log and graph projection both live in the test database. Point the harness
at a checkout:

```bash
ACTIVEGRAPH_PACKS_PATH=/path/to/activegraph-packs pytest -m packs tests/packs
```

When `ACTIVEGRAPH_PACKS_PATH` is unset the harness uses the reference checkout
baked into this environment; the lane skips cleanly when neither is present. The
lane is read-only with respect to the pack repository — it imports packs and
loads them, and never modifies the checkout.

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

## Exploration lanes

Four opt-in exploration lanes extend the disposable environment. They are
non-gating evidence lanes under
[ADR 0003](adr/0003-latest-stable-pins-and-exploration-lanes.md): none of
them changes the default Compose service or the stable pins, a lane
failure is recorded rather than treated as a stable-lane regression, and
each lane uses its own volume or environment so it cannot disturb the
stable lane's data.

### Upgrade lane (3.2 to 3.3)

The upgrade lane rehearses the one-way door as an opt-in test gated behind
the `upgrade` marker. It starts `v3.2.4` on a fresh volume through the
existing qualification environment variables, writes a small event
history through the provider, takes and verifies a `surreal export` via
`docker exec` against the live server (export is a client command and
needs a serving endpoint), and stops the container gracefully. It then
recreates the container with the `v3.3.0` image on that same volume and
asserts `server_version()`, event
hash-chain and run-head integrity, and graph clear/replay equivalence
after the automatic migrations. A final phase restores the pre-upgrade
export into a fresh `v3.2.4` datastore and re-verifies the chain,
completing the rollback rehearsal. The lane provisions its own volumes
derived from the qualification volume variable and never upgrades the
stable lane's volume. It is non-gating: a failure keeps the roadmap
upgrade item open and is recorded.

### TLS lane

The TLS lane qualifies `wss://`. Generate a local certificate authority
with `mkcert` or `openssl` and trust it on the host; the SDK applies
standard certificate verification. Set `SURREALDB_WEB_CRT` and
`SURREALDB_WEB_KEY` and start only the TLS service with
`docker compose --profile tls up -d surrealdb-tls`; a plain profile
start would also start the default server and collide with it; likewise
stop an already-running default lane first (`docker compose stop
surrealdb`), since both services publish the same port. Confirm
readiness with `surreal is-ready --endpoint https://127.0.0.1:8000`,
and run the same integration and qualification suites with
`ACTIVEGRAPH_SURREALDB_TEST_URL=wss://127.0.0.1:8000` and
`ACTIVEGRAPH_SURREALDB_DOCKER_CONTAINER` set to the TLS container name
(`activegraph-surrealdb-tls` by default). The lane uses its own
`SURREALDB_VOLUME` value. It is non-gating: no TLS qualification claim
exists until it passes and the [compatibility](compatibility.md) matrix
records the result.

### SurrealKV lane

The SurrealKV lane runs the server on the beta SurrealKV engine. Set
`SURREALDB_ENGINE=surrealkv` and a `SURREALDB_VOLUME` value that no
RocksDB lane has ever used; a storage location must never be shared
between backends. The service renders `surrealkv:///data/activegraph.db`.
Run the live integration suite; an optional qualification pass sets
`ACTIVEGRAPH_SURREALDB_TEST_ENGINE=surrealkv` and asserts the SurrealKV
storage URL and `server_version()`. The result is recorded as beta
evidence. SurrealKV remains beta upstream, targets embedded and
local-first workloads, and never gates the stable RocksDB lane, which
remains the recommended write-heavy server engine.

### Embedded lane

The embedded lane records why the provider cannot move to the SDK 3.0
beta line. It creates an isolated `.venv-embedded` with
`surrealdb[embedded]==3.0.0b10`, which installs the Rust
`surrealdb-embedded` extension (engines `mem` and SurrealKV only, no
RocksDB). The lane runs an opt-in test gated behind the `embedded`
marker: it opens `mem://` and `surrealkv://` handles and asserts that
`attach`, `begin`, `commit`, and `cancel` raise
`UnsupportedFeatureError`, because the embedded connection refuses the
client-side transactions this provider's append and head contracts depend
on. The expected result is a documented rejection, not a defect to fix.
The lane is non-gating and isolated: it never changes the stable
dependency pins.

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
