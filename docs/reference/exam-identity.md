# Exam identity and bundle policy v2

Status: normative developer reference  
Owner: data-model and publication maintainers  
Applies to: catalog version exam-identity-v2, identity schema version 2

This is the current implementation reference for exam identity. The executable behavior is in app/classification.py, reviewed vocabulary is in catalog/, and serialized boundaries are in schemas/. It supersedes the old name-only grouping description.

## Why this exists

A display name such as 「一般行政」 is a track, not a complete exam identity. The same track can occur in high, ordinary, elementary, local, promotion, disability, indigenous, customs, or other programs. Grouping by a stripped display name therefore creates mixed bundles.

The v2 rule is catalog-wide:

- scan every provider registered for the site;
- classify every retained historical paper record;
- check every public bundle against the same identity dimensions;
- isolate unresolved evidence in review rather than silently merging it.

「一般行政」 is a regression example, not a special implementation path.

## Ownership and file layout

| Responsibility | Source of truth |
| --- | --- |
| Shared concepts and labels | catalog/taxonomy/exam-identity-v2.json |
| MOEX level and promotion rules | app/classification.py |
| Provider membership and publication policy | app/site_registry.py and catalog/mappings/publication-quarantine.json |
| Deterministic identity resolution | app/classification.py |
| Normalized paper contract | schemas/normalized-paper-v2.schema.json |
| Bundle contract | schemas/bundle-v2.schema.json |
| Frontend feed contract | schemas/frontend-bundle-feed-v2.schema.json |
| Release planning contract | schemas/release-plan-v2.schema.json |
| Audit output contract | schemas/classification-audit.schema.json |
| Operator procedure | docs/operations/catalog-audit.md |
| Durable decisions | docs/decisions/ |

data/ and bundles/ are generated state. They are not taxonomy sources and must not be hand-edited to fix classification.

## Identity dimensions

The classifier returns an immutable ExamIdentity containing:

- provider_id: source owner, for example moex or gept_cert;
- domain_id: broad domain such as civil service, admissions, certification, employment, or professional qualification;
- exam_family_id: stable family within the domain;
- exam_series_id: named official program, such as high, ordinary, elementary, local, promotion, or a provider-specific certification;
- level_id: official grade, proficiency band, qualification class, form, or explicit not-applicable;
- track_id: subject, 類科, profession, or qualification track;
- variant_ids: content-changing group, language choice, population/destination group, or form;
- stage_id: first/second/third/pretest stage when papers differ;
- exam_event_id: source occurrence retained for traceability;
- bundle_id: deterministic logical publication identity;
- bundle_name: human-readable title containing series/level distinction;
- confidence and reason: explainability and review state.

A provider may use not-applicable. It must not be forced into MOEX grades when its official system has no equivalent.

## Bundle purity

The default bundle policy groups papers only when these values match:

exam_series_id, level_id, track_id, stage_id when stage changes content, and every provider-policy variant that changes the paper set.

Years and separate exam events may vary inside one bundle. Legal equivalence is not identity: an equivalent grade in another program remains a separate bundle. A site policy may add a discriminator, but it may not remove one without a versioned decision record and invariant tests.

The bundle key is bundle_id (or the explicit v2 identity fields). canonical_id is retained as a legacy URL/lookup key only. It must never be the sole grouping key for v2 publication.

## Resolution and evidence

Resolution is deterministic and provider-aware:

1. normalize Unicode and source text without deleting raw fields;
2. apply provider-specific source/category and historical markers;
3. derive series, level, track, variants, and stage;
4. generate the identity signature and stable IDs;
5. mark the result high, medium, or review.

Stable IDs are curated slugs for known concepts. Unknown text gets a deterministic digest suffix so records cannot collide, but remains review until evidence is approved. Generic name stripping is token extraction only; it is not proof of an official level.

A review result includes provider, raw exam name/category, identity signature, candidate bundle, reason, and source event. Review records are isolated by event/identity candidate. Do not merge a review record into a confident bundle just to make coverage appear complete.

## Compatibility model

V2 is additive and reversible:

- v1 canonical_id, canonical_name, source IDs, and raw labels remain in normalized records;
- v2 fields carry schema_version 2 and catalog_version exam-identity-v2;
- legacy public asset names can remain in legacy_asset_names;
- the v1 reader remains available;
- v2 publication can be rebuilt without deleting v1 assets.

A taxonomy or mapping change requires full historical reclassification, because old records can change bundle identity even when no new source page was fetched.

## Safe change workflow

1. Add or amend the concept/mapping in catalog/ with evidence, effective dates, and an owner.
2. Change app/classification.py only when the rule cannot be represented as data.
3. Add golden fixtures for every affected provider, series, level, and historical spelling.
4. Run: uv run python -m app audit-catalog --output .tmp/catalog-audit.json
5. Run: uv run python -m app migrate-catalog for the complete retained provider set.
6. Rebuild or shadow-build bundles and inspect purity/conservation output.
7. Produce a release plan; count primary and compatibility ZIP names as physical assets.
8. Update the relevant ADR and operator procedure.
9. Run backend, frontend, schema, and link checks. Keep PLAN.md untracked.

Do not fix one visible bundle with a one-off alias. Acceptance is a repeatable whole-catalog audit with no unexplained public unknowns.

## Review invariants

A change is safe only when:

- every retained paper has exactly one deterministic identity;
- every v2 bundle has one series, one level, one track, and one value for each content-affecting variant;
- no paper disappears or appears in two primary bundles without an explicit policy;
- every old public bundle has a keep/rename/split/exclude/review disposition;
- release tags stay at or below the 900 operational target and never exceed the 1,000 hard cap;
- frontend series/level facets come from the v2 feed, not name regexes;
- legacy assets remain recoverable until separately authorized retirement.
