"""Native recruitment purpose, category applicability and conserving migration."""

import copy
import hashlib
import json
from collections import Counter
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.bundler import validate_public_materials
from app.models import ParsedPaper, SourceExamPage
from app.normalizer import normalize_papers
from app.providers.base import DownloadedFile
from app.providers.teacher_recruit_newtaipei import evidence
from app.providers.teacher_recruit_newtaipei import provider as provider_module
from app.providers.teacher_recruit_newtaipei.client import (
    LIST_API_URL,
    NewTaipeiTeacherRecruitClient,
)
from app.providers.teacher_recruit_newtaipei.evidence import (
    CANONICAL_CATEGORY,
    PROVIDER_ID,
    annual_exams,
    legacy_category_targets,
    native_programmes_path,
    project_native_page,
    read_annual_exams,
    validate_public_papers,
)
from app.publication_metadata import derive_public_metadata

ROOT = Path(__file__).resolve().parents[1]


def acquisition_page(exam):
    files = [(d.file_type, d.source_url) for d in exam.documents if d.applies_to_booklets]
    if exam.archive is not None:
        files.append(("question_answer", exam.archive.source_url))
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
                f"slot-{i}",
                exam.programme_name,
                {role: url},
            )
            for i, (role, url) in enumerate(files)
        ],
        PROVIDER_ID,
    )


def checksums(exam):
    result = {d.source_url: d.checksum for d in exam.documents}
    if exam.archive is not None:
        result[exam.archive.source_url] = exam.archive.checksum
        result.update({exam.archive.locator(m): m.checksum for m in exam.archive.files})
    return result


def reviewed_catalog():
    result = []
    for exam in annual_exams():
        page = project_native_page(acquisition_page(exam), checksums(exam))
        metadata = {
            (p.category_code, p.subject_code, role): {"checksum": checksums(exam)[url]}
            for p in page.papers
            for role, url in p.files.items()
        }
        normalized = normalize_papers(
            page.source_exam_id,
            page.year_ad,
            page.exam_name_raw,
            page.papers,
            [],
            "",
            metadata,
            provider_id=PROVIDER_ID,
        )
        assert not normalized.review_queue
        result.extend(normalized.papers)
    return result


def test_native_teacher_posts_and_care_workers_have_correct_purpose_and_complete_files():
    junior, care = annual_exams()
    assert (len(junior.jobs), len(care.jobs)) == (50, 1)
    rows = reviewed_catalog()
    assert len(rows) == 395
    assert len({p.bundle_id for p in rows}) == len({p.bundle_name for p in rows}) == 51
    assert len({p.checksum for p in rows}) == 63
    assert Counter(p.file_type for p in rows) == {
        "question": 100,
        "answer": 99,
        "corrected_answer": 98,
        "answer_clarification": 98,
    }
    assert {p.level_id for p in rows} == {"not-applicable"}
    assert {p.stage_id for p in rows} == {"stage-1"}
    assert all("初試（筆試）" in p.bundle_name for p in rows)
    care_rows = [p for p in rows if p.source_exam_id == care.source_exam_id]
    assert len(care_rows) == 2
    assert {p.domain_id for p in care_rows} == {"employment"}
    assert {p.exam_class for p in care_rows} == {"國營／就業甄試"}
    assert {p.exam_series_id for p in care_rows} == {care.series_id}
    assert all("契約進用教保員" in p.bundle_name for p in care_rows)
    assert len(care.jobs[0].registration_groups) == 4
    validate_public_papers(rows, complete_history=True)


def test_shared_booklets_do_not_merge_native_posts_and_counseling_has_no_fabricated_key():
    rows = reviewed_catalog()

    def select(code):
        return [p for p in rows if p.category_code == "job-" + code]

    music = select("music")
    bilingual = select("music-bilingual")
    assert {p.download_url_source for p in music} == {p.download_url_source for p in bilingual}
    assert music[0].bundle_id != bilingual[0].bundle_id
    assert "雙語專長" in bilingual[0].bundle_name
    assert "坪林實驗國中" in select("experimental-pinglin-biology")[0].bundle_name
    counselor = select("dedicated-counselor")
    assert len(counselor) == 1
    assert (counselor[0].file_type, counselor[0].subject_name_raw) == ("question", "輔導專業知能科")
    junior = annual_exams()[0]
    placeholder = [a for a in junior.attachments() if "未提供參考答案" in a.title]
    assert len(placeholder) == 1
    assert placeholder[0].file_type == "source_document"
    assert placeholder[0].source_material.kind == "reference"
    for code in ("chinese", "english", "mathematics", "geography"):
        job = next(j for j in junior.jobs if j.code == "job-" + code)
        assert job.registration_groups == ("一般組", "偏鄉組")


