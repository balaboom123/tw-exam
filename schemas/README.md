# Versioned JSON contracts

Schemas here validate persisted v2 boundaries. They are separate from taxonomy data and generated runtime state.

- taxonomy.schema.json: shared identity dimensions and bundle-purity source.
- provider-mapping.schema.json: provider rule and review-policy source.
- normalized-paper-v2.schema.json: provider paper records with identity facets and provenance.
- provider-index-v1.schema.json: generated event and paper projection used by fast data gates.
- review-queue-v2.schema.json: lossless compact provider review ledgers and earlier rich arrays.
- provider-sync-status-v1.schema.json: optional successful event sync timestamps, separate from discovery snapshots.
- bundle-v2.schema.json: site bundle inventory entries.
- bundle-archive-manifest-v2.schema.json: compact embedded `bundle.json` rows with paper keys, checksums, and ZIP entry names; `manifest_version` versions this archive format independently of identity schema versions.
- frontend-bundle-feed-v2.schema.json: public structured feed consumed by the frontend.
- release-assets-v2.schema.json: site release asset inventory and shard assignments.
- release-plan-v2.schema.json: dry-run shard assignments and physical asset counts.
- classification-audit.schema.json: whole-catalog audit output.
- source-coverage-exceptions.schema.json: reviewed event/file exceptions for official sources that are blocked or intentionally out of scope.
- source-inventory.schema.json: reviewed official-source scope, status, evidence, and local-state observations for the default site.

A v2 payload must declare schema_version 2 and catalog_version exam-identity-v2 where required. Consumers should reject unsupported versions instead of guessing.

Validate the checked-in site artifacts, reviewed source inventory, and a current paper file from each provider with:

~~~bash
uv run python scripts/validate_schemas.py
~~~
