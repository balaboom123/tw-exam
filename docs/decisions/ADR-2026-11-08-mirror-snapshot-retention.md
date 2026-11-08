# ADR-2026-11-08: keep current, previous and recent mirror snapshot generations

- Status: implemented
- Owners: `.github/scripts/mirror_snapshots.py` (`save`, `prune_generations`)
- Scope: public `mirror-<provider_id>` prerelease snapshots

## Problem

The 2026-09-30 maintenance plan (Task 5) kept two snapshot generations per
provider. Commit `9576b7b` (2026-10-20) removed that pruning without a recorded
decision, because a publication retry restores the generation its own sync
saved, and advancing the pointer did not prove that no retry still needed an
older one. Generations then accumulated: by 2026-11-08 mirror storage was
98.3 GB, nine releases held three or four generations, and the 1,000-asset
release limit was approaching again.

## Decision

After a save commits the new pointer, delete the assets of every generation
except:

1. the generation just saved,
2. the previous pointer's generation, and
3. any generation with an asset uploaded within the last 7 days.

Seven days matches the sync job's provider artifact retention
(`retention-days: 7` in `_sync-provider.yml`). A publication job cannot run
without that artifact, so no publication retry can need a generation older
than this window. An asset without a readable upload time counts as recent.

Pruning never fails the save. A listing or delete error is reported and the
next save retries. A save that reuses an unchanged generation uploads nothing
and prunes nothing.

## Consequences

Each provider normally keeps two generations, and briefly up to three or four
when several syncs run within a week. Raising the artifact retention requires
raising `RECENT_GENERATION_DAYS` with it.
