import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from app.models import NormalizedCatalog, ParsedPaper
from app.normalizer import normalize_papers, renormalize_catalog
from app.taipower_file_roles import (
    read_taipower_corrections,
    taipower_corrections,
    taipower_corrections_path,
    taipower_file_role,
    validate_taipower_corrections,
)

ROOT = Path(__file__).resolve().parents[1]


def arguments(fact):
    return {
        "year_roc": int(fact.category_code),
        **{
            key: value
            for key, value in asdict(fact).items()
            if key not in {"native_page", "reviewed_at", "reason"}
        },
    }


@pytest.mark.parametrize("fact", taipower_corrections())
def test_reviewed_original_corrections_receive_the_correct_role(fact):
    assert taipower_file_role("answer", **arguments(fact)) == "corrected_answer"
    assert taipower_file_role("question", **arguments(fact)) == "question"


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_exam_id", "taipower-recruit-99"),
        ("year_roc", 99),
        ("category_code", "99"),
        ("subject_code", "hiring-99"),
        ("title", "修正答案"),
        ("source_url", "https://www.taipower.com.tw/media/changed/answer.pdf"),
        ("checksum", "0" * 64),
    ],
)
def test_changed_context_or_bytes_cannot_inherit_a_reviewed_correction(field, value):
    values = {**arguments(taipower_corrections()[0]), field: value}
    if field == "checksum":
        with pytest.raises(ValueError, match="changed or unreviewed"):
            taipower_file_role("answer", **values)
    else:
        assert taipower_file_role("answer", **values) == "answer"
    with pytest.raises(ValueError, match="changed or unreviewed"):
        taipower_file_role("corrected_answer", **values)


def test_correction_words_in_a_question_do_not_establish_answer_role():
    values = {
        **arguments(taipower_corrections()[0]),
        "title": "114年度新進僱用人員甄試答案_會計審計法規、採購法概要",
    }
    assert taipower_file_role("answer", **values) == "answer"


def test_fresh_and_retained_normalization_preserve_original_mirror_and_listing_role():
    fact = taipower_corrections()[0]
    original = ParsedPaper(
        category_raw="台電新進僱用人員甄試",
        category_code=fact.category_code,
        subject_code=fact.subject_code,
        subject_name_raw=fact.title,
        files={"answer": fact.source_url},
        mirror_files={
            "answer": {
                "storage_key": "providers/taipower_recruit/original/answer.pdf",
                "checksum": fact.checksum,
            }
        },
    )
    current = normalize_papers(
        source_exam_id=fact.source_exam_id,
        year_ad=int(fact.category_code) + 1911,
        exam_name_raw="台電新進僱用人員甄試",
        papers=[original],
        alias_rules=[],
        mirror_base_url="",
        mirror_metadata={},
        provider_id="taipower_recruit",
    )
    corrected = current.papers[0]
    assert corrected.file_type == "corrected_answer"
    assert corrected.paper_code.endswith("-corrected_answer")
    assert corrected.storage_key == original.mirror_files["answer"]["storage_key"]
    assert original.files == {"answer": fact.source_url}
    previous = replace(
        corrected, file_type="answer", paper_code=f"{fact.category_code}-{fact.subject_code}-answer"
    )
    replayed = renormalize_catalog(NormalizedCatalog([previous], []), [])
    assert replayed.papers == current.papers
    assert renormalize_catalog(replayed, []).papers == replayed.papers


def test_duplicate_evidence_is_rejected(tmp_path):
    fact = asdict(taipower_corrections()[0])
    path = tmp_path / "corrections.json"
    path.write_text(
        json.dumps({"schema_version": 1, "provider_id": "taipower_recruit", "facts": [fact, fact]})
    )
    with pytest.raises(ValueError, match="Duplicate"):
        read_taipower_corrections(path)


@pytest.mark.repo_data
def test_reviewed_correction_anchors_are_retained():
    assert validate_taipower_corrections(ROOT) == len(
        read_taipower_corrections(taipower_corrections_path(ROOT))
    )
