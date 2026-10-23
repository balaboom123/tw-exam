# Contracts

When an identity split conserves every native source key and checksum of a
retained ZIP, the site writer derives its landing-page successors from those
exact sources. The optional frontend `compatibilityPages` projection contains
explicit current bundle IDs; the frontend does not infer them from names.
Partial native overlap cannot approve a successor list. A file-role correction
can follow an immutable historical source record through the same normalizer
used for current publication. This requires unchanged source context and bytes,
an approved resulting identity, and a matching current public record. A shared
URL or checksum alone cannot establish that relation. More complex source
transformations retain their separately reviewed compatibility pages.

Executable fields, types, versions, and vocabularies live in the owners below. This reference defines ownership, traceability, publication boundaries, and compatibility obligations. Identity and bundle purity are defined in [exam identity](exam-identity.md).

## Contract owners

| Boundary | Executable owner | Persisted location |
| --- | --- | --- |
| Source discovery/probe manifest | [SourceManifest](../../app/manifest.py) | `data/providers/<provider_id>/source-manifest.json` |
| Raw events, review entries, failures, aliases | [Models](../../app/models.py), [state](../../app/state.py) | `data/providers/<provider_id>/` |
| Compact review ledger | [Review schema](../../schemas/review-queue-v2.schema.json), [codec](../../app/review_queue.py) | `data/providers/<provider_id>/review-queue.json` |
| Successful event sync receipts | [Sync status schema](../../schemas/provider-sync-status-v1.schema.json), [provenance](../../app/provenance.py) | `data/providers/<provider_id>/sync-status.json` |
| Superseded source references, context and payloads | [Revision v2 schema](../../schemas/provider-source-revisions-v2.schema.json), [legacy v1 schema](../../schemas/provider-source-revisions-v1.schema.json), [retention owner](../../app/source_revisions.py) | `data/providers/<provider_id>/source-revisions.json`, `mirror/providers/<provider_id>/recovery/source-revisions/` |
| Reviewed non-paper revision meaning | [Reference evidence schema](../../schemas/source-revision-reference-v1.schema.json), [review owner](../../app/source_revision_review.py) | `catalog/mappings/source-revisions/reference-material-v1.json` |
| Material nature and official date evidence | [Material schema](../../schemas/source-material-v1.schema.json), [fact model](../../app/source_material.py) | Provider raw events/papers and normalized records |
| Reviewed TCU domestic annual admission and native papers | [Evidence schema](../../schemas/tcu-annual-papers-v1.schema.json), [checksum resolver and coverage gate](../../app/providers/hce_tcu/evidence.py) | `catalog/mappings/tcu/annual-papers-v1.json` |
| Reviewed postal employment grades, job applicability and annual papers | [Evidence schema](../../schemas/post-native-papers-v1.schema.json), [native resolver and coverage gate](../../app/providers/post_recruit/evidence.py) | `catalog/mappings/post/native-papers-v1.json` |
| Reviewed NTHU annual selection and shared-paper applicability | [Evidence schema](../../schemas/nthu-annual-papers-v1.schema.json), [resolver and evidence gate](../../app/providers/hce_nthu/identity.py) | `catalog/mappings/nthu/annual-papers-v1.json` |
| Reviewed TQC sample identities | [Evidence schema](../../schemas/tqc-sample-identity-v1.schema.json), [checksum resolver and evidence gate](../../app/tqc_identity_evidence.py) | `catalog/mappings/tqc/sample-identity-v1.json` |
| Reviewed WDA native paper forms and explicit grade/role corrections | [Evidence schema](../../schemas/wdasec-native-paper-facts-v1.schema.json), [native resolver and evidence gate](../../app/providers/wdasec_skill/papers.py) | `catalog/mappings/wdasec/native-paper-facts-v1.json` |
| Reviewed native Taipower answer corrections | [Evidence schema](../../schemas/taipower-answer-corrections-v1.schema.json), [role resolver and evidence gate](../../app/taipower_file_roles.py) | `catalog/mappings/taipower/answer-corrections-v1.json` |
| Reviewed Taiwan Water native papers and occupational applicability | [Evidence schema](../../schemas/twc-archive-papers-v1.schema.json), [member/category resolver and conservation gate](../../app/providers/twc_recruit/archive.py) | `catalog/mappings/twc/archive-papers-v1.json` |
| Reviewed NSYSU native documents, page selections and admission applicability | [Evidence schema](../../schemas/nsysu-native-selections-v1.schema.json), [identity and conservation owner](../../app/providers/hce_nsysu/evidence.py) | `catalog/mappings/nsysu/native-selections-v1.json` |
| Generated NSYSU PDF reconstruction plans | [Plan schema](../../schemas/compiled-pdf-selection-v1.schema.json), [bounded applicator](../../app/providers/hce_nsysu/selection_plan.py) | `data/providers/hce_nsysu/native-selections/` |
| Normalized papers | [V3 facts contract](../../schemas/normalized-paper-v3.schema.json), [V2 compatibility contract](../../schemas/normalized-paper-v2.schema.json) | `data/providers/<provider_id>/papers/` |
| Derived provider index | [V3 material/date index](../../schemas/provider-index-v3.schema.json), [V2 compatibility index](../../schemas/provider-index-v2.schema.json), [V1 compatibility index](../../schemas/provider-index-v1.schema.json), [index builder](../../app/provider_index.py) | `data/providers/<provider_id>/index.json` |
| Reviewed source scope and evidence | [Inventory schema](../../schemas/source-inventory.schema.json), [inventory validator](../../app/source_inventory.py) | `catalog/source-inventory.json` |
| Coverage exceptions | [Exception schema](../../schemas/source-coverage-exceptions.schema.json), [matcher](../../app/coverage_exceptions.py) | `catalog/source-coverage/` |
| Publication quarantine | [Quarantine loader](../../app/publication_quarantine.py) | `catalog/mappings/publication-quarantine.json` |
| Site bundles | [V3 material bundle](../../schemas/bundle-v3.schema.json), [V2 compatibility bundle](../../schemas/bundle-v2.schema.json), [publisher](../../app/publisher.py) | `data/sites/<site_id>/bundles.json` |
| Embedded archive manifest | [V4 material manifest](../../schemas/bundle-archive-manifest-v4.schema.json), [V3 compatibility manifest](../../schemas/bundle-archive-manifest-v3.schema.json), [bundler](../../app/bundler.py) | `bundle.json` inside ZIPs |
| Retained historical download snapshots | [Snapshot schema](../../schemas/retained-downloads-v1.schema.json), [site owner and source-history verifier](../../app/retained_downloads.py) | `data/sites/<site_id>/retained-downloads.json` |
| Release assets and plans | [V3 asset schema](../../schemas/release-assets-v3.schema.json), [V2 compatibility schema](../../schemas/release-assets-v2.schema.json), [plan schema](../../schemas/release-plan-v2.schema.json), [shard policy](../../app/release_tags.py) | `data/sites/<site_id>/` |
| Site frontend feed | [V3 material feed](../../schemas/frontend-bundle-feed-v3.schema.json), [V2 compatibility feed](../../schemas/frontend-bundle-feed-v2.schema.json), [publisher](../../app/publisher.py) | `data/sites/<site_id>/frontend-bundles.json` |

