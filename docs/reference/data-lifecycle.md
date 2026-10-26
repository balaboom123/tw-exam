# Data lifecycle

Official sources flow through provider discovery, validated mirrors, normalized retained state, site selection, bundle generation, Release upload, and frontend projection. [Contracts](contracts.md) defines the ownership boundaries; [the runbook](../operations/runbook.md) and [recovery guide](../operations/recovery.md) own repeatable procedures.

## Discover, probe, and acquire

Discovery returns official availability for the selected provider. It is read-only unless manifest writing is explicitly requested; it never writes site publication state. A broad discovery capture should use a positive source-request delay.

Providers that implement the probe URL model compare year/event HEAD responses against their own source manifest and fetch pages when those comparisons indicate change. The probe produces an explicit sync decision. Its output and optional manifest remain provider-owned.

Sync downloads into `mirror/providers/<provider_id>/`. Mirror locators preserve year, event, category, subject, and file-role distinctions. Valid files are reused unless explicit refresh or provider policy requires acquisition. Successfully refreshed files replace stale siblings with incorrect extensions. Each retained payload receives a SHA-256 checksum. The sync owner fetches a shared source locator once per event, including during refresh.

`--refresh-files` re-fetches selected source files even when a valid local copy
exists. Providers whose download slots are mutable declare that policy in their
adapter. Payload validation precedes replacement. The mirror writer copies old
scoped bytes into its provider's checksum-addressed recovery directory before
overwriting a file or removing an older extension. These immutable copies are
excluded from hard-link deduplication and orphan pruning. Current source files
may still share byte-identical payloads independently of the recovery copies;
deduplication statistics describe current files.

When one source page assigns different URLs to the same paper codes, acquisition
adds a source-URL digest to each mirror locator. Already split locators remain
split when later listings shrink. Normalization uses each parsed paper's own
mirror metadata so one source cannot overwrite another source's checksum.
`repair-mirror-collisions` re-fetches ambiguous retained locators and updates raw
and normalized references only after every affected URL for a provider succeeds.
It does not advance whole-event sync receipts for this partial repair.

An exact native-evidence exception may resolve conflicting source labels only
after its complete source context and reviewed bytes have been verified. A
checksum supplied by the adapter's reviewed native evidence takes precedence
over an earlier retained checksum when deciding mirror reuse. A stale valid file is restored
from the verified download with its earlier bytes preserved for recovery;
changed or unreviewed native files fail before source-event replacement.
Such an acquisition exception does not approve classification or publication.

An adapter for an archive without native subject codes must derive stable
acquisition keys from source references, rather than renumbering files by listing
position. A source-key change is a storage migration: verify every old reference
and payload, map it to the new locator, retain rollback evidence, and separately
review examination identity before publication.

Legacy mirror fallback validates each candidate once. A valid historical payload
can replace an invalid scoped copy without another source download, using the
checksum returned by the mirror writer. Mirror deduplication uses hard links;
the mirror root and its temporary/canonical files must share a filesystem that
supports them.

Cleanup and deduplication count reclaimable payload bytes once per inode, and
only when every hard link to that inode is removed or replaced. Removing an
alias of a retained file releases no payload bytes. Recovery blobs remain
excluded, and duplicate-only pruning preserves unique unreferenced history.
Provider snapshot origin markers and their temporary writes are operational
recovery metadata; payload scanning, deduplication and pruning exclude them.

Source pages are fetched serially. Independent files may download with bounded concurrency, while mirror writes remain serial; stateful or rate-limited providers restrict concurrency. Shared HTTP adapters relying on sync retries use one transport attempt to avoid multiplying retries. Transient discovery/fetch requests use bounded retries and honor `Retry-After`.

An interrupted HTTP response body uses that same retry budget. Each attempt
reads a fresh complete response; partial bytes never reach payload validation
or storage. Exhaustion remains a network failure, allowing the sync owner to
retain an exact, checksum-verified earlier file while recording the failed
refresh. A complete response that fails native payload validation remains a
validation failure.

