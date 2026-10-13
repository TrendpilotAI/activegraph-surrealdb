# Upgrade plan: latest-stable pins and exploration lanes

- Status: planned; not started
- Date: 2026-10-12
- Decision record: [ADR 0003](adr/0003-latest-stable-pins-and-exploration-lanes.md)
- Branch: `non-agentic/graph-db-upgrades`
- Execution model: one coordinating session plus a sub-agent swarm

## Purpose and swarm model

This plan implements ADR 0003. It is written to be executed by a swarm: a
coordinator session and stateless sub-agents. Sub-agents start with no
context of this repository or the research behind it, so the plan carries
its own facts. A sub-agent prompt is only a pointer into this document
plus the work-package identifier.

| Role | Count | Duties |
| --- | --- | --- |
| coordinator | 1 | owns the branch and all commits; spawns, reviews, and accepts sub-agents; holds the environment token between ladder steps; makes fallback decisions and records them |
| editor | up to 6, in parallel | applies exactly one work package (WP); edits only the files it owns; verifies statically; never commits, never starts Docker, never runs live suites |
| executor | 1 at a time | holds the environment token for one ladder step (V); runs the venv, Docker, and test suites; edits nothing |

Swarm rules:

1. File ownership is exclusive. A sub-agent edits only the files listed
   in its brief. Anything discovered outside that scope is reported to
   the coordinator, never fixed in place.
2. Editors verify statically with `ruff check` and
   `ruff format --check` on their own files. `mypy`, `pytest`, Docker,
   and the example script belong to the ladder, not to editors.
3. Exactly one executor runs at a time. Ladder steps are serialized
   because they share one Docker daemon, one `.venv`, and one set of
   named volumes and containers.
4. Sub-agents do not commit. The coordinator reviews each work package
   and commits it as exactly one logical change.
5. A failing ladder step stops the line. The coordinator records the
   failure, applies the documented fallback, and re-runs the step before
   anything later starts.
6. Known-issue tests are reviewed and converted with written rationale;
   they are never deleted blindly (see
   [compatibility](compatibility.md)).

## Shared context

Every brief assumes this section as given fact, verified on 2026-10-12.

Upstream releases:

- SurrealDB server `3.3.0` (2026-09-28) is the latest stable release;
  the 3.3.x line has left prerelease. `3.2.5` (2026-10-06) is the latest
  patch on the 3.2.x line and is the documented fallback. Docker tags
  follow the existing `v`-prefixed style (`surrealdb/surrealdb:v3.3.0`);
  verify with `docker manifest inspect` before first use.
- ActiveGraph `1.12.0` (2026-09-26) ships the staged 1.11.0 work:
  `Graph.emit` appends to the event store before projection; a failed
  append changes no graph, queue, projection, or observer state;
  `ConcurrentWriterError` enforces one logical writer per run;
  `InvalidPatchOperation` closes the patch vocabulary to `update` and
  `replace`; `attach_store` requires the graph and store to name the
  same run at the same event count; store reads return detached values;
  `GraphStore.query_objects` gains a working default implementation.
- SurrealDB Python SDK `2.0.0` remains the latest stable SDK. The
  embedded engine exists only in the SDK `3.0.0` beta line via
  `surrealdb[embedded]`, which installs `surrealdb-embedded` (Rust
  extension; engines `mem` and SurrealKV only, no RocksDB). The embedded
  connection refuses client-side transactions: `attach`, `begin`,
  `commit`, and `cancel` raise `UnsupportedFeatureError`.
- Surrealist is superseded by SurrealDB Studio, the official visual
  interface at `studio.surrealdb.com` (hosted web and desktop). Studio
  is not self-hostable; no image exists to pin. Surrealist `3.9.13`
  (2026-10-03) is end-of-life and is not adopted.

SurrealDB 3.2 to 3.3 upgrade behavior:

- A 3.2 datastore opens on 3.3.0 directly; the first 3.3.0 start applies
  automatic data migrations recorded in a datastore ledger. Once 3.3.0
  has started against a datastore, rollback to 3.2 is only possible by
  restoring a pre-upgrade `surreal export` into a fresh 3.2 datastore.