## Ownership and versioning

Every persisted contract has one owner. Provider ingestion state and site publication state remain separate, using the scoped paths defined by [app/paths.py](../../app/paths.py). New providers and sites must not introduce root-level equivalents.

Public-facing contracts must be versioned, with integer schema versions. A breaking contract change requires a version increment, migration and rollback paths, affected-consumer analysis, tests, and operator procedure updates. An additive change may retain its version when all consumers safely handle unknown fields. Critical readers must reject unsupported versions and provider ownership mismatches. Converting current year-scoped provider arrays to a versioned envelope is a migration, not a documentation-only change.

Raw events preserve enough official-source detail to rebuild normalized records. They must not depend on site publication choices. Normalized records are source-agnostic; provider-specific parser fields require a reviewed shared-contract change before entering those records.

A raw paper may retain its native acquisition category in `source_category_code`
while `category_code` distinguishes reviewed examination-group applicability.
Shared acquisition uses the native category for the mirror prefix, still checking
the exact source URL, file role and checksum. Logical group references do not
require additional native downloads or mirror copies. The optional field is
omitted for existing raw records and does not enter normalized publication
contracts. A single-role paper must use that role's own mirror checksum during
classification, even when another role shares its category and subject codes.

Postal acquisition keys identify native download URLs independently of listing
order. The provider verifies each command's native payload before applying
reviewed job, grade, role and date evidence. Shared common papers retain one
acquisition locator while each occupation receives its own logical reference.
A changed URL, applicability context or checksum requires review. The full
postal provider projection must preserve every reviewed job and native paper;
partial archive builds additionally verify the selected jobs' complete sets.

