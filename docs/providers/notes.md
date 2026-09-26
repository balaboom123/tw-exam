# Source judgment

Provider-specific source boundaries and durable exceptions are maintained here. The [generated index](README.md) and [reviewed inventory](../../catalog/source-inventory.json) own status, URLs, availability, restrictions, counts, and evidence. Use the [runbook](../operations/runbook.md) for shared operations and [add a provider](../contributing/add-a-provider.md) for source-proof requirements.

Sections use stable provider IDs; evidence links may point to their Markdown anchors. Add a section only for source-specific judgment, and update changing measurements at their executable owner.

## `ceec_ast`

Limit this provider to the official AST archive. The predecessor Subject Competency Test is a distinct official series and must not be inferred from older selector rows.

## `ceec_gsat`

Use the official CEEC GSAT archive and its directly linked paper, answer, and scoring artifacts. Legacy document or archive formats are eligible only when payload signatures match the advertised role.

## `cpc_recruit`

Only administered examination packages in the official static past-exam archive are papers. Recruitment brochures, application material, and similarly named operational documents remain outside the paper boundary.

## `gept_cert`

Use official GEPT paper material only when level and event identity are supported by the source. Rolling samples must not acquire a synthetic current-year identity merely because they were fetched recently.

## `hakka_cert`

Use official Hakka proficiency assessment material while keeping examination papers distinct from audiovisual practice resources. Redistribution limits on audiovisual works are publication constraints, not permission to relabel or omit retained evidence.

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

## `moea_recruit`

A MOEA recruitment provider must be supported by MOEA-owned recruitment identity and source material. Taipower records cannot stand in for MOEA records even when the subject matter or filenames look compatible.

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

## `tocfl_cert`

Use official TOCFL paper and mock-test banks while preserving whether material has a stable event identity. Rolling mock resources must not receive synthetic current-year identities.

## `tqc_cert`

Use official TQC examination artifacts with source identity strong enough to keep distinct payloads separate. Generic labels must not share storage keys when their source files differ.

## `twc_recruit`

Use official Taiwan Water recruitment examination archives and inspect nested artifacts rather than validating only an outer archive signature. Retain corrupt official payload evidence without publishing unusable papers.

## `wdasec_skill`

Use official workforce skills-test question-bank material within the declared paper scope. Preserve occupation, level, subject, and source-event distinctions across the large archive.