def test_native_registration_contexts_are_searchable_without_duplicate_paper_forms():
    rows = reviewed_catalog()
    for code, expected_groups, expected_subjects in (
        ("job-chinese", {"一般組", "偏鄉組"}, {"教育專業科", "國文科"}),
        ("job-experimental-pinglin-biology", {"實驗教育組"}, {"教育專業科", "生物科"}),
        (
            "job-care-worker",
            {"第1組（一般組）", "第2組（偏特組）", "第3組（原住民族組）", "第4組（臨時人員組）"},
            {"教保概論與教保活動設計"},
        ),
    ):
        selected = [p for p in rows if p.category_code == code]
        aliases, subjects = derive_public_metadata(
            selected,
            bundle_id=selected[0].bundle_id,
            canonical_name=selected[0].bundle_name,
        )
        assert expected_groups <= set(aliases)
        assert set(subjects) == expected_subjects
        if code != "job-care-worker":
            assert "答案疑義彙整表" in aliases


@pytest.mark.parametrize("fault", ["missing", "extra", "changed-sha", "wrong-context"])
def test_native_listing_changes_fail_before_projection(fault):
    exam = annual_exams()[0]
    page = acquisition_page(exam)
    digests = checksums(exam)
    if fault == "missing":
        page.papers.pop()
    elif fault == "extra":
        page.papers.append(copy.deepcopy(page.papers[0]))
    elif fault == "changed-sha":
        digests[next(iter(page.papers[0].files.values()))] = "0" * 64
    else:
        page.papers[0].category_raw = "另一個同年度甄選"
    with pytest.raises(ValueError):
        project_native_page(page, digests)


@pytest.mark.parametrize(
    "fault", ["missing-job", "missing-key", "missing-supplement", "duplicate", "wrong-purpose"]
)
def test_publication_requires_all_categories_roles_and_correct_programme(fault):
    rows = reviewed_catalog()
    if fault == "missing-job":
        rows = [p for p in rows if p.category_code != "job-music-bilingual"]
    elif fault == "missing-key":
        rows.pop(next(i for i, p in enumerate(rows) if p.file_type == "answer"))
    elif fault == "missing-supplement":
        rows = [p for p in rows if "補充公告" not in p.subject_name_raw]
    elif fault == "duplicate":
        rows.append(rows[0])
    else:
        rows[-1] = replace(rows[-1], domain_id="teacher", exam_family_id="teacher-exam")
    with pytest.raises(ValueError):
        validate_public_papers(rows, complete_history=True)
    if fault != "missing-job":
        with pytest.raises(ValueError):
            validate_public_materials(rows)


def old_reference(exam, role, url, checksum, template):
    return replace(
        template,
        schema_version=2,
        source_material=None,
        source_exam_id=exam.source_exam_id,
        year_roc=exam.year_roc,
        exam_name_raw=f"{exam.year_roc}學年度{CANONICAL_CATEGORY}",
        category_code=str(exam.year_roc),
        category_raw=CANONICAL_CATEGORY,
        subject_code=exam.source_exam_id.rsplit("-", 1)[-1],
        subject_name_raw=exam.programme_name,
        file_type=role,
        download_url_source=url,
        checksum=checksum,
    )


