# ADR-2026-09-26: retire original v1 bundle Releases

- Status: proposed
- Owners: publication and operations maintainers
- Scope: the original default-site v1 Releases

## Context

The [accepted identity decision](ADR-2026-07-16-exam-identity-and-release-shards.md)
made migration additive and required separate authorization for v1 deletion.
The current site publishes v2 bundles split by official identity. Keeping the
original mixed-identity ZIPs consumes hosted storage and leaves an obsolete
download surface available to saved links.

The proposal is limited to `moex-bundles`, `default-bundles-001`, and
`default-bundles-002`. These names identify the historical Releases being
retired; live asset inventory and coverage are determined from GitHub and the
site-owned publication state during preflight.

## Proposed decision

1. Publish the dated README notice before deleting any Release.
2. Require a fresh retirement report proving that current site downloads are
   on v2 Releases, all expected v2 primary ZIP digests match, and retained
   provider state no longer writes derived v1 download URLs.
3. Obtain explicit authorization for the three named Releases after reviewing
   their exact IDs, asset inventory, total bytes, and observed downloads.
4. Delete only those Releases after rechecking their IDs and inventory. Keep
   their git tags, source records, provider mirrors, and active v2 Releases.
5. Preserve v2 compatibility aliases and their generated metadata. Their names
   still occupy physical asset slots; capacity rules and existing shard
   assignments remain governed by the accepted identity decision.

Retiring active v2 aliases or consolidating shards requires a separate decision.
Removing a v1 Release does not make active compatibility metadata obsolete.

## Consequences

Saved links to the three v1 Releases will fail after deletion. The current
catalog remains the supported entry point; a mixed v1 ZIP cannot always be
redirected to one correct v2 ZIP because its papers may belong to several
official identities.

Deletion removes the hosted rollback copy of those ZIPs. Provider state and
mirrors support rebuilding current v2 output, but they do not establish that an
old ZIP can be reproduced byte for byte. If retaining exact old bytes is required,
download and checksum the retiring assets into an explicitly chosen archive
before authorizing deletion.

This decision does not assert that every historic v1 payload is present in the
current public projection. Retained providers below site eligibility or under
quarantine remain outside that projection by policy. Source records and mirrors
remain subject to their normal retention rules.

## Execution and evidence

Follow the [retirement procedure](../operations/release-checklist.md#v1-retirement)
and retain the fresh report with the authorization record. This proposal and the
README notice do not themselves authorize public deletion. Once accepted, this
decision is append-only.
