# Release checklist

## Preflight

```bash
uv run python -m pytest -q
uv run python scripts/validate_source_inventory.py
uv run python scripts/validate_publication.py
uv run python scripts/render_docs.py
uv run python scripts/validate_docs.py --check
uv run python -m app audit-catalog --repo-root . --site-id default --strict --output .tmp/catalog-audit.json
uv run python -m app history-audit --repo-root . --site-id default --strict --output .tmp/history-audit.json
uv run python -m app plan-release --repo-root . --site-id default --output .tmp/release-plan.json
```

With the persistent mirror available, do not use `--skip-mirror-check`.

## Build the site projection

```bash
uv run python -m app publish-site --site-id default --repository <owner>/<repo>
uv run python -m app plan-release --repo-root . --site-id default --output .tmp/release-plan.json
```

Review provider failures, publication quarantine, expected asset names, release tags, and the release plan before uploading anything.

## Frontend verification

```bash
cd frontend
npm test
npm run lint
npm run build
```

## Publish and verify

Use the repository release workflow to ensure and upload expected site assets. Prune only after expected assets are present and the generated inventory proves which external assets are stale. Deploy the site, verify representative bundle URLs across release shards, and retain the audit reports for the release review.

## V1 retirement

The [proposed retirement decision](../decisions/ADR-2026-09-26-v1-release-retirement.md)
defines the historical Release scope. Publication pruning is not a substitute
for this separately authorized operation.

1. Confirm that the decision is accepted and the dated README notice is public.
2. Verify current site publication, provider serialization, and the deployed
   frontend contain no download references to the retiring tags. Provider URL
   removal must be merged before retirement; historical git revisions are not
   evidence of a current live dependency.
3. Run the normal publication preflight. With authenticated `gh` and
   `GITHUB_REPOSITORY` set, run the read-only external coverage check:

   ```bash
   uv run python .github/scripts/release_assets.py coverage
   ```

   Require both `bootstrap_required` and `stale_required` to be false. This
   checks primary v2 names and digests; it does not audit historical v1 contents.
4. Use the GitHub API to capture each retiring Release's ID and its complete,
   paginated asset inventory, including name, size, digest, and download count.
   Retain the captured report outside generated repository state. Review the
   loss of external saved links and hosted rollback bytes before requesting
   explicit deletion authorization.
5. Immediately before deletion, re-read the Release IDs and asset inventories.
   If they differ from the reviewed report, refresh the report and authorization.
   Delete only the authorized Release IDs. Do not remove git tags or invoke
   broad asset pruning as part of this operation.
6. Re-run v2 coverage and verify representative deployed download links. Record
   the deleted IDs and released bytes in the retirement report. Confirm active
   v2 aliases, site assignment metadata, and provider retention are unchanged.
