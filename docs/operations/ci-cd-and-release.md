# CI/CD and release

CI validates checked-in behavior; provider workflows refresh retained source state; site workflows publish site-owned bundles and release shards; deploy workflows build the frontend from site feeds.

## CI gates

The fast Python job runs tests that do not load the retained catalog. The catalog job runs the `repo_data` tests plus strict catalog and history audits, publication validation, JSON Schema validation of site artifacts and current provider paper samples, source-inventory validation, release planning, documentation validation, shell syntax checks, and whitespace checks. Both Python jobs install from the tracked `uv.lock` with frozen resolution. The frontend job installs locked dependencies, tests, lints, builds, and enforces a 100 KiB gzip budget for built JavaScript.

The workflow file is the owner of exact CI commands. This document explains why the gates exist and does not duplicate an exhaustive command list.

## Frontend build assets

The frontend build emits a compact, content-hashed bundle feed, a lazy search index, bundle landing pages, and a sitemap from the site-owned frontend feed. Its Traditional Chinese font subsets are checked in under `frontend/src/assets/fonts/`. After changing public bundle names or visible UI copy, regenerate them with `uv run scripts/build_frontend_fonts.py` on a machine with the `fonts-noto-cjk` package, then run the frontend build. The accompanying OFL texts are shipped from `frontend/public/fonts/`. Regenerate the share card with `uv run scripts/build_og_card.py` after changing its text or visual design.

## Release ownership

- Providers own retained source state, not public release tags.
- Sites own bundle selection, asset naming, and deterministic release-shard assignment.
- `data/sites/<site_id>/release-assets.json` describes the expected public asset set.
- The release plan assigns assets to bounded site-owned tags without relying on one global release.
- Upload and prune operations must compare external state with generated expectations before mutation.

Changing shard policy, compatibility aliases, or release ownership requires tests, an updated operator procedure, and an ADR when the rationale is durable.

## Documentation enforcement

CI runs `scripts/validate_docs.py --check`. Changes to provider scope, CLI commands, or the maintained document set must be rendered before push so generated provider facts, the provider index, command reference, and single document index stay current.
