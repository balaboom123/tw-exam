# tw-exam

Mirror, normalize, audit, bundle, and publish Taiwan examination papers from provider-scoped official sources into a site-scoped public catalog.

Start with the [documentation router](docs/README.md). Repository authority and agent rules are in [`AGENTS.md`](AGENTS.md); the generated [provider index](docs/providers/README.md) reports current reviewed coverage.

## Ownership at a glance

- `catalog/` and `schemas/`: executable taxonomy, mappings, source scope, and serialized contracts
- `app/providers/` and `data/providers/<provider_id>/`: source ingestion and retained provider state
- `mirror/providers/<provider_id>/`: provider-owned downloaded payloads
- `data/sites/default/` and `bundles/sites/default/`: site publication feeds, release assets, and bundles
- `frontend/`: public presentation over site-generated data

Generated provider and site outputs are not documentation sources of truth and should not be edited to implement behavior.

## Common commands

```bash
uv run python -m app discover --provider moex
uv run python -m app sync-incremental --provider moex --site-id default
uv run python -m app audit-catalog --repo-root . --site-id default --strict
uv run python -m app history-audit --repo-root . --site-id default --strict
uv run python -m app publish-site --site-id default --repository <owner>/<repo>
```

The full generated CLI list is in the [command reference](docs/operations/commands.md); task sequences and recovery guidance are in the [operations runbook](docs/operations/runbook.md).

## Bundle format

Primary bundle filenames derive from versioned identities and include a stable digest. Release assets may retain compatibility aliases. Archive paths preserve years and readable subject labels; the embedded manifest carries machine identity and paper locators. The [bundler](app/bundler.py) owns these formats.

## Licensing

Repository code is available under the [MIT License](LICENSE). Examination papers and other source materials retain their original terms; see [data sources and reuse](DATA-LICENSE.md).

## Older download links

2026-09-26 notice: the original v1 bundle Releases are proposed for retirement.
Use the [current catalog](https://balaboom123.github.io/tw-exam/) to find the
separate v2 exam bundles. Saved v1 download links will stop working if retirement
is approved. The [retirement proposal](docs/decisions/ADR-2026-09-26-v1-release-retirement.md)
defines the affected Releases and required verification; no deletion is scheduled
or authorized by this notice alone.

## Verification

```bash
uv run python -m pytest -q
uv run python scripts/validate_source_inventory.py
uv run python scripts/validate_publication.py
uv run python scripts/render_docs.py
uv run python scripts/validate_docs.py --check
```

Run frontend checks from `frontend/` with `npm test`, `npm run lint`, and `npm run build`.
