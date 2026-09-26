# Catalog sources of truth

This directory contains reviewed, versioned domain knowledge used to classify exam records. It is not generated crawl state.

## Layout

- taxonomy/exam-identity-v2.json: shared domains, families, series, levels, and policy metadata.
- mappings/publication-quarantine.json: reviewed provider publication exclusions. Provider membership and minimum-history rules are executable in `app/site_registry.py`; MOEX phrase rules are executable in `app/classification.py`.
- mappings/<provider>/: future provider-specific mappings when shared rules are insufficient.
- source-coverage/<provider_id>.json: reviewed evidence for official source events/files that are blocked or intentionally out of scope; these are manual inputs, not generated crawl state.
- source-inventory.json: reviewed source-scope matrix with status/evidence and exact local-state observations; it is not proof of live source completeness.

## Change protocol

Every concept or rule change must include:

- stable ID and human label;
- provider and effective date/year range when applicable;
- evidence or source reference;
- reason the dimension affects paper identity;
- a fixture/test or audit signature proving the intended result;
- catalog version impact.
- for a blocked or excluded source, exact official URL, capture date, response fingerprint, observation, and narrow reason.

IDs are keys. Labels and aliases may change without renaming an ID. Do not put generated paper/bundle JSON or source downloads here.

Provider-specific phrase rules live in `app/classification.py`; catalog taxonomy remains the reviewed shared vocabulary. A change that can alter historical bundles requires full migrate-catalog and audit-catalog runs.
