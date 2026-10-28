# Exam identity and bundle policy v2

Status: normative developer reference  
Owner: data-model and publication maintainers  
Applies to: catalog version exam-identity-v2, identity schema version 2

This is the current implementation reference for exam identity. The executable behavior is in app/classification.py, reviewed vocabulary is in catalog/, and serialized boundaries are in schemas/. It supersedes the old name-only grouping description.

An unresolved selection stage uses `unknown` and requires review. It cannot be
represented as `not-applicable`, which means the dimension genuinely does not
apply. Provider-owned annual evidence may establish a written stage even when an
archive filename omits it. Source partitions remain stable when an official
programme holds its examination in a later year; reviewed material date facts
drive publication years and year folders independently of those source keys.

One retained source event can collect independently governed recruitment
programmes in the same year. Taiwan Sugar annual evidence binds each primary
rule to a disjoint set of exact native paper slots. Neither the year nor the
listing event alone selects the applicable rule. The reader rejects repeated
rule URLs and paper slots across groups so resolution cannot depend on row
order. Shared questions do not merge applicant populations or occupations.
Native anchors may be direct PDFs or version-bound ZIP members. Both role and
annual evidence use the same source-context validator; byte audits also verify
every matching retained parent and the member within it. A valid extracted PDF
cannot hide a changed container or a missing historical parent. Taiwan Sugar
role evidence records question-only and combined question/answer judgments
explicitly, independently of annual-stage approval. Recovery uses evidence
from the matching runtime version.

Postal recruitment uses its native employment grades, with occupations and
indoor/outdoor work settings as tracks. Education eligibility and the numbered
paper sessions do not define grades or selection stages. The IT-specific
recruitment programme is a separate series from general recruitment. Reviewed
annual rules establish the first written-test stage, while exact PDF bytes and
printed applicant codes establish applicability and question/answer roles.
An occupation remains distinct when it shares every written paper with another
occupation. Regional or disability allocations using identical papers do not
create additional content variants. The postal evidence catalog is the owner
of these decisions; source-page order and frontend title parsing are not.

WDA's Chinese masters for foreign-assisted written tests are content-changing
paper forms. Their native headers, original source contexts and checksums own
this distinction; a foreign language cannot be assigned from a Chinese master.
Form evidence covers retained originals without approving other identity or
material dimensions. Unreviewed source contexts or bytes remain in review.
The form review keeps existing IDs for ordinary identities without an assisted counterpart.
The assisted collection explicitly covers written papers; the ordinary
collection retains already published practical components. This grouping is
not a separate qualification path or an exemption from practical requirements.

Native-paper evidence can independently correct a source table's grade and file
role. Each judgment requires the complete native context and checksum. A role
review does not approve a grade, subject label or examination identity; a grade
review does not establish whether answers are present. Form approval alone
cannot supply either judgment. The source fields remain traceability keys.
The v3 evidence format also stores component judgments independently. A source
table's practical column can contain a native theory booklet. Correct the
published component only from its exact native evidence; written presentation
alone does not establish theory, since practical tests can also be written.
The original source component and code remain traceability keys. Readers can recover
the earlier v1 combined review as its originally approved grade and role facts,
without inventing either dimension or changing retained identifiers. Only one
versioned evidence file may own a repository's current WDA facts.
The earlier v2 format retains its separate grade and role meanings.
The v4 format adds independently reviewed material and date facts through the
shared source-material contract. A native annual heading can establish an
administered paper's examination year even when the archive cross-lists it under
another annual event. Keep the original partition year, event, storage key and
source reference; publish the reviewed date only after reviewing the complete
bundle history. A table date or upload filename alone does not establish an
examination year. Identical native payloads with the same reviewed year, subject
and role share one ZIP entry while every source context remains in its manifest.
Different native bytes remain separate. Printed answers can contain multiple
responses; the combined role requires the actual questions and their answers,
independently of material/date approval.
A shared theory paper can apply to distinct qualification tracks. Review the
archive's applicability together with the annual administrative rules before
changing a track; a generic heading or transferable component credit alone does
not establish equivalent qualifications. Preserve the source occupation code
even when its native paper uses a broader code.
Recovery pairs the evidence file with a compatible runtime; reverting to an
older runtime also requires its matching evidence snapshot.
For example, piano-tuning files whose headers say 單一級 belong to that native
grade even when the table says 甲級; files that contain questions and answers
use the combined role. Public subject labels and archive paths use the reviewed
facts, while immutable history retains the earlier source-role observation.