An unavailable year listing preserves retained provider state, returns failure, and produces no publish plan. Payload validation rejects HTML placeholders and signatures inconsistent with the expected file role. ZIP-based downloads (including OpenXML and ODS files) must also have a readable container and valid CRCs for every entry, both when downloaded and before mirror reuse. Failed validation leaves retained evidence intact and records a download failure. The [sync implementation](../../app/sync.py) owns these checks; acceptance of an outer archive does not establish the integrity of its nested papers or the internal contents of a RAR.

Original ZIP attachments use the `source_archive` role and the same container
validation as paper archives. Their cache reuse still requires the retained
source URL and checksum. An unrecognized role cannot bypass ZIP integrity
checks: the advertised ZIP format or a ZIP signature invokes validation before
storage. Unrecognized roles remain ineligible for mirror reuse until their
payload policy is defined.

Native HTML is accepted only for explicit `source_page` attachments or the
existing `answer_table` role. Questions, audio and source assets still reject
HTML placeholders. Capturing a source page does not establish that its dynamic
application and dependencies form a complete offline examination package.

After a network failure, a refreshed reference may retain its earlier local
payload only when provider, event, paper, role, URL and material context match,
the full checksum verifies, and its role's payload validation passes. The failed
request remains in the failure ledger. Changed context, invalid new content and
missing or corrupt prior bytes cannot use this fallback. It does not create a
successful-sync receipt.

When a ZIP-based download has no supported filename or MIME extension,
acquisition checks its native document parts to distinguish Word, spreadsheet
and OpenDocument files from ordinary ZIPs. The ordering of a material role's
allowed extensions must not turn an unnamed archive into a document. A native
format unsupported by that role fails validation.

## Normalize and retain history

Normalization applies reviewed provider aliases and shared identity rules to raw events; unresolved cases enter the provider review queue. Full provider writes also regenerate a compact index. Index readers fall back to source files when an index is missing or its source snapshot is stale; the catalog audit still reclassifies full records. The index must remain rebuildable, and [its check](../../scripts/build_provider_indexes.py) compares complete contents against source files.

A parsed subject can carry several file roles. The shared normalizer evaluates
each file with its own source URL and mirror metadata before assigning its
identity and evidence. A known question cannot approve an unreviewed paired
answer or supply its checksum-bound provenance. Raw acquisition pairs remain
intact; provider-owned native facts still decide the meaning of each version.

Full and incremental sync retain previously acquired events and papers that disappear from current listings, including their source-manifest evidence. Incremental and targeted merging preserve unaffected history. Refreshed names may derive canonical migrations; they must not orphan prior compatibility identities or silently merge distinct official programs.

Before replacing provider state, the scoped writer records superseded paper and
attachment references and superseded source titles or material facts in the
provider source revision journal, even when the payload bytes have not changed.
Reclassification alone does not add revisions. It requires the
checksum-verified earlier payload or its already retained recovery blob before
retiring a verified reference. Before this check, the writer can restore missing
current locators and revision blobs from another recorded source path in the
same provider, using the expected full checksum. Recovery creates independent
copies, leaves existing files untouched, and never substitutes today's bytes
from a reused URL. A missing or mismatched earlier payload stops the state write
when no recorded, checksum-verified copy remains. Unverified references retain metadata
only. An explicitly requested discovery manifest is written after the provider
state succeeds, so a rejected retention write cannot advance that manifest.
Current publication continues to use the current catalog; retired roles,
URLs and older bytes are not republished automatically. Strict history audits
with mirror checking verify every journal blob as well as current references.

