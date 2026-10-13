# Conformance and evidence

The repository separates fast API contracts from live database and destructive
qualification evidence. A single green test count should not collapse these
different claims.

## Test layers

| Layer | Marker/path | What it establishes |
| --- | --- | --- |
| Serialization and configuration | unmarked unit tests | Canonical encoding, hashes, redaction, key scoping, and rejected paths |
| Write ordering | `tests/test_write_ordering.py` | A rejected append leaves no projected state (ActiveGraph 1.12) |
| Released ActiveGraph suites | `tests/integration/*conformance.py` | Compatibility with the public EventStore and GraphStore contracts |
| Native query pushdown | `tests/integration/test_graph_pushdown.py` | The query hooks run in SurrealQL, not the Python full-scan fallback |
| Projection order | `tests/integration/test_projection_order.py` | Objects read back in the reference store's insertion order, including across reopens |
| Schema upgrade | `tests/integration/test_schema_upgrade.py` | An older database upgrades in place; a newer schema is rejected |
| Backup and restore | `tests/integration/test_backup_restore.py` | Export restores into a clean database with identical integrity and projection; a newer schema is rejected there too |
| Provider invariants | `pytest -m surrealdb` | Real server transactions, isolation, native edges, replay/fork/diff, restart within a live server |
| Pack harness | `pytest -m packs tests/packs` | The published pack library loads and projects into one SurrealDB database |
| Pack fixture suites | `tests/packs/test_pack_fixtures_on_surrealdb.py` | Each pack's own `fixtures/run_fixtures.py` runs unchanged on SurrealDB-backed stores |
| Embedded SDK lane | `tests/embedded` (`-m embedded`) | Under `surrealdb[embedded]==3.0.0b10`, embedded transports stay rejected — expected-rejection evidence |
| Qualification | `pytest -m qualification` | Exact pinned image/SDK/engine and container restart/kill persistence |
| Upstream known issues | `tests/known_issues` | Strict expected failures for lifecycle gaps outside this provider |

## Commands

Install the development dependencies, then run the layers explicitly:

```bash
python -m pip install -e '.[dev]'
pytest -m 'not surrealdb and not qualification and not embedded'
pytest -m surrealdb tests/integration
ACTIVEGRAPH_PACKS_PATH=/path/to/activegraph-packs pytest -m packs tests/packs
ACTIVEGRAPH_SURREALDB_QUALIFY=1 pytest -m qualification tests/qualification
ACTIVEGRAPH_SURREALDB_EMBEDDED=1 .venv-embedded/bin/python -m pytest tests/embedded -m embedded
```

The live layers require the environment described in [deployment.md](deployment.md).
The qualification command must target only the disposable container it is
authorized to restart and kill. Lane tests (embedded and the exploration lanes)
are selected-but-skipped unless their gate environment variable is set; the
embedded lane runs inside an isolated `.venv-embedded` built with
`surrealdb[embedded]==3.0.0b10` on top of the normal dev install.

## Required invariants

Promotion evidence must include at least:

- released EventStore and GraphStore conformance;
- exact JSON round-trip and literal canonical/hash golden values;
- append-before-projection ordering (a rejected append leaves no projection);
- native query pushdown for every overridden GraphStore hook;
- projected objects returned in the reference store's insertion order;
- an in-place, data-preserving schema upgrade and rejection of a newer schema;
- loading the published pack library onto one SurrealDB-backed Runtime;
- running each pack's own fixture suite on SurrealDB-backed stores, with any
  non-portable suite recorded as an explicit expected divergence;
- transaction rollback without an orphan event or advanced head;
- deterministic same-run stale-writer rejection and independent run progress;
- scope/run isolation across reads, clears, truncation, forks, and relations;
- native `TYPE RELATION` records with bound `RecordID` endpoints;
- replay that clears stale state, marks failure, and becomes ready only on success;
- prefix-only forks with traceable parent metadata and independent suffixes;
- rejection of unsupported compacted histories;
- close/reopen, server restart, and abrupt container termination; and
- an existing-database upgrade test before any supported-version change.

## Recording evidence

A repeatable result records:

- repository commit SHA and clean/dirty status;
- Python, ActiveGraph, SDK, and server versions;
- Docker image digest, command, transport, and storage engine;
- exact commands and marker selections;
- pass, fail, skip, and expected-failure counts;
- host/OS and test timestamp; and
- logs for any failure or replay repair.

CI status is implementation evidence only for its recorded environment. It is
not a performance benchmark, deployed-service proof, backup proof, or proof that
an application has modeled authorization correctly.

## Contract-first status

Tests may intentionally be RED while a contract is being implemented. That is
useful only when the failure is the expected missing behavior. Do not mark a
test xfail, weaken an assertion, or replace a live path with a mock merely to
produce green CI. Make the provider satisfy the contract, then record the green
trace.
