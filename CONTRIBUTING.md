# Contributing

Thank you for improving `activegraph-surrealdb`. This project is contract-first:
we agree on the observable behavior and failure boundary before making a
provider implementation pass it.

## Before starting

Use an issue to establish the problem, owning layer, proposed invariant, and
non-goals. A good proposal answers:

1. Is this behavior owned by ActiveGraph, the provider, SurrealDB, or an application?
2. What minimal reproduction currently fails?
3. What result would prove the fix, including its failure path?
4. Does it change the supported version/transport/engine matrix?

Application-specific schemas, features, and data do not belong in this generic
provider. Upstream runtime changes should be proposed to ActiveGraph rather than
implemented by copying or patching private runtime code here.

## Development setup

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
pytest -m 'not surrealdb and not qualification'
```

For live integration and qualification, use the disposable environment in
[docs/deployment.md](docs/deployment.md). Qualification tests are destructive to
the named container and must never target a shared or production service.

## Change workflow

1. Add or refine the smallest test that expresses the public contract.
2. Confirm it fails for the expected missing behavior, not a setup error.
3. Implement against public ActiveGraph and SurrealDB interfaces.
4. Run the focused test, then the relevant complete layer.
5. Run formatting, typing, build, and package checks.
6. Update compatibility, security, or operational docs when a boundary changes.

Useful checks:

```bash
ruff check src examples
ruff format --check src examples
mypy
pytest -m 'not surrealdb and not qualification'
pytest -m surrealdb tests/integration
python -m build
python -m twine check dist/*
git diff --check
```

Do not weaken an assertion, introduce a mock in place of required live evidence,
or mark a provider failure xfail merely to make CI green. Strict xfails under
`tests/known_issues` are reserved for linked upstream lifecycle issues.

## Pull requests

Keep changes bounded. A pull request should include:

- a plain-language explanation of why the behavior matters;
- the technical cause and owning layer;
- the chosen solution and explicit non-goals;
- repeatable test commands and results;
- versions and environment for live evidence;
- security, migration, and compatibility impact; and
- provenance for any adapted material.

Do not include real payloads, credentials, customer identifiers, private paths,
database files, caches, or generated build output. Follow
[docs/provenance.md](docs/provenance.md) before staging.

## Code style

- Support Python `3.11+` and use type annotations.
- Prefer small explicit interfaces and fail-closed errors.
- Bind values and complete `RecordID` objects; never interpolate caller-controlled
  identifiers or types into SurrealQL.
- Keep event authority separate from projection state.
- Use synthetic fixtures and deterministic assertions.
- Document every private test hook; it must not alter normal runtime behavior.

## Licensing

Contributions are accepted under the repository's
[Apache License 2.0](LICENSE). By submitting a contribution, you represent that
you have the right to provide it under that license. Identify third-party source
and preserve required copyright, license, and NOTICE material.
