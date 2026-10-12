# Telemetry: Arize AX export

The conformance layers produce evidence that this repository records as
traces and evaluations in [Arize AX](https://arize.com). Export is optional,
explicitly enabled, and strictly metadata-only.

## What gets exported

| Span | Attributes |
| --- | --- |
| `pytest::session` (one per run) | command and marker selection, commit SHA and dirty status, ActiveGraph / adaptor / SDK / Python versions, OS, exit status, pass/fail/skip/xfail counts |
| `pytest::<nodeid>` (one per test) | node id, layer (the five layers from [conformance.md](conformance.md)), outcome, duration |

Released-conformance cases (`tests/integration/*conformance.py`) also carry
OpenInference evaluation attributes on their span, which Arize AX renders as
evaluations:

- `evaluation.name`: `conformance`
- `evaluation.label`: `passed`, `failed`, `skipped`, `expected-failure`,
  `xpassed`, or `xpass-strict`
- `evaluation.score`: `1.0` for `passed` and `expected-failure`; `0.0` for
  `failed`, `xpassed`, and `xpass-strict`; absent for `skipped`
- `evaluation.explanation`: truncated failure text, when present

An `expected-failure` scores `1.0` because the suite behaved as documented
for a known upstream gap. An `xpass-strict` scores `0.0`: a known-issue test
that suddenly passes means the upstream contract moved and this repository
must act (version bump, xfail removal).

## Enabling export

Export requires the optional OpenTelemetry dependencies plus credentials and
an explicit flag. All three must be present:

```bash
python -m pip install -e '.[dev,telemetry]'
export ARIZE_SPACE_ID=...            # from the Arize AX space settings
export ARIZE_API_KEY=...             # from the Arize AX API keys
ACTIVEGRAPH_SURREALDB_ARIZE=1 pytest -m surrealdb tests/integration
```

Without `ACTIVEGRAPH_SURREALDB_ARIZE=1` nothing is imported, nothing is
sent, and the test run is byte-for-byte unchanged. Missing credentials while
the flag is set disables export silently; a missing `opentelemetry`
installation prints one warning line.

| Variable | Default | Meaning |
| --- | --- | --- |
| `ACTIVEGRAPH_SURREALDB_ARIZE` | `0` | Opt-in export switch |
| `ARIZE_SPACE_ID` | unset | Arize AX space identifier |
| `ARIZE_API_KEY` | unset | Arize AX API key |
| `ARIZE_PROJECT_NAME` | `activegraph-surrealdb` | Target Arize AX project |
| `ARIZE_OTLP_ENDPOINT` | `https://otlp.arize.com/v1` | OTLP/HTTP traces endpoint |

The endpoint and `space_id`/`api_key`/`Arize-Project` headers follow the
standard Arize AX OpenTelemetry ingestion convention; adjust
`ARIZE_OTLP_ENDPOINT` for region-specific or self-hosted ingestion.

## Security posture

- Only metadata is exported: test identities, outcomes, durations, and
  environment versions. Event payloads, graph objects, relations, patch
  values, SurrealQL query text, and credentials are never exported.
- The failure `evaluation.explanation` is truncated assertion text and can
  reference test-generated identifiers (event or object ids created by the
  test itself). It never dumps store contents.
- Credentials are read from the environment and used only as export headers;
  they are never placed in span attributes or logs.
- Export failures print a warning and never fail the run. Telemetry is
  fail-open; the provider contracts remain fail-closed.

## Limitations

- `pytest-xdist` is not supported: the plugin assumes a single process.
- The Docker image digest recorded under the qualification layer is part of
  manual evidence (see conformance.md), not of this export; the session span
  records what pytest can observe directly.
- Interrupted sessions (Ctrl-C, crash) flush completed cases only.
