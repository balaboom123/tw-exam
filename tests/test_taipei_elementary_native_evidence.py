"""Post boundaries, shared native booklets and limited correction amendments."""

import copy
import hashlib
import json
from collections import Counter
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.bundler import validate_public_materials
from app.models import NormalizedCatalog, ParsedPaper, SourceExamPage
from app.normalizer import normalize_papers, renormalize_catalog
from app.providers.base import DownloadedFile
from app.providers.teacher_recruit_taipei_elementary.client import TaipeiElementaryRecruitClient
from app.providers.teacher_recruit_taipei_elementary.evidence import (
    CANONICAL_CATEGORY,
    PROVIDER_ID,
    annual_exams,
    legacy_category_targets,
    native_programmes_path,
    project_native_page,
    read_annual_exams,
    validate_public_papers,
)
from app.providers.teacher_recruit_taipei_elementary.provider import TaipeiElementaryRecruitProvider
from app.publication_metadata import (
    derive_public_metadata,
    public_coverage_notes,
    publication_paper_filename_subject,
)

ROOT = Path(__file__).resolve().parents[1]


def acquisition_page(exam):
    return SourceExamPage(
        exam.source_exam_id,
        exam.year_roc + 1911,
        exam.year_roc,
        f"{exam.year_roc}學年度{CANONICAL_CATEGORY}",
        [],
        [
            ParsedPaper(
                CANONICAL_CATEGORY,
                str(exam.year_roc),
                f.subject_code,
                f.subject_name_raw,
                {f.file_type: f.source_url},
            )
            for f in exam.files
            if f.file_type == "question_answer"
        ],
        provider_id=PROVIDER_ID,
    )


def normalize(page, checksums):
    metadata = {
        (p.category_code, p.subject_code, role): {"checksum": checksums[url]}
        for p in page.papers
        for role, url in p.files.items()
    }
    return normalize_papers(
        page.source_exam_id,
        page.year_ad,
        page.exam_name_raw,
        page.papers,
        [],
        "",
        metadata,
        provider_id=PROVIDER_ID,
    )


def reviewed_catalog():
    result = []
    for exam in annual_exams():
        checksums = {f.source_url: f.checksum for f in exam.files}
        catalog = normalize(project_native_page(acquisition_page(exam), checksums), checksums)
        assert not catalog.review_queue
        result.extend(catalog.papers)
    return result


def test_complete_history_keeps_posts_separate_and_common_booklet_in_each_post():
    rows = reviewed_catalog()
    assert Counter(p.year_roc for p in rows) == {114: 26, 115: 32}
    assert Counter(p.file_type for p in rows) == {"question_answer": 50, "corrected_answer": 8}
    assert len({p.bundle_id for p in rows}) == len({p.bundle_name for p in rows}) == 14
    assert len({p.checksum for p in rows}) == 26
    assert {p.level_id for p in rows} == {"not-applicable"}
    assert {p.stage_id for p in rows} == {"stage-1"}
    assert {p.classification_confidence for p in rows} == {"high"}
    assert {p.exam_series_id for p in rows} == {"taipei-elementary-teacher-recruitment"}
    assert not any(p.variant_ids for p in rows)
    assert Counter(p.year_roc for p in rows if p.subject_code == "basic-category-knowledge") == {
        114: 13,
        115: 12,
    }
    for exam in annual_exams():
        for job in exam.jobs:
            initial = [f for f in exam.papers_for(job) if f.file_type == "question_answer"]
            assert {f.subject_code for f in initial} == {
                "basic-category-knowledge",
                job.source_subject_code,
            }
    validate_public_papers(rows, complete_history=True)
    validate_public_materials(rows)


def test_amendment_keeps_original_notice_and_does_not_spread_to_other_booklets():
    exam = annual_exams()[1]
    full = next(f for f in exam.files if f.subject_code == "answer-review")
    amendment = next(f for f in exam.files if f.subject_code == "answer-review-amendment")
    assert set(full.applies_to_subjects) == {
        "general",
        "physical-education",
        "music",
        "taiwanese-minnan",
        "special-education-disability",
        "science",
    }
    assert amendment.applies_to_subjects == ("taiwanese-minnan",)
    assert amendment.amends_subject_code == full.subject_code
    assert amendment.amended_items == (4, 5)
    for job in exam.jobs:
        corrections = {
            f.subject_code for f in exam.papers_for(job) if f.file_type == "corrected_answer"
        }
        if job.source_subject_code == "taiwanese-minnan":
            assert corrections == {"answer-review", "answer-review-amendment"}
        elif job.source_subject_code in full.applies_to_subjects:
            assert corrections == {"answer-review"}
        else:
            assert not corrections
    assert full in exam.papers_for(next(j for j in exam.jobs if j.bilingual))


