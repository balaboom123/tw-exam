"""Native combined booklets and mislabelled answer endpoints preserve every source."""

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from app.bundler import _resolve_arcnames
from app.models import NormalizedCatalog, ParsedPaper
from app.normalizer import normalize_papers, renormalize_catalog
from app.paths import provider_paths
from app.state import load_provider_state

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def booklets():
    _, cat, _ = load_provider_state(provider_paths(ROOT, "tcte_tve"))
    return {
        year: [
            p
            for p in cat.papers
            if p.year_roc == year
            and p.category_code == "common"
            and p.file_type == "question"
            and p.subject_code in {"math-a", "math-b", "math-c"}
        ]
        for year in [92, 93]
    }


@pytest.mark.parametrize("year", [92, 93])
def test_native_combined_booklet_is_stored_once_with_every_form_reference(booklets, year):
    papers = booklets[year]
    assert len(papers) == 3
    names = _resolve_arcnames(papers)
    assert len(set(names)) == 1
    assert "math-abc" in names[0]
    assert "數學(A、B、C)" in names[0]
    assert len(papers) == 3 and {p.subject_code for p in papers} == {"math-a", "math-b", "math-c"}


def question_fact():
    return json.loads(
        (ROOT / "catalog/mappings/moex/native-question-references-v1.json").read_text()
    )["facts"][0]


def parsed_answer(fact):
    return ParsedPaper(
        "正駕駛",
        fact["category_code"],
        fact["subject_code"],
        fact["title"],
        {"answer": fact["source_url"]},
        {"answer": {"storage_key": "84/084140/013/c131/answer.pdf", "checksum": fact["checksum"]}},
    )


def normalize_answer(paper, fact):
    return normalize_papers(
        fact["source_exam_id"],
        fact["year_roc"] + 1911,
        "084年特種考試第二次航海人員引水人考試",
        [paper],
        [],
        "",
        {},
        provider_id="moex",
    )


def test_native_essay_answer_endpoint_becomes_a_question_without_rewriting_acquisition():
    fact = question_fact()
    paper = parsed_answer(fact)
    original = deepcopy(paper)
    fresh = normalize_answer(paper, fact)
    assert fresh.papers[0].file_type == "question"
    assert fresh.papers[0].paper_code == "013-c131-question"
    assert fresh.papers[0].storage_key == paper.mirror_files["answer"]["storage_key"]
    previous = replace(fresh.papers[0], file_type="answer", paper_code="013-c131-answer")
    assert renormalize_catalog(NormalizedCatalog([previous], []), []).papers == fresh.papers
    assert renormalize_catalog(fresh, []).papers == fresh.papers
    assert paper == original


def test_changed_native_essay_bytes_require_a_new_review():
    fact = question_fact()
    paper = parsed_answer(fact)
    paper.mirror_files["answer"]["checksum"] = "0" * 64
    with pytest.raises(ValueError, match="unreviewed"):
        normalize_answer(paper, fact)


@pytest.mark.parametrize(
    "change",
    [
        {"checksum": "0" * 64},
        {"year_roc": 94},
        {"source_exam_id": "tcte-tve-94"},
        {"category_code": "other"},
        {"category_raw": "other"},
        {"exam_name_raw": "other"},
        {"file_type": "answer"},
        {"subject_name_raw": "數學合訂本"},
        {"download_url_source": "https://web1.tcte.edu.tw/unreviewed.pdf"},
    ],
)
def test_unreviewed_booklet_context_does_not_share_subject_payloads(booklets, change):
    papers = [
        replace(
            p,
            **{
                k: (f"{v} {p.subject_code}" if k == "subject_name_raw" else v)
                for k, v in change.items()
            },
        )
        for p in booklets[92]
    ]
    names = _resolve_arcnames(papers)
    assert len(set(names)) == 3
    assert all("math-abc" not in name for name in names)


