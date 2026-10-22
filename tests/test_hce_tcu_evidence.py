"""Domestic selection rules must follow native bytes, roles and annual context."""

import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock
from urllib.error import URLError

import pytest

from app.bundler import validate_public_materials
from app.models import ParsedPaper, SourceExamPage
from app.normalizer import normalize_papers, renormalize_catalog
from app.paths import provider_paths
from app.providers.base import DownloadedFile
from app.providers.hce_tcu.evidence import (
    annual_exams, annual_papers_path, read_annual_papers, separate_file_roles,
    validate_annual_papers,
)
from app.providers.hce_tcu.provider import HceTcuProvider
from app.publisher import write_provider_state
from app.source_revisions import load_source_revisions
from app.storage import MirrorStore
from app.sync import sync_exam_pages

ROOT = Path(__file__).resolve().parents[1]


def native_page(exam):
    grouped = {}
    for paper in exam.papers:
        item = grouped.setdefault(paper.subject_code, ParsedPaper(
            exam.category_raw, exam.category_code, paper.subject_code,
            paper.subject_name_raw, {}, {},
        ))
        item.files[paper.file_type] = paper.source_url
        item.mirror_files[paper.file_type] = {"checksum": paper.checksum}
    return SourceExamPage(
        exam.source_exam_id, exam.year_roc + 1911, exam.year_roc, exam.exam_name_raw,
        [], list(grouped.values()), provider_id="hce_tcu",
    )


def normalize(page):
    return normalize_papers(
        page.source_exam_id, page.year_ad, page.exam_name_raw, page.papers, [], "", {},
        provider_id="hce_tcu",
    )


def test_native_domestic_rules_keep_identity_and_original_file_roles():
    page = separate_file_roles(native_page(annual_exams()[0]))
    catalog = normalize(page)
    assert len(page.papers) == len(catalog.papers) == 9
    assert not catalog.review_queue
    assert {paper.bundle_id for paper in catalog.papers} == {
        "hce-tcu-post-baccalaureate-medical-not-applicable-hce-tcu"
    }
    assert {paper.classification_confidence for paper in catalog.papers} == {"high"}
    assert {paper.stage_id for paper in catalog.papers} == {"not-applicable"}
    assert {paper.level_id for paper in catalog.papers} == {"not-applicable"}
    assert {paper.source_material.date.year_ad for paper in catalog.papers} == {2026}
    assert {paper.schema_version for paper in catalog.papers} == {3}
    assert sum(paper.file_type == "question" for paper in catalog.papers) == 4
    assert sum(paper.file_type == "answer" for paper in catalog.papers) == 4
    assert sum(paper.file_type == "answer_clarification" for paper in catalog.papers) == 1
    assert renormalize_catalog(catalog, []).papers == catalog.papers
    validate_public_materials(catalog.papers)


def test_single_answer_does_not_borrow_question_checksum_from_shared_mirror_metadata():
    page = separate_file_roles(native_page(annual_exams()[0]))
    answer = next(paper for paper in page.papers if "answer" in paper.files)
    question = next(paper for paper in page.papers if "question" in paper.files)
    answer.mirror_files = {}
    catalog = normalize_papers(
        page.source_exam_id, page.year_ad, page.exam_name_raw, [answer], [], "",
        {
            (question.category_code, question.subject_code, "question"):
                question.mirror_files["question"],
            (answer.category_code, answer.subject_code, "answer"):
                {"checksum": annual_exams()[0].papers[1].checksum},
        }, provider_id="hce_tcu",
    )
    assert catalog.papers[0].classification_confidence == "high"
    assert catalog.papers[0].file_type == "answer"


@pytest.mark.parametrize("change", [
    "checksum", "URL", "role", "event", "year", "category", "subject", "label", "route",
])
def test_unreviewed_native_context_cannot_inherit_domestic_rules(change):
    page = separate_file_roles(native_page(annual_exams()[0]))
    paper = page.papers[0]
    if change == "checksum":
        paper.mirror_files["question"]["checksum"] = "a" * 64
    elif change == "URL":
        paper.files["question"] += "?replacement=1"
    elif change == "role":
        paper.files = {"answer": paper.files["question"]}
        paper.mirror_files = {"answer": paper.mirror_files["question"]}
    elif change == "event":
        page.source_exam_id = "hce-tcu-foreign-115"
    elif change == "year":
        page.year_ad += 1
    elif change == "category":
        paper.category_code = "another-programme"
    elif change == "subject":
        paper.subject_code = "foreign-mandarin"
    elif change == "label":
        paper.subject_name_raw = "普通化學"
    elif change == "route":
        page.exam_name_raw = "115外國學生申請入學"
    page.papers = [paper]
    catalog = normalize(page)
    assert catalog.review_queue
    assert catalog.papers[0].classification_confidence == "review"
    assert catalog.papers[0].stage_id == "unknown"
    with pytest.raises(ValueError, match="TCU publication"):
        validate_public_materials(catalog.papers)