def test_counseling_inline_key_covers_mcqs_and_does_not_invent_essay_answers():
    for exam in annual_exams():
        counseling = next(f for f in exam.files if f.subject_code == "counseling")
        assert counseling.multiple_choice_count == 15
        assert counseling.essay_count == 3
        assert counseling.file_type == "question_answer"
        assert "申論題" in counseling.public_section_labels[1]
        assert "no model answers" in counseling.review_notes
    rows = [p for p in reviewed_catalog() if p.track_id == "taipei-elementary-counseling"]
    aliases, subjects = derive_public_metadata(
        rows, bundle_id=rows[0].bundle_id, canonical_name=rows[0].bundle_name
    )
    assert "輔導專門知能（申論題）" in subjects
    assert "教育專業（選擇題含答案）" in aliases
    assert {p.file_type for p in rows} == {"question_answer"}


def test_bilingual_shared_booklets_and_taiwanese_wording_keep_native_context():
    rows = reviewed_catalog()
    for year, subject in ((114, "physical-education"), (114, "visual-arts"), (115, "music")):
        selected = [p for p in rows if p.year_roc == year and p.subject_code == subject]
        assert len(selected) == 2
        assert len({p.checksum for p in selected}) == 1
        assert len({p.bundle_id for p in selected}) == 2
    taiwanese = [p for p in rows if p.subject_code == "taiwanese-minnan"]
    assert len({p.bundle_id for p in taiwanese}) == 1
    assert {p.subject_name_raw for p in taiwanese} == {"閩南語", "閩南語科"}
    assert {publication_paper_filename_subject(p) for p in taiwanese} == {"閩南語", "閩南語科"}


def test_historical_document_gap_is_not_a_successful_resource_or_attachment():
    historical, current = annual_exams()
    assert len(historical.resources) == 12
    assert historical.attachments() == []
    assert {d.document_id for d in historical.unavailable_documents} == {
        "rule",
        "vacancy",
        "answer-review",
    }
    assert all(
        d.evidence_basis == "indexed-official-text-only" for d in historical.unavailable_documents
    )
    assert "UNRESOLVED" in historical.unavailable_documents[-1].review_notes
    assert len(current.resources) == 16
    assert len(current.attachments()) == 2
    assert not current.unavailable_documents
    rows = reviewed_catalog()
    assert all(
        public_coverage_notes(p) == ["114 學年度含原公布答案；答案更正公告原檔尚待補齊。"]
        for p in rows
        if p.year_roc == 114
    )
    assert not any(public_coverage_notes(p) for p in rows if p.year_roc == 115)
    assert not public_coverage_notes(replace(rows[0], checksum="0" * 64))


@pytest.mark.parametrize(
    "field",
    [
        "event",
        "year",
        "programme",
        "category",
        "category-label",
        "subject",
        "subject-label",
        "URL",
        "checksum",
        "material",
        "role",
    ],
)
def test_exact_source_context_cannot_approve_a_different_native_claim(field):
    original = reviewed_catalog()[0]
    changes = {
        "event": {"source_exam_id": "teacher-recruit-taipei-junior-114"},
        "year": {"year_roc": 115},
        "programme": {"exam_name_raw": "其他國小教師甄選"},
        "category": {"category_code": "job-unknown"},
        "category-label": {"category_raw": "其他甄選類科"},
        "subject": {"subject_code": "different"},
        "subject-label": {"subject_name_raw": "不同試卷"},
        "URL": {"download_url_source": original.download_url_source + "&changed=1"},
        "checksum": {"checksum": "0" * 64},
        "material": {
            "source_material": replace(
                original.source_material,
                kind="sample",
                date=replace(original.source_material.date, basis="publication_year"),
            )
        },
        "role": {"file_type": "answer"},
    }
    changed = replace(original, **changes[field])
    if field != "role":
        catalog = renormalize_catalog(NormalizedCatalog([changed], []), [])
        assert catalog.papers[0].classification_confidence == "review"
    with pytest.raises(ValueError, match="exact native"):
        validate_public_papers([changed])


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate-post",
        "wrong-weight",
        "missing-basic",
        "unknown-scope",
        "missing-original-notice",
        "missing-gap-disposition",
        "false-inline-key",
        "foreign-document",
    ],
)
def test_reader_rejects_incomplete_or_conflicting_evidence(tmp_path, mutation):
    doc = json.loads(native_programmes_path(ROOT).read_text())
    if mutation == "duplicate-post":
        doc["events"][0]["jobs"].append(copy.deepcopy(doc["events"][0]["jobs"][0]))
    elif mutation == "wrong-weight":
        doc["events"][0]["jobs"][1]["basic_specialist_weights"] = [50, 50]
    elif mutation == "missing-basic":
        doc["events"][0]["files"] = doc["events"][0]["files"][1:]
    elif mutation == "unknown-scope":
        doc["events"][1]["files"][-1]["applies_to_subjects"] = ["missing"]
    elif mutation == "missing-original-notice":
        doc["events"][1]["files"] = [
            f for f in doc["events"][1]["files"] if f["subject_code"] != "answer-review"
        ]
    elif mutation == "missing-gap-disposition":
        doc["events"][0]["unavailable_documents"] = doc["events"][0]["unavailable_documents"][:-1]
    elif mutation == "false-inline-key":
        doc["events"][0]["files"][6]["multiple_choice_count"] = 50
    elif mutation == "foreign-document":
        doc["events"][1]["documents"][0]["source_url"] = "https://example.com/rule.pdf"
    path = tmp_path / "native.json"
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError):
        read_annual_exams(path)


