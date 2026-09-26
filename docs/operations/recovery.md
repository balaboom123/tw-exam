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

For a matrix caller, identify the provider from the failed job name and its sync summary. Provider snapshots retain failure evidence in the run's artifacts; the mirror cache and durable backup preserve downloaded payloads. If publication reports that the provider changed after sync started, rerun the caller against current `main`. A publication-only retry reuses its original artifact and exact mirror cache, recovering that sync's pinned durable generation if the cache was evicted. If the artifact or durable generation is unavailable, run a fresh sync.

## Durable provider mirror backup

`.github/scripts/mirror_snapshots.py` stores provider payloads in public prereleases
named `mirror-<provider_id>` in the source repository. These archives are recovery
inputs, separate from site release shards and the publication inventory. Public
storage includes retained provider files even when their site projection is
quarantined. It does not change quarantine or authorize site publication.

With an authenticated `gh` CLI, save one provider's local mirror:

```bash
uv run python .github/scripts/mirror_snapshots.py save --provider <provider_id> --repository <owner>/<repo>
```

Restore into an empty provider directory:

```bash
uv run python .github/scripts/mirror_snapshots.py restore --provider <provider_id> --repository <owner>/<repo>
```

The helper streams a gzip archive into chunks below GitHub's asset size limit.
It first hashes the archive without staging its compressed bytes. Changed
mirrors get a second pass that uploads and discards one completed chunk at a
time; a source change between passes stops publication of the snapshot pointer.
It verifies uploaded digests before atomically advancing the release metadata
pointer. An unchanged archive reuses its existing generation. Existing snapshots
and their chunks are immutable; a different upload needs a new generation.
An interrupted upload cannot replace the last committed pointer.

Restore checks the manifest digest, provider, generation, chunk hashes and sizes,
archive paths, file types, and declared file inventory before installing the
provider tree. It preserves other providers and refuses to overwrite a nonempty
provider mirror during operator restores. Snapshot format v2 preserves hard-linked source payloads as
links to earlier regular members within the same provider archive. Restore
validates those targets and retains shared storage; symlinks, forward links,
and link chains are rejected. Older v1 archives remain readable. The derived
root dedupe index is discarded after restore.

Sync workflows use `hydrate` inside their Actions workspace before acquisition.
A restored or successfully backed-up mirror carries a local origin marker.
A matching marker and retained inventory avoid another payload download;
unmarked, older, or incomplete warm caches are refreshed from the committed
durable generation. The replacement is staged and verified before swapping
provider directories. Durable bytes win at overlapping paths; cache-only files
from an interrupted backup are retained. A failed download or changed remote
pointer preserves the old cache. The marker is excluded from backup archives.
Operator `restore` keeps its empty-directory guard, and publication retries
continue to use their original pinned generation.

Durable backup runs before Actions cache save, so cached origin markers describe
the committed snapshot. The helper emits its cache eligibility before attempting
an upload. Mirrors above its unique-payload budget skip warm cache save and
recover from public storage on the next run; shared hard-linked files count
once. This avoids a large mirror competing with smaller providers for the
repository's Actions cache allowance. Incremental MOEX runs hydrate only when
the source probe requests sync.

`--allow-missing` permits source bootstrap only when the release does not exist;
transport errors, incomplete releases, or corrupt archives stop recovery.
Publication recovery can pin `--generation <generation>` and
`--manifest-sha256 <sha256>` from its original sync outputs instead of using a
later provider snapshot. Mirror generation and SHA outputs are written when
`GITHUB_OUTPUT` is present.

For an admissions recovery pilot, dispatch `sync-admissions.yml` with
`provider_id` set to the affected provider. Other provider jobs are skipped;
the default `all` and scheduled runs retain their full matrix.
Verify a durable snapshot before deliberately evicting that provider's cache.

The helper never deletes remote snapshots. Before removing old generations,
check that no retained publication artifact or pending job needs them. Interrupted
uploads may leave unreferenced chunks; diagnose those before manual cleanup.
The release asset cap is checked before uploading additional files. Backup
staging uses at most one compressed chunk plus small manifests. Restore
verifies and discards each downloaded chunk as it is consumed, then installs
the complete provider tree atomically. Its preflight requires free space for
the unique payload bytes, one compressed chunk, and filesystem overhead;
large providers still need a runner or local machine with enough room for
their restored files. Deploy the v2 reader before seeding v2 snapshots;
older code rejects that format.

The manual `verify-mirror-recovery.yml` workflow restores a selected provider
on an empty hosted runner with read-only repository permissions. It reports the
verified manifest, recovered inventory, unique storage, and hard links. Supply
both generation and manifest SHA256 to check a pinned snapshot. It performs no
sync, cache save or eviction, site publication, or Release mutation.

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

Provider mismatches require reconciling the runtime registry, site registry, source inventory, generated index, and referenced note sections. A stale generated block is never repaired by hand. Historical documents remain reachable through the commit-pinned archive index and do not supply current evidence.

## After recovery

Run the relevant strict audit, tests, and publication validation again. Update this guide when the incident exposed a missing repeatable procedure; record durable architectural rationale as an ADR.
