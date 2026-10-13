# Overview: ActiveGraph, this adapter, and the three workstreams

This page is the plain-language tour. Everything here is stated precisely —
with its evidence and its limits — in [architecture](architecture.md),
[compatibility](compatibility.md), and [conformance](conformance.md).

## What ActiveGraph is

[ActiveGraph](https://github.com/yoheinakajima/activegraph-packs) is a Python
framework for building agents on an event-sourced graph. Instead of letting an
agent mutate a database directly, the runtime appends every decision and
observation to an ordered event log and projects that log into a graph of
objects and relations. Behaviors read and react to the graph until the run is
idle. The event log is the authority; the graph is a rebuildable view.
A conformance suite ships with it, so any storage backend can be held to the
same contracts, and an optional pack library defines ready-made agent
behaviors and fixtures.

## What this adapter adds

`activegraph-surrealdb` implements ActiveGraph's two storage contracts — the
`EventStore` (append-ordered, hash-chained events with per-run heads,
stale-writer detection, and prefix forks) and the `GraphStore` (objects,
patches, and native `TYPE RELATION` edges) — on one SurrealDB deployment,
with query hooks pushed down into SurrealQL rather than scanned in Python.

The practical shape: auditable memory and current state share a single
database. A process can crash, restart, and rebuild its graph from events;
an experiment can fork a run's history at any decision point; and every read
is backed by the same engine that stored the evidence.

## The three workstreams

### 1. Database upgrades with conformance testing

Version changes are gated by evidence, not hope. Conformance suites re-prove
ordering, isolation, hash chains, and replay on every candidate version, and
exploration lanes (TLS, the SurrealKV engine, the embedded SDK) are tested in
isolation without weakening the stable lane. A rollback lane proves a database
can be restored and that newer schemas are rejected rather than mis-read.
The serialized ladder and its recorded results live in
[upgrade-plan](upgrade-plan.md).

### 2. Telemetry and traces for evals

The hash-chained event log *is* the trace — no instrumentation layer to bolt
on. A run can be reproduced exactly, forked at a decision so two alternatives
play out from the same history, and diffed deterministically. Integrity
verification detects any tampering with recorded events. That is the
substrate for evaluating agent behavior: reproduce, compare alternatives
fairly, and trust the record.

### 3. An agentic harness

The pack harness loads published agent packs onto a runtime whose memory
persists in SurrealDB. Agents get durable, restartable memory; a crashed
process replays to repair its view; and experiments fork rather than
hand-copy state. It is a conformance-tested, single-database home for running
agent workloads with audit and experimentation built in.

## Status

All of this is a research preview: passing tests are evidence for the tested
versions and failure paths only, not a production certification. See the
[roadmap](../ROADMAP.md) for the open evidence gates.
