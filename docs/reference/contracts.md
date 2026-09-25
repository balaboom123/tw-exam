# Contracts

Status: current field-level contract reference. For identity ownership and bundle purity, see exam-identity.md. Older name-only grouping assumptions in this document are superseded by the v2 rules.

This document defines the concrete data and interface contracts that future providers and sites MUST follow.

The goal is to prevent the multi-source expansion from drifting into ad hoc JSON shapes, implicit compatibility assumptions, or frontend/backend coupling.

## Contract Principles

- Every persisted schema MUST have an explicit owner.
- Provider-owned contracts and site-owned contracts MUST remain separate.
- Public-facing contracts MUST be versioned.
- Additive changes are preferred over destructive changes.
- Breaking contract changes MUST ship with a migration plan.

## Contract Ownership

| Contract | Owner | Current location |
| --- | --- | --- |
| source manifest | provider | `data/providers/<provider_id>/source-manifest.json` |
| raw exam pages | provider | `data/providers/<provider_id>/exams/*.json` |
| normalized papers | provider | `data/providers/<provider_id>/papers/*.json` |
| review queue | provider | `data/providers/<provider_id>/review-queue.json` |
| sync failures | provider | `data/providers/<provider_id>/sync-failures.json` |
| alias rules | provider | `data/providers/<provider_id>/aliases.json` |
| bundle metadata | site | `data/sites/<site_id>/bundles.json` |
| release asset inventory | site | `data/sites/<site_id>/release-assets.json` |
| frontend bundle feed | site | `data/sites/<site_id>/frontend-bundles.json` |

## Versioning Rules

- Persisted JSON contracts SHOULD include `schema_version` once they become multi-provider or multi-site scoped.
- Schema versions MUST be integers.
- A breaking change MUST increment `schema_version`.
- A non-breaking additive change MAY keep the current version if all consumers safely ignore unknown fields.
- Consumers MUST fail loudly on unsupported `schema_version` values for critical contracts.

## Provider Contract: Source Manifest

Implemented contract:

- `app/manifest.py` defines `SourceManifest`
- current schema version is `1`
- sections include `probe_policy`, `years`, `exams`, and `files`

Serialized shape:

```json
{
  "schema_version": 1,
  "provider_id": "moex",
  "probe_policy": {},
  "years": {},
  "exams": {},
  "files": {}
}
```

Required fields:

- `schema_version`: integer
- `provider_id`: stable provider identifier
- `probe_policy`: provider-specific probe settings
- `years`: year-level probe state
- `exams`: source exam-level probe state
- `files`: optional file-level probe state if the provider needs it

The reviewed source scope is separate from generated manifests:

- `catalog/source-inventory.json` records every provider and candidate source in the documented scope, its official URL/status/evidence, and an exact observation of local provider state.
- `scripts/validate_source_inventory.py` verifies provider registry coverage, local-state drift, and local evidence references. It reports missing, not-applicable, or partial live discovery manifests plus official events listed without local state, and only makes those discovery gaps fatal with `--require-discovery-manifests`.
- The inventory must never be used to infer that a source was discovered merely because local manifests agree.

Rules:

- A manifest MUST belong to exactly one provider.
- A manifest MUST NOT describe release or site state.
- `years[].exam_codes` records the current source listing. When a full or
  incremental sync retains a previously discovered event that the source has
  delisted, `exams` also retains that event and `probe_policy.retained_exam_codes`
  identifies it so the manifest remains evidence for all retained provider state.

## Site Contract: Public Bundle Inventory

Rules:

- `data/sites/<site_id>/bundles.json` is the public publication inventory for that site, not a dump of every provider-owned canonical bundle.
- Site policy is allowed to filter bundles before publication. For the current `default` site, the public inventory contains multi-year bundles only.
- `data/sites/<site_id>/release-assets.json` MUST describe the same published bundle set as `data/sites/<site_id>/bundles.json`.
- Every release asset entry MUST carry an explicit `release_tag`. Missing tags are treated as a contract error.
- Probe consumers MUST reject manifests that do not match the expected provider.

## Provider Contract: Raw Exam Page Record

Current shape is derived from `SourceExamPage`, `ExamAttachment`, and `ParsedPaper`.

Required fields:

- `source_exam_id`
- `year_ad`
- `year_roc`
- `exam_name_raw`
- `attachments`
- `papers`

Attachment fields:

- `title`
- `file_type`
- `download_url_source`
- `storage_key`
- `asset_name`
- `checksum`
- `download_url_mirror`

Paper fields:

- `category_raw`
- `category_code`
- `subject_code`
- `subject_name_raw`
- `files`
- `mirror_files`

Rules:

- Raw exam page records are provider-owned and MUST preserve enough source detail to rebuild normalized records.
- Raw records MUST NOT depend on site publication choices.

## Provider Contract: Normalized Paper Record

Current shape is derived from `NormalizedPaper`.

Required fields:

- `canonical_id`
- `canonical_name`
- `year_roc`
- `exam_name_raw`
- `category_raw`
- `subject_name_raw`
- `paper_code`
- `file_type`
- `download_url_source`

Current optional-but-supported fields:

- `category_code`
- `source_exam_id`
- `subject_code`
- `download_url_mirror`
- `download_url_bundle`
- `storage_key`
- `checksum`

Rules:

- `canonical_id` is the stable legacy lookup/URL identity and MUST be preserved for compatibility.
- `bundle_id` plus the v2 identity dimensions is the grouping key for v2 bundle generation.
- A parser MUST NOT merge official programs or levels merely because their canonical track labels match.
- `source_exam_id` is the stable provider traceability key.
- `download_url_bundle` is publication-derived and MUST remain optional at provider-normalization time.
- Provider-specific parser fields MUST NOT leak into this contract without an explicit schema update.

A future envelope change would require a schema version and migration; the current provider files remain year-scoped arrays. Illustrative envelope:

```json
{
  "schema_version": 1,
  "provider_id": "moex",
  "records": []
}
```

## Provider Contract: Review Queue

Required fields for each record:

- `raw_category`
- `normalized_candidate`
- `source_exam_id`
- `year_roc`

Rules:

- Review queue entries MUST only represent unresolved normalization work.
- Review queue records MUST be provider-scoped unless a site explicitly owns cross-provider canonicalization.

## Provider Contract: Sync Failure Record

Current shape is derived from `SyncFailure`.

Required fields:

- `stage`
- `source_exam_id`
- `year_roc`
- `paper_code`
- `file_type`
- `url`
- `message`

Rules:

- Failure records MUST be machine-readable enough for triage and operator recovery.
- New providers MUST reuse these semantics unless there is a documented reason to extend them.

## Site Contract: Bundle Metadata

Current frontend publication depends on fields derived from `BundleAsset`.

Required fields:

- `schema_version`
- `bundle_id`
- `canonical_id`
- `canonical_name`
- `catalog_version` for v2 site state
- `domain_id`, `exam_family_id`, `exam_series_id`, `level_id`, `track_id`, `variant_ids`, `stage_id`
- `years`
- `file_count`
- `storage_key`
- `asset_name`
- `release_tag`
- `download_url`
- `checksum`
- `legacy_asset_names`
- `part_index` and `part_count` when a logical bundle is multipart

Rules:

- Bundle metadata is site-owned publication state.
- Bundle metadata MUST identify the GitHub release tag that owns the final asset.
- `download_url` MUST point to the ungated final artifact target.
- `legacy_asset_names` MAY be used for compatibility but MUST remain site-owned, not provider-owned.
- Consumers MUST NOT assume that all bundles for a site live under one release tag.
- GitHub release assets MUST be smaller than 2 GiB; the generator targets a lower safety ceiling (currently 1.9 GB).
- A logical identity MAY have multiple physical records when its payload exceeds the byte ceiling. Multipart records MUST share `bundle_id`, carry `part_index`/`part_count`, and have distinct `asset_name` values.
- Multipart projections MUST NOT publish a legacy alias as if it were a complete archive. Legacy aliases are retained only for unsplit assets; old v1 releases remain the compatibility source.

A future envelope change would require a schema version and migration; the current provider files remain year-scoped arrays. Illustrative envelope:

```json
{
  "schema_version": 1,
  "site_id": "default",
  "bundles": []
}
```

## Site Contract: Release Asset Inventory

Required fields:

- `release_tag`
- `storage_key`
- `asset_name`
- `checksum`
- `legacy_asset_names`

Rules:

- The release asset inventory is the source of truth for what ZIP assets a site expects on its release.
- Release publication logic MUST derive upload and prune behavior from this contract.
- Release asset inventory entries MUST be site-owned even when multiple providers contribute bundle content.
- Release tag assignment MUST be deterministic.
- Site publication MUST support multiple release tags.
- The default operational ceiling is to shard before any one release exceeds 900 physical ZIP assets.
- Physical count includes the primary asset and every `legacy_asset_names` alias; no release may exceed 1,000 physical assets.
- Byte size is an independent constraint: every uploaded ZIP MUST be strictly below GitHub's 2 GiB per-asset limit.
- Release tooling MUST check local byte size before invoking `gh release upload`; a failed upload MUST NOT leave a manifest pointing at an oversized asset.

## Site Contract: Frontend Bundle Feed

Serialized feed shape:

```json
{
  "schema_version": 2,
  "site_id": "default",
  "catalog_version": "exam-identity-v2",
  "bundles": [
    {
      "id": "nurse",
      "name": "Nurse",
      "years": [115, 114],
      "fileCount": 42,
      "url": "https://..."
    }
  ]
}
```

Rules:

- The frontend feed MUST be site-owned.
- The frontend feed MUST NOT expose raw provider-specific crawl fields.
- The frontend feed MUST be derivable entirely from site publication outputs.
- The public build projection MAY replace a direct URL with its GitHub repository, release tag, and asset name. It MUST reconstruct the same URL without consulting provider state.
- Search aliases MAY be split into a lazy index. Index positions MUST align with the public bundle order, and both content-hashed files MUST be emitted by the same build.
- V2 frontend entries MUST consume structured series/level/track facets; they MUST NOT reconstruct official identity from display-name regexes.
- Multipart entries MUST render one logical row with one download control per part; the row's file count is the sum of its parts.

## Compatibility Policy

- Scoped provider and site contracts are the only supported persisted ownership model.
- A compatibility output requires an explicit consumer, owner, removal condition, and test.
- New providers and sites MUST NOT introduce root-level equivalents of scoped state.

## Contract Change Checklist

A contract change MUST identify the owner, schema version impact, migration path, affected providers and sites, publication consequences, rollback path, tests, and operator procedure updates.
