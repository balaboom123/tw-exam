# CI/CD and release

CI validates checked-in behavior; provider workflows refresh retained source state; site workflows publish site-owned bundles and release shards; deploy workflows build the frontend from site feeds.

## CI gates

The fast Python job enforces Ruff lint and formatting rules and strict mypy checks for the application, then runs tests that do not load the retained catalog, documentation validation, shell syntax checks, and whitespace checks on every CI run. A path check runs the catalog job for changes to retained data, application code, schemas, scripts, tests, or dependency and CI configuration; manual runs and uncertain comparisons run it as well. Frontend-only and prose-only changes skip that job. The catalog job runs the `repo_data` tests plus strict catalog and history audits, publication validation, JSON Schema validation of site artifacts, provider review ledgers, and current provider paper samples, source-inventory validation, a full generated-index comparison against provider files, and release planning. Both Python jobs install from the tracked `uv.lock` with frozen resolution. The frontend job installs locked dependencies, tests, lints, builds, and enforces a 100 KiB gzip budget for built JavaScript.

The path selector checks out recent history rather than downloading the entire
repository history. Normal pushes and pull-request merge commits can compare
their available parents. A multi-commit push, rewritten branch or missing merge
base can exceed that shallow checkout; an uncertain comparison then runs every
catalog gate. History availability is a performance hint, never permission to
skip checks. Provider acquisition and publication retain their separate history
requirements.

The workflow file is the owner of exact CI commands. This document explains why the gates exist and does not duplicate an exhaustive command list.

The monthly archive verification workflow derives its release shards from the
committed site asset inventory, downloads each shard, and reads every ZIP entry
to check it against the catalog. A failed shard is reported through workflow
health monitoring. This complements the fast metadata gates; local cleanup
still requires a complete content audit of the active archive set.

## Frontend build assets

Use Node 22.18 or newer: build projections share the frontend provenance validator through native TypeScript loading. CI and Netlify previews use Node 22.

`npm run build` checks the browser, Vite configuration, and production build helpers with strict TypeScript before emitting the site. The compact feed types are shared by its producer and browser consumer; build helpers keep their implementations and types together. `npm test` imports the TypeScript sources directly through Node's native type stripping.

The frontend build emits a compact, content-hashed bundle feed, a lazy search index, bundle landing pages, and a sitemap from the site-owned frontend feed. Its Traditional Chinese font subsets are checked in under `frontend/src/assets/fonts/`. After changing public bundle names or visible UI copy, regenerate them with `uv run scripts/build_frontend_fonts.py` on a machine with the `fonts-noto-cjk` package, then run the frontend build. The accompanying OFL texts are shipped from `frontend/public/fonts/`. Regenerate the share card with `uv run scripts/build_og_card.py` after changing its text or visual design.

## Release ownership

- Providers own retained source state, not public release tags.
- Sites own bundle selection, asset naming, and deterministic release-shard assignment.
- `data/sites/<site_id>/release-assets.json` describes the expected public asset set.
- The release plan assigns assets to bounded site-owned tags without relying on one global release.
- Upload and prune operations must compare external state with generated expectations before mutation.

Release cleanup verifies every primary archive's hosted checksum across all
shards before deleting the first obsolete ZIP. Missing, stale, or unverifiable
primaries block cleanup. Upload also rejects a missing local ZIP unless the
hosted archive already matches its expected checksum. After an interrupted bulk
upload, rerun upload to skip matching archives, then verify coverage before
pruning and deploying the corresponding site feed.

Changing shard policy, compatibility aliases, or release ownership requires tests, an updated operator procedure, and an ADR when the rationale is durable.

## Documentation enforcement

CI runs `scripts/validate_docs.py --check`. Changes to provider scope, CLI commands, or the maintained document set must be rendered before push so generated provider facts, the provider index, command reference, and single document index stay current.
