# ADR-2026-10-26: preserve navigation when some corrected groups are withheld

- Status: implemented
- Owners: retained-download projection and frontend compatibility pages
- Scope: existing frozen bundle pages with exact current source anchors

## Problem

An identity correction can leave some annual candidates under review while
other successors qualify for publication. Requiring every successor to be
public suppresses the previous page entirely, breaking its bookmark even
though its frozen ZIP and some current replacements remain available.

## Decision

Keep the exact native-source and checksum requirements for every reference in
the frozen archive. A missing anchor still prevents automatic navigation.
When all references resolve, project only successors actually present in the
current site. If some successors are withheld, mark the compatibility page
with the optional `partial: true` field and explain that only the currently
available replacements are listed. Never describe that subset as the complete
original archive or redirect to a single successor as an equivalent download.
No page is inferred when no successor is public.

The page remains outside search results and the sitemap and uses `noindex`.
It carries no new ZIP alias. The original historical ZIP retains its own
separately verified URL, digest and retirement rules under the
[historical-download decision](ADR-2026-10-23-retained-historical-downloads.md).
Publication and source restrictions continue to control the successor set.

## Contract and removal

The optional field extends the existing frontend feed and compatibility-page
reader. Complete mappings retain their previous shape and behavior. Backend
projection, both feed schemas, frontend validation and page rendering share
the change. Tests cover missing native anchors, withheld successors, invalid
partial flags, explicit partial wording and unchanged full mappings.

The marker disappears automatically when every successor qualifies for the
current site. Removal of the saved page or frozen ZIP remains a separate
reviewed retirement; a rebuild does not grant that authorization.