def test_ordinary_math_forms_and_distinct_answers_are_not_merged(booklets):
    papers = [replace(p, year_roc=94, source_exam_id="tcte-tve-94") for p in booklets[92]]
    assert len(set(_resolve_arcnames(papers))) == 3
    answers = [replace(p, file_type="answer") for p in booklets[92]]
    assert len(set(_resolve_arcnames(answers))) == 3


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "year",
        "host",
        "partial_pages",
        "overlap",
        "missing_form",
        "boolean_page",
        "empty_reason",
    ],
)
def test_invalid_native_booklet_facts_fail_closed(tmp_path, mutation):
    from app.providers.tcte_tve.booklets import read_shared_booklets, shared_booklets_path

    data = json.loads(shared_booklets_path(ROOT).read_text())
    fact = data["facts"][0]
    if mutation == "duplicate":
        data["facts"].append(deepcopy(fact))
    elif mutation == "year":
        fact["year_roc"] = True
    elif mutation == "host":
        fact["source_url"] = "https://example.org/booklet.pdf"
    elif mutation == "partial_pages":
        fact["inspected_pages"].pop()
    elif mutation == "overlap":
        fact["members"][1]["first_page"] = 4
    elif mutation == "missing_form":
        fact["members"].pop()
    elif mutation == "boolean_page":
        fact["members"][0]["first_page"] = True
    else:
        fact["reason"] = ""
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        read_shared_booklets(path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_exam_id", "084141"),
        ("year_roc", 85),
        ("category_code", "015"),
        ("subject_code", "c132"),
        ("title", "other"),
        ("source_url", "https://wwwq.moex.gov.tw/other"),
    ],
)
def test_other_answer_contexts_do_not_inherit_native_question_role(field, value):
    from app.providers.moex.file_roles import file_role

    fact = question_fact()
    args = {k: v for k, v in fact.items() if k not in {"native_page", "reviewed_at", "reason"}}
    args[field] = value
    assert file_role("answer", **args) == "answer"
    assert file_role("question", **args) == "question"


@pytest.mark.parametrize("mutation", ["duplicate", "event", "host", "role", "checksum"])
def test_invalid_moex_native_question_evidence_is_rejected(tmp_path, mutation):
    from app.providers.moex.file_roles import read_question_references

    data = {"schema_version": 1, "provider_id": "moex", "facts": [question_fact()]}
    fact = data["facts"][0]
    if mutation == "duplicate":
        data["facts"].append(deepcopy(fact))
    elif mutation == "event":
        fact["source_exam_id"] = "085140"
    elif mutation == "host":
        fact["source_url"] = fact["source_url"].replace("wwwq.moex.gov.tw", "example.org")
    elif mutation == "role":
        fact["source_url"] = fact["source_url"].replace("t=S", "t=Q")
    else:
        fact["checksum"] = "unverified"
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        read_question_references(path)


@pytest.mark.repo_data
def test_native_booklet_and_question_role_anchors_are_byte_verified():
    from app.providers.moex.file_roles import validate_question_references
    from app.providers.tcte_tve.booklets import validate_shared_booklets

    assert validate_shared_booklets(ROOT, verify_mirror=True) == 2
    assert validate_question_references(ROOT, verify_mirror=True) == 1


def test_local_publication_generates_repository_urls_without_network_or_subprocesses(
    tmp_path, monkeypatch
):
    import socket
    import subprocess
    from unittest.mock import Mock

    from app import publisher
    from tests.test_retained_downloads import migration

    site, old, _ = migration(tmp_path, monkeypatch)
    network = Mock(side_effect=AssertionError("Local publication attempted a network connection"))
    external = Mock(side_effect=AssertionError("Local publication attempted a subprocess"))
    monkeypatch.setattr(socket.socket, "connect", network)
    monkeypatch.setattr(subprocess, "Popen", external)
    _, bundles = publisher.publish_site(
        tmp_path,
        site_id="default",
        repository="balaboom123/tw-exam",
        retain_existing_download_ids={old.bundle_id},
    )
    assert len(bundles) == 2
    assert all((site.bundle_dir / b.asset_name).is_file() for b in bundles)
    assert all(
        b.download_url.startswith("https://github.com/balaboom123/tw-exam/releases/download/")
        for b in bundles
    )
    assert site.bundles_path.is_file() and site.frontend_bundles_path.is_file()
    network.assert_not_called()
    external.assert_not_called()