- 3.3.0 starts its startup work in the background, reports readiness at
  `/ready` (not `/health`), and applies `--startup-operation-timeout`
  (60 seconds by default).
- `UPDATE` and `UPSERT` evaluate `WHERE` before the data clause.
- Server TLS is configured with `--web-crt` and `--web-key` (environment
  variables `SURREAL_WEB_CRT` and `SURREAL_WEB_KEY`). Server-side
  SurrealKV uses `surrealkv:///path/to/db` and remains beta; a storage
  location must never be shared between backends.

Repository conventions:

- Contract-first: a green run is evidence only for the tested lane; a
  new server or SDK release is unsupported until its own compatibility
  branch passes conformance, restart, isolation, and exact-serialization
  tests (this branch).
- Test layers and commands:

```bash
pytest -m 'not surrealdb and not qualification'   # unit + known issues
pytest -m surrealdb tests/integration             # live server
python examples/replay_fork_diff.py               # fork/diff/replay trace
ACTIVEGRAPH_SURREALDB_QUALIFY=1 pytest -m qualification tests/qualification
```

- `pytest` runs with `--strict-markers`; every new marker must be
  registered in a `pytest_configure` hook.
- Code lines wrap at 100 columns; documentation at roughly 80.
- ADRs are dated records and are never rewritten; `docs/adr/0001` and
  `docs/adr/0002` must not be edited.
- Qualification tests deliberately stop and kill the container named by
  `ACTIVEGRAPH_SURREALDB_DOCKER_CONTAINER`; they run only against the
  disposable Compose service.
- The qualification conftest asserts the transport scheme (currently
  exactly `ws`), the container's host port, and the
  `rocksdb:///data/` storage URL in the container command.
- The research sandbox that produced this plan could not reach Docker
  Hub; verify image tags from the execution environment.

## Work packages

Editors run in parallel. Ownership is exclusive.

| ID | Package | Owned files |
| --- | --- | --- |
| WP1 | server pin and lane variables | `compose.yaml`, `.env.example`, `.gitignore`, `.github/workflows/qualification.yml`, `tests/qualification/test_environment.py` |
| WP2 | ActiveGraph dependency | `pyproject.toml`, `src/activegraph_surrealdb/__init__.py`, `src/activegraph_surrealdb/runtime.py` |
| WP3 | known-issue review | `tests/known_issues/` |
| WP4 | core documents | `README.md`, `docs/architecture.md`, `docs/compatibility.md`, `docs/experiment.md`, `docs/provenance.md`, `ROADMAP.md` |
| WP5 | deployment document | `docs/deployment.md` |
| WP6 | lane test harness | `tests/qualification/conftest.py`, `tests/integration/conftest.py`, new files under `tests/qualification/` and `tests/embedded/` |

### WP1: server pin and lane variables

Context: the Compose service, the CI qualification workflow, and the
qualification environment test pin `surrealdb/surrealdb:v3.2.4`. The pin
moves to `v3.3.0`. The fallback `v3.2.5` is documented but never applied
by an editor; applying it is a coordinator decision after V2 or V3.

Steps:

1. `compose.yaml`: pin `surrealdb/surrealdb:v3.3.0`. Parameterize the
   lanes without changing the default: storage engine via
   `SURREALDB_ENGINE` (default `rocksdb`; `surrealkv` renders
   `surrealkv:///data/activegraph.db`), TLS via optional
   `SURREALDB_WEB_CRT`/`SURREALDB_WEB_KEY` (mount the certificate and
   key, append `--web-crt`/`--web-key` to the start command only when
   set). Keep the default lane byte-identical to today's command,
   because the qualification conftest asserts `rocksdb:///data/`.
2. `.env.example`: document the new lane variables and the rule that a
   lane uses its own `SURREALDB_VOLUME` value.
3. `.gitignore`: add `.venv-embedded/`.
4. `.github/workflows/qualification.yml`: update the image (line 67)
   and the job name (line 33) to `SurrealDB 3.3.0 / SDK 2.0.0 /
   WebSocket / RocksDB`.
5. `tests/qualification/test_environment.py`: the SDK assertion at line
   14 stays `2.0.0`; the image assertion (line 17) and
   `server_version()` assertion (line 20) move to `3.3.0`.

