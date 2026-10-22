# ADR-2026-10-23: retain existing historical ZIP URLs during identity corrections

- Status: implemented for nonbreaking download preservation; retirement authorization unchanged
- Owners: site publication, archive verification and Release operations
- Scope: explicitly selected existing downloads in a site's v2 Release namespace

## Problem

A corrected identity can replace one published aggregate with several current
bundles. Treating the former ZIP as unexpected couples the correction to deleting
a saved direct download URL. Reusing that filename as an alias for one successor
would misrepresent the aggregate's coverage.

## Decision

Keep current exam identity and historical download retention separate. An explicit
site-writer option snapshots an existing bundle's owner, ID, filename, tag and
SHA-256 after verifying its complete native payloads and retained source anchors.
The snapshot remains site-owned generated state. Current bundles and frontend
results contain the corrected identities; the frozen download is not another
current result or an alias for a successor.

Project frozen downloads into the Release inventory under a versioned contract.
Every physical download consumes a shard slot. Existing current primary URLs
remain fixed; new assignments use available capacity after reserving frozen
assets. Release verification and upload compare every declared digest, and
routine pruning preserves every declared frozen asset. Unknown assets remain
errors or ordinary guarded cleanup candidates.

Complete archive verification checks historical ZIP structure, payloads, pinned
archive digest and provider-history anchors separately from current identity and
multipart conservation. Local cleanup and isolation cannot remove frozen assets,
including when an older report lists them as unreferenced.

Routine publication cannot rewrite or remove a snapshot. A separate retirement
operation requires the exact filename and original checksum and remains subject
to its own external authorization and hosted verification.

Rollback can promote a historical download back to a current primary only with
the same bundle ID, filename, tag, storage key and checksum, after full byte and
source-history verification. Newly published successors can become frozen
downloads in the same transition. This transfers ownership without removing any
download URL or changing its bytes; it is not retirement.

## Relationship to existing decisions

The accepted [alias decision](ADR-2026-09-27-unused-v2-release-aliases.md) remains
unchanged. Speculative legacy aliases remain disabled for the default site.
This decision concerns existing, byte-pinned historical downloads with a
demonstrated saved-link purpose. It neither invents aliases nor authorizes the
retirement of v1 Releases, held v2 downloads or unrelated provider backups.

## Verification and activation

Before activation, verify migration and rollback against current production,
exercise unchanged URLs and digests, reserve actual physical capacity, prove
pruning protection, and run full current/retained archive and application gates.
Implementation and activation do not consume or infer any pending retirement approval.
