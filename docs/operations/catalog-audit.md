# Catalog audit

Catalog and history audits are read-only evidence gates unless paired with an explicit migration or publication command.

## Identity and bundle purity

```bash
uv run python -m app audit-catalog --repo-root . --site-id default --strict --output .tmp/catalog-audit.json
```

The strict audit fails when a paper lacks a resolved identity, review records are not isolated in their approved event bundles, or the persisted review queue is stale or incomplete. It also blocks multiple
source URLs sharing one mirror locator, even in CI without local mirror files. Its report describes mixed legacy groups and includes the publication backlog; the command also warns when that backlog is nonempty. Those findings are advisory here and are checked by separate publication and history gates. Fix the executable catalog, mapping, or normalizer rather than documenting an exception in prose.

## Event-level retained history

```bash
uv run python -m app history-audit --repo-root . --site-id default --strict --output .tmp/history-audit.json
```

The history audit reconciles raw events, normalized records, mirror payloads, publication, coverage exceptions, and quarantine. With `--probe-sources`, source-only events and failed source discovery count as parser gaps. CI adds `--skip-mirror-check` because the gitignored mirror is absent there; operators with retained mirror state must run the full check.

## Reviewed source scope

```bash
uv run python scripts/validate_source_inventory.py
```

This gate compares `catalog/source-inventory.json` with runtime provider membership and checked-in local state. It reports source-manifest coverage and fails on invalid reviewed scope.

## Migration and publication sequence

When an identity correction affects retained records:

```bash
uv run python -m app migrate-catalog --repo-root . --site-id default
uv run python -m app audit-catalog --repo-root . --site-id default --strict
uv run python -m app publish-site --site-id default --repository <owner>/<repo>
uv run python -m app plan-release --repo-root . --site-id default --output .tmp/release-plan.json
```

Review the migration diff and generated release plan before external release mutation.

## Local ZIP contents and redundant files

After local publication, verify the complete archive set:

```bash
uv run python -m app audit-files --repo-root . --site-id default --verify-content --output .tmp/file-audit.json
```

The audit checks portable paths, entry/manifest agreement, physical file counts,
source-record conservation across parts, and both paper and archive checksums.
It reports unreferenced local ZIPs separately from errors in active archives.
Missing or stale active ZIPs require a local publication rebuild before cleanup.
The v3 archive migration rebuilds ZIPs without changing logical identities; upload
all changed assets before committing/deploying the matching site inventory.
This check requires the gitignored archives; metadata-only CI cannot substitute
for it. Run it before uploading changed release assets.

To remove verified redundant local ZIPs after the active set passes:

```bash
uv run python -m app audit-files --repo-root . --site-id default --prune-redundant --output .tmp/file-cleanup.json
```

Cleanup implies full content verification. It removes only unreferenced archives
whose source keys and checksums are all covered by the active set. Archives with
unique historical records, corrupt payloads, or unsupported manifests remain in
place with a reason in the report. Add `--isolate-unreferenced` to move these
remaining archives into `bundles/sites/<site_id>/recovery/`. Isolation verifies
the active set first and refuses to overwrite an existing recovery file. The
builder only scans the active directory, so suspect archives cannot override a
valid archive during fallback recovery. Inspect recovery files explicitly before
restoring them; they are excluded from publication and automatic deletion. Mirrors, provider history, and remote Releases
are unaffected; remote reconciliation remains owned by Release tooling.

If the file audit reports several source URLs sharing a mirror locator, inspect
and repair that acquisition ambiguity before rebuilding ZIPs:

```bash
uv run python -m app repair-mirror-collisions --repo-root . --site-id default
uv run python -m app repair-mirror-collisions --repo-root . --site-id default --apply
uv run python -m app publish-site --repo-root . --site-id default --repository <owner>/<repo>
```

The first command reports collisions without fetching or writing state. Apply
fetches official retained URLs with provider concurrency limits, validates their
payloads, and preserves every source record. Failed providers retain their old
state and produce a nonzero result; verified repair payloads remain reusable for
a retry. Repeat the file audit before cleanup or uploading archives.