A confirmed acquisition-owner error can be corrected before examination identity
review is complete. Reconcile the replacement against the full official listing
and checksum-verified payloads, rehearse the conserving write and rollback, and
verify durable recovery before transferring current state. Preserve unresolved
identity dimensions and review dispositions; source correction does not approve
cross-year grouping or lift publication quarantine. A replay of retained originals
does not create a successful-sync receipt. Retired source metadata stays immutable
even when its earlier provider, event or role assignment was wrong.

Native answer-correction reviews share exact context and checksum validation.
Each provider retains its own official URL and source-key rules. Both fresh and
retained normalization use the same provider resolver; a changed payload at a
reviewed slot requires new evidence before receiving the correction role.

Provider-owned material-role corrections apply during ingestion and catalog
migration through the same resolver. Migration preserves source URLs, checksums
and historical storage locators while retaining the former role in the source
journal. A subsequent source sync refreshes raw role metadata and assigns
current mirror locators only to validated downloads. A failed source retains its
earlier payload and failure evidence without receiving a successful-sync date.
Retained papers also keep their unresolved classification reviews when another
paper in the same event refreshes. A fresh review for the same identity replaces
its earlier review; an explicit event removal removes both records and reviews.

Historical acquisition errors are reviewed alongside the journal rather than
rewriting it. The source-revision review owner distinguishes a checksum-backed
non-paper reference from an examination paper. History audits report that
disposition separately; the original role, storage partition and discovery group
remain traceability evidence. Do not normalize such a reviewed reference into a
new examination identity or infer its date from the original partition.

History audits distinguish an explicitly reviewed reference-only collection
from an examination page that lost its normalized papers. Reference collections
have no paper-publication obligation, but byte checks require every attachment
to remain within its provider and match its recorded checksum. Unreviewed
attachments cannot establish this disposition, and pages that actually list
papers still require normalization. Metadata-only audits explicitly omit byte
verification.

If earlier imports survive in repository history but no longer appear in current
state, reconcile their original source dictionaries against verified mirror
bytes before cleanup. Retain the recovered references and exact legacy fields
in the revision journal. A file absent from the active catalog is not redundant
until its unique history and independently recoverable bytes are accounted for.

Successful full, incremental, targeted, and repair syncs record a UTC receipt for each completely refreshed event. Failed and unrefreshed events keep their previous receipt. Discovery, catalog migration, and bundle builds do not advance dates. Retained state may lack receipts; never backfill them from discovery or file modification time.

Failure behavior is owned by [the CLI](../../app/cli.py) and [state merging](../../app/state.py):

- Targeted sync aborts generated-state writes on failure by default. Explicit partial mode may retain the valid subset and failure rows, returns nonzero, and requires follow-up audit/publication.
- Incremental sync preserves previous records for failed events and returns nonzero while failures remain.
- Full sync and local bundle construction may produce failure reports and local outputs, but failures remain visible and block an undeployable generated-state commit.
- [The commit guard](../operations/workflows.md) checks retained-state floors and global publication invariants before generated changes can reach main.

## Reviewed exceptions and quarantine

Source-coverage evidence belongs in `catalog/source-coverage/`. Event exceptions must match the current raw event; file exceptions must match the exact provider, event, year, paper, role, URL, and download failure stage. An event exception conflicts with acquired papers, attachments, normalized records, or failures. Unmatched entries are orphans. Conflicts and orphans fail strict history audit; publication validation ignores only exact reviewed file exceptions. Re-probe or remove evidence when the official source changes or becomes available.

Quarantine belongs in `catalog/mappings/publication-quarantine.json` and withholds a defective site projection. It does not change source acquisition denominators: registered providers continue discovery, sync, mirroring, normalization, and audits. Source-coverage exceptions explain acquisition blockers; quarantine explains withheld publication.

A documented redistribution restriction can also withhold the site's ZIP
projection. This is distinct from an unavailable download: an acquired file
may be retained for evidence without being eligible for republication. A
source's private-study permission does not automatically cover a public ZIP
archive containing third-party material. The quarantine entry records the
publication disposition and links to reviewed source evidence.

