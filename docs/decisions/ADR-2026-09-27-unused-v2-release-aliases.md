# ADR-2026-09-27: retire unpublished v2 alias metadata

- Status: proposed
- Owners: publication and operations maintainers
- Scope: the default site's v2 Release projection

## Context

The accepted [identity decision](ADR-2026-07-16-exam-identity-and-release-shards.md) counts primary and compatibility ZIP names as physical assets, preserves existing primary assignments, and requires explicit authorization for alias retirement.

The upload helper uploads primary ZIPs and retains declared aliases only when they already exist remotely. Unpublished aliases therefore reserve capacity without providing working downloads. Their names are absent from the frontend feed and its generated landing pages.

This proposal is separate from original v1 Release retirement. Accepting that notice or deleting those Releases does not authorize this change.

## Proposed decision

After explicit acceptance and a fresh remote inventory proves every active primary is current and no declared alias exists on its assigned v2 Release, disable alias retention for the default site in `app/site_registry.py`.

Apply that policy to rebuilt and preserved bundles before release planning. Omit empty `legacy_asset_names` from both site bundle metadata and the Release inventory. Regenerate those projections through the site writer, preserving all other fields, row order, primary names, checksums, URLs, and tag assignments. Future primary assignments can use the resulting headroom in existing shards.

Keep physical asset counting unchanged: any alias retained by another site still consumes a slot. Keep legacy readers, canonical lookup, generic archive builders, and download fallback for historical records. Do not delete or move any remote asset as part of this activation.

If a fresh check finds a remotely hosted alias, stop activation and obtain a decision covering its users and any deletion. Do not assume an alias is unused from its download count.

## Consequences

The default site's metadata and future capacity match its primary-only public downloads. Existing primary downloads continue at their assigned URLs. A rollback can restore the previous metadata and retention policy without restoring remote bytes, because activation requires that no alias bytes were hosted.

Future default-site publications no longer declare speculative legacy fallback names. Historical snapshots can still use their recorded names. A deliberate new compatibility download requires a reviewed policy change and corresponding physical capacity reservation.

## Authorization boundary

This ADR remains proposed until the maintainer explicitly accepts it. Its draft implementation and generated metadata must not be merged before acceptance and final CI. Neither this document nor its local verification authorizes deleting v1 Releases or hosted v2 aliases.