Done: `docker compose config` renders the default lane unchanged apart
from the image; the two new variables are documented; ruff passes on the
owned files that are Python.

### WP2: ActiveGraph dependency

Context: `pyproject.toml` pins `activegraph==1.10.0`. ActiveGraph 1.12.0
changes runtime behavior (see shared context). The mypy overrides
(`pyproject.toml` lines 59-64) exist because 1.10.0 lacks a PEP 561
marker; whether 1.12.0 ships `py.typed` is checked in the ladder (V0),
and the override decision is recorded then, not guessed now.

Steps:

1. Pin `activegraph==1.12.0`.
2. Update the `src/activegraph_surrealdb/__init__.py` docstring from
   "ActiveGraph v1.10" to "ActiveGraph v1.12".
3. Review `src/activegraph_surrealdb/runtime.py` against 1.12.0: the
   replay/replace helpers must tolerate append-before-projection
   ordering, and any patch operation handled by the provider must be
   `update` or `replace`. Apply only necessary changes and record the
   review result in the report even when no change is required.

Done: the dependency and docstring are updated; the runtime review is
written out; ruff passes on owned files.

### WP3: known-issue review

Context: `tests/known_issues/` holds three expected-failure tests pinned
to ActiveGraph 1.10 behavior: `test_activegraph_v110_load.py` (upstream
issue 81, `Runtime.load` creating an unknown run),
`test_activegraph_v110_ordering.py` (projection before append), and
`test_activegraph_v110_stale_projection.py` (upstream issue 82, replay
into a stale supplied projection). ActiveGraph 1.12.0 reverses the
ordering guarantee and changes `attach_store` and load/resume behavior,
so some expected failures will now pass.

Steps:

1. Read each test and classify it against 1.12.0 (see shared context).
2. For each test that now contradicts 1.12.0, convert it into a positive
   contract test with the version-correct name (for example,
   `test_activegraph_v112_append_before_projection.py`), and keep or
   adapt the strictness: assert the new guarantee, including that a
   failed append changes no projection state.
3. For each test whose upstream issue may now be fixed (load, stale
   projection), keep the expected-failure form until the ladder proves
   the fix (V1); record in the report which tests are expected to flip
   and why.
4. Never delete a test without a written rationale in the report.

Done: every file is converted, kept, or adapted with a per-file
rationale; ruff passes; no marker changes.

### WP4: core documents

Context: the version and ordering narrative is written against 1.10.0
and 3.2.4 throughout. With 1.12.0 the partial-failure mode flips from
projected-but-unlogged to logged-but-unprojected, and replay remains the
repair path. The cross-store non-atomicity claim stays.

Steps:

1. `README.md`: research lane table (lines 53-55) to ActiveGraph
   `1.12.0`, SurrealDB server `3.3.0` stable, SDK `2.0.0`; replace the
   3.3.x-prerelease preview sentence (line 60) so 3.3.x is the stable
   lane and only later prereleases are preview; update the ordering
   sentence (line 78) for 1.12.0.
2. `docs/architecture.md`: rewrite the "Live writes are not cross-store
   atomic" section (line 75 onward): 1.12.0 appends before projection;
   a failed append changes nothing; a failed projection leaves an
   event logged without its projection and replay repairs it; no
   cross-store atomicity is claimed.
3. `docs/compatibility.md`: update the lane table (lines 7-10) and the
   upstream lifecycle section (lines 41-52): PR 78's ordering is part
   of 1.12.0; issues 81 and 82 are re-evaluated by WP3 with ladder
   evidence; update the claims list (line 63).
4. `docs/experiment.md` (line 79) and `docs/provenance.md` (lines
   10-12): update the version references.
5. `ROADMAP.md`: mark the Phase 3 append/projection-ordering item as
   done with an ADR 0003 pointer; leave the Phase 2 upgrade-lane item
   unchecked pending V5; do not rewrite history elsewhere.
6. Do not edit `docs/adr/0001` or `docs/adr/0002`.

Done: no stale `1.10.0` or `3.2.4` claims remain outside the dated ADRs
and the historical ROADMAP pin line; all prose wraps near 80 columns.

