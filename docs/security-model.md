# Security model

## Scope

The provider protects the integrity and isolation of its own event and graph
records inside a configured SurrealDB namespace/database. It does not provide
application authentication, customer authorization, secret management, network
perimeter controls, or policy for unrelated tables.

## Assets and boundaries

Important assets are:

- ordered event payloads and their run metadata;
- per-run sequence/head state;
- scope/run isolation;
- projection readiness state;
- database credentials and endpoints; and
- schema-version metadata.

The Python process and SurrealDB server are trusted to execute the provider
correctly. Callers are not trusted to provide identifier strings safe for query
interpolation. Networks are untrusted unless separately protected; the local
`ws://` lane binds only to loopback and is not a remote-deployment pattern.

## Required controls

| Risk | Provider control |
| --- | --- |
| Identifier/query injection | Opaque derived record keys; bound values and complete `RecordID` parameters; fixed provider table names |
| Cross-tenant or cross-run mutation | Every record and destructive operation is scoped by both scope and run |
| Concurrent append race | Event and head update share one SurrealDB transaction; stale expected heads fail closed |
| Partial replay served as current | Projection status changes to incomplete before clear and to ready only after successful replay |
| Silent event alteration | Canonical event material and previous hash are linked and verifiable |
| Credential disclosure | Secrets are environment-provided and must be absent from IDs, reprs, errors, events, and traces |
| Schema drift | Explicit version metadata; unknown versions fail closed |

Hash chaining is tamper evidence, not a digital signature, access-control system,
or defense against a database administrator who can rewrite both events and
hashes. Stronger adversaries require signed checkpoints or an independently
anchored digest, which is outside this preview.

## Credentials

- Never commit `.env` or real connection values.
- Use the example root identity only for an isolated local test server.
- Use a dedicated least-privilege identity and `wss://` for any remote evaluation.
- Do not include passwords or tokens in exceptions, object representations,
  record IDs, event payloads, traces, or CI artifacts.
- Rotate any credential that appears in a public log or issue; deleting the text
  is not sufficient remediation.

The initial configuration accepts `wss://` syntax but has no qualified TLS lane.
Certificate validation, proxies, workload identity, and rotation need explicit
deployment tests before a remote security claim.

## Non-atomic runtime boundary

The provider does not promise one transaction across event append and graph
projection. A malicious input is not the only source of inconsistency; a normal
process or network failure between those operations can also diverge state.
Monitoring must detect failed writes and incomplete projections, and replay must
repair from the authoritative event log.

## Application responsibilities

Applications sharing the database must separately define:

- record/table permissions and identity mapping;
- encryption and key management;
- data classification, retention, deletion, and legal holds;
- source connector credentials and rate limits;
- audit access and redaction rules; and
- backup, restore, incident response, and high availability.

Provider scope/run strings are isolation dimensions, not proof that an
authenticated caller is authorized to use them.

For vulnerability reporting, follow [the repository security policy](../SECURITY.md).
