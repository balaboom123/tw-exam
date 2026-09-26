# Add a provider

Use this workflow for both source investigation and implementation. A provider is not registered until public official-source proof and the runtime, site, inventory, tests, workflow, and documentation changes agree.

## 1. Prove the source boundary

- Choose a stable snake-case `provider_id`.
- Identify official entry points, stable event and attachment identities, availability semantics, access restrictions, payload types, and update cadence.
- Reject authentication-gated, non-enumerable, third-party-only, or non-paper sources unless the product scope explicitly changes.
- Record an investigated but unshipped source under `candidates` in `catalog/source-inventory.json`; provider notes and adapters describe registered sources.

## 2. Define ownership and contracts

- Provider state: `data/providers/<provider_id>/` and `mirror/providers/<provider_id>/`.
- Site publication: `data/sites/<site_id>/` and `bundles/sites/<site_id>/`.
- Map source records into the normalized schema and deterministic identity catalog.
- Define discovery, manifest behavior, payload validation, failure semantics, aliases, and source-coverage exceptions.
- Decide which site consumes the provider and whether its bundle or shard policy changes.

## 3. Implement and register

- Add the provider adapter under `app/providers/<provider_id>/`.
- Register it in `app/providers/registry.py` and the consuming site in `app/site_registry.py`.
- Add the provider entry to `catalog/source-inventory.json` with reviewed evidence.
- Add source-specific parser, discovery, validation, state, publication, and workflow tests as applicable.
- Add or update provider sync automation and operator recovery steps.

## 4. Write source judgment

Add source-specific judgment to `docs/providers/notes.md` when the source needs a boundary, durable blocker, publication exception, or operating deviation. Use a stable ``## `provider_id` `` heading so evidence can reference `docs/providers/notes.md#provider_id`; both the file and section are checked. Keep shared procedures in the runbook and accepted rationale in an ADR.

Run the renderer to refresh the generated provider index. Changing facts such as status, URLs, years, record counts, or restrictions starts in `catalog/source-inventory.json`. Keep those facts out of maintained notes.

## 5. Verify definition of done

```bash
uv run python scripts/render_docs.py
uv run python scripts/validate_source_inventory.py
uv run python scripts/validate_docs.py --check
uv run python -m pytest -q
uv run python -m app audit-catalog --repo-root . --site-id default --strict
uv run python -m app history-audit --repo-root . --site-id default --strict --skip-mirror-check
```

The change is incomplete if the inventory, runtime registry, site registry, or generated index disagree; a note refers to an unregistered provider; an evidence file or section is missing; generated state is unscoped; publication ownership is unclear; or an operator cannot recover the provider from the shared procedures and its source-specific notes.