Cooking papers whose native headers identify 資深廚師 and contain a different
question set use a separate paper-form variant. Same-event ordinary controls
establish this content boundary; oral assistance alone does not. Generic senior
oral-assistance rules can specify unchanged questions, so they cannot supply a
blanket rule for these retained booklets. The form is independent of the native
qualification grade and occupation. Historical generic cooking papers that
explicitly cover both vegetarian and nonvegetarian tracks retain that shared
scope instead of being assigned to just one later track.

Taiwan Water archive evidence is keyed by programme year, official source URL,
and original SHA-256 checksum. Reviewed replacements are appended alongside
retained versions; reviewing new bytes must not remove the old anchor. A source
URL may retain several reviewed payload versions. A relocated copy with the
same programme year and checksum must agree on the written-test date, while its
material evidence resolves against the exact source URL. Changed bytes without
their own review remain unresolved.

Taiwan Water's native paper catalogue records the original archive checksum,
full decoded member path, member checksum, file role, and native subject and
category headers. The provider retains the original container as a source
attachment and projects PDFs only when every member agrees with the reviewed
inventory. Legacy CP950 filenames and verified Unicode path metadata are decoded
before applying the shared portable-path policy. A member locator contains both
the parent checksum and full path; changes in ZIP ordering do not rename a paper.
Desktop thumbnail databases and Office lock files remain in the original source
container and are excluded from paper projection. A damaged container remains
whole and unresolved rather than producing a partial successful projection.

Native file review alone does not establish occupational bundle identities.
Separate occupation entries in the same catalogue bind annual job-purpose and
written-subject evidence to exact native members. A shared paper can have several
occupation references with one native locator and shared mirror bytes. Listed
candidate groups using the same written papers do not create content variants.
Distinct occupations remain separate even when their papers are identical.
Only matching source, category, member checksum and material facts approve an
occupational classification. Earlier generic references remain recoverable in
source history. Unreviewed history and industry-school relationships retain
explicit review dispositions; an ordinary recruitment date cannot establish an
unreviewed route's date or stage. The provider's publication quarantine remains
independent of this classification review.

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
| Reviewed TQC sample subjects, native grade coverage and content editions | catalog/mappings/tqc/sample-identity-v1.json |
| Reviewed TCTE annual native groups, constituent groups and historical correspondence | catalog/mappings/tcte/group-identity-v1.json |
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

TQC sample identities require an exact normalized title and reviewed native PDF
checksum in the catalog. Their official subject rules and native sample headers
establish grade coverage; a paper-code numeral, software version or edition year
does not. Shared typing samples cover performance-based grades rather than a
single awarded grade. Content editions remain separate from publication dates.
Unknown titles and changed payloads return to review with isolated identities.
The evidence gate reconciles every approved anchor with current or retained
source history; its optional mirror check verifies the original bytes.