Reviewed `source_material` facts distinguish material nature from file role.
The schema owns their vocabulary; questions, answers, corrections, audio and
transcripts remain roles of their containing material. A raw event can provide
shared facts, with an explicit paper or attachment override for supporting
reference material. Normalization retains the selected facts in v3 records;
non-administered kinds are separate identity variants. Unknown or conflicting
material/date evidence requires a reason and a review disposition.

An answer-clarification result is a distinct file role. It can uphold some
answers and change others, so it stays alongside original answer keys and must
not be treated as an interchangeable copy or automatically described as a
complete corrected key. Its provider owns the source-page context and native
subject applicability; the shared payload policy validates native PDF and Word
formats using the same signature and archive-integrity checks as other roles.

A `source_document` attachment retains an original combined PDF when reviewed
subject selections become the paper records. A selection locator's fragment
identifies the parent checksum and native pages; the HTTP observation belongs
to the complete parent. Generated reconstruction operations are provider-owned
technical state. Their parent/output digests and sizes must agree with the
separate reviewed evidence, and applying them must reproduce the exact reviewed
PDF. They do not supply subject, group, stage, date or identity decisions.

The source date states whether a year belongs to an administered examination,
an edition, or a publication. Undated and unknown dates carry no year. Existing
`year_ad`/`year_roc` partitions and source keys remain traceability fields and
must not substitute for these facts. V3 indexes keep the partition year and
the reviewed date year in separate columns; legacy records have no reviewed
date value. V1/v2 records without facts retain their original serialized shape.

Reviewed material uses bundle v3, archive manifest v4, and site/feed v3. Legacy
bundles keep their v2 records, paths, years and public URLs; a mixed site uses a
v3 envelope. Release inventories and identity taxonomy remain v2. The complete
site catalog is validated before a partial update selects affected bundles:
unresolved facts, mixed material kinds, and partially migrated logical bundle
history fail before any archive mutation. Provider acquisition and source holds
remain independent; correct dates alone do not authorize publication.

V3 indexes distinguish legacy rows, reviewed dates, explicitly undated material,
and review holds. V2 indexes remain readable for recovery, but publication falls
back to full records until they are rebuilt because a nullable date alone cannot
establish material kind or date basis. Older application versions cannot read
normalized v3 records; rollback requires the matching provider-state baseline
and index, preserving revision journals and recovery bytes.

## Source evidence and traceability

The reviewed source inventory states the official boundary and records observed availability and local-state floors. Discovery manifests record actual source listings. Agreement between local files does not prove that a source was discovered.

The source-scope validator checks registry coverage, local losses, evidence, and discovery gaps. Ordinary growth preserves the reviewed floor; losses require a reviewed inventory change. The explicit strict discovery option also requires complete snapshots and representation of official events.

Evidence references identify repository files or code-styled level-two Markdown sections, such as `docs/providers/notes.md#provider_id`. Both file and section must resolve. Archived documentation is historical context and must not become current source evidence.

Each source manifest belongs to one provider and contains no site or Release state. Current listings may delist retained events: their exam records remain in the manifest, identified by its retained-event policy, so acquired history remains traceable.

`source_exam_id` retains the official event traceability key. `canonical_id` remains a compatibility lookup/URL identity. V2 publication groups by `bundle_id` and its reviewed identity dimensions; display labels alone must not merge distinct official programs or levels. Provider normalization does not require a site-derived bundle URL.

Every provider-identified paper is classified during normalization and migration,
regardless of its display language. Direct legacy bundle callers retain v1
filenames through the record version; ASCII labels do not select a separate
classification or publication path.

