# Source judgment

Provider-specific source boundaries and durable exceptions are maintained here. The [generated index](README.md) and [reviewed inventory](../../catalog/source-inventory.json) own status, URLs, availability, restrictions, counts, and evidence. Use the [runbook](../operations/runbook.md) for shared operations and [add a provider](../contributing/add-a-provider.md) for source-proof requirements.

Sections use stable provider IDs; evidence links may point to their Markdown anchors. Add a section only for source-specific judgment, and update changing measurements at their executable owner.

## `ceec_ast`

Limit this provider to the official AST archive. The predecessor Subject Competency Test is a distinct official series and must not be inferred from older selector rows.

CEEC's archive link labels own file roles. A blank 答題卷 is an answer sheet,
封面 is a question cover, and 評分原則、評分標準 or 評分說明 is scoring
guidance. These are separate from answer keys and explicitly corrected answers.
An unfamiliar link label requires source review instead of an answer-role guess.
Retain alternate official document formats and every source reference; identical
same-subject/year/role payloads can share one ZIP member under the bundler policy.

## `ceec_gsat`

Use the official CEEC GSAT archive and its directly linked paper, answer, and scoring artifacts. Legacy document or archive formats are eligible only when payload signatures match the advertised role.

Use the [CEEC file-role boundary](#ceec_ast) for covers, answer sheets, scoring
guidance and answer corrections. 國文 before the writing split, 國文（選擇題）,
國綜 and 國寫 retain their distinct paper identities across the official reforms;
the shared Chinese-subject score does not make their papers interchangeable.

## `cpc_recruit`

Only administered examination packages in the official static past-exam archive are papers. Recruitment brochures, application material, and similarly named operational documents remain outside the paper boundary.

The static archive is the doctoral recruitment programme's initial written
test. Its official doctoral brochures require a doctorate and separately describe
the later document review, research presentation and interview. A doctorate is
an eligibility requirement, not a certification grade. Keep this programme
distinct from ordinary company recruitment and preserve each complete original
package, including its different occupational papers and any A/B categories.
Historically misclassified hiring brochures belong in the provider's source
revision journal and recovery blobs, outside the current examination catalog.
Retention of a complete upstream package does not establish complete programme
coverage: the ROC 101 brochure lists economics and management papers absent
from its published package. Keep that source omission explicit. Conversely,
the ROC 108 chemical-engineering and materials-engineering papers explicitly
cover paired recruitment categories; preserve their shared originals.

Publication remains withheld because the ROC 98 geophysics A paper explicitly
identifies its fifth question's copyrighted figure; the next page includes an
image attributed to Genik (1993), AAPG Bulletin. CPC's
[reuse declaration](https://www.cpc.com.tw/cp.aspx?n=2559) does not resolve that
specific third-party restriction. Preserve the original, and resolve permission
or provide a reviewed publication treatment before lifting this hold. Public
mirror backup creation is also disabled; retain the verified provider mirror
and revision blobs locally and use the scheduled workflow's cache-only mode.

## `gept_cert`

Use official GEPT paper material only when level and event identity are supported by the source. Rolling samples must not acquire a synthetic current-year identity merely because they were fetched recently.

## `hakka_cert`

Use official Hakka proficiency assessment material while keeping examination papers distinct from audiovisual practice resources. Redistribution limits on audiovisual works are publication constraints, not permission to relabel or omit retained evidence.

The official downloadable question banks and sample-paper packages can contain
PDFs alone or PDFs with accompanying audio. A ZIP or RAR suffix does not make
a package an audio file. Preserve the original package, use its source label
to distinguish question packages from explicitly labeled 音檔, and inspect
the contents before changing the packaging. The retained archives and current
official listing were checked on 2026-10-07.

Recognize the source's pure 樣卷 labels as sample packages as well as 試題範例.
Annual 題庫 resources carry material edition dates rather than administered
sitting dates. The inspected 題庫及樣卷 packages contain a bank and a sample PDF;
retain them as practice collections, distinct from pure samples and banks.
Undated advanced samples carry no public year; the legacy discovery/storage
year stays only for compatibility. `app/hakka_identity.py` owns this meaning
for acquisition and reclassification.

Resolve a download's native grade from its own official title and content,
rather than the current navigation heading or legacy canonical key. Older
初級 banks remain separate from 基礎級暨初級 resources. The official
[2022 framework](https://www.hakka.gov.tw/File/Attach/45166/File_94088.pdf)
and [2023 framework](https://www.hakka.gov.tw/File/Attach/45962/File_97071.pdf)
document the introduction of a shared basic/elementary paper. The retained
bank covers corroborate this boundary. An elementary-only annual label after
that reform requires native review rather than an assumed shared grade.
Reviewed historical label corrections are anchored by exact title, source key
and payload checksum in `catalog/mappings/hakka/historical-grades-v1.json`.
The gate reconciles both the immutable older reference and its reviewed
same-edition, same-dialect counterpart. A later retirement into the immutable
journal keeps that evidence valid. Identical bytes can support an old label
correction; reformatted workbooks remain separate retained payload revisions.
Unseen bytes or source keys require another review. Preserve
the historical source label and journal rather than rewriting the evidence.
Dialect changes the paper/audio content and remains an identity variant.
Unsupported or conflicting dialect evidence stays in review.

The checksum-backed native conflicts in
`catalog/mappings/hakka/native-conflicts-v1.json` preserve official links whose
labels disagree with another dialect's identical source bytes. Isolate those
records; shared payloads do not authorize deleting their source references or
silently assigning another dialect. Match the individual source key as well as
the title; a correct distinct package can share the same official label.
Changed bytes at a known conflicting source key require another review.
The contract gate reconciles both retained
source references supporting each fact.

The intermediate sample ZIPs use CP950 filename metadata without the UTF-8 flag.
Preserve the original packages and explicitly decode their member names with
that charset when inspecting them; default CP437 decoding produces garbled
characters and apparent path separators. Both official download surfaces were
rechecked on 2026-10-09, including the previously omitted samples and annual
question-bank audio. The adapter joins the primary category pages with the
academy download center, accepting its API and legacy download paths while
excluding vocabulary-only resources. Repeated desktop/mobile links share one
source record. Source keys use the download group and decoded filename rather
than displayed byte counts. The download's title supplies its native grade;
unreviewed grades or a failed secondary listing stop discovery without caching
partial success. Each material fact retains its actual listing provenance.

Preserve complete audio packages and their PDF/spreadsheet companions. Matching
companion bytes or bank text do not make the whole packages interchangeable or
authorize discarding distinct container versions. Current manifest entries
reconcile with retained originals; initial-capture gaps and delisted source
links remain dated historical evidence. The retained all-grade standards ZIP is
an undated reference containing passing thresholds and question-format PDFs,
with no audio or administered paper. Its legacy listening-audio role and current-
year intermediate discovery group remain immutable acquisition evidence. The
source-revision review owner records its separate non-paper disposition using
the exact source context and native payload checksums, without assigning a
single examination grade or reintroducing it into current publication. Full
historical native content, source conflicts and audiovisual redistribution still
need review before publication.

Some intact original audio ZIPs also exceed the normal multipart target.
Do not relax the public target or rewrite retained source bytes to make that
check pass. A private conservation build can use an explicit target below the
hard asset limit, while recording the different scope. Public packaging still
requires its own verified treatment before the publication hold is cleared.

An earlier refresh replaced or dropped source references before immutable
revision retention existed. Recover those original records from Git and their
matching bytes from retained mirrors or conserved archives, then backfill them
through the source-revision owner. Keep the original roles and date claims as
historical evidence; corrections belong to current material facts and do not
make the earlier classification authoritative. Publication and public mirror
backup restrictions apply equally to recovered originals.

## `hce_cmu`

This provider covers official CMU post-baccalaureate medicine entrance papers reached from the university admission archive. General admission notices and forms are not paper records.

## `hce_nsysu`

This provider covers official NSYSU post-baccalaureate medicine entrance papers reached from the university archive. Schedule, registration, and result notices are supporting context rather than downloadable papers.

## `hce_nthu`

This provider covers official NTHU post-baccalaureate medicine entrance papers and answers linked from the admission archive. Do not broaden it to unrelated NTHU admissions material.

## `hce_tcu`

This provider covers official Tzu Chi University post-baccalaureate medicine entrance papers exposed by the maintained admission surface. A current-cycle listing is not proof of an enumerable historical archive.

## `ipas_cert`

Treat each official iPAS examination family as its own source boundary. Regulations, announcements, brochures, syllabi, and other paper-like PDFs are not questions or answers.

## `jlpt_cert`

Use official JLPT sample-question sets as samples, preserving their source role. They must not be presented as a complete year-by-year archive or assigned unsupported event years.

The workbook's 聴解スクリプト files are listening transcripts. They are distinct
from question papers and listening audio; correcting a retained role preserves
the earlier source reference and verified payload in the provider revision journal.

The [official workbook page](https://www.jlpt.jp/samples/sampleindex.html)
identifies the retained years as publication editions assembled from earlier
test questions. It separately restricts reproduction of attributed N1/N2 text
and all N1–N5 listening audio. The [official policy](https://www.jlpt.jp/policy.html)
permits specified uses with attribution but requires separate permission for
the third-party material. These pages were rechecked on 2026-10-07. The source
inventory's recorded redistribution hold applies to the site's ZIPs; the
publication quarantine enforces that hold while preserving source history.
Restoring publication requires evidence covering the included material and a
projection that distinguishes workbook editions from administered events.

The adapter records workbook material with `edition_year` facts from the
listing's edition sections. An audio URL containing another year does not
override its workbook edition. Discovery and fetching share one listing
snapshot per client; an absent edition or mismatched event key is rejected.

## `moea_recruit`

A MOEA recruitment provider must be supported by MOEA-owned recruitment identity and source material. Taipower records cannot stand in for MOEA records even when the subject matter or filenames look compatible.

The historical 新進職員 archive is hosted by Taipower, separately from its hiring
and 養成班 archive. Hosting does not establish the programme: the [ROC 91 common
paper](https://www.taipower.com.tw/media/tjnpg3wc/2017120111152173465.pdf?mediaDL=true)
names MOEA administering Taipower recruitment alone, while the [ROC 93
paper](https://www.taipower.com.tw/media/huyfqx2s/2017120114115741430.pdf?mediaDL=true)
and [ROC 95 paper](https://www.taipower.com.tw/media/tsze2tl5/2017120115215814733.pdf?mediaDL=true)
name Taipower and CPC. These native headings were inspected on 2026-10-10.
Do not project the modern joint programme or employer set onto every historical
event without complete native review and historical migration.

The adapter derives acquisition subject keys from the normalized request URL,
not a file's position in an annual listing. Reordering, inserting a file or
changing its label therefore leaves existing mirror locators stable. These keys
identify source references; they do not establish an occupation, subject, grade
or programme. A change from positional keys requires a checksum-verified
old-to-new mapping of the complete affected capture, with the previous state
and originals retained for rollback. It must not merge the misowned production
records into a newly acquired staff archive or lift publication quarantine.

## `moex`

Accept only Ministry of Examination event listings, result pages, and attachments directly linked by those pages. Preserve source-side placeholder and expired-result evidence instead of substituting unofficial copies.

## `post_recruit`

Use official Chunghwa Post recruitment event pages and their directly linked examination artifacts. Recruitment announcements without administered papers remain event evidence, not paper records.

## `rcpet_cap`

Use the official RCPET CAP archive and its event-linked assessment materials. Provider normalization must preserve the official assessment identity rather than deriving it only from filenames.

## `sfi_cert`

Use official SFI category and event pages, with embedded paper headings as identity evidence when adapter labels conflict. A reachable PDF is not publishable until its official category and event agree.

## `special_admission`

Use the official special-admission examination archive and its event-linked papers. General admissions content outside that archive is not part of this provider.

## `tabf_cert`

Use official TABF category and event rows and their linked artifacts. A PHID or other page key is not a cross-year identity unless the official category and event context also agrees.

## `taigi_cert`

Use official Taiwanese-language certification paper forms while preserving form variants and the absence of stable event years. Fetch time must not become examination time.

## `taipower_recruit`

Use official Taipower recruitment examination events and administered paper attachments. Corporate recruitment information that is not an administered paper remains outside the boundary.

The hiring and historical 養成班 archive is distinct from the MOEA joint
新進職員 programme, even though Taipower hosts both. Keep the two ROC 107
sessions as separate events. An official answer PDF may reproduce the questions
with annotated answers; its source role remains an answer.

Listing labels do not identify every corrected answer. The reviewed
[native correction facts](../../catalog/mappings/taipower/answer-corrections-v1.json)
bind the original event, category, subject, label, URL and checksum to a native
correction notice or visibly marked post-publication correction. Normalization
projects these originals as `corrected_answer` while preserving acquisition
roles, mirror locators and bytes. A correction word inside a question, a generic
footnote without an actual marked change, or different bytes is insufficient.
New bytes at a reviewed correction source stop normalization for native review;
previously corrected records with changed context also stop re-normalization.
This evidence does not resolve occupational or selection-stage identity.

Archive filenames and search labels omit the known Taipower file-label prefix;
the original labels remain in provider state. In particular, the historical
`科目A` filename boilerplate does not establish a professional A/B period.
The [official rule books](https://www.taipower.com.tw/2289/2544/2554/2555/)
separate recruitment categories from their shared written subjects. Preserve
year-specific subject wording and retain one original paper rather than copying
it into each applicable category. The two ROC 107 sessions use separate month
folders, derived from their structured source event IDs. Neither this layout
nor a shared subject establishes occupational equivalence or a selection stage.

Refresh payloads during sync: a locally valid PDF and checksum do not establish
that it matches the current official URL. Earlier common-paper copies contained
material from the other programme. The provider writer preserves replaced
records and immutable recovery bytes before updating current state. Complete
acquisition of the currently linked archive does not establish exhaustive
historical occupational or selection-stage classification.

Hosted discovery can fail even when a normal local source request succeeds.
The source manifest retains the dated runner evidence. Preserve the last
verified provider state on that failure; a retained cache is not a successful
source refresh. Perform acquisition from an accessible, authorized environment,
then use the normal publication and durable-mirror recovery procedures. Keep
the revision journal and its original payloads with the refreshed mirror.

## `taisugar_recruit`

Use official Taiwan Sugar recruitment examination listings and their administered paper artifacts. Current-cycle recruitment pages do not establish an unobserved historical range.

## `tcte_tve`

Use the official TCTE technological and vocational examination archive, retaining its official program and subject distinctions. Do not merge programs solely because display labels overlap.

## `teacher_qual`

Use official teacher-qualification assessment events and paper artifacts. Teacher recruitment exams are owned by separate providers and must not be folded into this qualification series.

## `teacher_recruit_central_alliance`

Use the official Central Alliance paper surface for alliance-administered teacher recruitment. Municipal notices may prove provenance but do not become separate paper providers when they delegate papers to the alliance.

The 115-school-year host is expired and returned HTTP 503 for every subject and
final-answer endpoint on 2026-09-02. Its workflow is manual-only until a new
official cycle is identified and the adapter is reviewed for that cycle.

```bash
uv run python -m app discover --provider teacher_recruit_central_alliance
uv run python -m app sync-incremental --provider teacher_recruit_central_alliance --site-id default
uv run python -m app publish-site --site-id default --repository <owner>/<repo>
```

Run the strict catalog and history audits from [catalog audit](../operations/catalog-audit.md) before publication.

## `teacher_recruit_kaohsiung`

Use official Kaohsiung teacher-recruitment event pages and linked papers. Keep school level and event identity from the source rather than inferring them from local filenames.

## `teacher_recruit_newtaipei`

Use official New Taipei teacher-recruitment event pages and linked papers. Notices, schedules, and results without administered papers remain non-paper source context.

## `teacher_recruit_tainan`

Use official Tainan teacher-recruitment event pages and linked papers. Preserve the source event and school-level boundary during normalization.

## `teacher_recruit_taipei_elementary`

Use the official Taipei elementary teacher-recruitment source for elementary events and papers. Other Taipei school levels remain separate source identities.

## `teacher_recruit_taipei_junior`

Use the official Taipei junior-high teacher-recruitment source for its events and papers. Do not merge elementary or other municipal recruitment series by city name alone.

## `teacher_recruit_taoyuan_elementary`

Use the official Taoyuan elementary teacher-recruitment source and its linked papers. Preserve elementary scope and official event identity.

## `tii_cert`

Use official Taiwan Insurance Institute examination pages and administered paper material. Annual reports, product brochures, and other institute publications are not exam questions.

Read dated anchors only from historical-paper rows. Preserve separate sections,
morning/afternoon papers and corrected answers; a revision date does not change
the examination date. For the official history ZIP, retain the original package
and use its exact member paths as provenance for extracted papers. Discovery
must use those listings, rather than the first download on an introduction page.

The source omits a TLS intermediate. The adapter supplies the issuer's
[2023 G3 certificate](https://www.twca.com.tw/upload/saveArea/filePage/20240327/9a2b62d266824935ac33759f90cd1e23/9a2b62d266824935ac33759f90cd1e23.pdf)
while retaining system roots, complete-chain validation and hostname checking.
Do not bypass certificate verification when the source rotates its certificate.

The reviewed redistribution hold remains separate from acquisition correctness.
It applies to public mirror releases as well as site bundles. Keep verified local
mirrors and source revisions for recovery until a republication grant is established.

## `tocfl_cert`

Use official TOCFL paper and mock-test banks while preserving whether material has a stable event identity. Rolling mock resources must not receive synthetic current-year identities.

## `tqc_cert`

Use official TQC examination artifacts with source identity strong enough to keep distinct payloads separate. Generic labels must not share storage keys when their source files differ.

The official sample listing in the source inventory describes its PDFs as references for question formats. Treat this collection as sample material, including previews whose native headings say 模擬試卷. Dates alongside those downloads are publication dates, not administered examination dates. Preserve legacy discovery partitions for traceability; a missing listing date requires review rather than a synthetic public year. Software versions and book editions are separate content distinctions.

Reviewed subject, software/edition and native grade facts live in the [TQC identity catalog](../../catalog/mappings/tqc/sample-identity-v1.json). Exact titles and PDF checksums guard these decisions. Native 專業級 sample headers establish professional-grade coverage; the official MySQL rules establish the grade omitted from that sample's header. Typing samples serve performance-based grades and must not inherit a single grade from an edition or paper-code numeral. Changed payloads and new titles require fresh review. Subject certificates remain distinct from composite personnel certificates and from TQC+.

The complete retained sample set has been reclassified through the shared owner. Publication remains withheld because the existing review did not establish a republication grant. First-page inspection and the reviewed initial grade/rule sections do not constitute full-body or complete historical-rule review. Backup policy remains a separate operational decision.

## `twc_recruit`

Use official Taiwan Water recruitment examination archives and inspect nested artifacts rather than validating only an outer archive signature. Retain corrupt official payload evidence without publishing unusable papers.

## `wdasec_skill`

Use official workforce skills-test question-bank material within the declared paper scope. Preserve occupation, level, subject, and source-event distinctions across the large archive.
