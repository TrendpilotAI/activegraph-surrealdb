# Security policy

## Support status

`activegraph-surrealdb` is a research preview. No release is currently supported
for production security use, and there is no long-term support branch.

| Version | Status |
| --- | --- |
| `0.1.0a1` and unreleased main | Research evaluation only |
| Any earlier experiment | Unsupported |

This status does not make security reports less important. It means operators
must not treat a patch cadence or green CI result as a production guarantee.

## Reporting a vulnerability

Do not file a public issue for a suspected vulnerability. Use GitHub's private
vulnerability-reporting flow from this repository's **Security** tab. If that
flow is unavailable, contact the repository owner privately and ask for a secure
reporting channel before sharing exploit details.

Include only the minimum information needed to reproduce:

- affected commit or package version;
- SurrealDB server, Python SDK, ActiveGraph, Python, and OS versions;
- transport and storage engine;
- synthetic reproduction steps;
- expected versus observed behavior and likely impact; and
- whether a credential, real record, or public deployment may be exposed.

Do not attach production databases, event payloads, credentials, or customer
data. Redact tokens from command output and rotate any secret that was exposed.

Maintainers will acknowledge reports on a best-effort basis, validate scope,
coordinate a fix and release note when warranted, and agree on disclosure timing
with the reporter. This preview does not promise a response or remediation SLA.

## In-scope examples

- query or record-ID injection;
- scope/run isolation bypass;
- stale-writer acceptance or event/head transaction corruption;
- credential disclosure through representations, errors, events, or traces;
- serving a failed/incomplete projection as ready;
- unsafe schema migration or unknown-version acceptance; and
- package or CI supply-chain compromise.

General SurrealDB or ActiveGraph vulnerabilities should also be reported to the
owning upstream project. A provider reproduction is welcome when it demonstrates
how the issue crosses this package's boundary.

## Safe research

Test only systems and data you own or have explicit authorization to assess.
Use the disposable local Compose environment. Do not perform denial-of-service,
social engineering, persistence, data extraction, or destructive tests against
shared or public infrastructure.

The current threat model and non-goals are documented in
[docs/security-model.md](docs/security-model.md).
