# Fork, diff, and replay experiment

## Question

Why keep an authoritative event log when SurrealDB can already store and query a
graph?

The experiment creates one decision run, forks it at a shared event, makes two
different decisions, diffs the outcomes, deliberately contaminates one graph
projection with a ghost object, and rebuilds it from events.

If the event log is authority and the projection is derived, the trace must show:

1. parent and child share the same event prefix;
2. each has a distinct suffix and divergent current state;
3. the parent event history is unchanged by the fork;
4. replay reproduces the parent's pre-contamination graph exactly; and
5. the ghost disappears because it was never in the event history.

## Run it

Start the pinned environment and export `.env` as described in
[deployment.md](deployment.md), then run:

```bash
python examples/replay_fork_diff.py
```

The script exits non-zero if a required invariant is false. Its JSON output has
this shape; IDs and hashes are generated at runtime:

```json
{
  "event_log": {
    "event_count": 2,
    "head_hash": "<sha256>",
    "next_sequence": 2
  },
  "fork": {
    "divergent_object_ids": ["<object-id>"],
    "fork_only_event_ids": ["<event-id>"],
    "parent_only_event_ids": ["<event-id>"],
    "shared_event_ids": ["<event-id>"]
  },
  "projection": {
    "ghost_removed": true,
    "rebuilt_exactly": true,
    "status": "ready"
  },
  "scope": "experiment_<random>"
}
```

## What the trace proves

For the pinned server, SDK, package commit, and successful execution, the trace
is direct evidence that:

- a fork is a prefix copy rather than an edit to the parent;
- alternative suffixes produce a meaningful ActiveGraph diff;
- event records survive independently of a corrupted/stale graph row; and
- replay clears and deterministically reconstructs the provider projection.

That is the practical value of an event-sourced reactive graph: operators can
recover a query model and investigators can compare alternative decisions
without destroying the original sequence.

ActiveGraph's deterministic ID generator may assign the same event ID to the
first divergent event in both runs. That is valid because event identity is
scoped by run. The experiment therefore compares complete events and projected
state, not event-ID strings alone, when proving that a parent-only suffix did
not leak into the fork.

## What it does not prove

The experiment does not prove:

- atomic event-plus-projection acceptance;
- behavior after an append failure in ActiveGraph `1.10.0`;
- snapshot/compaction or full `Runtime.load()` resume;
- TLS, authorization, backup/restore, distributed availability, or upgrades;
- representative performance or capacity; or
- correctness for application schemas outside the provider.

Use the broader [conformance](conformance.md) and qualification suites for their
specific claims, and keep the promotion gates in the ADRs visible.
