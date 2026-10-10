# Contracts

Executable fields, types, versions, and vocabularies live in the owners below. This reference defines ownership, traceability, publication boundaries, and compatibility obligations. Identity and bundle purity are defined in [exam identity](exam-identity.md).

## Contract owners

| Boundary | Executable owner | Persisted location |
| --- | --- | --- |
| Source discovery/probe manifest | [SourceManifest](../../app/manifest.py) | `data/providers/<provider_id>/source-manifest.json` |
| Raw events, review entries, failures, aliases | [Models](../../app/models.py), [state](../../app/state.py) | `data/providers/<provider_id>/` |
| Compact review ledger | [Review schema](../../schemas/review-queue-v2.schema.json), [codec](../../app/review_queue.py) | `data/providers/<provider_id>/review-queue.json` |
| Successful event sync receipts | [Sync status schema](../../schemas/provider-sync-status-v1.schema.json), [provenance](../../app/provenance.py) | `data/providers/<provider_id>/sync-status.json` |
| Superseded source references and payloads | [Revision schema](../../schemas/provider-source-revisions-v1.schema.json), [retention owner](../../app/source_revisions.py) | `data/providers/<provider_id>/source-revisions.json`, `mirror/providers/<provider_id>/recovery/source-revisions/` |
| Reviewed non-paper revision meaning | [Reference evidence schema](../../schemas/source-revision-reference-v1.schema.json), [review owner](../../app/source_revision_review.py) | `catalog/mappings/source-revisions/reference-material-v1.json` |
| Material nature and official date evidence | [Material schema](../../schemas/source-material-v1.schema.json), [fact model](../../app/source_material.py) | Provider raw events/papers and normalized records |
| Reviewed TQC sample identities | [Evidence schema](../../schemas/tqc-sample-identity-v1.schema.json), [checksum resolver and evidence gate](../../app/tqc_identity_evidence.py) | `catalog/mappings/tqc/sample-identity-v1.json` |
| Reviewed native Taipower answer corrections | [Evidence schema](../../schemas/taipower-answer-corrections-v1.schema.json), [role resolver and evidence gate](../../app/taipower_file_roles.py) | `catalog/mappings/taipower/answer-corrections-v1.json` |
| Normalized papers | [V3 facts contract](../../schemas/normalized-paper-v3.schema.json), [V2 compatibility contract](../../schemas/normalized-paper-v2.schema.json) | `data/providers/<provider_id>/papers/` |
| Derived provider index | [V3 material/date index](../../schemas/provider-index-v3.schema.json), [V2 compatibility index](../../schemas/provider-index-v2.schema.json), [V1 compatibility index](../../schemas/provider-index-v1.schema.json), [index builder](../../app/provider_index.py) | `data/providers/<provider_id>/index.json` |
| Reviewed source scope and evidence | [Inventory schema](../../schemas/source-inventory.schema.json), [inventory validator](../../app/source_inventory.py) | `catalog/source-inventory.json` |
| Coverage exceptions | [Exception schema](../../schemas/source-coverage-exceptions.schema.json), [matcher](../../app/coverage_exceptions.py) | `catalog/source-coverage/` |
| Publication quarantine | [Quarantine loader](../../app/publication_quarantine.py) | `catalog/mappings/publication-quarantine.json` |
| Site bundles | [V3 material bundle](../../schemas/bundle-v3.schema.json), [V2 compatibility bundle](../../schemas/bundle-v2.schema.json), [publisher](../../app/publisher.py) | `data/sites/<site_id>/bundles.json` |
| Embedded archive manifest | [V4 material manifest](../../schemas/bundle-archive-manifest-v4.schema.json), [V3 compatibility manifest](../../schemas/bundle-archive-manifest-v3.schema.json), [bundler](../../app/bundler.py) | `bundle.json` inside ZIPs |
| Release assets and plans | [Asset schema](../../schemas/release-assets-v2.schema.json), [plan schema](../../schemas/release-plan-v2.schema.json), [shard policy](../../app/release_tags.py) | `data/sites/<site_id>/` |
| Site frontend feed | [V3 material feed](../../schemas/frontend-bundle-feed-v3.schema.json), [V2 compatibility feed](../../schemas/frontend-bundle-feed-v2.schema.json), [publisher](../../app/publisher.py) | `data/sites/<site_id>/frontend-bundles.json` |

## Ownership and versioning

Every persisted contract has one owner. Provider ingestion state and site publication state remain separate, using the scoped paths defined by [app/paths.py](../../app/paths.py). New providers and sites must not introduce root-level equivalents.

Public-facing contracts must be versioned, with integer schema versions. A breaking contract change requires a version increment, migration and rollback paths, affected-consumer analysis, tests, and operator procedure updates. An additive change may retain its version when all consumers safely handle unknown fields. Critical readers must reject unsupported versions and provider ownership mismatches. Converting current year-scoped provider arrays to a versioned envelope is a migration, not a documentation-only change.

Raw events preserve enough official-source detail to rebuild normalized records. They must not depend on site publication choices. Normalized records are source-agnostic; provider-specific parser fields require a reviewed shared-contract change before entering those records.

Reviewed `source_material` facts distinguish material nature from file role.
The schema owns their vocabulary; questions, answers, corrections, audio and
transcripts remain roles of their containing material. A raw event can provide
shared facts, with an explicit paper or attachment override for supporting
reference material. Normalization retains the selected facts in v3 records;
non-administered kinds are separate identity variants. Unknown or conflicting
material/date evidence requires a reason and a review disposition.

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
when its source reference is retired or its payload checksum changes. Its key
includes the provider, native event, year, category, subject, file role, source
URL, and checksum. Classification changes alone do not create source revisions.
The journal retains original source fields and independently copied,
checksum-addressed bytes; it is recovery evidence outside the current site feed.
An earlier revision must not satisfy a current download merely because its URL
matches. Records without a verified checksum retain metadata with no claimed
payload. `retained_at` is the UTC retention time, not an examination or edition
date. Readers reject unsupported versions, mismatched ownership, duplicate ids,
and inconsistent source keys or blob locators.

An old acquisition record may describe a reference document as a paper or audio.
Keep that record immutable. Exact revision id, source context and checksum can
anchor a separate reviewed non-paper disposition, exposed by the history audit.
The judgment records material nature and its own official date evidence, without
assigning an examination grade, event or bundle identity. Native member names and
checksums support optional byte verification. An unmatched source key, changed
payload or unresolved material/date fact cannot inherit the reviewed disposition.
This review does not add a retired reference to the current publication catalog.

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
