# Extension Rules

This is the normative governance document for expanding the repository.

## 1. Architecture Rules

- Every new source MUST have a unique `provider_id`.
- Every public deployment MUST have a unique `site_id`.
- Provider-owned state MUST remain separate from site-owned publication state.
- Shared-core utilities MAY be reused across providers, but provider-specific parsing MUST stay inside provider-owned modules.
- New code MUST prefer scoped paths over new root-level global files.

## 2. Provider Rules

Every provider MUST define:

- discovery logic
- probe strategy
- source manifest ownership
- normalized schema mapping
- failure recording behavior
- mirror key rules
- operator runbook changes

Every provider MUST NOT:

- write to another provider's state
- depend on frontend-specific logic to complete ingestion
- depend on monetization or frontend download gates to complete sync

## 3. Site Rules

Every site MUST define:

- input provider set
- bundle-building and bundle-selection rules
- release tag ownership
- compatibility alias asset policy
- frontend feed contract
- deployment target

Every site MUST NOT:

- read raw provider crawl state directly in the frontend
- reuse another site's release tag by default
- mix operator secrets or monetization settings without explicit documentation

## 4. Data Rules

- New generated state MUST be provider-scoped or site-scoped.
- New source additions MUST NOT introduce another root-level equivalent of `data/bundles.json` or `data/source-manifest.json`.
- Manual inputs MUST be clearly separated from generated outputs.
- If a legacy compatibility output is required, it MUST be documented as transitional and have an owner.

## 5. CI/CD Rules

- New workflows MUST declare ownership by provider or site.
- Scheduled sync and audit workflows SHOULD be provider-scoped.
- Release publication workflows SHOULD be site-scoped.
- Deploy workflows MUST be site-scoped.
- Workflow names, environment variables, and release tags MUST move away from MOEX-only naming for new work.

## 6. Download Gate Rules

- Download gates MUST apply to final bundle URLs only.
- Download gates MUST remain optional per site.
- Download gate failure MUST NOT corrupt provider state.

## 7. Testing Rules

Every new provider or site change MUST add or update:

- unit tests for parsing or schema logic
- tests or validations for manifest or publication contracts
- operator verification steps

At minimum, contributors SHOULD run:

- `uv run python -m pytest -q`
- `npm test` in `frontend/` when frontend feed behavior changes
- `npm run build` in `frontend/` when deploy inputs change

## 8. Documentation Rules

Every expansion PR MUST update:

- the owning catalog, schema, registry, or mapping
- the maintained reference or ADR when rules or rationale change
- the operator procedure when execution or recovery changes
- the generated provider index through `scripts/render_docs.py`, and source-specific judgment in `docs/providers/notes.md` when needed

No provider or site is production-ready until the runbook and recovery guide describe how to operate it. Changing status, URLs, years, counts, or restrictions starts in `catalog/source-inventory.json`, never in generated Markdown.

## 9. Migration Rules

- Provider and site state MUST remain in their scoped paths.
- Legacy root-level outputs MUST NOT be revived.
- Compatibility outputs require an explicit owner, consumer, removal condition, and test.
- Removing a compatibility path requires updated procedures and successful validation of its scoped replacement.

## 10. Change Review Checklist

Before approving an expansion change, confirm:

- What is the `provider_id`?
- What is the `site_id`, if any?
- Which files are provider-owned?
- Which files are site-owned?
- Which workflow owns sync?
- Which workflow owns release publication?
- Which workflow owns deploy?
- How is failure recorded?
- How does an operator recover?
- Which docs changed?

If these questions cannot be answered from the PR, the change is not ready.
