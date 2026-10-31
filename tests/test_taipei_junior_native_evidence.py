"""Native post boundaries, complete answer revisions and conserving intake."""

import copy
import hashlib
import json
from collections import Counter
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.bundler import build_bundles, validate_public_materials
from app.models import NormalizedCatalog, ParsedPaper, SourceExamPage
from app.normalizer import normalize_papers, renormalize_catalog
from app.providers.base import DownloadedFile
from app.providers.teacher_recruit_taipei_junior.evidence import (
    CANONICAL_CATEGORY,
    PROVIDER_ID,
    annual_exams,
    legacy_category_targets,
    native_programmes_path,
    project_native_page,
    read_annual_exams,
    reviewed_native_revisions,
    validate_native_programmes,
    validate_public_papers,
)
from app.providers.teacher_recruit_taipei_junior.provider import TaipeiJuniorRecruitProvider
from app.publication_metadata import derive_public_metadata, publication_paper_filename_subject
from app.storage import MirrorStore
from app.sync import sync_exam_pages

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
            if f.file_type != "corrected_answer"
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


def test_native_categories_and_combined_booklets_keep_all_three_years_distinct():
    rows = reviewed_catalog()
    assert Counter(p.year_roc for p in rows) == {113: 42, 114: 60, 115: 57}
    assert len(rows) == 159
    assert len({p.bundle_id for p in rows}) == len({p.bundle_name for p in rows}) == 27
    assert len({p.checksum for p in rows}) == 109
    assert Counter(p.file_type for p in rows) == {
        "question": 53,
        "answer": 53,
        "corrected_answer": 53,
    }
    assert {p.level_id for p in rows} == {"not-applicable"}
    assert {p.stage_id for p in rows} == {"stage-1"}
    assert {p.classification_confidence for p in rows} == {"high"}
    assert {p.exam_series_id for p in rows} == {"taipei-junior-teacher-recruitment"}
    assert {p.exam_family_id for p in rows} == {"teacher-exam"}
    for exam in annual_exams():
        assert exam.education_specialist_weights == (30, 70)
        assert exam.question_counts == (40, 40)
        assert len(exam.attachments()) == 2
    validate_public_papers(rows, complete_history=True)
    validate_public_materials(rows)


def test_wording_aliases_group_across_years_without_merging_distinct_posts():
    rows = reviewed_catalog()
    english = [p for p in rows if p.track_id == "taipei-junior-english"]
    assert {p.subject_name_raw for p in english if p.file_type == "question"} == {
        "英文",
        "英語",
        "英語科",
    }
    assert len({p.bundle_id for p in english}) == 1
    for track in ("counseling", "dedicated-counselor", "mathematics", "gifted-mathematics"):
        selected = [p for p in rows if p.track_id == "taipei-junior-" + track]
        assert selected
        assert {p.file_type for p in selected} == {"question", "answer", "corrected_answer"}
        assert all("初試（筆試）" in p.bundle_name for p in selected)
    assert len({p.bundle_id for p in rows if p.year_roc == 115}) == 19
    bilingual = [p for p in rows if p.track_id.endswith("-bilingual")]
    assert all(
        p.variant_ids == ["written-language-chinese-english"]
        for p in bilingual
        if p.year_roc == 113
    )
    assert all(
        p.variant_ids == ["written-language-chinese"] for p in bilingual if p.year_roc in {114, 115}
    )
    assert all("學科中文命題" in p.bundle_name for p in bilingual if p.year_roc in {114, 115})


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
def test_native_identity_cannot_be_borrowed_by_another_source_context(field):
    original = next(p for p in reviewed_catalog() if p.file_type == "question")
    changes = {
        "event": {"source_exam_id": "teacher-recruit-taipei-elementary-113"},
        "year": {"year_roc": 115},
        "programme": {"exam_name_raw": "臺北市公立國民小學教師聯合甄選"},
        "category": {"category_code": "job-other"},
        "category-label": {"category_raw": "其他教師"},
        "subject": {"subject_code": "another-subject"},
        "subject-label": {"subject_name_raw": "另一題本"},
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
    altered = replace(original, **changes[field])
    with pytest.raises(ValueError, match="Taipei junior publication"):
        validate_public_materials([altered])
    if field != "role":
        changed = renormalize_catalog(NormalizedCatalog([altered], []), [])
        assert changed.papers[0].classification_confidence == "review"
        assert changed.papers[0].stage_id == "unknown"


def test_partial_bundle_or_history_cannot_be_published(tmp_path):
    rows = reviewed_catalog()
    one_post = [p for p in rows if p.year_roc == 115 and p.category_code == "job-counseling"]
    validate_public_materials(one_post)
    for selected in (rows[1:], [p for p in rows if p.year_roc != 115], [], [*rows, rows[0]]):
        with pytest.raises(ValueError, match="omitted or duplicated"):
            validate_public_papers(selected, complete_history=True)
    with pytest.raises(ValueError, match="omitted or duplicated"):
        build_bundles(
            tmp_path / "bundles", tmp_path / "mirror", NormalizedCatalog(one_post[:-1], []), ""
        )
    assert not (tmp_path / "bundles").exists()


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "changed-bytes", "changed-year", "missing-review"]
)
def test_changed_source_population_stops_projection(mutation):
    exam = annual_exams()[0]
    raw = acquisition_page(exam)
    checksums = {f.source_url: f.checksum for f in exam.files}
    if mutation == "missing":
        raw.papers.pop()
    if mutation == "duplicate":
        raw.papers.append(copy.deepcopy(raw.papers[0]))
    if mutation == "changed-bytes":
        checksums[exam.files[0].source_url] = "0" * 64
    if mutation == "changed-year":
        raw.year_ad += 1
    if mutation == "missing-review":
        checksums.pop(next(f.source_url for f in exam.files if f.file_type == "corrected_answer"))
    with pytest.raises(ValueError, match="Taipei junior"):
        project_native_page(raw, checksums)