@pytest.mark.parametrize("role", ["question", "answer", "answer_clarification"])
def test_publication_cannot_lose_a_known_original_or_clarification(role):
    catalog = normalize(separate_file_roles(native_page(annual_exams()[0])))
    missing = next(paper for paper in catalog.papers if paper.file_type == role)
    with pytest.raises(ValueError, match="cannot omit"):
        validate_public_materials(paper for paper in catalog.papers if paper is not missing)
    with pytest.raises(ValueError, match="Duplicate TCU"):
        validate_public_materials([*catalog.papers, missing])


def test_annual_evidence_rejects_conflicts_and_accepts_explicit_native_revisions(tmp_path):
    original = json.loads(annual_papers_path(ROOT).read_text())
    target = tmp_path / "annual.json"
    for field, value in (("selection_model", "two-stage"), ("guide_checksum", "bad"),
                         ("guide_url", "https://example.com/foreign.pdf")):
        document = copy.deepcopy(original)
        document["exams"][0][field] = value
        target.write_text(json.dumps(document))
        with pytest.raises(ValueError, match="annual guide"):
            read_annual_papers(target)
    document = copy.deepcopy(original)
    exam = document["exams"][0]
    exam["papers"].append(dict(exam["papers"][1], checksum="a" * 64))
    target.write_text(json.dumps(document))
    assert len(read_annual_papers(target)[0].papers) == 10
    exam["papers"].append(exam["papers"][0])
    target.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="duplicate TCU native"):
        read_annual_papers(target)


def test_normal_sync_refreshes_changed_keys_and_retains_recoverable_originals(tmp_path, monkeypatch):
    original = annual_exams()[0]
    payloads = {paper.source_url: b"%PDF-1.7 fixture " + paper.source_url.encode()
                for paper in original.papers}
    exam = replace(original, papers=tuple(
        replace(paper, checksum=hashlib.sha256(payloads[paper.source_url]).hexdigest())
        for paper in original.papers
    ))
    monkeypatch.setattr("app.providers.hce_tcu.evidence.annual_exams", lambda: (exam,))
    client = Mock()
    client.fetch_exam_page.side_effect = lambda *_: copy.deepcopy(native_page(exam))
    client.download_file.side_effect = lambda url: DownloadedFile(payloads[url], "application/pdf", "native.pdf")
    provider = HceTcuProvider(client)
    paths = provider_paths(tmp_path, "hce_tcu")
    store = MirrorStore(tmp_path / "mirror")
    first = sync_exam_pages(provider, [(exam.source_exam_id, 2026)], store, [], "")
    assert not first[2] and not first[1].review_queue
    assert client.download_file.call_count == 9
    write_provider_state(paths, first[0], first[1], [], [], None)
    client.download_file.reset_mock()
    second = sync_exam_pages(provider, [(exam.source_exam_id, 2026)], store, [], "", previous_pages=first[0])
    assert second == first
    assert client.download_file.call_count == 9
    answer = next(paper for paper in first[1].papers if paper.file_type == "answer")
    payloads[answer.download_url_source] = b"%PDF-1.7 changed native key"
    changed = sync_exam_pages(provider, [(exam.source_exam_id, 2026)], store, [], "", previous_pages=second[0])
    assert not changed[2] and changed[1].review_queue
    revised = next(paper for paper in changed[1].papers if paper.paper_code == answer.paper_code)
    assert revised.classification_confidence == "review"
    assert revised.source_material.kind == "unknown"
    assert revised.checksum != answer.checksum
    write_provider_state(paths, changed[0], changed[1], [], [], None)
    journal = load_source_revisions(paths)
    assert len(journal) == 1
    assert journal[0]["source_record"]["checksum"] == answer.checksum
    old_bytes = (tmp_path / "mirror" / journal[0]["blob_storage_key"]).read_bytes()
    assert hashlib.sha256(old_bytes).hexdigest() == answer.checksum
    with pytest.raises(ValueError, match="TCU publication"):
        validate_public_materials(changed[1].papers)
    # A failed refresh keeps its prior bytes and reports failure, rather than
    # converting a transient source outage into lost historical coverage.
    client.download_file.side_effect = URLError("source unavailable")
    monkeypatch.setattr("app.sync.retry_network", lambda operation: operation())
    failed = sync_exam_pages(provider, [(exam.source_exam_id, 2026)], store, [], "", previous_pages=changed[0])
    assert len(failed[2]) == 9
    assert failed[1].papers == changed[1].papers
    # Evidence remains anchored in the retired native key after replacement.
    target = annual_papers_path(tmp_path)
    target.parent.mkdir(parents=True)
    document = json.loads(annual_papers_path(ROOT).read_text())
    for row, fact in zip(document["exams"][0]["papers"], exam.papers, strict=True):
        row["checksum"] = fact.checksum
    target.write_text(json.dumps(document))
    assert validate_annual_papers(tmp_path, verify_mirror=True) == 9
    document["exams"][0]["papers"][0]["checksum"] = "b" * 64
    target.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="retained source anchor"):
        validate_annual_papers(tmp_path)
