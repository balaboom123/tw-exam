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
| Reviewed exact historical MOEX category facts | catalog/mappings/moex/category-identity-v1.json |
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
- level_id: official grade, proficiency band, qualification class, or explicit not-applicable;
- track_id: subject, 類科, profession, or qualification track;
- variant_ids: content-changing group, language choice, population/destination group, or form;
- stage_id: first/second/third/pretest stage when papers differ;
- exam_event_id: source occurrence retained for traceability;
- bundle_id: deterministic logical publication identity;
- bundle_name: human-readable title containing series/level distinction;
- confidence and reason: explainability and review state.

A provider may use not-applicable. It must not be forced into MOEX grades when its official system has no equivalent.

Historical MOEX listings may omit a programme heading or grade even when the
official question header supplies it. Reviewed catalog facts match the exact
event, year, native category code, category wording and event title. Native codes
identify that source context; they are not grade numbering rules. The classifier
uses an approved fact before applying missing-evidence review. A changed context
does not inherit the decision. The schema gate checks retained question URLs,
roles and checksums against every fact. Header extraction is evidence for manual
review and must never automatically approve a mapping. Unreadable or conflicting
headers remain unresolved.

Once national-security or Investigation Bureau programme identity is resolved,
its explicit heading is removed from the track label. A historical programme
prefix and a current grade/track separator must not create different identities
for the same programme, grade and group. Language choices remain content-changing
variants; occupation words alone do not select a programme.