def test_retained_container_navigation_requires_exact_old_context_and_complete_successors():
    junior, care = annual_exams()
    rows = reviewed_catalog()
    previous = old_reference(
        junior, "question_answer", junior.archive.source_url, junior.archive.checksum, rows[0]
    )
    targets = legacy_category_targets(previous, rows)
    assert len(targets) == 197
    assert len({p.bundle_id for p in targets}) == 50
    assert {p.file_type for p in targets} == {"question", "answer"}
    assert not legacy_category_targets(replace(previous, category_raw="另一個 programme"), rows)
    assert not legacy_category_targets(previous, rows[1:])
    for document in care.documents:
        if document.file_type not in {"question", "answer"}:
            continue
        old = old_reference(
            care, document.file_type, document.source_url, document.checksum, rows[-1]
        )
        successors = legacy_category_targets(old, rows)
        assert len(successors) == 1
        assert successors[0].domain_id == "employment"


def test_expired_notice_can_be_revisited_but_active_foreign_or_changed_history_cannot():
    exam = annual_exams()[1]
    client = NewTaipeiTeacherRecruitClient()
    native = exam.acquisition_event()
    original = native.original
    detail = {
        "opn_title": original.title,
        "opn_tag": original.tag,
        "attachment2": [
            {
                "fileName": f"筆試{('試題' if d.file_type == 'question' else '答案')}.pdf",
                "fileUuid": d.source_url.rsplit("/", 1)[-1],
            }
            for d in exam.documents
            if d.applies_to_booklets
        ],
    }

    def fetch(url):
        return [] if url == LIST_API_URL else [detail]

    client._fetch_json = fetch
    page = client.fetch_reviewed_exam_page(exam.source_exam_id, 2026, native)
    assert len(page.papers) == 1
    assert client.discover_exams(2026) == []
    detail["opn_tag"] = "同樣幼兒園名稱的教師甄選"
    with pytest.raises(ValueError, match="does not match"):
        client.fetch_reviewed_exam_page(exam.source_exam_id, 2026, native)
    with pytest.raises(ValueError, match="year"):
        client.fetch_reviewed_exam_page(exam.source_exam_id, 2025, native)
    client._exam_notice_cache = {exam.source_exam_id: replace(native, notices=(original, original))}
    with pytest.raises(ValueError, match="history changed"):
        client.fetch_reviewed_exam_page(exam.source_exam_id, 2026, native)


def test_provider_verifies_rules_before_returning_a_native_page(monkeypatch):
    exam = annual_exams()[1]
    payloads = {d.source_url: b"%PDF-1.7\n" + d.document_id.encode() for d in exam.documents}
    test_exam = replace(
        exam,
        documents=tuple(
            replace(d, checksum=hashlib.sha256(payloads[d.source_url]).hexdigest())
            for d in exam.documents
        ),
    )
    monkeypatch.setattr(evidence, "annual_exams", lambda: (test_exam,))
    monkeypatch.setattr(provider_module, "annual_exams", lambda: (test_exam,))
    client = Mock()
    client.fetch_reviewed_exam_page.return_value = acquisition_page(test_exam)
    client.download_file.side_effect = lambda url: DownloadedFile(
        payloads[url], "application/pdf", "native.pdf"
    )
    provider = provider_module.NewTaipeiTeacherRecruitProvider(client)
    assert len(provider.fetch_exam_page(exam.source_exam_id, 2026).papers) == 2
    assert client.download_file.call_count == 3
    rule = next(d for d in exam.documents if d.file_type == "instructions")
    payloads[rule.source_url] += b"changed rule"
    with pytest.raises(ValueError, match="changed"):
        provider_module.NewTaipeiTeacherRecruitProvider(client).fetch_exam_page(
            exam.source_exam_id, 2026
        )


@pytest.mark.parametrize(
    "fault", ["missing-care-key", "wrong-family", "missing-reference", "unknown-booklet"]
)
def test_catalogue_rejects_incomplete_roles_and_unsupported_meaning(tmp_path, fault):
    payload = json.loads(native_programmes_path(ROOT).read_text())
    if fault == "missing-care-key":
        payload["events"][1]["documents"].pop()
    elif fault == "wrong-family":
        payload["events"][1]["domain_id"] = "teacher"
    elif fault == "missing-reference":
        payload["events"][0]["category_document_id"] = "not-reviewed"
    else:
        payload["events"][0]["jobs"][0]["booklets"] = ["同名但未審核試卷"]
    path = tmp_path / "native.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        read_annual_exams(path)