def fake_provider(monkeypatch):
    import app.providers.teacher_recruit_taipei_junior.evidence as evidence
    import app.providers.teacher_recruit_taipei_junior.provider as provider_module

    exam = annual_exams()[-1]
    payloads = {
        f.source_url: b"%PDF-1.7 fixture " + str(i).encode() for i, f in enumerate(exam.resources)
    }
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
    client = Mock()
    client.fetch_exam_page.return_value = acquisition_page(exam)
    links = {
        exam.rule_notice_url: [
            (d.title + ".pdf", d.source_url) for d in exam.documents if d.document_id == "rule"
        ],
        exam.review_notice_url: [
            (f.subject_name_raw + ".pdf", f.source_url)
            for f in exam.files
            if f.file_type == "corrected_answer"
        ]
        + [(d.title + ".pdf", d.source_url) for d in exam.documents if d.document_id == "vacancy"]
        + [("初試錄取與備取名單.pdf", "private-results"), ("複試場地公告.pdf", "venue-notice")],
    }
    client.pdf_downloads.side_effect = links.__getitem__
    client.download_file.side_effect = lambda u: DownloadedFile(
        payloads[u], "application/pdf", "native.pdf"
    )
    return TaipeiJuniorRecruitProvider(client), exam, payloads, links


def test_shared_correction_bytes_and_reference_documents_are_acquired_once(tmp_path, monkeypatch):
    provider, exam, payloads, _links = fake_provider(monkeypatch)
    pages, catalog, failures = sync_exam_pages(
        provider,
        [(exam.source_exam_id, exam.year_roc + 1911)],
        MirrorStore(tmp_path / "mirror"),
        [],
        "",
        download_attachments=False,
    )
    assert not failures and not catalog.review_queue
    assert len(catalog.papers) == 57 and len(pages[0].attachments) == 2
    assert len({p.storage_key for p in catalog.papers}) == 39
    assert provider.client.download_file.call_count == len(payloads) == 41
    assert {c.args[0] for c in provider.client.download_file.call_args_list} == payloads.keys()
    corrections = [p for p in catalog.papers if p.file_type == "corrected_answer"]
    assert len(corrections) == 19 and len({p.storage_key for p in corrections}) == 1
    validate_public_papers(catalog.papers, complete_history=True)


