"""Each normalized file must use its own native bytes and evidence context."""

from copy import deepcopy
from unittest.mock import patch

import pytest

from app.classification import classify_paper
from app.models import ParsedPaper
from app.normalizer import normalize_papers, renormalize_catalog
from app.providers.sfi_cert import evidence
from tests.test_sfi_native import source_pair


def sfi_paper(monkeypatch):
    _, facts, _ = source_pair()
    monkeypatch.setattr(evidence, "native_papers", lambda: tuple(facts))
    question = facts[0]
    paper = ParsedPaper(
        question.programme.label,
        question.category_code,
        "main",
        "、".join(question.subjects),
        {f.file_type: f.source_urls[0] for f in facts},
        {
            f.file_type: {
                "storage_key": f"providers/sfi_cert/native/{f.file_type}.pdf",
                "checksum": f.checksum,
            }
            for f in facts
        },
        source_material=question.material,
    )
    return paper, facts


def normalize_sfi(paper, facts, metadata=None):
    q = facts[0]
    return normalize_papers(
        q.source_exam_id,
        q.year_roc + 1911,
        f"{q.year_roc}年第{q.round_no}次 {q.programme.label}",
        [paper],
        [],
        "",
        metadata or {},
        provider_id="sfi_cert",
        source_material=q.material,
    )


def test_paired_answer_cites_its_own_native_fact_and_replay_is_stable(monkeypatch):
    paper, facts = sfi_paper(monkeypatch)
    before = deepcopy(paper)
    result = normalize_sfi(paper, facts)
    assert not result.review_queue
    assert {p.file_type: p.classification_reason.split(": ")[-1] for p in result.papers} == {
        f.file_type: f.id for f in facts
    }
    assert renormalize_catalog(result, [], collect_reviews=True) == result
    assert paper == before


@pytest.mark.parametrize("location", ["inline", "fallback"])
@pytest.mark.parametrize("answer_bytes", ["missing", "unreviewed"])
@pytest.mark.parametrize("answer_first", [False, True])
def test_paired_question_cannot_approve_an_unreviewed_answer(
    monkeypatch, location, answer_bytes, answer_first
):
    paper, facts = sfi_paper(monkeypatch)
    paper.mirror_files["answer"]["checksum"] = "" if answer_bytes == "missing" else "0" * 64
    if answer_first:
        paper.files = dict(reversed(list(paper.files.items())))
    metadata = {}
    if location == "fallback":
        metadata = {
            (paper.category_code, paper.subject_code, role): value
            for role, value in paper.mirror_files.items()
        }
        paper.mirror_files = {}
    before = deepcopy(paper)
    result = normalize_sfi(paper, facts, metadata)
    by_role = {p.file_type: p for p in result.papers}
    assert by_role["question"].classification_confidence == "high"
    assert by_role["answer"].classification_confidence == "review"
    assert by_role["answer"].track_id == "sfi-unresolved"
    assert len(result.review_queue) == 1
    assert renormalize_catalog(result, [], collect_reviews=True) == result
    assert paper == before


def test_shared_normalizer_passes_each_role_source_to_the_classifier():
    paper = ParsedPaper(
        "學科能力測驗",
        "gsat",
        "english",
        "英文",
        {r: f"https://official.example/{r}" for r in ["question", "answer", "audio"]},
        {r: {"checksum": str(i) * 64} for i, r in enumerate(["question", "answer", "audio"], 1)},
    )
    before = deepcopy(paper)
    with patch("app.normalizer.classify_paper", wraps=classify_paper) as classify:
        result = normalize_papers(
            "gsat-115", 2026, "115學年度學科能力測驗", [paper], [], "", {}, provider_id="ceec_gsat"
        )
    assert [
        (c.kwargs["source_url"], c.kwargs["source_checksum"]) for c in classify.call_args_list
    ] == [(url, paper.mirror_files[role]["checksum"]) for role, url in paper.files.items()]
    assert [p.file_type for p in result.papers] == list(paper.files)
    assert paper == before
