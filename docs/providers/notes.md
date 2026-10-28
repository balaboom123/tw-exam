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

Use the official GEPT proficiency levels independently of scores within a
writing rubric. The native 初級、中級、中高級、高級、優級 pages establish the
level; a rubric's 級分 does not establish another qualification level. Advanced
practice forms use internal codes that differ from their visible 練習題 labels.
Keep those practice sets as separate variants, without treating them as levels
or examination stages.

Rolling practice, scoring examples and superior sample papers have no supported
examination or publication date. Existing acquisition partitions and event keys
remain compatibility locators; neither a website directory nor a fetch date
supplies their material date. Scoring examples are references, oral prompts are
speaking audio, and transcripts remain distinct from recordings. Retain native
practice HTML and its source assets as attachments: they contain questions and
answers that an audio-only crawl misses. This is evidence capture, not a promise
that a dynamic source application can run offline.

The computer practice download is an installer archive. Preserve the original
container and its installation instructions separately from papers; inspect
archive paths and CRCs without running executables. A currently listed PDF that
returns an HTTP error may retain its earlier checksum-verified bytes in the same
reviewed context. Keep the failed request visible and do not claim a fresh
download or successful sync.

The current level pages recorded in the source inventory and their linked
materials were checked on 2026-10-20. The removed historical
pilot-paper listing remains a coverage gap. The current
[LTTC terms](https://www.lttc.ntu.edu.tw/tw/disclaimer) do not establish permission
for this archive's republication. Keep GEPT publication quarantined and disable
public mirror snapshots while retaining local recovery evidence. This hold is
independent of the corrected acquisition and classification.

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

This provider covers official CMU post-baccalaureate Chinese medicine degree-admission papers reached from the university admission archive. General admission notices and forms are not paper records.

The native archive uses both `生物` and `生物學` for biology questions.
Resolve these source labels to the same biology subject before filtering links;
the shorter historical label must not silently drop a question. Keep each
printed academic year and its original question PDF, together with the
separate all-subject reference-answer PDF where the source provides one.

An academic year can span separate subject pages and a later clarification
notice. Discovery retains all of those pages together, including older titles
without `學年度`, and preserves the primary paper page as the discovery link.
Conflicting URLs for the same subject and role within a page require review;
one link must not overwrite another. Exact repeated references are deduplicated
within the annual event.

Annual selection-stage facts belong to the CMU native-paper mapping. A numbered
interview stage selected by written results establishes the preceding written
stage for that academic year; a later guide must not supply historical rules.
The institutional report's embedded historical calendar supports a sequence,
but does not establish formal numbered selection-stage conditions. Such years
remain explicitly under review. A bachelor degree is an admission prerequisite,
not a native examination grade. These papers are degree admission, not medical
licensing or graduate research selection.

Each question, reference-answer book and answer-doubt clarification requires its
own exact source context and checksum, including paired question/answer links.
Clarifications remain alongside original keys: they can uphold or change an
answer without replacing the entire key. Combined answer books preserve their
subject sections. Scanned pages without extractable text remain original pages.
The historical report references an answer-doubt correction notice absent from
the retained archive; current listing completeness does not close that gap.
Supporting admission notices and reports establish identity only and are not
published as exam-file downloads.
Unresolved annual candidates stay isolated by event and below the ordinary
site minimum-year requirement; the redundant CMU single-year exception is
removed. Their source records and the previous all-years download remain
recoverable while the current listing contains the supported written-stage
group. Do not lower that requirement to publish unresolved stage candidates.

## `hce_nsysu`

This provider covers official NSYSU post-baccalaureate medicine entrance papers reached from the university archive. Schedule, registration, and result notices are supporting context rather than downloadable papers.

The [reviewed annual evidence](../../catalog/mappings/nsysu/native-selections-v1.json)
binds each original combined PDF to its subjects, page ranges, written-test
stage and admission rules. Bachelor eligibility and the medical degree's
duration are not examination levels. General medicine and smart medical
admission routes share English and biology/biochemistry; their optional
physics/chemistry and computing papers differ. The later ungrouped admission
must remain separate. Native guides and the issued paper headings establish
these boundaries; institution names and matching filenames do not.

The site permits a single reviewed year for this source, as for the other
post-baccalaureate medical archives. This preserves the already-public ungrouped
annual papers after separating the earlier routes. A minimum-history threshold
must not force a merger of different admissions identities or silently hide
previously covered papers; the general site threshold remains unchanged.

Keep each complete source PDF as a `source_document` attachment. Reviewed page
selections are derived PDFs, with the original URL, parent checksum and native
page range in their provenance. They are not independently discovered official
downloads. Share identical subject bytes across applicable groups while retaining
each group's logical reference. Keep the complete shared answer tables beside
the first year's questions; subsequent native documents contain inline answers.

Routine acquisition verifies the complete parent PDF before using a reviewed
selection. An unreviewed year, URL or parent revision remains a whole document
with unresolved classification. Cached subject PDFs cannot establish that a
changed parent still has the same page boundaries. Generated byte plans keep
PDF tooling out of routine acquisition; they contain no identity facts and are
checked against the separate annual evidence. See [recovery](../operations/recovery.md)
for review and regeneration. Earlier whole-document source records remain
immutable; the revision audit describes their native combined-document meaning
without assigning a mixed original to one admission route.

## `hce_nthu`

This provider covers official NTHU post-baccalaureate medicine entrance papers and answers linked from the admission archive. Do not broaden it to unrelated NTHU admissions material.

Keep each year's separate answer-clarification pages with its primary question
and reference-answer page. Preserve subject-specific clarification results under
`answer_clarification`, including decisions that uphold the original key, and
retain the original keys unchanged. The native article route requires a literal
comma before its category suffix; percent-encoding that comma can redirect to
the admission homepage. A response without recognized native papers must fail
rather than replace a retained annual event with an empty result. Current source
membership and retained checksum verification do not establish that older source
payloads were refetched.

The [annual evidence owner](../../app/providers/hce_nthu/identity.py) binds native
paper URLs and checksums to reviewed selection rules, dates and admissions-group
applicability. Use those annual facts rather than carrying an earlier group or
subject set into a later ungrouped route. Shared questions, whole reference keys
and clarification notices retain their native acquisition category and one
mirror locator while providing the appropriate logical group references.
Preserve native subject names across curriculum changes. Unreviewed years,
changed payloads or unmatched source contexts require review instead of inheriting
a stage or group identity. The former aggregate's website landing path links to
the current NTHU results; it does not choose an arbitrary replacement group.

## `hce_tcu`

This provider covers official Tzu Chi University post-baccalaureate medicine entrance papers exposed by the maintained admission surface. A current-cycle listing is not proof of an enumerable historical archive.

The reviewed domestic annual guide selects applicants by their written results.
A prior bachelor's degree is an entry requirement, not an examination grade;
the programme's five-year curriculum is not a paper level. Keep this route
separate from foreign-student admission and from other universities' staged
selection. Annual guide and native checksum evidence live in the
[TCU catalog](../../catalog/mappings/tcu/annual-papers-v1.json). Unreviewed years,
changed files or mismatched source contexts require review. Refresh native files
on ordinary synchronization so a key replaced at the same URL is observed;
retain its previous bytes through the shared revision owner. Each question and
answer classifies using its own checksum. Publication must conserve the reviewed
subject papers, original keys and clarification roles for each retained year.

Keep the separately published answer-clarification result notice beside the
original questions and keys. The `answer_clarification` role covers both upheld
answers and actual changes; it does not imply that every reviewed answer was
changed. An application form is not a result notice. Preserve the complete
official document and its all-subject applicability rather than rewriting or
discarding the original answer sheets.

## `ipas_cert`

The [official purpose](https://ipd.nat.gov.tw/ipas/plan/goal) is industrial
competency certification. Keep each native programme and its own grade system
distinct. The [AI application planner rules](https://ipd.nat.gov.tw/ipas/certification/AIAP/exam-info)
award separate intermediate certificates for data analysis and machine learning;
their shared first subject belongs to both routes. Optional subjects in another
programme do not alone establish separate certificates.

The adapter reviews every locally hosted programme exposed by the official home
page. Its native-document owner binds programme, source URL and full payload
checksum before assigning a paper role, event, grade, subject or qualification
route. Unfamiliar bytes or changed programme membership require review. External
certification brands linked from the home page are separate acquisition sources.

Administered papers, samples and mocks retain their native dates independently
of storage partitions. Older colour-planning and electric-vehicle samples keep
their own curriculum edition and subject scope. Worked drawing solutions count
as answers even when text extraction misses the graphical content. A headerless
plastics paper uses its exact official listing context plus reviewed native
structure; this exception cannot approve another payload.

Brochures, rules, application forms, registration spreadsheets and study guides
are reference attachments in their original PDF, ODT or ODS format. An annual
edition is not an examination date; unknown reference dates remain unresolved.
The fixed resource collection partition is not an event-year claim. Earlier
generic-import records and incorrectly assigned papers remain immutable in the
source journal, with separate native-role interpretations and verified bytes.
An older brochure absent from current listings remains retired recovery evidence.

The current source surface was rechecked on 2026-10-21. Its rolling listings do
not establish a complete historical archive. No blanket republication permission
has been established, so the corrected source state remains excluded from site
ZIP publication. Source capture and the existing backup policy are separate
from this publication disposition.

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

Each download must agree with its native table row's proficiency level and
the workbook's edition heading. Supported question parts, blank answer sheets,
answer keys, transcripts and numbered audio tasks have explicit roles and
formats. Unknown filenames or missing/duplicated edition headings stop discovery
for review. A shared URL can apply to both explicitly listed workbook editions;
deduplicate repeated links within an edition while preserving both edition
references. Unsupported or conflicting level markers remain unresolved rather
than selecting the first numeral or treating `N10` as `N1`.

Checksum-bound native observations in the provider manifest corroborate the
retained corpus. Original sample covers keep their printed timing and page
instructions; these examples do not establish today's administration format.
The native review used retained payloads and a current official HTML listing;
it does not claim fresh remote payload bytes, full answer correctness or a
semantic listen-through of every recording. The dated robots observation names
restricted agents, including GPTBot and ChatGPT-User. It does not change the
repository's existing acquisition or durable-backup policy, and the
redistribution hold remains in force.

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

Acquisition preserves each official listing-group title in `category_raw`,
including common papers, occupational names and electrical-track qualifiers.
It does not substitute the shared canonical display name for that evidence.
Annual discovery and acquired event names preserve the reviewed ROC 91,
93 and 95 employer boundaries. The source category is listing evidence;
it still needs comparison with native paper headings and annual rules before
assigning a cross-year occupation identity.

Until those native identity facts are implemented, classification preserves the
complete source category as an unresolved track and isolates it by event. Level
and selection stage remain `unknown` with a review disposition. The shared
legacy alias does not establish that all occupations are one track, and filename
A/B periods, eligibility degrees or an unverified stage word cannot resolve
these dimensions. Publication quarantine remains in force during migration.

Correct the acquired source independently from approving its examination meaning.
The complete native listing and verified originals can replace the known hiring
archive contamination while preserving those unresolved identities. Retire the
earlier references through the scoped provider writer and conserve their exact
metadata and independent recovery bytes. Source URL ownership and the purpose of
its retained payload are separate evidence: some old hiring URLs retained bytes
that match staff papers. Do not rewrite those original records to make them appear
to have been acquired from the correct staff source. Verify a full backup restore
and conserving rollback before cutover; complete annual/native review remains a
prerequisite for approved identity and publication.

Reject an event when an unreviewed file label in one listing group points to
different normalized source URLs. Equal labels in different occupational
groups remain separate references. On 2026-10-11 the [ROC 102 electrical B
question link](https://www.taipower.com.tw/media/yxifagqi/2018012910571494833.pdf?mediaDL=true)
still returned a one-page answer sheet headed 「專業科目 A 計算機概論、電子學」.
The same B label also links the genuine B question paper. On 2026-10-12, all
eight pages of the genuine A questions, genuine B questions and mislabeled
A answer sheet were visually inspected and their standard-client downloads
matched the retained checksums. The exact reviewed exception is owned by
[`source-label-conflicts-v1.json`](../../catalog/mappings/moea/source-label-conflicts-v1.json)
and its schema. Event acquisition verifies the complete conflicting URL set
and all three native payloads before mirror writes. New conflicts, changed
controls or changed bytes still fail. Normalization and catalog migration use
the same exact source context and checksum to identify the A answer sheet;
raw labels, listing roles, source keys and historical storage locators remain
traceability evidence. This file correction does not approve examination
identity or lift the source's publication hold.

Other complete native visibility reviews are owned by
[`native-file-roles-v1.json`](../../catalog/mappings/moea/native-file-roles-v1.json).
These exact references distinguish unanswered papers mislabeled as answers,
papers containing visibly printed answers, and the aviation answer key with an
explicit native correction date. Hidden extracted letters are not visible
answers, and a multiple-answer item alone is not a dated correction. The
adapter binds a role fact to its complete event/group/file context before
cache reuse and checks downloaded bytes before mirror writes. Normalization
and migration use the same fact; original source labels and paths remain.
These facts approve the recorded file content, not examination identity.
Publication metadata and ZIP entry names use the reviewed native subject only
when the complete source context, checksum and migrated native role match.
The mislabeled electrical answer therefore names its actual A subject. Changed
bytes or an unmigrated role stop the projection for review. ZIP payload sharing
uses that same subject together with the existing year, role, suffix and
checksum boundaries; every original source reference remains in the manifest.

The available ROC 100–115 brochures' main purpose, occupation/subject matrices
and written/retest sections have been inspected. Detailed major appendices,
complete native file roles and older unavailable annual rules remain separate
review scopes. Electrical routes and agricultural subjects changed within this
history; neither a shared job name nor a numbered route proves interchangeable
paper coverage. ROC 115's scheduled examination remains future material as of
2026-10-12, and must not create an administered paper or publication receipt.

Identical bytes may represent one paper applicable to several listed occupations.
Keep their source references and native applicability when sharing storage.
Extracted answer letters alone do not establish a file role: some PDFs contain
hidden answer text that is absent from their visible question pages. The
[ROC 109 chemistry question link](https://www.taipower.com.tw/media/ylinuevz/2021122413491339388.pdf?mediaDL=true)
and [answer link](https://www.taipower.com.tw/media/0g3jfaw1/2020111609474591417.pdf?mediaDL=true)
returned identical bytes with visibly printed answers on 2026-10-10. The exact
native-role evidence resolves both references as an answered question paper.
Preserve both original references and that checksum-bound role; do not claim an
unanswered copy exists.

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

An official answer endpoint can expose the original question instead. Correct
that reference only with exact native source context and checksum evidence;
retain its acquisition URL, original reference in immutable history and the
original bytes. A shared essay does not merge the distinct qualifications named
on its cover, and an empty text extraction does not establish a blank document.

## `post_recruit`

Use official Chunghwa Post recruitment event pages and their directly linked examination artifacts. Recruitment announcements without administered papers remain event evidence, not paper records.

The commissioned TABF history windows include both general recruitment and the
separate IT-specific programme. The postal native evidence owner records annual
issuer rules, actual written dates, employment grades, occupations, printed
subjects, file roles and applicant-code applicability against exact PDF digests.
The programme year is an acquisition partition: the 112 programme held its first
written test on 113-01-07. Do not move its source keys to a different event or use
the programme numeral as the ZIP's examination-year folder.

營運職、專業職(一) and 專業職(二) are employment grades. 內勤 and 外勤 qualify
the occupation; eligibility education, paper sessions and regional allocations
are separate concepts. Different occupations can share the same common or
professional paper. Preserve their distinct job identities and share native
mirror bytes. Reserved disability allocations use the same written papers.
Question PDFs with printed choice answers use `question_answer`; this does not
claim an essay solution. Preserve inline corrections within their original PDF.

Some older annual brochures now return 404. Reviewed issuer releases and the
completed annual report can establish historical dates; the evidence notes
distinguish captured native documents, indexed issuer announcements and failed
live requests. A later discovery or changed PDF cannot inherit those facts
without matching source context and checksum. The original aggregate download
is retained independently when its replacement job identities are published.

## `rcpet_cap`

Use the official RCPET CAP archive and its event-linked assessment materials. Provider normalization must preserve the official assessment identity rather than deriving it only from filenames.

The [ROC 103 archive](https://cap.rcpet.edu.tw/exam/103/103exam.html)
linked both 試題說明 and 國文科 to the same Google Drive file on 2026-10-10.
A fresh download matched the retained Chinese paper, whereas the retained
explanation begins 「103 年國中教育會考試題說明」. Keep the last verified
explanation reference and its bytes; reject conflicting source labels before
replacing that event. On 2026-10-20 the official listing again linked the
explanation to its separate file, and a fresh download matched the retained
native explanation exactly. The obsolete failure exception was removed.
Earlier source references and the rejected metadata remain in provider revision
recovery, without treating that metadata as proof of the linked payload.

The [ROC 111 reference-test archive](https://cap.rcpet.edu.tw/exam/111c/111practice.html)
relocated its eight files from Google Drive to Chinese filenames on the official
site. Fresh encoded requests verified that all eight payloads match the retained
checksums. By 2026-10-20 its links had returned to Google Drive; the retained
payloads still matched. Encode request URLs without changing the recorded
source spelling.

## `sfi_cert`

Use the native table label and the PDF's own programme, year, round and regional
form. Download numerals are resource codes, and the seasonal folders are mutable
slots. The page-wide recent-round headings do not establish every linked PDF's
event. Reviewed native facts bind each assignment to exact PDF checksums;
unfamiliar replacement bytes require a new native review before discovery can
assign an examination event. The client pins those reviewed responses for the
remainder of its sync so a slot change cannot substitute another sitting.

Each answer cites its own native PDF fact. A paired question establishes neither
the answer's checksum nor its evidence record; shared normalization evaluates
the roles independently while preserving the original acquisition pair.

These are distinct qualification and ability programmes, rather than one common
grade ladder. Keep 高級 within its official programme name, and distinguish 乙科
from a certification grade. The current
[written-test rules](https://www.sfi.org.tw/Node?id=216) distinguish candidate
routes with different subject counts. The official 投信投顧 download combines
those routes in one original PDF; retain that collection scope without making
copies for each route or merging it into the separate law-only programme.
Sections within a paper are not successive selection stages. The 高雄考區
paper has its own native context and content variant.

Preserve each original combined PDF and expose its native subject headings.
Answer files contain the source's selected-choice answers; their availability
does not establish full worked solutions. Earlier payloads from reused URLs keep
their actual native events even after leaving the current rolling listing.
Keep the former wrong assignments in immutable source history, and preserve
their verified bytes before retiring redundant mirror locators. Earlier sync
receipts for those wrong assignments cannot credit the corrected events.

The current listing, retained native PDF generations and current programme rules
were checked on 2026-10-20/21. This establishes the recorded native contexts,
not complete historical annual eligibility rules or a visual review of every
paper body. The source remains a rolling archive with a historical coverage
limit. The recorded intellectual-property notice does not establish permission
for archive republication; keep site ZIP publication quarantined.

## `special_admission`

This provider covers the university academic branch of the entrance examination
for students with disabilities. Its accepted papers are the archive's 大學組 /
共同 rows; other school groups and arts/practical papers have separate source
scope. The programme is distinct from general university special selection.

Keep the source-native 數學甲/乙 subjects separate from 數學A/B. The official
[historical mathematics scope](https://cis.ncu.edu.tw/EnableSys/home/readExamInfoFile?examInfoId=383&row=0)
and the [scope applying from ROC111](https://cis.ncu.edu.tw/EnableSys/home/readExamInfoFile?examInfoId=894&row=0)
describe different curriculum coverage. A whitelist containing only the modern
names omits historical papers; aliasing the old names to the new ones would hide
that distinction. Preserve each original subject label, annual event, source
URL and native PDF. Shared CEEC authorship does not make these papers part of
the separate national university entrance programmes.

An advertised selector year can have no papers yet. Retain that observed event
as awaiting papers, without creating a paper download or claiming an
administered sitting. Blank unused rows in an original answer-table template
do not establish missing questions; preserve the original PDF and any inline
correction notes.

The provider's reviewed answer-correction evidence binds the full source context
and original checksum to native correction notes. The archive's generic answer
label and filename are insufficient: some corrected PDFs have neither a revised
filename nor a separate correction link. Acquisition retains the original answer
slot and mirror path; both fresh and retained normalization project the reviewed
role as `corrected_answer`. A different payload at a reviewed slot requires a new
native review. Preserve previous role records through the source-revision writer;
the evidence does not reconstruct an unavailable earlier answer version or infer
scoring rules from the words 無標準答案.

## `tabf_cert`

Use official TABF category and event rows and their linked artifacts. A PHID or other page key is not a cross-year identity unless the official category and event context also agrees.

The [official programme rules](https://www.tabf.org.tw/License.aspx) distinguish
initial and advanced lending grades, full trust qualification from the
trust-law-only route, and general-finance from consumer-finance internal
control. The law-only route requires an existing qualifying securities
credential. Shared law questions can legitimately serve both qualification
contexts; identical bytes do not merge their identities. `基本`, `基礎` and
the senior-client population `高齡` do not establish a common grade ladder.
Family trust and senior financial planning are divided into test sections
(`分節不分科`); those sections are neither qualification levels nor successive
selection stages.

The [FIT FAQ](https://service.tabf.org.tw/fit/QA.aspx) and
[score explanation](https://service.tabf.org.tw/fit/AboutScore.aspx) describe a
banking employment assessment with independently selectable modules, no pass
threshold and no qualification certificate. Its performance bands are score
interpretation, not separately administered examination grades. Retained early
FIT editions are actual historical papers with reviewed native years; their
continued reference availability does not turn them into current-year samples.

The [reviewed native contexts](../../catalog/mappings/tabf/native-papers-v1.json)
bind each programme, round, URL and full checksum to native headings, subject
or section scope, file role and answer-objection year. Calendars and handbooks
corroborate dates where available. The advanced lending round supported only by
its native answer-period year makes no claim about an exact examination day.
Capture years, document ordering and upload timestamps do not establish event
identity. Earlier source dictionaries remain unchanged in the revision journal;
history audits expose their independently reviewed native contexts.

New events, changed categories, links or bytes require review before acquisition
or classification. Use the HTML/local-mirror capture procedure in the
[recovery guide](../operations/recovery.md) to inspect them without automatically
fetching restricted examination PDFs. The observed current listing has native review coverage;
the source inventory and discovery manifest retain the partial historical
coverage disposition and distinguish verified retained bytes from fresh downloads. The asset-host robots restriction and
unresolved republication permission continue to withhold public publication.

## `taigi_cert`

Use the official Taiwanese-language certification resource groups while
preserving the distinction between paper forms, proficiency outcomes and sample
materials. The official rules assign A/B/C forms to paired proficiency bands.
Each candidate chooses one
form, and the result determines the attained level. Listening, speaking,
reading and writing are skills within a form, not successive selection stages.
The current computer-based administration does not turn older downloadable
examples into administered papers or establish a new date for them.

The [native material facts](../../catalog/mappings/taigi/native-materials-v1.json)
bind source labels, forms, skills, roles and dates to exact URLs and full
checksums. Some native PDFs print both an initial publication date and a later
modification date. Project the explicit original publication year; retain the
modification date separately and never treat it as an exam year or automatic
proof of a corrected answer. Undated companion answer tables, audio and ZIPs do
not inherit dates from a question booklet, archive timestamps or capture time.
The retained acquisition partition is a storage key; stable sample resource
IDs and reviewed source-material dates determine meaning.

Reference-answer PDFs remain answers, including booklets that reproduce
questions alongside model responses. Keep listening, speaking and writing
dictation audio roles distinct. Original source ZIPs have reviewed portable
member paths and checksum-bound correspondence with the individually linked
files. Preserve those originals as provider-owned attachments and exclude them
from generated paper ZIPs so the same sample material is not packaged twice.

Unknown links, changed labels and replacement bytes require native review.
An approved new acquisition revision can coexist with older native evidence;
historical audits project its corrected role/date without rewriting the
original source dictionary. The source manifest records the dated robots
recheck separately from the earlier restriction observation. A missing robots
file does not grant redistribution permission or automatically change the
existing acquisition or public-backup policy; publication remains withheld.

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

Original ZIPs are source containers, retained as `source_archive` attachments.
A combined question/answer download label does not establish an accessibility
format, a native subject, or one applicant population. Review the individual
members before assigning question/answer roles and publication identities; keep
general-worker and industry-school applicability explicit. A PDF named standard answers may contain full questions
and visible answer letters, so its filename does not establish an answer-only role.

Intake preserves each PDF as a neutral `source_document` until its native role
and applicability are reviewed. ZIP members have parent-checksum-bound locators;
their subject keys use the parent URL and member path, so a changed parent version
retains the earlier bytes without renumbering the reference. The original ZIP
and its exact PDF members remain independently recoverable.

Some scanned originals combine several occupational booklets in one PDF.
The [native role reviews](../../catalog/mappings/taisugar/native-file-roles-v2.json)
approve a question-only or combined question/answer role only for an exact
source context and checksum. A shared question/answer ZIP label cannot assign
the combined role to a writing or translation paper without printed answers. Choice answers printed beside the questions do not establish that open
questions have published model answers. Preserve each complete original and its
native booklet boundaries; its first subject header cannot classify the whole
collection as one occupation. Role approval leaves stage, occupational identity
and publication eligibility subject to their own evidence and review.
The same exact native evidence can reinterpret earlier question-only references
without rewriting their immutable source records. Verify every matching current
and historical locator; a valid current copy does not establish that the retained
historical copy is intact.

The [annual written-stage evidence](../../catalog/mappings/taisugar/annual-written-exams-v2.json)
binds each reviewed original to the matching official timetable
and subject tables. It establishes the initial written stage and examination
year only for those exact source contexts and checksums. Preserve the annual
rules as checksum-guarded `instructions` attachments with reference material and
publication dates. A rule's later practical, physical or oral assessment date,
or its certificate-eligibility deadline, must not date the written paper.
Keep each annual rule once, with its exact reviewed paper versions. Initial
publication and printed revision dates remain separate; a later official
revision can document an earlier sitting. Conflicting later-stage wording
does not establish a second written-test date and remains unresolved.
School prerequisites, question difficulty and prerequisite certificate grades
do not establish a recruitment grade. Shared common papers also cover
disability-restricted applicants; keep occupation and applicant applicability
unresolved until their separate evidence is complete.

Older Windows archives use CP950 filename bytes without the ZIP UTF-8 flag.
Decode those native names before checking portable paths; when a Unicode Path
extra field is present, require agreement. Reject traversal, collisions, symlinks,
unknown formats, corrupt CRCs and oversized expansions. Derive member PDFs from
the original in memory rather than extracting source-controlled paths onto disk.

Acquire the required containers during ordinary sync. Refresh ensures the parent
version inspected by the adapter is the version stored with its members. Direct
PDF acquisition keys follow their exact source URLs, independent of order and display labels. A change
from an earlier paper/container assignment is a conserving migration: prove the
replacement's URL and complete checksum, retain the earlier reference and
independent recovery bytes, then reconcile the current manifest through its owner.
Source acquisition alone does not approve unresolved native paper identities.

The [company's public paper announcement](https://www.taisugar.com.tw/chinese/News_detail.aspx?n=10080&p=3&s=15158)
is a different delivery route from the contractor's earlier login redirect.
Retain the dated route observation, and recheck the company archive before
carrying that restriction into a current coverage claim. The provider's sync
manifest projection derives current file paths, checksums and counts from the
verified retained originals; discovery timestamps remain separate observations.

## `tcte_tve`

Use the official TCTE technological and vocational examination archive, retaining its official program and subject distinctions. Do not merge programs solely because display labels overlap.

Some native mathematics PDFs combine common questions and separate A/B/C forms
in one original booklet. The reviewed booklet evidence binds its complete page
structure and each form reference to exact source context and SHA-256. Store that
original once under a combined-form publication filename while preserving every
form reference and its separate answer key. Unreviewed files retain ordinary
subject grouping; matching bytes alone do not approve cross-subject sharing.

Native group numbers belong to annual source rows. The
[TCTE-authored historical comparison](https://www.ceec.edu.tw/files/file_pool/1/0L104584383520359905/06-%E7%AC%AC%E5%85%AD%E6%9C%9F.pdf#page=144)
and official [historical](https://www.tcte.edu.tw/index.php?mod=TVETest%2Fmajtype%2FPage%2F092majtype_4Y)
and [post-reform](https://www.tcte.edu.tw/index.php?mod=TVETest%2Fmajtype%2FPage%2F098majtype_4Y)
group tables distinguish renumbering from mergers and splits. Preserve the
separate predecessor groups when a later programme combines their scope. A
one-to-one correspondence or renumbering retains its reviewed identity.

The [group catalogue](../../catalog/mappings/tcte/group-identity-v1.json) owns
these meanings and their exact annual source captions, including parenthesized
composite-group ordinals. A composite group retains its complete candidate
combination even when its papers are shared with individual constituent groups.
The older commerce component stays distinct from the later combined business
and management group; combinations containing those components remain distinct.
The continuing English/Japanese-only combination retains one identity across
its number change and the observed publication gap, without inventing events.

Match the event, year, category key, complete native caption and professional
subject part in both fresh and retained classification. A missing or conflicting
context remains in an event-specific review identity. The historical article's
prospective subject plan does not establish adopted ROC102 rules; use the
[actual official annual groups](https://www.tcte.edu.tw/index.php?mod=TVETest%2Fmajtype%2FPage%2F102majtype_4Y).
Later selective-subject rules do not establish historical applicability.

Public titles use the reviewed group meaning, while native numbers remain in
source records, archive entries and search aliases. Preserve coherent existing
IDs. Replaced mixed identities leave current website results; their existing
ZIP URLs remain byte-pinned historical downloads under the site's explicit
retention policy. Identity migration preserves all original payloads and source
roles, and does not create source-sync receipts.

Historical mathematics links can share one table cell. The visible form label
belongs to each anchor or download button's `value`; reading only anchor text
silently keeps the first button and drops the other forms. Pair questions and
answers by that native label, including the historical D and S forms. Preserve
the earlier shared question book when the source supplies one question link and
separate form-specific keys. Conflicting links for one form, or a key without a
matching book among several distinct question links, require review rather than
substituting another form's question.

When repairing the former generic mathematics slot, keep its exact source URL
and checksum in provider history before replacing its subject context with the
native form. Existing correctly named subjects and the common-subject download
URL remain stable. Original answer PDFs retain their correction notes and blank
unused table rows; neither should be removed as redundant material.

Reviewed native correction notes override the generic standard-answer download
label only for the exact event, form, source URL and payload checksum in the
provider's answer-correction catalogue. Apply the same resolver to fresh and
retained records. Keep acquisition answer slots and original mirror paths;
preserve the earlier published role through the source-revision writer. An
explicit statement that an answer changed establishes a correction, while an
isolated 送分 cell does not prove a superseded key. This review neither recovers
an unavailable earlier key nor changes historical group identities.

## `teacher_qual`

Use official teacher-qualification assessment events and paper artifacts. Teacher recruitment exams are owned by separate providers and must not be folded into this qualification series.

The category selector and each subject cell own the individual paper roles.
Enumerate every selector value, retaining distinct values even when their labels
match; an empty option is not evidence that another option duplicates it. The
combined download is retained as a source archive, and does not establish
complete individual question/answer coverage. The native kindergarten learner
development answer is separately available although omitted from the ROC 115
combined ZIP.

School routes and the special-education candidate groups are structured tracks
and variants. They are not numerical qualification grades, and the two ROC 108
sittings remain distinct events within cross-year identities. The source's
sample-only heading establishes sample material; its selector/storage year does
not establish an administered examination or a publication date. Undated sample
collections must remain separate from administered papers.

The [exact native fallback](../../catalog/mappings/teacher_qual/archive-fallbacks-v1.json)
records kindergarten links whose PDF headers name secondary school or another
examination year. Preserve those conflicting source references with their actual
native dates outside the kindergarten paper projection, and use the verified
kindergarten members from the corresponding original archives. Their source
context, original ZIP checksum and member checksum are required; changed
conflicting bytes or originals require another review.

The [native member evidence](../../catalog/mappings/teacher_qual/archive-papers-v1.json)
binds each retained combined archive and nested member to exact checksums,
categories and roles. Publish its Word alternatives without conversion and keep
distinct PDF bytes even when their complete extracted text matches. Mark the
source-container version explicitly; retain `更改前` as a filename fact without
inventing a correction date. Verify the complete member set and CRC before
projection, including excluded operating-system metadata. Reviewed originals
that change must stop for another native review.

Retire the former whole-programme download only after every original member is
accounted for in the category/material projection or preserved source artifacts.
The active primary-only site policy introduces no alias for that obsolete mixed
identity. Keep its exact ZIP and original state in a restore-tested transition
backup, and retain immutable earlier containers and conflicting references.
Rollback restores the earlier source state through the conserving provider
writer so the newer native records also remain recoverable.

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

Keep the rolling TOCFL question bank separate from administered sittings and
reference vocabulary. The [native resource evidence](../../catalog/mappings/tocfl/native-materials-v1.json)
binds each URL and full payload checksum to its official band, skill, unit,
writing-script applicability and file role. Bands cover paired proficiency
outcomes; skills are not selection stages. Novice retains TOCFL's own levels
and must not inherit another programme's CEFR band.

Traditional and simplified questions have separate publication identities.
Their common answer workbooks, listening audio and transcripts retain explicit
applicability to both scripts and can share verified mirror bytes. Preserve
native workbook coordinates: a prompt can have several answers and separate
audio segments. An empty answer cell is not an answer, and a question document
count is not a question count. Native E/F choices in paragraph completion are
valid source choices.

The instruction RARs contain reference examples and retain their original
bytes as attachments outside generated paper ZIPs. Native Novice instruction
folders include trailing spaces, so preserving those originals does not claim
their internal paths meet the project's portable publication policy. The
vocabulary ZIP and spreadsheets are references too. CCCC vocabulary belongs to
the separate children's certification programme and does not establish adult
TOCFL band identities.

Stable resource-group IDs replace filename/current-year groupings. The retained
acquisition partition is a storage key. Project only an explicitly printed
material date; bank update statements, filenames and Office/archive timestamps
do not date every file. The native catalogue records companion-file gaps and
the unresolved disagreement between the landing-page total and workbook
answers. Preserve the complete official packages without inventing missing
questions or silently dropping unmatched answers.

Unknown links, changed headings and changed payloads require native review.
Sync refreshes payloads even when a retained file has a valid checksum: cached
validity does not prove that a rolling source URL still serves those bytes.
Earlier original source dictionaries remain unchanged in the revision journal;
independent native history views correct their interpretation. Retained unique
older payloads without complete source dictionaries remain explicitly unresolved
in the source manifest. Keep their bytes and original locators without assigning
URLs, examination years or paper identities from filenames. Routine duplicate
cleanup preserves them. The copyright/redistribution hold and existing durable
backup policy remain separate from acquisition correctness.

## `tqc_cert`

Use official TQC examination artifacts with source identity strong enough to keep distinct payloads separate. Generic labels must not share storage keys when their source files differ.

The official sample listing in the source inventory describes its PDFs as references for question formats. Treat this collection as sample material, including previews whose native headings say 模擬試卷. Dates alongside those downloads are publication dates, not administered examination dates. Preserve legacy discovery partitions for traceability; a missing listing date requires review rather than a synthetic public year. Software versions and book editions are separate content distinctions.

Reviewed subject, software/edition and native grade facts live in the [TQC identity catalog](../../catalog/mappings/tqc/sample-identity-v1.json). Exact titles and PDF checksums guard these decisions. Native 專業級 sample headers establish professional-grade coverage; the official MySQL rules establish the grade omitted from that sample's header. Typing samples serve performance-based grades and must not inherit a single grade from an edition or paper-code numeral. Changed payloads and new titles require fresh review. Subject certificates remain distinct from composite personnel certificates and from TQC+.

The complete retained sample set has been reclassified through the shared owner. Publication remains withheld because the existing review did not establish a republication grant. First-page inspection and the reviewed initial grade/rule sections do not constitute full-body or complete historical-rule review. Backup policy remains a separate operational decision.

## `twc_recruit`

Use official Taiwan Water recruitment examination archives and inspect nested artifacts rather than validating only an outer archive signature. Retain corrupt official payload evidence without publishing unusable papers.

Programme years are retained source partitions, not assumed examination dates.
The [reviewed archive facts](../../catalog/mappings/twc/archive-identity-v1.json)
bind annual initial-written dates and stages to exact original ZIP checksums.
Normalization and migration share these facts. Missing annual evidence or changed
bytes require an unknown date/stage and review; they must not inherit the year in
a filename. Initial written papers remain separate from the later practical,
physical and oral assessments. Professional subject numbers are paper slots,
not selection stages, and a school prerequisite is not an examination grade.
These outer-archive facts do not establish every native occupational applicability
relationship or authorize child publication. The checksum-anchored
[native paper inventory](../../catalog/mappings/twc/archive-papers-v1.json) now
drives portable PDF projection while retaining original containers separately.
Historical answer-change and 疑義後 labels, shared native category headers, and
different original renditions remain traceable. Separate reviewed occupation
entries bind a native paper to annual job-purpose and written-subject tables.
Shared papers receive one source reference per applicable occupation while their
identical mirror bytes are shared. The earlier generic references remain in
source history. A native path, checksum, category code and material context must
all match before classification approves the relationship.

Candidate groups using the same written content remain listed in the occupation
evidence; their eligibility conditions do not create duplicate content variants.
The native 產學 headers identify a separate relationship: the ordinary recruitment
date must not establish its date or stage. The
[industry-school recruitment rules](https://svc.tabf.org.tw/_File/Download/110water02/Readme/10121Readme500197.pdf)
also demonstrate that this route can have its own sitting date. Missing annual
rules and common-paper evidence remain unresolved. Reviewing some occupational
contexts does not remove the provider's publication hold or approve older
unreviewed history.

Download anchors may use a generic accessibility title; use the visible
year-bearing file label when the title has no year. Keep candidate reconciliation
and duplicate-year/URL checks so a layout change cannot silently omit history.
Availability on the official archive is independent of access to a contractor's
applicant portal.

Ordinary 試題(解答) archives use the question-and-answer role. They can contain
answer-marked questions or separate question papers and standard answer sheets;
an anchor's generic accessibility title does not make them accessibility
editions. The provider's native-label resolver also corrects its retained
wrapper records during catalog migration. Former roles remain traceable in the
source revision journal, and historical source locators are preserved until a
successful acquisition supplies their current role-specific mirror locator.

Preserve original ZIP bytes separately from portable native paper
projection. Legacy filename decoding and corrupt nested members require explicit
review. Native recruitment categories may share a subject paper; answer-marked
questions must not be presented as an unanswered copy merely because the outer
link says 試題. Do not infer an official grade from professional-subject letters
or occupational 甲/乙 labels.

Native projection decodes legacy Big5 filenames while respecting native
UTF-8 flags and Unicode path fields. Windows thumbnail caches and Office owner
lock files are acquisition artifacts; exclude them from examination downloads
while preserving the original ZIP. Equivalent bytes in repeated native folders
retain their original references while verified equivalent payloads share bytes.

## `wdasec_skill`

The adapter reads the official past-paper archive, which is separate from the
centre's reference question banks. Preserve occupation, native grade, written
and practical components, and source-event distinctions. The event's related
documents include official question/answer doubt decisions and correction
announcements; retain these independently of the original papers. A notice can
uphold an answer as well as change one, so it is not automatically a replacement
answer key. Older notices can be native Word documents. An empty related-table
directory link is a source placeholder, not a downloadable file.

Discovery must traverse each supported native archive category with its own
submit value, session fields and pagination. An unexpected category button
requires source-scope review instead of being silently omitted. The source's
annual project pages can contain several test dates, and different pages can share an annual title;
use the stable source page identifier rather than a title or row position.
A menu mentioning a grade does not determine every linked paper's grade.
Preserve the native row labels, and keep conflicts with paper headers unresolved
until an exact source-and-byte review supports a correction. Newly discovered
project contexts require review and cannot inherit approval from a matching
occupation, file URL or checksum in the national archive. The centre's
[explanation of project examinations](https://www.wdasec.gov.tw/News_Content.aspx?n=611E645F4BAB689F&s=AA734EAEEBAC3F68&sms=47C68486FEC19CA4)
distinguishes student, trainee and military projects from the national sessions;
an archive menu alone does not establish a particular candidate population.

The source's identical occupation/grade labels can link different written papers:
native headers identify the Chinese masters for the foreign-assistance form.
These are not published English, Vietnamese or other translated-language files.
Exact native contexts and checksums in the native-paper evidence own this boundary;
changed bytes or unreviewed contexts require review. Ordinary source papers and
already published practical components remain in the ordinary-paper collection.
The assisted collection explicitly identifies its written-paper scope; it does
not describe a separate qualification path or exempt candidates from practical
requirements. This form review does not approve historical material purpose,
answer roles, occupation renaming or an annual examination date.

Some annual beverage archive pages cross-list identical native papers from
different years. The reviewed annual heading and footer own the examination
year; source-event titles and table dates remain provenance. Preserve each
source context and its original bytes, and use complete-history material/date
reviews before consolidating equivalent public ZIP entries. Beverage theory
papers can print multiple-response answers beside their questions; a filter
limited to single-digit answers misses these combined question/answer booklets.
Neither printed answers nor an archive date alone establishes material purpose.

Historical software-design originals can contain a common first booklet and a
language-specific second booklet in one PDF, each restarting question numbering.
Later originals can publish the booklets separately. Preserve the actual native
packaging and the distinct common, Java and C++ track contexts; shared first-booklet
content does not make the combined originals interchangeable. Reviewed question
counts describe the whole original, while the combined question/answer role is
independent of qualification identity. Inconsistent native footer totals do not
justify deleting a final page or inventing a replacement; retain the exact official
bytes, including continued question content.

An accounting theory booklet can retain the generic occupation heading after
the qualification splits into tracks. The official
[annual briefing](https://skill.tcte.edu.tw/doc/YR100/100F3_Promote.pdf#page=8)
states that the generic, manual-bookkeeping and information tracks share their
theory paper, and dates the track split to ROC year 99. Preserve the archive's
track applicability and its practical components; a common theory heading does
not merge the qualifications or move a manual-track paper to the generic bundle.
Likewise, a historical source-code difference alone does not establish a new
paper identity when the native occupation and grade agree. Preserve both codes
as source evidence and review the actual scope before changing publication.


The native-paper facts separately approve corrections to piano tuning. Its
retained files identify 單一級 even where the source table says 甲級, and contain
questions together with answers. Classification, public subject labels and ZIP
filenames use those exact native facts. Original table labels, source codes,
URLs and bytes remain unchanged in provider evidence. The previous role is
retained in immutable history, so the old grade download stays recoverable and
its landing page can lead to the conserved single-grade collection. This review
does not approve other occupations' native grade, purpose, cohort or file role.

Further exact native reviews correct isolated listing grades for welding,
forklift operation, massage and visual/electronic skills. These approvals bind
the reviewed source context and bytes; they do not substitute a single grade
throughout an occupation's history. The older argon-welding archive itself prints
丙級 even where adjacent years print 單一級, so preserve that native distinction.
The incorrectly listed visual/electronic 甲級 reference and its correctly listed
乙級 reference serve the same native paper in the same event. Preserve both
original references and share the verified payload only after their native
grade, subject and combined question/answer role agree.

Independent role facts can identify a native written booklet that already
contains its answers. Review the complete declared question sequence and the
printed answer at each item; sparse mathematical or graphical stems require
native visual inspection when extracted text cannot establish the content.
Keep the original source role and bytes recoverable when normalizing that
booklet as questions with answers. This judgment alone does not resolve a
conflicting occupation, component, year, programme or material purpose.

Native cover counts can conflict with the actual numbered sections. Record the
items actually present and preserve the original header and section boundaries.
A combined question/answer role does not establish complete question coverage.
If the official original skips an item, keep that omission explicit and check
the related official notices; do not infer cancellation or scoring, or fill the
gap from another booklet or question bank.

Printed answers can contain several accepted option digits, including in older
booklets whose covers describe all questions as single-choice. Inspect the
actual numbered questions and answer markers; this layout establishes a combined
question/answer file role without establishing why several answers were accepted.
Keep correction notices separate, and do not infer cancellation, a different
paper form or a new qualification track from the answer digits. Later booklets
can also start their multiple-response section after question 61; the printed
sequence owns that boundary.

Some original PDFs combine a text answer key with scanned question pages. Review
the complete native document before assigning its role: an answer-only text
extraction can omit every question image. Keep the original combined file and
bind its independently reviewed role to the exact source context and checksum.
Fully scanned originals can likewise combine a printed answer report with
unfilled question pages. Match the native year, occupation, booklet and set
across both parts; an empty text extraction establishes neither missing questions
nor an answer-only original.
OCR prefix screening can miss an answer-report header and individual answer
labels. Use it to select originals for review, then check the complete native
document, including restarted section numbering and page continuations, before
approving its role or question count.

Retained cooking booklets explicitly headed 資深廚師 contain a different question
set from their same-event ordinary counterparts. Keep this native paper form
distinct from ordinary and foreign-assisted masters. The
[102 official guide](https://skill.tcte.edu.tw/doc/YR102/102Skill_reg_1020808.pdf)
has a cooking-specific senior application alongside the general senior
oral-assistance form. Later generic oral-assistance wording about unchanged
questions does not prove that the actual retained senior-chef booklets are
ordinary papers. Do not infer a new grade, a universal eligibility rule or a
practical exemption from this paper-form judgment.

Some native listing rows put these theory booklets in the practical column.
Independent component evidence corrects public labels and archive paths while
retaining the original column-derived source fields. Native scanned answer
reports and original image ZIP members can establish a combined question/answer
role; unreadable text extraction alone cannot. Preserve original files and
handwritten corrections. Identical source ZIPs in separate historical events
retain both source references until their event relationship is established.

The year-101 manual-bookkeeping papers linked from the native theory column
explicitly identify the practical component in their original headings. Each
contains two question pages followed by three candidate response sheets. Blank
response areas and supplied financial data do not establish worked answers, so
these files retain their question role. The
[official third-session briefing](https://skill.tcte.edu.tw/doc/YR101/101F3_Promote.pdf)
also lists manual bookkeeping among practical components delivered as written
non-multiple-choice tests. Exact component facts correct the public subject
labels and ZIP paths without changing grades, tracks or retained source fields.
