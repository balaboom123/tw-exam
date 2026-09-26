# Operations runbook

This runbook covers the provider-to-site path. Use the generated [command reference](commands.md) for the complete CLI surface and option help.

## Prerequisites

- Python supported by `pyproject.toml` and `uv`
- Node and npm for `frontend/`
- a persistent mirror for full or repair syncs
- `GH_TOKEN` when downloading or publishing GitHub Release assets

Run commands from the repository root. Local Python examples use `uv run python` consistently.

## Refresh one provider

Use the smallest source operation that matches the event:

```bash
uv run python -m app discover --provider <provider_id>
uv run python -m app sync-incremental --provider <provider_id> --site-id default
```

Use `probe-latest` and `sync-targeted` when the provider implements a probe model and the changed event set is known. Use `sync-full` only for bootstrap, broad reconciliation, or recovery. Inspect provider-owned failures and review state before publication:

```text
data/providers/<provider_id>/sync-failures.json
data/providers/<provider_id>/review-queue.json
data/providers/<provider_id>/source-manifest.json
data/providers/<provider_id>/sync-status.json
```

The [generated index](../providers/README.md) projects reviewed source facts; [source judgment](../providers/notes.md) records provider-specific boundaries and operational exceptions.

Hosted provider runs use the matrix caller that owns the provider in `.github/workflows/`. Inspect the provider job's sync summary and artifact when diagnosing a failure. Publication queues behind other site writers and checks current `main` before applying the sync snapshot; a stale provider baseline requires a fresh caller run. Quarantined providers continue collecting state through provider-only commits.

## Inspect normalization reviews

Expand a provider's compact review ledger without reading its paper catalog:

```bash
uv run python -m app review-queue --provider <provider_id> --output .tmp/reviews.json
```

The expanded records retain their original review reasons and source evidence.
They are diagnostics; resolve them through normalization rather than editing
stored tables by hand.

## Repair retained failures

Prefer the recorded failure set over a broad recrawl:

```bash
uv run python -m app repair-failures --provider <provider_id>
```

If mirror storage contains byte-identical payloads, preview and then explicitly apply deduplication. Orphan pruning is fail-closed and requires a provider plus `--apply`; follow [recovery](recovery.md) before deleting retained payload paths.

## Back up provider mirrors

Use the provider-scoped public Release snapshot helper described in
[recovery](recovery.md#durable-provider-mirror-backup) to retain payloads beyond
Actions cache eviction. A mirror backup does not publish site bundles.

## Audit before publication

```bash
uv run python scripts/validate_source_inventory.py
uv run python scripts/validate_publication.py
uv run python -m app audit-catalog --repo-root . --site-id default --strict --output .tmp/catalog-audit.json
uv run python -m app history-audit --repo-root . --site-id default --strict --output .tmp/history-audit.json
uv run python -m app plan-release --repo-root . --site-id default --output .tmp/release-plan.json
```

`--skip-mirror-check` is acceptable only in an environment such as CI where the gitignored mirror is intentionally absent. An operator with the mirror available should not skip it.

## Publish the default site

Publication aggregates provider state, applies site policy, builds bundles, and assigns release shards:

```bash
uv run python -m app publish-site --site-id default --repository <owner>/<repo>
```

Verify `data/sites/default/bundles.json`, `data/sites/default/release-assets.json`, and the planned asset-to-tag assignments before any external upload. Release uploads and pruning are separate explicit operations owned by `.github/scripts/release_assets.py` and the release workflows.

The frontend feed also projects reviewed source names/links and successful event sync receipts. Republish after updating source attribution or completing a sync. For an attribution-only refresh, an empty affected-ID publish plan preserves existing bundles and refreshes site metadata without rebuilding ZIPs. To roll back these optional v2 fields, regenerate the frontend feed with the previous publisher; release bytes and assignments need no changes. Older data with no receipts displays an unrecorded sync date until a successful sync establishes one.

## Verify the repository

```bash
uv run python -m pytest -q
uv run python scripts/render_docs.py
uv run python scripts/validate_docs.py --check
```

Run frontend checks from their working directory:

```bash
cd frontend
npm test
npm run lint
npm run build
```
