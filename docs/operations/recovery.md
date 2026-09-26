# Recovery guide

Preserve retained provider state and evidence first. Use the smallest repair that restores a checked invariant, then rerun the generator and audit that own the affected output.

## Scenario 1: discovery or sync fails

1. Identify the provider, source event, stage, and exact failure in `data/providers/<provider_id>/sync-failures.json`.
2. Distinguish a transient transport failure from schema drift, an invalid payload, or a reproducible official-source blocker.
3. Retry `sync-targeted` for a known changed set or `repair-failures` for recorded failures.
4. Use `sync-incremental` for a bounded recent window; reserve `sync-full` for broadly untrusted state.
5. If non-deployable partial output already reached `main`, first record the reviewed source status in its authoritative inventory/evidence, then restore only the affected generated files from the last deployable commit in a repair change.
6. Confirm the generated-state commit guard left `main` at its last deployable state; use the failed Actions run and workflow-health issue as the failure record.
7. Do not hand-edit generated state to make publication pass.

For a matrix caller, identify the provider from the failed job name and its sync summary. Provider snapshots retain failure evidence in the run's artifacts; the mirror cache preserves downloaded payloads. If publication reports that the provider changed after sync started, rerun the caller against current `main`. A publication-only retry reuses its original artifact and exact mirror cache; if either has expired, run a fresh sync.

## Scenario 2: an official source is blocked

Capture the narrowest reproducible event or file evidence and update `catalog/source-coverage/<provider_id>.json` where a coverage ledger exists. Keep valid records even when sibling files are blocked.

```bash
uv run python -m app history-audit --repo-root . --site-id default --strict --output .tmp/history-audit.json
uv run python scripts/validate_publication.py
```

Do not delete raw pages, retained failures, or source evidence merely to satisfy a gate. Remove an exception when the official source becomes available; strict audits intentionally reject orphaned exceptions.

## Scenario 3: catalog or identity audit fails

Inspect the emitted audit report before changing mappings. Fix executable taxonomy or provider normalization at its owner, then reclassify retained state and republish:

```bash
uv run python -m app migrate-catalog --repo-root . --site-id default
uv run python -m app audit-catalog --repo-root . --site-id default --strict
uv run python -m app publish-site --site-id default --repository <owner>/<repo>
```

Do not weaken bundle-purity checks or introduce prose-only classification exceptions.

## Scenario 4: publication or release coverage differs

1. Confirm required provider state and mirrors are present.
2. Rerun `publish-site` and `plan-release` locally.
3. Compare `data/sites/default/release-assets.json` with the release plan and external release assets.
4. Upload missing expected assets before pruning unexpected ones.
5. Never collapse shards or move assets between tags manually; shard assignment is deterministic executable policy.

When a targeted sync makes a previously unpublished bundle meet the site's
minimum-year policy, there is no release ZIP containing its retained older
papers. With `--download-affected-bundles`, targeted, incremental, and full sync
download existing affected release ZIPs and restore newly public files from
their recorded source URLs before writing publication inputs.
Restoration validates the payload and recorded checksum; a failure stops the
sync. If the source bytes have changed, refresh that source exam before retrying.
An empty runner mirror therefore does not require a full bootstrap just because
a bundle becomes newly eligible.

When sync and publication run in separate jobs, use `--restore-new-public-files` during full or incremental sync to restore newly eligible retained papers without fetching old ZIPs. Transfer the provider state and publish plan, then use `publish-site --download-affected-bundles --publish-plan <path>` against current site state. This keeps recovery tied to the latest release assignments rather than those present when a parallel sync started. These ZIP downloads require `GH_TOKEN`.

## Scenario 5: frontend build or deployed data fails

Confirm the site publication files exist, then test in the frontend working directory:

```bash
cd frontend
npm test
npm run lint
npm run build
```

Fix the owning site feed or frontend contract and redeploy. The frontend must not read raw provider state as a shortcut.

## Scenario 6: documentation validation fails

Change the authoritative owner first, then regenerate:

```bash
uv run python scripts/render_docs.py
uv run python scripts/validate_docs.py --check
```

Provider/page mismatches require reconciling the runtime registry, site registry, source inventory, and provider pages. A stale generated block is never repaired by hand. A broken archive link may be updated for navigability, but archived claims remain non-authoritative.

## After recovery

Run the relevant strict audit, tests, and publication validation again. Update this guide when the incident exposed a missing repeatable procedure; record durable architectural rationale as an ADR.