@pytest.mark.parametrize(
    "mutation", ["missing-review", "new-supplement", "changed-rule", "changed-key"]
)
def test_changed_notice_or_native_payload_leaves_mirror_untouched(tmp_path, monkeypatch, mutation):
    provider, exam, payloads, links = fake_provider(monkeypatch)
    if mutation == "missing-review":
        links[exam.review_notice_url].pop(0)
    if mutation == "new-supplement":
        links[exam.review_notice_url].append(("補充說明.pdf", "unreviewed"))
    if mutation == "changed-rule":
        payloads[exam.documents[0].source_url] = b"%PDF-1.7 changed"
    if mutation == "changed-key":
        payloads[exam.files[0].source_url] = b"%PDF-1.7 changed"
    pages, catalog, failures = sync_exam_pages(
        provider,
        [(exam.source_exam_id, exam.year_roc + 1911)],
        MirrorStore(tmp_path / "mirror"),
        [],
        "",
    )
    assert not pages and not catalog.papers
    assert len(failures) == 1 and failures[0].stage == "fetch"
    assert not list((tmp_path / "mirror").rglob("*.pdf"))


def test_legacy_targets_require_the_complete_exact_generic_context():
    rows = reviewed_catalog()
    exam = annual_exams()[0]
    legacy = normalize(
        acquisition_page(exam), {f.source_url: f.checksum for f in exam.files}
    ).papers
    assert len(legacy) == 28
    for previous in legacy:
        targets = legacy_category_targets(previous, rows)
        assert len(targets) == 1
        assert targets[0].checksum == previous.checksum
        assert targets[0].download_url_source == previous.download_url_source
        assert targets[0].file_type == previous.file_type
        assert not legacy_category_targets(replace(previous, category_code="114"), rows)
        assert not legacy_category_targets(
            replace(previous, schema_version=3, source_material=targets[0].source_material), rows
        )
    entries = [
        {"id": str(i), "record_type": "paper", "source_record": vars(p)}
        for i, p in enumerate(legacy)
    ]
    assert len(reviewed_native_revisions(entries)) == 28
    entries[0]["source_record"] = {**entries[0]["source_record"], "category_raw": "其他甄選"}
    assert len(reviewed_native_revisions(entries)) == 27


def test_public_sections_are_backend_facts_and_native_filenames_are_preserved():
    rows = [p for p in reviewed_catalog() if p.track_id == "taipei-junior-english"]
    aliases, subjects = derive_public_metadata(
        rows, bundle_id=rows[0].bundle_id, canonical_name=rows[0].canonical_name
    )
    assert subjects == ["教育專業科目", "英語科專業科目"]
    assert "筆試釋疑答案修訂彙整表" in aliases
    assert "英文" in aliases
    assert all(publication_paper_filename_subject(p) == p.subject_name_raw for p in rows)


@pytest.mark.parametrize(
    "mutation",
    [
        "foreign-notice",
        "foreign-download",
        "missing-pair",
        "duplicate-post",
        "wrong-subject",
        "missing-rule",
    ],
)
def test_native_mapping_rejects_incomplete_or_foreign_evidence(tmp_path, mutation):
    doc = json.loads(native_programmes_path(ROOT).read_text())
    exam = doc["events"][0]
    if mutation == "foreign-notice":
        exam["rule_notice_url"] = exam["rule_notice_url"].replace(
            "www.doe.gov.taipei", "other.example"
        )
    if mutation == "foreign-download":
        exam["files"][0]["source_url"] = exam["files"][0]["source_url"].replace(
            "www-ws.gov.taipei", "other.example"
        )
    if mutation == "missing-pair":
        exam["files"].pop(0)
    if mutation == "duplicate-post":
        exam["jobs"].append(copy.deepcopy(exam["jobs"][0]))
    if mutation == "wrong-subject":
        exam["jobs"][0]["source_subject_name"] = "另一題本"
    if mutation == "missing-rule":
        exam["documents"] = [d for d in exam["documents"] if d["document_id"] != "rule"]
    path = tmp_path / "native.json"
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="Taipei junior"):
        read_annual_exams(path)


@pytest.mark.repo_data
def test_persisted_native_context_has_source_or_retained_history_anchors():
    assert validate_native_programmes(ROOT) == 115
