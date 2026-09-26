# Contracts

Executable fields, types, versions, and vocabularies live in the owners below. This reference defines ownership, traceability, publication boundaries, and compatibility obligations. Identity and bundle purity are defined in [exam identity](exam-identity.md).

## Contract owners

| Boundary | Executable owner | Persisted location |
| --- | --- | --- |
| Source discovery/probe manifest | [SourceManifest](../../app/manifest.py) | `data/providers/<provider_id>/source-manifest.json` |
| Raw events, review entries, failures, aliases | [Models](../../app/models.py), [state](../../app/state.py) | `data/providers/<provider_id>/` |
| Compact review ledger | [Review schema](../../schemas/review-queue-v2.schema.json), [codec](../../app/review_queue.py) | `data/providers/<provider_id>/review-queue.json` |
| Successful event sync receipts | [Sync status schema](../../schemas/provider-sync-status-v1.schema.json), [provenance](../../app/provenance.py) | `data/providers/<provider_id>/sync-status.json` |
| Normalized papers | [Normalized-paper schema](../../schemas/normalized-paper-v2.schema.json) | `data/providers/<provider_id>/papers/` |
| Derived provider index | [Index schema](../../schemas/provider-index-v1.schema.json), [index builder](../../app/provider_index.py) | `data/providers/<provider_id>/index.json` |
| Reviewed source scope and evidence | [Inventory schema](../../schemas/source-inventory.schema.json), [inventory validator](../../app/source_inventory.py) | `catalog/source-inventory.json` |
| Coverage exceptions | [Exception schema](../../schemas/source-coverage-exceptions.schema.json), [matcher](../../app/coverage_exceptions.py) | `catalog/source-coverage/` |
| Publication quarantine | [Quarantine loader](../../app/publication_quarantine.py) | `catalog/mappings/publication-quarantine.json` |
| Site bundles | [Bundle schema](../../schemas/bundle-v2.schema.json), [publisher](../../app/publisher.py) | `data/sites/<site_id>/bundles.json` |
| Embedded archive manifest | [Manifest schema](../../schemas/bundle-archive-manifest-v2.schema.json), [bundler](../../app/bundler.py) | `bundle.json` inside ZIPs |
| Release assets and plans | [Asset schema](../../schemas/release-assets-v2.schema.json), [plan schema](../../schemas/release-plan-v2.schema.json), [shard policy](../../app/release_tags.py) | `data/sites/<site_id>/` |
| Site frontend feed | [Feed schema](../../schemas/frontend-bundle-feed-v2.schema.json), [publisher](../../app/publisher.py) | `data/sites/<site_id>/frontend-bundles.json` |

## Ownership and versioning

Every persisted contract has one owner. Provider ingestion state and site publication state remain separate, using the scoped paths defined by [app/paths.py](../../app/paths.py). New providers and sites must not introduce root-level equivalents.

Public-facing contracts must be versioned, with integer schema versions. A breaking contract change requires a version increment, migration and rollback paths, affected-consumer analysis, tests, and operator procedure updates. An additive change may retain its version when all consumers safely handle unknown fields. Critical readers must reject unsupported versions and provider ownership mismatches. Converting current year-scoped provider arrays to a versioned envelope is a migration, not a documentation-only change.

Raw events preserve enough official-source detail to rebuild normalized records. They must not depend on site publication choices. Normalized records are source-agnostic; provider-specific parser fields require a reviewed shared-contract change before entering those records.

## Source evidence and traceability

The reviewed source inventory states the official boundary and records observed availability and local-state floors. Discovery manifests record actual source listings. Agreement between local files does not prove that a source was discovered.

The source-scope validator checks registry coverage, local losses, evidence, and discovery gaps. Ordinary growth preserves the reviewed floor; losses require a reviewed inventory change. The explicit strict discovery option also requires complete snapshots and representation of official events.

