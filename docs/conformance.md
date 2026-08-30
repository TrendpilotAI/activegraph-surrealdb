# Conformance and evidence

The repository separates fast API contracts from live database and destructive
qualification evidence. A single green test count should not collapse these
different claims.

## Test layers

| Layer | Marker/path | What it establishes |
| --- | --- | --- |
| Serialization and configuration | unmarked unit tests | Canonical encoding, hashes, redaction, key scoping, and rejected paths |
| Released ActiveGraph suites | `tests/integration/*conformance.py` | Compatibility with the public EventStore and GraphStore contracts |
| Provider invariants | `pytest -m surrealdb` | Real server transactions, isolation, native edges, replay/fork/diff, restart within a live server |
| Qualification | `pytest -m qualification` | Exact pinned image/SDK/engine and container restart/kill persistence |
| Upstream known issues | `tests/known_issues` | Strict expected failures for lifecycle gaps outside this provider |

## Commands

Install the development dependencies, then run the layers explicitly:

```bash
python -m pip install -e '.[dev]'
pytest -m 'not surrealdb and not qualification'
pytest -m surrealdb tests/integration
ACTIVEGRAPH_SURREALDB_QUALIFY=1 pytest -m qualification tests/qualification
```

The live layers require the environment described in [deployment.md](deployment.md).
The qualification command must target only the disposable container it is
authorized to restart and kill.

## Required invariants

Promotion evidence must include at least:

- released EventStore and GraphStore conformance;
- exact JSON round-trip and literal canonical/hash golden values;
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
