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

Verify `data/sites/default/bundles.json`, `data/sites/default/release-assets.json`, and the planned asset-to-tag assignments before any external upload. The upload and prune stages are owned by `.github/scripts/release_assets.py` and the release workflows; the full republish script runs them after verification.

For a full rebuild after a classification or bundling change, run
`scripts/republish.sh` from a machine that holds `mirror/` or
`bundles/sites/default/`. Pass `--download` when neither is present; it needs
about 50 GB of free disk. The script stops at the first failing stage, uploads
only after the content audit passes, and leaves the commit to you.

The frontend feed also projects reviewed source names/links and successful event sync receipts. Republish after updating source attribution or completing a sync. For an attribution-only refresh, an empty affected-ID publish plan preserves existing bundles and refreshes site metadata without rebuilding ZIPs. To roll back these optional v2 fields, regenerate the frontend feed with the previous publisher; release bytes and assignments need no changes. Older data with no receipts displays an unrecorded sync date until a successful sync establishes one.

## When something fails

| What you see | Meaning | Action |
| --- | --- | --- |
| Health issue "`<caller>` concluded failure" listing a `<provider> sync` job | The source may be unreachable or its layout may have changed | Open the job log. A transient fetch or download error may clear on the next schedule. "drops below the reviewed source inventory floor" or zero discovered events means the adapter needs investigation. |
| Health issue for `verify-archives` | A shard download or archive content check failed | Read the failed job log. For a confirmed ZIP/catalog mismatch, run `scripts/republish.sh`, then rerun the failed shard. Retry a failed download before republishing. |
| Health issue "quarantine-review" | A quarantine entry passed its `review_by` date | Re-check the entry's evidence; remove the entry or set a new date in `catalog/mappings/publication-quarantine.json`. |
| Health issue "no successful run within the last N days" | A schedule stopped firing or every run fails | Re-enable the workflow if disabled, then dispatch it by hand. [GitHub disables public-repository schedules after 60 days without activity](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule). |
| CI fails with "public titles expose internal identifiers" | A new variant or stage has no readable label | Add the wording in `_variants` or `_STAGE_LABELS` in `app/classification.py`, then run `scripts/republish.sh`. |

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
