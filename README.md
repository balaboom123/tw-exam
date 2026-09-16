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

Bundle filenames use Chinese display names plus canonical IDs. Release assets can include legacy compatibility alias names during migration. Archive entry paths remain human-readable while machine identity stays in bundle metadata.

- Bundle asset: `護理師__nurse.zip`
- Archive entry: `115/115030_護理師/101_0101_基礎醫學_試題.pdf`

## Verification

```bash
uv run python -m pytest -q
uv run python scripts/validate_source_inventory.py
uv run python scripts/validate_publication.py
uv run python scripts/render_docs.py
uv run python scripts/validate_docs.py --check
```

Run frontend checks from `frontend/` with `npm test`, `npm run lint`, and `npm run build`.
