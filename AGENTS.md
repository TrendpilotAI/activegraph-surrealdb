# Project Instructions for AI Agents

This file provides instructions and context for AI coding agents working on this project.

<!-- BEGIN BEADS INTEGRATION v:1 profile:full hash:bacef91e -->
## Issue Tracking with bd (beads)

**IMPORTANT**: This project uses **bd (beads)** for ALL issue tracking. Do NOT use markdown TODOs, task lists, or other tracking methods.

### Why bd?

- Dependency-aware: Track blockers and relationships between issues
- Git-friendly: Dolt-powered version control with native sync
- Agent-optimized: JSON output, ready work detection, discovered-from links
- Prevents duplicate tracking systems and confusion

### Quick Start

**Check for ready work:**

```bash
bd ready --json
```

**Create new issues:**

```bash
bd create "Issue title" --description="Detailed context" -t bug|feature|task -p 0-4 --json
bd create "Issue title" --description="What this issue is about" -p 1 --deps discovered-from:bd-123 --json
```

**Claim and update:**

```bash
bd update <id> --claim --json
bd update bd-42 --priority 1 --json
```

**Complete work:**

```bash
bd close bd-42 --reason "Completed" --json
```

### Issue Types

- `bug` - Something broken
- `feature` - New functionality
- `task` - Work item (tests, docs, refactoring)
- `epic` - Large feature with subtasks
- `chore` - Maintenance (dependencies, tooling)

### Priorities

- `0` - Critical (security, data loss, broken builds)
- `1` - High (major features, important bugs)
- `2` - Medium (default, nice-to-have)
- `3` - Low (polish, optimization)
- `4` - Backlog (future ideas)

### Workflow for AI Agents

1. **Check ready work**: `bd ready` shows unblocked issues
2. **Claim your task atomically**: `bd update <id> --claim`
3. **Work on it**: Implement, test, document
4. **Discover new work?** Create linked issue:
   - `bd create "Found bug" --description="Details about what was found" -p 1 --deps discovered-from:<parent-id>`
5. **Complete**: `bd close <id> --reason "Done"`

### Quality
- Use `--acceptance` and `--design` fields when creating issues
- Use `--validate` to check description completeness

### Lifecycle
- `bd defer <id>` / `bd supersede <id>` for issue management
- `bd stale` / `bd orphans` / `bd lint` for hygiene
- `bd human <id>` to flag for human decisions
- `bd formula list` / `bd mol pour <name>` for structured workflows

### Sync

bd stores issue history in Dolt:

- Each write auto-commits to Dolt history
- Use `bd dolt push`/`bd dolt pull` for remote sync
- Do not treat `.beads/issues.jsonl` as the sync protocol

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/core-concepts/sync-concepts.md for details and anti-patterns.

### Important Rules

- ✅ Use bd for ALL task tracking
- ✅ Always use `--json` flag for programmatic use
- ✅ Link discovered work with `discovered-from` dependencies
- ✅ Check `bd ready` before asking "what should I work on?"
- ❌ Do NOT create markdown TODO lists
- ❌ Do NOT use external issue trackers
- ❌ Do NOT duplicate tracking systems

For more details, see README.md and https://github.com/gastownhall/beads/blob/main/docs/getting-started/quickstart.md.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   bd dolt push
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.

<!-- END BEADS INTEGRATION -->


## Build & Test

Python ≥3.11. Fresh environment:

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
```

Test ladder (see docs/upgrade-plan.md for the serialized evidence ladder V0–V9):

```bash
# Unit (no server needed; the embedded lane self-skips without its gate)
pytest -m 'not surrealdb and not qualification and not embedded'

# Live integration (needs a running server)
docker compose up -d
pytest -m surrealdb tests/integration

# Qualification (controls a pinned Docker server; restart/kill sequences)
ACTIVEGRAPH_SURREALDB_QUALIFY=1 pytest -m qualification tests/qualification

# Embedded SDK lane (isolated venv; expected-rejection evidence)
ACTIVEGRAPH_SURREALDB_EMBEDDED=1 .venv-embedded/bin/python -m pytest tests/embedded -m embedded

# Published evidence example
python examples/replay_fork_diff.py
```

Quality gates (all must pass before a commit is accepted):

```bash
ruff check && ruff format --check && mypy
python -m build && twine check dist/*
```

Exploration lanes (compose services / markers): TLS lane via
`docker compose --profile tls` (wss), upgrade lane via the `upgrade` marker,
surrealkv lane via the engine test variable, embedded lane in an isolated
`.venv-embedded` with `surrealdb[embedded]==3.0.0b10` (expected-rejection
evidence). Lane tests are selected-but-skipped unless their gate env vars
are set.

## Architecture Overview

`src/activegraph_surrealdb/` is a research-preview provider that runs two
distinct ActiveGraph responsibilities on one SurrealDB deployment
(pins: `activegraph==1.12.0`, `surrealdb==2.0.0` SDK):

- **EventStore** (`event_store.py`) — the authoritative, append-ordered event
  log; transactional append/head checks; uncompacted-prefix forks.
- **GraphStore** (`graph_store.py`) — a disposable graph projection that can
  be erased and rebuilt (replayed) from the event log; native relation
  storage, SurrealQL pushdown.
- Supporting modules: `serde.py` (exact-JSON serialization contract),
  `connection.py`, `config.py`, `runtime.py`, `keys.py`, `errors.py`,
  `schema.surql`.

Docs are authoritative: `docs/architecture.md`, `docs/compatibility.md`,
`docs/conformance.md`, `docs/deployment.md`, `docs/security-model.md`,
`docs/upgrade-plan.md`, and ADRs `docs/adr/0001..0005`.

## Conventions & Patterns

- **Conventional commits** (`feat:`, `fix:`, `test:`, `docs:`, `chore:`), one
  logical change per commit; the coordinator commits after review.
- **Evidence-gated roadmap**: `ROADMAP.md` checkboxes move only when a
  boundary is repeatably demonstrated; passing tests are evidence for tested
  versions only, never production-readiness claims.
- **Contract-first**: upstream limitations are recorded, not patched —
  `tests/known_issues/` holds xfail/evidence tests for upstream behaviors
  (e.g. ActiveGraph append-before-projection); no private runtime forks.
- **pytest runs with `--strict-markers`**; markers are registered in
  per-suite `conftest.py` files: `surrealdb` (integration), `qualification`
  and `upgrade` (qualification), `embedded` (embedded lane).
- **Lint/type config**: ruff line-length 100, rules `E,F,I,UP,B,C4,SIM`;
  mypy `strict = true` over `src` and `examples`.