### WP5: deployment document

Context: `docs/deployment.md` documents the pinned server (line 27) and
the disposable qualification environment. Studio replaces Surrealist as
the documented interface, and the exploration lanes need operator
instructions.

Steps:

1. Update the pinned image reference to `v3.3.0` and describe the 3.2
   to 3.3 one-way door: automatic migrations at first start, and export
   as the only route back to 3.2.
2. Add a "Studio" section: Studio at `studio.surrealdb.com` (hosted web
   and desktop) is the documented interface; the desktop application is
   the clean path to a local `ws://127.0.0.1:8000` connection; a
   hosted page reaching a local endpoint is a verification note, not a
   claim; no UI image is pinned because Studio is not self-hostable, and
   end-of-life Surrealist is not adopted.
3. Add one subsection per exploration lane (upgrade, TLS, surrealkv,
   embedded) summarizing the procedure, its own volume or environment,
   and its non-gating status, pointing at ADR 0003.

Done: deployment documentation matches the Compose service rendered by
WP1 and cites ADR 0003 for lane status.

### WP6: lane test harness

Context: the qualification conftest asserts scheme `ws`, and the
container command contains `rocksdb:///data/`. Lanes need opt-in
parametrization and new evidence tests, all gated behind markers, never
run by default.

Steps:

1. `tests/qualification/conftest.py`: accept scheme `ws` or `wss`
   (assert the endpoint port still matches the container binding); make
   the asserted storage URL derive from an
   `ACTIVEGRAPH_SURREALDB_TEST_ENGINE` variable defaulting to `rocksdb`.
2. `tests/integration/conftest.py`: keep behavior identical for the
   default lane (no functional change expected; review only).
3. New `tests/qualification/test_upgrade_volume.py` (Lane B): gated
   behind a new `upgrade` marker, it starts 3.2.4 on a fresh volume
   through the existing qualification environment variables, writes a
   small event history through the provider, stops the container
   gracefully, takes and verifies a `surreal export`, recreates the
   container with the 3.3.0 image on the same volume, then asserts
   `server_version()`, event hash-chain and run-head integrity, and
   graph clear/replay equivalence; a final phase restores the export
   into a fresh 3.2.4 datastore and re-verifies the chain. Register the
   marker in `pytest_configure`.
4. New Lane A evidence: parametrize the existing qualification
   environment test is unnecessary; instead the TLS lane reuses the same
   suites with `ACTIVEGRAPH_SURREALDB_TEST_URL=wss://...` (conftest
   change from step 1). Add no new test file unless a gap appears.
5. New Lane C evidence: a small `tests/qualification/` test asserting
   the engine URL and `server_version()` when
   `ACTIVEGRAPH_SURREALDB_TEST_ENGINE=surrealkv`, gated behind the
   existing `qualification` marker.
6. New `tests/embedded/test_embedded_transactions.py` (Lane D): gated
   behind a new `embedded` marker, it imports `surrealdb` from the
   isolated venv, opens `mem://` and `surrealkv://` handles, and asserts
   that `attach`, `begin`, `commit`, and `cancel` raise
   `UnsupportedFeatureError` — the expected rejection evidence for ADR
   0003. Register the marker.
7. Every new file carries a docstring naming its lane and its non-gating
   status.

Done: default test selection is unchanged (`-m 'not surrealdb and not
qualification'` still runs unit work; lane-marked tests match that
expression but skip outside their lanes); ruff passes; all markers
registered for `--strict-markers`.

## Ladder

The ladder is serialized; each step is one executor holding the
environment token. A step that fails stops the line (see swarm rule 5).