Each quarantine entry requires a reason, source evidence, and a resolvable maintained note section. Required providers cannot be quarantined; the site publisher fails closed rather than bypassing its required-state guard. Quarantine must not delete retained provider state, mirrors, or bundle archives.

The site publisher owns exclusion. History audit reports quarantined events separately from minimum-year exclusions and unexplained publication gaps. Lifting a reviewed entry requires republishing. Removing a projection leaves previously uploaded assets directly downloadable until Release reconciliation/pruning completes.

History audits check all retained records and mirror obligations before applying
quarantine to public eligibility. Unknown dates in withheld records must remain
visible without being submitted to the public-date contract. Eligible records
are reconciled against reviewed material years, including explicitly undated
resources, rather than their legacy storage partitions. A missing required year
still leaves an unexplained publication gap when the bundle meets the site's
minimum-history policy.

## Build and publish

Reviewed material facts can be acquired before they are publishable. Their
normalized v3 records preserve native source keys and storage partitions while
recording official dates separately. Reviewed facts pass the coordinated archive,
index, site and frontend contracts; unresolved or partially migrated bundle
history is rejected before archive mutation. Existing
quarantine is applied before site aggregation, so a withheld provider's fact
migration does not change unrelated publication. Acquisition does not lift
a source hold.

The [site configuration](../../app/site_registry.py) selects eligible providers and bundles. The [bundler](../../app/bundler.py) reads normalized papers and validated mirror files, reuses matching unchanged single-part archives, and verifies ZIP CRC for entries whose mirror files are absent before reuse. Deterministic entry timestamps and permissions stabilize rebuilt bytes; completed archive checksums are streamed from files.

New archive manifests retain year- and source-URL-aware paper keys, checksums,
and entry names. Earlier full-record and compact manifests remain readable for
recovery but are rebuilt into the current manifest version. Already compressed media are stored without a second compression pass; manifest text remains compressed.

Publication also checks a reusable local ZIP against its retained published archive checksum. A differing local archive is rebuilt; mirrored files and entries recovered from an older ZIP must match their recorded paper checksums. Rebuilds hash mirrored bytes as they are copied and stage the new archive separately, preserving the previous ZIP when a mirror checksum or I/O operation fails. Restore a verified mirror or download before retrying a checksum failure.

Site state and bundle files remain under `data/sites/<site_id>/` and `bundles/sites/<site_id>/`. Bundle metadata, Release asset inventories, compatibility aliases, multipart records, and shard assignment remain site-owned. Provider syncs may pass affected-bundle plans to publication; those plans do not authorize hand-written tag assignments.

Release tooling ensures assigned Releases exist, uploads expected artifacts, checks coverage, and prunes unexpected ZIPs according to the inventory. Compatibility aliases remain published while listed. Successful upload precedes the matching generated-state commit, so a site feed never promises unavailable changed artifacts.

## Frontend projection

The frontend build consumes the site-owned feed and emits a content-hashed compact feed plus a separate search index. Search loads the index lazily, and its positions match bundle order. The same projection creates static bundle landing pages and a sitemap using recorded site URLs and Release locators.

The frontend join flow unlocks final ZIP links locally. It does not affect source ingestion, provider state, or Release publication. Provider and publication commands must complete independently of frontend gating.

## Manual inputs and generated state

Reviewed source inventory, source-coverage evidence, quarantine decisions, taxonomy/mappings, and provider aliases are maintained inputs. Raw events, normalized papers, derived indexes, review queues, failures, discovery manifests, site inventories, and publication assets are derived outputs. [Scoped paths](../../app/paths.py) and their schemas own the layout.

Fix the owner and regenerate. Manual edits to generated state are temporary recovery actions and must be followed by rebuilding or a code fix. New providers retain their own discovery, failures, review state, mirrors, and evidence; new sites retain their own publication state. Shared contracts may evolve through the reviewed migration process, while provider parsing fields remain outside frontend feeds.