Scoped provider writers omit the optional legacy `download_url_bundle` field; current download URLs belong to site publication state. Readers retain compatibility with earlier provider records and default an absent field to an empty string. The legacy root-layout writer remains available for migration consumers.

The source revision journal preserves an earlier normalized paper or attachment
when its source reference is retired, its payload checksum changes, or its
source context changes while the reference and bytes stay the same. Its key
includes the provider, native event, year, category, subject, file role, source
URL, and checksum. V2 entries additionally bind the original exam, category,
subject and attachment titles and reviewed source material facts. Classification,
publication and storage-locator changes alone do not create source revisions.
Multiple earlier contexts can share one checksum-addressed blob. Repeated
observations of the same context do not create duplicate journal entries.
Readers support v1 and v2 journals. Appending a new entry writes a v2 wrapper;
existing v1 entries, identifiers, timestamps and review bindings remain unchanged.
An unchanged provider write leaves the original journal bytes untouched.
The journal retains original source fields and independently copied,
checksum-addressed bytes; it is recovery evidence outside the current site feed.
An earlier revision must not satisfy a current download merely because its URL
matches. Records without a verified checksum retain metadata with no claimed
payload. `retained_at` is the UTC retention time, not an examination or edition
date. Readers reject unsupported versions, mismatched ownership, duplicate ids,
and inconsistent source keys or blob locators.

When an older mirror generation predates corrected storage paths or revision
blobs, the provider writer can rehome missing files from recorded current,
previous or retired locators within that provider. Both the expected checksum
and the candidate's actual full checksum must match. It copies bytes independently,
does not overwrite existing files, and creates no successful-download receipt.
Unrecorded files, foreign provider paths and symbolic-link candidates cannot
supply recovery bytes.

An old acquisition record may describe a reference document as a paper or audio.
Keep that record immutable. Exact revision id, source context and checksum can
anchor a separate reviewed non-paper disposition, exposed by the history audit.
The judgment records material nature and its own official date evidence, without
assigning an examination grade, event or bundle identity. Native member names and
checksums support optional byte verification. An unmatched source key, changed
payload or unresolved material/date fact cannot inherit the reviewed disposition.
This review does not add a retired reference to the current publication catalog.

SFI native-paper facts can also explain an earlier wrong programme/event
assignment in the history audit. Exact payload, resource-code and file-role
evidence supplies the independent native programme, date, round, subject scope
and regional form. The original journal record and its source provenance remain
immutable; unresolved revisions are reported separately.

iPAS native-document facts bind each programme, exact URL and full checksum to
its reviewed material role. A paper context includes the native event, grade,
subject and any qualification route or content edition. Combined question and
answer files use their actual role's mirror checksum during normalization.
Reference PDFs and native OpenDocument forms remain attachments, with no exam
identity inferred from their storage collection. Historical generic-import
records can receive independent native-role interpretations without changing
their original dictionaries. Unknown reference dates stay explicitly unresolved.
The native evidence catalog contains factual labels and source context, not
question or answer body excerpts.

Review queues contain unresolved normalization work and remain provider-scoped unless a site explicitly owns cross-provider canonicalization. Failure rows use the shared model and retain enough event, file, URL, and stage information for machine-readable triage and recovery.

The compact review codec preserves original text, classification signatures, source keys, and row order without reclassifying retained evidence. Empty queues remain empty arrays; readers also accept earlier rich record arrays. Use `review-queue` to expand a ledger without loading papers.

## Site publication

The site inventory contains only bundles selected by its executable policy, including minimum-year and quarantine rules. The bundle inventory and Release asset inventory must describe the same public physical assets, with an explicit owning Release tag for each asset. Frontend state must be derived from those site outputs.

Final download URLs identify ungated Release artifacts. Frontend download gates may wrap those URLs, but ingestion and publication must complete independently of the gate. A site can span multiple Release tags; consumers must use recorded assignments.

The frontend consumes structured identity and classification from the feed. It must not reconstruct official identity from display-name regexes or read raw provider crawl fields. Its compact build projection may replace URLs with repository/tag/asset locators and defer search aliases to an index, provided reconstructed URLs are identical and index positions match bundle order. Both hashed assets come from one build.

