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

## Unpublished v2 alias metadata

The [proposed alias decision](../decisions/ADR-2026-09-27-unused-v2-release-aliases.md)
requires its own explicit acceptance before activating the default-site policy.
Original v1 Release retirement is a separate authorization.

Before activation, verify against freshly fetched main and GitHub:

```bash
GITHUB_REPOSITORY=<owner>/<repo> uv run python .github/scripts/release_assets.py primary-only-check
```

This read-only gate rejects hosted aliases, other unexpected ZIPs, and missing,
stale, or unverifiable primary downloads. If it fails, keep the current metadata
and investigate before merging the policy change. Regenerate site metadata with
the site writer and verify that removing `legacy_asset_names` is the only JSON
change; the frontend feed and primary names, tags, URLs, and checksums must match.
Run final CI and repeat the remote check immediately before merging. No remote
prune or asset deletion is part of this procedure.

## Frontend verification

```bash
cd frontend
npm test
npm run lint
npm run build
```

## Publish and verify

Use the repository release workflow to ensure and upload expected site assets. Prune only after expected assets are present and the generated inventory proves which external assets are stale. Deploy the site, verify representative bundle URLs across release shards, and retain the audit reports for the release review.