MOEX grade labels preserve the programme's native wording. Civil high
examinations use 級; special examinations use 等. Legal equivalence does not
rename a special grade or make its papers interchangeable with civil high
papers. The [civil-service implementing rules](https://law.exam.gov.tw/LawContent.aspx?id=FL016730)
also document the 1996 change from two civil high levels to three. The classifier
preserves the recorded grade numeral and separates pre-reform civil high papers
with a historical-system variant instead of grouping them with today's same
numeral. An Arabic grade in a category is authoritative; the first grade named
in a combined event is not. An occupation such as 司法行政 does not select the
judicial programme.

Historical high and ordinary eligibility tests belong to the examination
eligibility family, alongside the distinct ungraded Chinese-medicine eligibility
programme. The [historical eligibility rules](https://law.exam.gov.tw/LawContentHistory.aspx?hid=1424&media=print)
describe their purpose as obtaining eligibility to enter another examination.
They are not civil-service appointments or professional licensing examinations.
A shared event title mentioning 中醫師 does not change an administrative
eligibility paper into a Chinese-medicine paper. Missing category levels require
review or an exact checksum-anchored question-header fact. Cohosted professional
papers retain their own programme and native level.

Historical MOEX listings may omit a programme heading or grade even when the
official question header supplies it. Reviewed catalog facts match the exact
event, year, native category code, category wording and event title. Native codes
identify that source context; they are not grade numbering rules. The classifier
uses an approved fact before applying missing-evidence review. A changed context
does not inherit the decision. The schema gate checks retained question URLs,
roles and checksums against every fact. Header extraction is evidence for manual
review and must never automatically approve a mapping. Unreadable or conflicting
headers remain unresolved.

A reviewed category can also identify a conflicting native subject. Every such
subject needs its own official question anchor and review reason in the same
catalog fact. Only that subject is isolated by event; other subjects retain
their resolved category identity. Classification caches preserve this boundary,
and the evidence gate rejects missing or changed question checksums. A conflict
does not justify guessing another candidate route or deleting the source bytes.

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

TOCFL bank identities likewise distinguish native proficiency bands from their
paired score outcomes. Its Novice system is separate from CEFR A1–A2. Exact
[native resource facts](../../catalog/mappings/tocfl/native-materials-v1.json)
bind band, skill, role, script applicability and source-material facts to each
URL/checksum. Traditional and simplified questions remain separate variants;
common answer workbooks, audio and transcripts apply to both. Vocabulary and
instruction originals remain reference attachments, including the distinct
CCCC children's programme. A rolling collection update and a capture/storage
partition do not create administered examination years.

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

Public titles show programme, meaningful level, occupation and applicable
variants or stages. When programme and level have the same display label,
show that wording once while retaining both structured identity dimensions.
Distinct native grades and stages remain visible.

Shared professional-exam event headings contain independently administered
programmes. Resolve each qualification from its own programme clause and native
category. Programme boilerplate and qualification-specific level prefixes do
not create new occupations. A generic combined high/ordinary heading requires
the qualification's own regulated level; its punctuation does not select a
level. When the same qualification appears under regular and special programmes
in one event, use its explicit category programme or retain an unresolved,
event-isolated identity. An abbreviated event that omits a qualification or
level requires an exact checksum-anchored native-header fact.

Historical special qualifications remain distinct from regular high and
ordinary examinations. For example, the
[historical insurance rules](https://law.exam.gov.tw/LawContent.aspx?id=FL016876)
and [fire-equipment rules](https://law.exam.gov.tw/LawContent.aspx?id=FL016895)
describe changes to the actual examination programme. Legal equivalence to a
high or ordinary examination is not itself an administered level. Preserve
separate qualifications such as 師/士, 師/生 and each insurance occupation,
including when their common papers are identical. Explicit programme evidence,
corroborated by the historical rules, determines the reform boundary; a shared
event's first marker or a year-only rule does not.

The [geotechnical staged-exam rules](https://law.exam.gov.tw/LawContent.aspx?id=FL077429)
define separate first and second phases. Native category spellings for these
phases share the corresponding stage identity and display 第一階段 or 第二階段.
The regular unstaged route remains separate. A cohosted phase or medical
第一試 marker does not stage unrelated qualifications in the same event.
These boundaries and retained native question headings were checked on 2026-10-08.

Navigation qualifications belong to professional examination programmes.
The [historical navigation rules](https://law.exam.gov.tw/LawContentHistory.aspx?hid=1356&id=FL016913)
distinguish ship grades, occupations, naval-transfer subject tables and
endorsements for another type of ship engine. Native 一等、二等、三等 and
正、副 grades remain separate from their legal equivalence to other examinations.
Programme prefixes and punctuation do not create new occupations; for example,
`加註一等管輪` and `航海人員一等管輪（加註）` have the same identity.
Endorsement archives preserve each recorded engine subject and its file role.
They remain separate from regular, naval-transfer and explicitly labelled
subject-retake papers, even when an individual question is shared.

The [ROC 98 revision](https://law.exam.gov.tw/LawContentHistory.aspx?hid=1366&id=FL016913)
established high and ordinary navigation examinations. Their programme identity
is resolved from the source heading, while the native ship grade remains 一等
or 二等; a year-only cutoff would misclassify the special examinations held
earlier in the same year. The
[ROC 100 revision](https://law.exam.gov.tw/LawContentHistory.aspx?hid=1367&id=FL016913)
ended regular MOEX navigation examinations after July 2012 and retained a
limited period for unfinished cases. Events explicitly marked 舊案補考 keep
that candidate-route variant. Fishing-crew qualifications are a separate
programme and do not inherit navigation identity from a shared event title.
These primary rules and retained boundary headers were checked on 2026-10-08.

Fishing-crew qualifications use a separate professional programme, even when
a shared event title lists navigation or ship-radio exams and omits fishing.
The [historical screening rules](https://law.exam.gov.tw/LawContentHistory.aspx?hid=1453&id=FL016872)
distinguish native 一級 through 四級 fishing navigators and engineers, plus
technical occupations without a numerical grade. Explicit 檢覈 and 檢覈筆試
headings identify the written screening programme; an unmarked ordinary
examination must not inherit that purpose from a shared paper or subject count.
Explicit subject-retake routes remain distinct, including technical officers.

The [ROC90 fishing reform](https://law.exam.gov.tw/LawContentHistory.aspx?hid=1376&id=FL016890)
defines fifteen captain, deck, engine and radio qualifications within the
professional-special fishing programme. Vessel length, operating water and
engine power give these qualifications their own scope; legal equivalence to
high, ordinary or elementary exams does not replace native grades. Prefix
separators and compatibility characters do not split a qualification across
years. A shared native question header can name fishing, ship-radio, regular
and retake candidates together; preserve their programme and candidate-route
identities while retaining each original source reference and file role.
These rules and every retained fishing category header were checked on 2026-10-08.

Ship-inspector examinations use their own category or scoped programme heading
within a shared event. The [historical ship-inspector rules](https://law.exam.gov.tw/LawContentHistory.aspx?hid=1323&id=FL016883)
define a special examination without a numerical qualification grade; its legal
equivalence to a professional high examination does not create a native grade.
The [ROC98 reform](https://law.exam.gov.tw/LawContentHistory.aspx?hid=1324&id=FL016883)
changes the actual examination to professional high. Explicit high category
headings take precedence over unrelated special exams in the same event.
Missing or abbreviated historical event wording is resolved only by exact,
checksum-anchored native-header facts. These primary rules and every retained
ship-inspector category header were checked on 2026-10-08.

Historical pilot rules distinguish native type and numerical grade. When one
local-waterway header conflicts with the official category and other professional
subject headers, the checksum-anchored category fact preserves the supported
programme and grade while isolating that subject in review. A single conflicting
paper cannot regrade all other subjects or select a cohosted navigation programme.
The [historical pilot rules](https://law.exam.gov.tw/LawContentHistory.aspx?hid=1328&id=FL016856)
and the exact conflicting source context were checked on 2026-10-08.

Ship-radio qualifications use their own professional programme. The
[historical ship-radio rules](https://law.exam.gov.tw/LawContentHistory.aspx?hid=1373&id=FL016859)
distinguish telegraphists, telephonists, electronic operators and operators,
their native grades, retakes and military-transfer subject tables. The
[ROC 92 revision](https://law.exam.gov.tw/LawContentHistory.aspx?hid=1374&id=FL016859)
retains second-class electronic operators and general operators. Legal
high/ordinary examination equivalence does not replace those native grades.
Programme headings in later categories and punctuation or prefix placement
around 補考 and 海軍 do not create different qualifications. Retake and navy
routes remain explicit variants because their paper sets differ.

An explicit ship-radio category takes priority over cohosted navigation,
ship-inspector or other professional examinations. Fishing-crew operators
remain outside that rule: the amended ship-radio scope excludes fishing
vessels. Some official papers explicitly serve multiple qualifications, such
as the [ROC 88 navigation-geography paper](https://wwwq.moex.gov.tw/exam/wHandExamQandA_File.ashx?t=Q&code=088270&c=793&s=c151&q=1).
Shared bytes retain each source reference and do not merge the qualifications.
These references and retained native headers were checked on 2026-10-08.

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