Optional source entries project reviewed names and official HTTPS URLs from the inventory. Optional `updated` is the latest successful sync timestamp among contributing events; it is not the source's publication date. Both projections are validated against their owners before commit. These additive fields require no ZIP or identity migration; missing historical receipts remain unknown.

Multipart bundles share one logical identity and have distinct physical assets. The frontend presents one logical row, sums its file counts, and provides a control for each part. A legacy alias must not present a partial ZIP as a complete archive; aliases are retained only for unsplit assets, with older Releases remaining the compatibility source.

Embedded archive manifests store content locators and checksums rather than full provider records.
Manifest version 3 records the year and source URL alongside event/category/
subject/role codes. These codes alone are not unique: one event can reference
multiple URLs for the same paper. Every retained source record remains in
`papers`; equivalent payloads for the same
subject, year, and file role may share one `bundle_entry` when their SHA-256
checksums and extensions match. `file_count` counts physical payloads, excluding
`bundle.json`, rather than source references. Revised bytes, different subjects,
years, and roles remain separate. Multipart construction keeps all references to
a shared entry in the same part. ZIP paths must be relative, unique even on
case-insensitive filesystems, and portable to Windows and UTF-8 filesystems.
Readers recover entries from manifest v1 through v4. Legacy publication rebuilds
older manifests into v3; reviewed material uses v4, retaining each native source
key and full material evidence. Reviewed ZIP roots use the official date basis:
ROC exam years, `edition-<Gregorian year>`, `published-<Gregorian year>`, or
`undated`. Storage/acquisition years stay in source keys rather than becoming
public dates. Equivalent payload sharing uses the reviewed date folder, subject,
role, extension and checksum, while retaining every source reference and its
evidence. Cleanup preserves archives containing unique material evidence.

Eligibility, planning, validation and recovery use the shared publication date
policy. Explicitly undated resources satisfy a minimum of one year, but never
inflate a multi-year requirement. Frontend filters use reviewed ROC years or the
explicit undated choice; labels and landing pages distinguish material kind,
edition, publication and exam dates. Signed ROC years preserve earlier Gregorian
history. Rollback requires the earlier code, matching site inventory and released
archives; never pair old checksums with rebuilt ZIPs.

## Release and compatibility rules

Release tooling derives upload, coverage, and prune decisions from the site-owned asset inventory. Tag assignment is deterministic. Capacity counts every primary, compatibility alias, and multipart ZIP as a physical asset; [shard policy](../../app/release_tags.py) and the [accepted decision](../decisions/ADR-2026-07-16-exam-identity-and-release-shards.md) own the limits and assignment rules.

Every uploaded ZIP must remain strictly below GitHub's per-asset byte ceiling. The [bundler](../../app/bundler.py) targets a lower safety limit and partitions oversized logical bundles; [Release tooling](../../.github/scripts/release_assets.py) checks local size before uploading. A failed upload must not leave committed metadata pointing at an oversized or unpublished artifact.

Compatibility outputs require an explicit consumer, owner, removal condition, and test. Retiring public paths or assets requires its own reviewed decision and successful validation of the scoped replacement. Taxonomy changes require historical reclassification and disposition checks before republishing; see [catalog audit](../operations/catalog-audit.md).

For execution and recovery, use [data lifecycle](data-lifecycle.md), [the runbook](../operations/runbook.md), and [the recovery guide](../operations/recovery.md).

A retained historical download preserves an existing published ZIP at its exact
name, tag and SHA-256 while its former identity is replaced. It is not an alias
for one successor and does not enter the current bundle/feed population. Its
original source keys and checksums must remain anchored in current provider state
or immutable source history. The site snapshot owns retention; the Release
inventory projects it, capacity planning reserves its physical slot, and complete
archive audits verify its original bytes separately from current identity
conservation. Routine publication, local cleanup and Release pruning preserve it.
A separate retirement operation requires exact asset/checksum authorization.
Rollback may instead promote a snapshot back to its exact original primary
identity and locator after verifying its bytes and source anchors. Retain any
published successors before restoring the earlier current inventory so their
URLs remain available too.

Release inventories use v3 when frozen downloads are present; inventories without
them retain their existing contract. Critical release readers reject unsupported
versions and foreign site ownership before upload or cleanup. Release plans list
retained downloads explicitly and count all declared physical asset names.