| Step | Action | Pass criteria |
| --- | --- | --- |
| V0 | pre-flight | `docker manifest inspect surrealdb/surrealdb:v3.3.0` succeeds; fresh `.venv` with `pip install -e '.[dev]'`; record whether installed ActiveGraph 1.12.0 ships `py.typed` (decides the mypy override change, owned then by the coordinator); `docker compose down` and remove the old dev volume |
| V1 | unit | `pytest -m 'not surrealdb and not qualification'` green, including converted known-issue tests |
| V2 | live integration | `docker compose up -d` on a fresh volume; `pytest -m surrealdb tests/integration` green. Failure of a 3.3.0-specific wire issue triggers the fallback: coordinator re-pins `v3.2.5` in the WP1 files, records it, and re-runs V2 and V3 |
| V3 | qualification | `ACTIVEGRAPH_SURREALDB_QUALIFY=1 pytest -m qualification tests/qualification` green (restart and kill sequences) |
| V4 | example | `python examples/replay_fork_diff.py` completes its published trace |
| V5 | Lane B upgrade | the new `upgrade` test passes end to end, including the export-restore rollback rehearsal; failure keeps the roadmap item open and is recorded |
| V6 | Lane A TLS | generate a local CA (`mkcert` or `openssl`) trusted by the host; start the TLS lane; `surreal is-ready --endpoint https://127.0.0.1:8000` responds; `ACTIVEGRAPH_SURREALDB_TEST_URL=wss://127.0.0.1:8000` with the same suites as V2 and V3 green |
| V7 | Lane C surrealkv | engine variable `surrealkv`, its own volume; live integration suite green; qualification optional; recorded as beta evidence |
| V8 | Lane D embedded | isolated `.venv-embedded` with `surrealdb[embedded]==3.0.0b10`; the `embedded` marker test passes as the expected rejection evidence |
| V9 | records and parity | full `ruff check`, `ruff format --check`, `mypy`, `build`, `twine check`; unit suite green once more; update ADR 0003 status with evidence links; finalize the compatibility matrix and ROADMAP checkboxes; the coordinator makes the final commit |

## Commit plan

The coordinator commits one logical change per accepted package, after
review, in this order:

1. `feat: pin SurrealDB 3.3.0 and parameterize lane deployment` (WP1)
2. `feat: upgrade ActiveGraph to 1.12.0` (WP2)
3. `test: convert v1.10 known issues for ActiveGraph 1.12.0` (WP3)
4. `docs: update pins, ordering, and lane documents` (WP4, WP5)
5. `test: add upgrade, TLS, surrealkv, and embedded lane harness` (WP6)
6. `docs: record lane evidence and accept ADR 0003` (V9)

## Dependency graph

```mermaid
flowchart LR
    E[WP1..WP6 editors in parallel] --> B[coordinator review and commits]
    B --> V0[V0 pre-flight]
    V0 --> V1[V1 unit] --> V2[V2 integration] --> V3[V3 qualification]
    V3 --> V4[V4 example] --> V5[V5 upgrade lane] --> V6[V6 TLS lane]
    V6 --> V7[V7 surrealkv lane] --> V8[V8 embedded lane] --> V9[V9 records]
    V2 -. fallback re-pin 3.2.5 .-> F[coordinator fallback] --> V2
    V3 -. fallback .-> F
```

## Sub-agent prompts

Spawn editors in parallel, one prompt per work package:

```text
In this repository, read docs/upgrade-plan.md fully, especially
"Shared context" and the brief for your package. Execute work package
<WPn> exactly.

Scope: edit only the files that package owns. Do not commit, do not
branch, do not start Docker, do not run pytest or mypy.

Verify: run `ruff check` and `ruff format --check` on the files you own.

Report back: (1) each file changed with a one-line summary;
(2) decisions, rationale, and open questions; (3) verification output;
(4) confirmation that nothing outside your owned files was modified.
```

Spawn one executor per ladder step, after the previous step passes:

```text
In this repository, read docs/upgrade-plan.md fully, especially
"Shared context", "Repository conventions", and the ladder. You hold
the environment token for step <Vn>. Execute exactly that step.

Scope: run the step's commands; edit nothing. If the step fails, stop
and report the failure with the full output; do not attempt fixes.

Report back: (1) each command and its outcome; (2) evidence artifacts
produced; (3) any observed deviation from the plan.
```

## Acceptance criteria

The plan is complete when every item of the evidence list in
[ADR 0003](adr/0003-latest-stable-pins-and-exploration-lanes.md) holds
on this branch, every ladder step has a recorded result, and ADR 0003
moves from proposed to accepted with evidence links.
