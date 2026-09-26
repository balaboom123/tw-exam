# Data lifecycle

Official sources flow through provider discovery, validated mirrors, normalized retained state, site selection, bundle generation, Release upload, and frontend projection. [Contracts](contracts.md) defines the ownership boundaries; [the runbook](../operations/runbook.md) and [recovery guide](../operations/recovery.md) own repeatable procedures.

## Discover, probe, and acquire

Discovery returns official availability for the selected provider. It is read-only unless manifest writing is explicitly requested; it never writes site publication state. A broad discovery capture should use a positive source-request delay.

Providers that implement the probe URL model compare year/event HEAD responses against their own source manifest and fetch pages when those comparisons indicate change. The probe produces an explicit sync decision. Its output and optional manifest remain provider-owned.

Sync downloads into `mirror/providers/<provider_id>/`. Mirror locators preserve year, event, category, subject, and file-role distinctions. Valid files are reused; successfully refreshed files replace stale siblings with incorrect extensions. Each retained payload receives a SHA-256 checksum.

Source pages are fetched serially. Independent files may download with bounded concurrency, while mirror writes remain serial; stateful or rate-limited providers restrict concurrency. Shared HTTP adapters relying on sync retries use one transport attempt to avoid multiplying retries. Transient discovery/fetch requests use bounded retries and honor `Retry-After`.

An unavailable year listing preserves retained provider state, returns failure, and produces no publish plan. Payload validation rejects HTML placeholders and signatures inconsistent with the expected file role. The [sync implementation](../../app/sync.py) owns supported signatures; acceptance of an outer archive does not establish the integrity of its nested papers.

## Normalize and retain history

Normalization applies reviewed provider aliases and shared identity rules to raw events; unresolved cases enter the provider review queue. Full provider writes also regenerate a compact index. Index readers fall back to source files when an index is missing or its source snapshot is stale; the catalog audit still reclassifies full records. The index must remain rebuildable, and [its check](../../scripts/build_provider_indexes.py) compares complete contents against source files.

Full and incremental sync retain previously acquired events and papers that disappear from current listings, including their source-manifest evidence. Incremental and targeted merging preserve unaffected history. Refreshed names may derive canonical migrations; they must not orphan prior compatibility identities or silently merge distinct official programs.

Successful full, incremental, targeted, and repair syncs record a UTC receipt for each completely refreshed event. Failed and unrefreshed events keep their previous receipt. Discovery, catalog migration, and bundle builds do not advance dates. Retained state may lack receipts; never backfill them from discovery or file modification time.

Failure behavior is owned by [the CLI](../../app/cli.py) and [state merging](../../app/state.py):

- Targeted sync aborts generated-state writes on failure by default. Explicit partial mode may retain the valid subset and failure rows, returns nonzero, and requires follow-up audit/publication.
- Incremental sync preserves previous records for failed events and returns nonzero while failures remain.
- Full sync and local bundle construction may produce failure reports and local outputs, but failures remain visible and block an undeployable generated-state commit.
- [The commit guard](../operations/workflows.md) checks retained-state floors and global publication invariants before generated changes can reach main.

## Reviewed exceptions and quarantine

Source-coverage evidence belongs in `catalog/source-coverage/`. Event exceptions must match the current raw event; file exceptions must match the exact provider, event, year, paper, role, URL, and download failure stage. An event exception conflicts with acquired papers, attachments, normalized records, or failures. Unmatched entries are orphans. Conflicts and orphans fail strict history audit; publication validation ignores only exact reviewed file exceptions. Re-probe or remove evidence when the official source changes or becomes available.

Quarantine belongs in `catalog/mappings/publication-quarantine.json` and withholds a defective site projection. It does not change source acquisition denominators: registered providers continue discovery, sync, mirroring, normalization, and audits. Source-coverage exceptions explain acquisition blockers; quarantine explains withheld publication.

Each quarantine entry requires a reason, source evidence, and a resolvable maintained note section. Required providers cannot be quarantined; the site publisher fails closed rather than bypassing its required-state guard. Quarantine must not delete retained provider state, mirrors, or bundle archives.

The site publisher owns exclusion. History audit reports quarantined events separately from minimum-year exclusions and unexplained publication gaps. Lifting a reviewed entry requires republishing. Removing a projection leaves previously uploaded assets directly downloadable until Release reconciliation/pruning completes.

## Build and publish

The [site configuration](../../app/site_registry.py) selects eligible providers and bundles. The [bundler](../../app/bundler.py) reads normalized papers and validated mirror files, reuses matching unchanged single-part archives, and verifies ZIP CRC for entries whose mirror files are absent before reuse. Deterministic entry timestamps and permissions stabilize rebuilt bytes; completed archive checksums are streamed from files.

New archive manifests retain paper keys, checksums, and entry names. Older full-record manifests remain readable and may be reused when projected content agrees. Already compressed media are stored without a second compression pass; manifest text remains compressed.

Site state and bundle files remain under `data/sites/<site_id>/` and `bundles/sites/<site_id>/`. Bundle metadata, Release asset inventories, compatibility aliases, multipart records, and shard assignment remain site-owned. Provider syncs may pass affected-bundle plans to publication; those plans do not authorize hand-written tag assignments.

Release tooling ensures assigned Releases exist, uploads expected artifacts, checks coverage, and prunes unexpected ZIPs according to the inventory. Compatibility aliases remain published while listed. Successful upload precedes the matching generated-state commit, so a site feed never promises unavailable changed artifacts.

## Frontend projection

The frontend build consumes the site-owned feed and emits a content-hashed compact feed plus a separate search index. Search loads the index lazily, and its positions match bundle order. The same projection creates static bundle landing pages and a sitemap using recorded site URLs and Release locators.

The frontend join flow unlocks final ZIP links locally. It does not affect source ingestion, provider state, or Release publication. Provider and publication commands must complete independently of frontend gating.

## Manual inputs and generated state

Reviewed source inventory, source-coverage evidence, quarantine decisions, taxonomy/mappings, and provider aliases are maintained inputs. Raw events, normalized papers, derived indexes, review queues, failures, discovery manifests, site inventories, and publication assets are derived outputs. [Scoped paths](../../app/paths.py) and their schemas own the layout.

Fix the owner and regenerate. Manual edits to generated state are temporary recovery actions and must be followed by rebuilding or a code fix. New providers retain their own discovery, failures, review state, mirrors, and evidence; new sites retain their own publication state. Shared contracts may evolve through the reviewed migration process, while provider parsing fields remain outside frontend feeds.