Evidence references identify repository files or code-styled level-two Markdown sections, such as `docs/providers/notes.md#provider_id`. Both file and section must resolve. Archived documentation is historical context and must not become current source evidence.

Each source manifest belongs to one provider and contains no site or Release state. Current listings may delist retained events: their exam records remain in the manifest, identified by its retained-event policy, so acquired history remains traceable.

`source_exam_id` retains the official event traceability key. `canonical_id` remains a compatibility lookup/URL identity. V2 publication groups by `bundle_id` and its reviewed identity dimensions; display labels alone must not merge distinct official programs or levels. Provider normalization does not require a site-derived bundle URL.

Scoped provider writers omit the optional legacy `download_url_bundle` field; current download URLs belong to site publication state. Readers retain compatibility with earlier provider records and default an absent field to an empty string. The legacy root-layout writer remains available for migration consumers.

Review queues contain unresolved normalization work and remain provider-scoped unless a site explicitly owns cross-provider canonicalization. Failure rows use the shared model and retain enough event, file, URL, and stage information for machine-readable triage and recovery.

The compact review codec preserves original text, classification signatures, source keys, and row order without reclassifying retained evidence. Empty queues remain empty arrays; readers also accept earlier rich record arrays. Use `review-queue` to expand a ledger without loading papers.

## Site publication

The site inventory contains only bundles selected by its executable policy, including minimum-year and quarantine rules. The bundle inventory and Release asset inventory must describe the same public physical assets, with an explicit owning Release tag for each asset. Frontend state must be derived from those site outputs.

Final download URLs identify ungated Release artifacts. Frontend download gates may wrap those URLs, but ingestion and publication must complete independently of the gate. A site can span multiple Release tags; consumers must use recorded assignments.

The frontend consumes structured identity and classification from the feed. It must not reconstruct official identity from display-name regexes or read raw provider crawl fields. Its compact build projection may replace URLs with repository/tag/asset locators and defer search aliases to an index, provided reconstructed URLs are identical and index positions match bundle order. Both hashed assets come from one build.

Optional source entries project reviewed names and official HTTPS URLs from the inventory. Optional `updated` is the latest successful sync timestamp among contributing events; it is not the source's publication date. Both projections are validated against their owners before commit. These additive fields require no ZIP or identity migration; missing historical receipts remain unknown.

Multipart bundles share one logical identity and have distinct physical assets. The frontend presents one logical row, sums its file counts, and provides a control for each part. A legacy alias must not present a partial ZIP as a complete archive; aliases are retained only for unsplit assets, with older Releases remaining the compatibility source.

Embedded archive manifests store content locators and checksums rather than full provider records. Readers accept both earlier rich manifests and the compact version; semantic equality permits reuse of unchanged released ZIPs.

## Release and compatibility rules

Release tooling derives upload, coverage, and prune decisions from the site-owned asset inventory. Tag assignment is deterministic. Capacity counts every primary, compatibility alias, and multipart ZIP as a physical asset; [shard policy](../../app/release_tags.py) and the [accepted decision](../decisions/ADR-2026-07-16-exam-identity-and-release-shards.md) own the limits and assignment rules.

Every uploaded ZIP must remain strictly below GitHub's per-asset byte ceiling. The [bundler](../../app/bundler.py) targets a lower safety limit and partitions oversized logical bundles; [Release tooling](../../.github/scripts/release_assets.py) checks local size before uploading. A failed upload must not leave committed metadata pointing at an oversized or unpublished artifact.

Compatibility outputs require an explicit consumer, owner, removal condition, and test. Retiring public paths or assets requires its own reviewed decision and successful validation of the scoped replacement. Taxonomy changes require historical reclassification and disposition checks before republishing; see [catalog audit](../operations/catalog-audit.md).

For execution and recovery, use [data lifecycle](data-lifecycle.md), [the runbook](../operations/runbook.md), and [the recovery guide](../operations/recovery.md).
