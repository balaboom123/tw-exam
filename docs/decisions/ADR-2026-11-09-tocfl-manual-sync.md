# ADR-2026-11-09: sync TOCFL manually from the local mirror

- Status: implemented
- Owners: `.github/workflows/sync-tocfl-cert.yml`, `.github/workflows/sync-certifications.yml`
- Scope: `tocfl_cert` acquisition schedule

## Problem

On 2026-11-08 the maintainer kept TOCFL's mirror out of public Release storage
(`public_mirror_backup_allowed = False`), and its leftover `mirror-tocfl_cert`
release was deleted. The mirror is about 23 GB (324 RAR packages), above the
10 GB Actions cache limit, so no CI runner can restore it. The scheduled
certification sync on 2026-11-09 (run 42563026674) started cold, re-downloaded
the packages and still recorded 9 failures: 7 retained attachments it could not
verify and 2 unreviewed upstream revisions. TOCFL is quarantined, so the leg
published nothing and only produced a weekly failure.

## Decision

Remove `tocfl_cert` from the scheduled certification matrix. Keep a manual
`sync-tocfl-cert` workflow, matching Hakka (plan decision D6), with
`publish: false` and `durable_snapshot: false`. The maintainer refreshes TOCFL
with `uv run python -m app sync-full --provider tocfl_cert` from the verified
local mirror, which holds every retained attachment and journaled original.

## Consequences

TOCFL no longer appears in weekly sync health. Its state changes only when the
maintainer syncs it locally, or dispatches the manual workflow knowing that a
cold runner cannot verify retained attachments. Lifting the public-backup
restriction, or a durable private store, would allow scheduling again.
