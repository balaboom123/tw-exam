import base64
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from app.models import NormalizedCatalog, ParsedPaper
from app.normalizer import normalize_papers, renormalize_catalog
from app.providers.tcte_tve.evidence import (
    corrections,
    corrections_path,
    file_role,
    read_corrections,
    validate_corrections,
)

ROOT = Path(__file__).resolve().parents[1]


def arguments(fact):
    return {
        key: value
        for key, value in asdict(fact).items()
        if key not in {"native_page", "reviewed_at", "reason"}
    }


@pytest.mark.parametrize("fact", corrections())
def test_reviewed_original_corrections_receive_the_correct_role(fact):
    assert file_role("answer", **arguments(fact)) == "corrected_answer"
    assert file_role("question", **arguments(fact)) == "question"


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_exam_id", "tcte-tve-98"),
        ("year_roc", 98),
        ("category_code", "technical-common"),
        ("subject_code", "different-subject"),
        ("title", "修正答案"),
        (
            "source_url",
            "https://web1.tcte.edu.tw/EXAM/099_4y/downloader.php?obj=changed",
        ),
        ("checksum", "0" * 64),
    ],
)
def test_changed_context_or_bytes_cannot_inherit_a_reviewed_correction(field, value):
    values = {**arguments(corrections()[0]), field: value}
    if field == "checksum":
        with pytest.raises(ValueError, match="unreviewed"):
            file_role("answer", **values)
    else:
        assert file_role("answer", **values) == "answer"
    with pytest.raises(ValueError, match="unreviewed"):
        file_role("corrected_answer", **values)


def test_correction_words_without_exact_evidence_do_not_establish_answer_role():
    values = {
        **arguments(corrections()[0]),
        "title": "參考答案修正",
    }
    assert file_role("answer", **values) == "answer"


@pytest.mark.parametrize("fact", corrections())
def test_fresh_and_retained_normalization_preserve_original_mirror_and_listing_role(fact):
    original = ParsedPaper(
        category_raw="四技二專統一入學測驗",
        category_code=fact.category_code,
        subject_code=fact.subject_code,
        subject_name_raw=fact.title,
        files={"answer": fact.source_url},
        mirror_files={
            "answer": {
                "storage_key": "providers/tcte_tve/original/answer.pdf",
                "checksum": fact.checksum,
            }
        },
    )
    current = normalize_papers(
        source_exam_id=fact.source_exam_id,
        year_ad=fact.year_roc + 1911,
        exam_name_raw="四技二專統一入學測驗",
        papers=[original],
        alias_rules=[],
        mirror_base_url="",
        mirror_metadata={},
        provider_id="tcte_tve",
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
    fact = asdict(corrections()[0])
    path = tmp_path / "corrections.json"
    path.write_text(
        json.dumps({"schema_version": 1, "provider_id": "tcte_tve", "facts": [fact, fact]})
    )
    with pytest.raises(ValueError, match="Duplicate"):
        read_corrections(path)


@pytest.mark.repo_data
def test_reviewed_correction_anchors_are_retained():
    assert validate_corrections(ROOT) == len(read_corrections(corrections_path(ROOT)))


def test_conflicting_checksums_at_one_source_slot_require_a_new_review(tmp_path):
    fact = asdict(corrections()[0])
    path = tmp_path / "corrections.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provider_id": "tcte_tve",
                "facts": [fact, {**fact, "checksum": "0" * 64}],
            }
        )
    )
    with pytest.raises(ValueError, match="Duplicate"):
        read_corrections(path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("year_roc", True),
        ("year_roc", 98),
        ("native_page", True),
        ("source_url", "https://example.com/answer.pdf"),
        (
            "source_url",
            "https://web1.tcte.edu.tw/EXAM/099_4y/downloader.php?obj=%%%",
        ),
        ("checksum", "unverified"),
        ("reason", ""),
    ],
)
def test_invalid_review_context_is_rejected(tmp_path, field, value):
    fact = {**asdict(corrections()[0]), field: value}
    path = tmp_path / "corrections.json"
    path.write_text(json.dumps({"schema_version": 1, "provider_id": "tcte_tve", "facts": [fact]}))
    with pytest.raises(ValueError):
        read_corrections(path)


@pytest.mark.parametrize("fact", corrections())
def test_native_download_context_is_checked_beyond_the_official_hostname(tmp_path, fact):
    wrong_form = "b" if fact.subject_code != "math-b" else "a"
    wrong_filename = f"{fact.year_roc}-4y-00-m{wrong_form}-standard.pdf"
    wrong_locator = base64.b64encode(wrong_filename.encode()).decode()
    changed = {
        **asdict(fact),
        "source_url": fact.source_url.split("?obj=")[0] + "?obj=" + wrong_locator,
    }
    path = tmp_path / "corrections.json"
    path.write_text(
        json.dumps({"schema_version": 1, "provider_id": "tcte_tve", "facts": [changed]})
    )
    with pytest.raises(ValueError):
        read_corrections(path)


def test_scoring_word_alone_does_not_establish_an_answer_change():
    values = {**arguments(corrections()[0]), "title": "送分"}
    assert file_role("answer", **values) == "answer"