Missing evidence for a level-based programme is `unknown` with a review
disposition, rather than `not-applicable`. GEPT uses its own proficiency levels
([official introduction](https://www.gept.org.tw/Exam_Intro/t01_introduction.asp));
JLPT uses N1–N5
([official levels](https://www.jlpt.jp/about/levelsummary.html)); skill
certification distinguishes 甲級、乙級、丙級 and 單一級
([official reference announcement](https://www.wdasec.gov.tw/News_Content.aspx?n=25D08F3407C71E8F&s=5A79A08661B15374&sms=1BE761BDBCE7C913)).
The classifier keeps records with absent or unsupported markers isolated by
event until the source level is resolved. These official references were
rechecked on 2026-10-07.

Taiwanese-language A/B/C papers each cover two native proficiency levels, as
defined by the [official test introduction](https://ttg.moe.edu.tw/tmt/view.php?page=questionBase),
rechecked on 2026-10-07. Classification retains the proficiency band as the level
and the paper form as a separate content-changing variant. An absent or
conflicting form remains in review; it does not mean that proficiency is
inapplicable. Resolving this dimension does not establish a date or turn sample
material into administered exam papers.

Transport recruitment and transport promotion have different meanings. Railway,
highway, and port recruitment remain separate programmes even when one source
event also hosts police, aviation, or patent-exam papers. Recruitment uses the
recorded 高員三級、員級、佐級 grade; 高員三級 is not 員級晉高員級.
An explicit transport programme in the category takes priority over other
programmes in the event title. Historical categories containing only a transport
grade can use the event when it identifies exactly one transport programme.
Multiple possible programmes remain in review. Promotion evidence retains the
promotion identity and its transition grade.

This boundary follows the official
[railway category listing](https://www.moex.gov.tw/other/105040/query.html),
[historical recruitment rules](https://law.exam.gov.tw/LawContentHistory.aspx?hid=588&media=print),
[port paper header](https://wwwq.moex.gov.tw/exam/wHandExamQandA_File.ashx?c=505&code=096270&q=1&s=3011&t=Q),
and [promotion rules](https://law.exam.gov.tw/LawContentHistory.aspx?hid=943&id=FL016765),
rechecked on 2026-10-07. Legal grade equivalence with 高考、普考 or 初考 does
not change the recruitment programme or grade identity.

Police recruitment, general-police recruitment, national-security intelligence
recruitment, and Investigation Bureau recruitment are separate programmes.
The [police rules](https://law.exam.gov.tw/LawContent.aspx?id=FL016741) and
[general-police rules](https://law.exam.gov.tw/LawContent.aspx?id=FL031745)
have separate eligibility and subject tables; equal grades and occupations do
not make their papers interchangeable. National-security intelligence follows
[its own rules](https://law.exam.gov.tw/LawContentSource.aspx?id=FL016782),
and Investigation Bureau recruitment follows
[different rules](https://law.exam.gov.tw/NewsContent.aspx?id=51141&media=print).
These sources were checked on 2026-10-07.

An explicit programme marker in the category takes priority over a co-hosted
event title. Historical programme headings between the grade and occupation
remain evidence, such as `三等考試_警察特考_行政警察人員`. An occupation alone
does not distinguish the two police programmes; an unmarked category in their
combined event remains in review. Unmarked national-security categories in an
event that also hosts civil-staff qualification must likewise remain unresolved.
The named graded security programmes use `unknown` with review when their
official grade is missing, rather than declaring the dimension inapplicable.
The occupation heading word `類別` after `人員` does not create another track;
for example, `行政警察人員類別` and `行政警察人員` share the same track.
Removing that heading word preserves the following group, so `航海組` and
`輪機組` remain different tracks. Raw source wording is retained unchanged.

Promotion purpose takes priority over occupation words. For example,
`公務人員升官等公務薦任_司法行政` is civil-service promotion, and
`警察人員警正_行政警察人員` in a police-promotion event is police promotion.
Neither is a recruitment examination. Police and customs promotion have
separate programme identities, following the official
[police-promotion rules](https://law.exam.gov.tw/LawContent.aspx?id=FL016771)
and [customs-promotion rules](https://law.exam.gov.tw/LawContent.aspx?id=FL016781).
Police promotion preserves the distinct 警監 and 警正 ranks.

Transport promotion preserves its sector as well as its rank transition:
railway, highway, port, and postal papers have separate subject tables under the
[transport-promotion rules](https://law.exam.gov.tw/LawContent.aspx?id=FL016765).
Retained telecommunications, water-transport, and aviation promotion also keep
their source sector, as documented in the Exam Yuan's
[historical account](https://ws.exam.gov.tw/Download.ashx?n=MDExMjE2MzEzMjYucGRm&u=LzAwMS9VcGxvYWQvMS9yZWxmaWxlLzkzMTkvMjE4MTMvMWE2NWRiNjktMWQ1ZC00ZDAyLTgwZGEtYjllMzc2Yjc2MTMwLnBkZg%3D%3D).
Sector evidence may precede or follow the rank in the raw category. A category
without sector evidence in a shared transport event remains in review; likewise,
an unmarked rank in a combined civil/customs promotion event cannot identify
the programme. Historical transport-promotion categories that abbreviate the
destination as 高員級、員級、佐級 use the corresponding full transition:
員級晉高員級、佐級晉員級、士級晉佐級. For example, the retained
[ROC 93 telecommunications paper](https://wwwq.moex.gov.tw/exam/wHandExamQandA_File.ashx?t=Q&code=093280&c=311&s=c381&q=1)
states 士級晉佐級 for the source category `物料(佐級)`. These references were
rechecked on 2026-10-07. Original wording,
source keys, roles, URLs, and checksums remain unchanged by reclassification.

## Bundle purity

The default bundle policy groups papers only when these values match:

exam_series_id, level_id, track_id, stage_id when stage changes content, and every provider-policy variant that changes the paper set.

Subject- and occupation-scoped providers include the track label in the public
bundle title, so different tracks do not appear as duplicate downloads. Display
label corrections preserve identity-derived asset names and release assignments.
When official labels vary historically within a reviewed track, publication uses
the latest retained label. Unmapped mixed Chinese/Latin labels include a digest
of the full text in their IDs; keeping only the Latin fragment can merge distinct
occupations such as CNC turning and CNC milling. Correcting such a collision
requires reclassification and archive splitting, not just a title change.

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
- site alias retention is owned by `app/site_registry.py`; retained legacy public asset names occupy physical slots;
- the v1 reader remains available;
- v2 publication can be rebuilt without deleting v1 assets.

The default site omits unpublished alias metadata under the
[accepted alias decision](../decisions/ADR-2026-09-27-unused-v2-release-aliases.md).
Its primary-only policy is owned by `app/site_registry.py`. Historical readers
and download fallback remain available; another site's declared compatibility
assets still consume physical release slots.

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
