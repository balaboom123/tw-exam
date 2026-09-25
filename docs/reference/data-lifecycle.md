# Data Lifecycle

This document defines the source-to-publication lifecycle, the integrity checks at each stage, and the write behavior of current commands.

## Lifecycle Overview

The lifecycle is:

1. discover source inventory
2. probe for recent changes
3. fetch pages and download files
4. validate and mirror payloads
5. normalize provider data into shared catalog records
6. merge refreshed state with existing published state
7. build bundles and release metadata
8. upload/prune release assets
9. publish frontend outputs
10. apply frontend social-gated download behavior

## Stage Details

### 1. Discovery

Behavior:

- `discover` asks the selected provider (MOEX by default) for available years and exam codes.
- discovery is read-only by default and produces JSON output for inspection.
- `discover --write-manifest` persists the official year/exam listing into the provider-scoped source manifest; operators should use a positive `--delay-seconds` value for broad source captures.

Invariant:

- each provider MUST have a provider-scoped discovery entrypoint
- discovery MUST NOT mutate publication state

### 2. Probe

Behavior:

- `probe-latest` uses `data/providers/<provider_id>/source-manifest.json` for providers that implement the probe URL model
- it compares current year or exam HEAD responses to prior manifest entries
- it fetches full exam pages only when a HEAD comparison indicates change

Integrity properties:

- cheap change detection
- minimized download volume
- explicit `should_sync` decision in `.tmp/source-probe.json`

Invariant:

- each provider MUST own its own source manifest
- probe manifests MUST be provider-scoped, not global

### 3. Fetch and mirror

Behavior:

- `sync-*` commands download source files into `mirror/providers/<provider_id>/`
- mirror paths are built from year, exam ID, category, subject, and file type
- existing mirrored files are reused when valid
- source pages are fetched serially; independent files within a page may download with up to four workers, while mirror writes remain serial
- providers with session state or source rate limits set `max_concurrency = 1`
- transient fetch and discovery requests use bounded retries and honor `Retry-After` when provided

Integrity properties:

- mirror files receive SHA-256 checksums
- invalid payloads are redownloaded
- stale sibling files with wrong extensions are removed after successful refresh

Invariant:

- mirror roots MUST be provider-scoped
- source download logic MUST remain separate from site publication logic

### 4. Payload validation

Behavior:

- PDF files are validated by signature
- ZIP files are validated by signature
- HTML placeholders are rejected
- wrong binary type for a known file type is treated as failure

Why this matters:

- source systems sometimes return an HTML page or placeholder instead of the file
- bundle generation MUST NOT consume invalid mirrored content

## Reviewed Source-Coverage Exceptions

Manual source evidence belongs in `catalog/source-coverage/<provider_id>.json`, not in generated provider state. An entry may be event-scoped (`blocked` or `intentionally_out_of_scope`) or file-scoped (`blocked`). Each entry records the official URL, capture date, response fingerprint, observation, and reason.

`history-audit` matches event entries to the current raw event and file entries to the exact current download failure: provider, exam ID, AD year, paper code, file type, download URL, and `download` stage must all agree. An event exception conflicts if the current raw page has papers or attachments, normalized records exist, or any failure is recorded. An unmatched exception is an orphan. Both conditions fail strict audit. The publication validator ignores only exact file exceptions; all other provider failures remain blocking.

A reviewed exception is therefore an evidence-backed denominator decision, not a generated-manifest shortcut. When the source changes or a file becomes available, the old entry becomes an orphan or conflict and must be removed or re-probed.

## Publication Quarantine

A source-coverage exception explains what the repository could not *acquire*. A quarantine entry explains what the repository must not *publish*. The two MUST NOT be conflated.

Quarantine lives in `catalog/mappings/publication-quarantine.json` because it is publication policy rather than source evidence. Each entry records the provider, site, a status drawn from `wrong_identity`, `wrong_payload`, `corrupt_payload`, `non_paper_role`, or `duplicate_source_identity`, a reason, and pointers to the source manifest and maintained provider page that evidence the defect. Both pointers MUST resolve; a dangling pointer fails loading, so withheld data can never become unexplained.

Rules:

- A quarantined provider MUST remain registered in `app.site_registry`. Quarantine withholds publication only; dropping the provider from the registry instead would remove it from the source-inventory, catalog-audit, and history-audit denominators and would hide the defect rather than expose it.
- `app.publisher.load_site_catalog` is the only consumer that skips quarantined providers. Discovery, sync, mirroring, normalization, and every audit continue to run.
- A provider in `required_provider_ids` MUST NOT be quarantined. `load_site_catalog` fails closed rather than silently bypassing the missing-state guard.
- `history-audit` reports quarantined events under the distinct `withheld_by_quarantine` status. It MUST NOT reuse `excluded_by_publication_policy`, which means the min-years rule, and it MUST NOT leave them as `normalized_not_published`, which means an unexplained gap. Keeping the status separate is what stops a deliberate withholding from turning a red gate green.
- Quarantine MUST NOT delete provider state, mirrored bytes, or bundle archives. Lifting an entry is a revert plus a republish.

Removing a provider from the projection also strands its already-uploaded release assets. They stay downloadable by direct URL until `release_assets.py prune` runs, so a quarantine is not fully effective until the release side is reconciled.

### 5. Normalize

Behavior:

- provider raw pages become `NormalizedPaper` records
- provider-scoped alias rules under `data/providers/<provider_id>/aliases.json` are applied during normalization
- unresolved naming cases are emitted to `data/providers/<provider_id>/review-queue.json`
- provider writes also regenerate `data/providers/<provider_id>/index.json`, a compact projection for inventory gates; readers scan source files when the index is missing or its file-size snapshot differs
- source inventory, publication eligibility, and event-level history checks read a current index; the catalog audit still reclassifies full paper records

Invariant:

- alias rules SHOULD be provider-scoped unless a site explicitly owns cross-provider canonicalization
- normalized schema MUST remain source-agnostic
- the derived index MUST be rebuildable from provider source files; `uv run python scripts/build_provider_indexes.py --check` compares its full contents with those files

### 6. Merge refreshed state

Behavior:

- full sync writes a complete regenerated state
- incremental sync merges refreshed state into existing generated state
- targeted sync merges only probe-identified exams
- full and incremental syncs retain previously downloaded events that disappear
  from the current listing; their source-manifest exam records are retained too
- canonical ID migrations are derived when refreshed records rename a prior category family

Why this matters:

- unaffected bundles should stay stable
- recent refreshes should not force full rebuilds
- canonical renames should not orphan prior records

### 7. Build bundles

Behavior:

- bundle generation reads normalized papers and mirrored files
- an unchanged single-part ZIP is reused when its full embedded manifest and entry names match current papers; entries whose mirror files are absent are streamed once to verify ZIP CRC before reuse
- generated site bundle metadata is written to `data/sites/<site_id>/bundles.json`
- release asset inventory is written to `data/sites/<site_id>/release-assets.json`
- legacy alias asset names may be preserved for compatibility

Invariant:

- bundle outputs MUST be site-scoped
- release asset inventory MUST belong to the site that publishes those bundles

### 8. Release synchronization

Behavior:

- `.github/scripts/release_assets.py` ensures the GitHub release exists
- coverage compares expected ZIP names to release ZIP names
- upload publishes local bundles
- prune removes stale ZIP assets not present in the current expected set

Integrity properties:

- release state is derived from generated metadata, not manual memory
- compatibility alias assets remain published when listed in generated metadata

### 9. Public output

Behavior:

- `app.publisher.publish_site` writes site-scoped publication metadata under `data/sites/<site_id>/`
- the frontend build projects `frontend-bundles.json` into a content-hashed `data/bundles-<hash>.json` feed and a separate `data/search-index-<hash>.json`
- the search index is fetched only when a visitor searches; its rows align with the public feed's bundle order
- the same projection generates one static `b/<bundle-id>.html` landing page per public bundle and `sitemap.xml`; page URLs and release links come from site publication metadata

Invariant:

- public outputs MUST be site-scoped
- frontend feed generation MUST consume publication outputs, never raw provider state

### 10. Frontend social gate

Behavior:

- the site-owned source feed keeps direct ZIP URLs; the public build feed carries their repository, release tag, and asset name so the browser can reconstruct them
- the frontend download row opens a category-specific LINE channel before unlocking ZIP downloads locally

Invariant:

- provider and publication commands MUST NOT depend on frontend download gating to complete ingestion or release publication

## Command Write Behavior

| Command | Writes generated data? | Partial writes allowed? | Failure semantics |
| --- | --- | --- | --- |
| `discover` | no | n/a | read-only |
| `probe-latest` | yes, only output file and optional manifest | yes | returns a probe result even when no sync is needed |
| `sync-targeted` | yes, if successful; `--allow-partial` may commit the valid subset | no by default; explicit partial mode is opt-in | default aborts on any failure; partial mode retains successful records and failure rows, returns non-zero, and requires follow-up audit/publication |
| `sync-incremental` | yes | yes, safe subset only | preserves existing state for failed exam IDs and returns non-zero if failures remain |
| `sync-full` | yes | yes | writes full regenerated outputs and returns non-zero if failures remain |
| `build-bundles` | yes | no | local rebuild path; returns non-zero if failures exist |

## Generated Versus Manual Inputs

Manual inputs today:

- `data/providers/<provider_id>/aliases.json`
- `catalog/source-coverage/<provider_id>.json` reviewed evidence for blocked or intentionally excluded official sources
- `catalog/source-inventory.json` reviewed source scope/status/evidence and exact local-state observations
- `catalog/mappings/publication-quarantine.json` reviewed decisions to withhold a registered provider from a site projection

Generated outputs today:

- `data/providers/<provider_id>/exams/**`
- `data/providers/<provider_id>/papers/**`
- `data/providers/<provider_id>/review-queue.json`
- `data/providers/<provider_id>/sync-failures.json`
- `data/providers/<provider_id>/source-manifest.json` when supported
- `data/sites/<site_id>/bundles.json`
- `data/sites/<site_id>/release-assets.json`
- `data/sites/<site_id>/*` publication indexes

Operators and developers MUST treat generated outputs as derived state. Manual edits to generated files are temporary recovery actions only and MUST be followed by a rebuilding command or code fix.

## Expansion Rules

- New providers MUST own their own manifests, review queues, failure logs, and source-coverage evidence where an official source is blocked or intentionally excluded. The reviewed source inventory must also gain a provider row before the provider is treated as in scope.
- New sites MUST own their own bundle metadata and release asset inventory.
- Shared schemas MAY evolve, but provider-specific fields MUST NOT leak into site-facing bundle feeds without an explicit contract update.