def test_missing_post_or_correction_cannot_be_published():
    rows = reviewed_catalog()
    with pytest.raises(ValueError, match="omitted or duplicated"):
        validate_public_papers(
            [p for p in rows if p.track_id != "taipei-elementary-english"], complete_history=True
        )
    with pytest.raises(ValueError, match="omitted or duplicated"):
        validate_public_papers([p for p in rows if p.subject_code != "answer-review-amendment"])
    with pytest.raises(ValueError, match="omitted or duplicated"):
        validate_public_papers([*rows, rows[0]])


def provider_fixture(monkeypatch):
    import app.providers.teacher_recruit_taipei_elementary.evidence as evidence
    import app.providers.teacher_recruit_taipei_elementary.provider as provider_module

    exam = annual_exams()[1]
    payloads = {f.source_url: b"%PDF-1.7\n" + f.source_url.encode() for f in exam.resources}
    exam = replace(
        exam,
        files=tuple(
            replace(f, checksum=hashlib.sha256(payloads[f.source_url]).hexdigest())
            for f in exam.files
        ),
        documents=tuple(
            replace(f, checksum=hashlib.sha256(payloads[f.source_url]).hexdigest())
            for f in exam.documents
        ),
    )
    monkeypatch.setattr(evidence, "annual_exams", lambda: (exam,))
    monkeypatch.setattr(provider_module, "annual_exams", lambda: (exam,))
    notices = {
        f.notice_url: [
            (getattr(r, "subject_name_raw", getattr(r, "title", "")), r.source_url)
            for r in exam.resources
            if r.notice_url == f.notice_url
        ]
        for f in exam.resources
    }
    client = Mock(spec=TaipeiElementaryRecruitClient)
    client.fetch_exam_page.return_value = acquisition_page(exam)
    client.pdf_downloads.side_effect = lambda url: notices[url]
    client.download_file.side_effect = lambda url: DownloadedFile(
        payloads[url], "application/pdf", "native.pdf"
    )
    return TaipeiElementaryRecruitProvider(client), exam, payloads, notices


def test_provider_acquires_both_notices_and_checks_each_shared_payload_once(monkeypatch):
    provider, exam, _payloads, _notices = provider_fixture(monkeypatch)
    page = provider.fetch_exam_page(exam.source_exam_id, exam.year_roc + 1911)
    assert len(page.papers) == 32
    assert len(page.attachments) == 2
    assert provider.client.download_file.call_count == 16
    assert {p.subject_code for p in page.papers if p.category_code == "job-taiwanese-minnan"} == {
        "basic-category-knowledge",
        "taiwanese-minnan",
        "answer-review",
        "answer-review-amendment",
    }


@pytest.mark.parametrize("change", ["new-PDF", "missing-notice", "changed-bytes", "unreviewed-URL"])
def test_source_changes_stop_before_replacing_retained_state(monkeypatch, change):
    provider, exam, payloads, notices = provider_fixture(monkeypatch)
    if change == "new-PDF":
        notices[exam.paper_article_url].append(
            ("new question-only PDF", "https://www-ws.gov.taipei/Download.ashx?u=new")
        )
    elif change == "missing-notice":
        amendment = next(f for f in exam.files if f.amends_subject_code)
        notices[amendment.notice_url].clear()
    elif change == "changed-bytes":
        payloads[exam.files[0].source_url] += b" changed"
    else:
        with pytest.raises(ValueError, match="Unreviewed"):
            provider.download_file("https://example.com/new.pdf")
        return
    with pytest.raises(ValueError, match="changed"):
        provider.fetch_exam_page(exam.source_exam_id, exam.year_roc + 1911)


def test_legacy_common_booklet_requires_all_thirteen_historical_post_successors():
    exam = annual_exams()[0]
    checksums = {f.source_url: f.checksum for f in exam.files}
    previous = normalize(acquisition_page(exam), checksums).papers
    current = reviewed_catalog()
    for old in previous:
        targets = legacy_category_targets(old, current)
        expected = (
            13
            if old.subject_code == "basic-category-knowledge"
            else 2
            if old.subject_code in {"physical-education", "visual-arts"}
            else 1
        )
        assert len(targets) == expected
        assert all(p.year_roc == 114 for p in targets)
        assert not legacy_category_targets(replace(old, checksum="0" * 64), current)
    basic = next(p for p in previous if p.subject_code == "basic-category-knowledge")
    assert not legacy_category_targets(
        basic, [p for p in current if p.category_code != "job-general"]
    )
