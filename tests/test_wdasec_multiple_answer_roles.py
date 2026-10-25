"""Printed multiple answers change the file role without changing exam meaning."""

from dataclasses import asdict, replace

import pytest

from app.models import NormalizedCatalog, ParsedPaper
from app.normalizer import normalize_papers, renormalize_catalog
from app.providers.wdasec_skill import papers as owner
from tests.test_wdasec_forms import normalized

# Native review boundaries: historical single-choice covers, assisted masters,
# wrapped accounting stems and later first-multiple-answer numbering.
BOUNDARIES = {
    "97-surveying-a": "27d3aaeb75352176934af2c7d078b1e0947a52431d4e9b5d7e1a1d711ac8a027",
    "97-generic-cooking": "71b930a0d78666d24f3e9b843fa78a635035666c7722b48e88b7b298663ad8d2",
    "97-generic-accounting": "327701774bd67e07c870b7410b53d10ebaabff5c272e34f7979f24f8ac498c19",
    "97-numerical-milling": "82bd882a960d6e9264d303918027f57363574f153a0eaa1c9431520c051128d2",
    "115-assisted-construction": "661cc1f427d6cb0fc7df4555c29e532bd12aad1b31c87ae5a2c6e80b7f595026",
    "115-assisted-cooking": "d3dc1bc3cb321a1c39a6f6819a1286379767d4fe3394038cdc646fbc22d361c0",
    "105-manual-accounting": "5f95c409d5c088257b8be6bccac9d772e5c2a0802d5602b9edf22f2c331e595e",
    "105-information-accounting": (
        "5898c03be76a067aefdc451a593653249570b402f17a44d5703c10bb25446f98"
    ),
}


@pytest.mark.parametrize("checksum", BOUNDARIES.values(), ids=BOUNDARIES)
def test_native_multiple_answers_only_correct_role_and_public_filename(checksum, monkeypatch):
    native = next(p for p in owner.native_papers() if p.checksum == checksum)
    assert native.reviewed_role is not None
    assert native.reviewed_role.question_count == 80
    assert "multiple accepted" in native.reviewed_role.evidence
    original = normalized(native)
    monkeypatch.setattr(owner, "_index", lambda: {native.key: replace(native, reviewed_role=None)})
    (before,) = renormalize_catalog(NormalizedCatalog([original], []), []).papers
    monkeypatch.setattr(owner, "_index", lambda: {native.key: native})
    (after,) = renormalize_catalog(NormalizedCatalog([original], []), []).papers
    changed = {k for k in asdict(before) if asdict(before)[k] != asdict(after)[k]}
    assert changed == {"file_type", "paper_code"}
    assert before.file_type == "question" and after.file_type == "question_answer"
    assert after.source_material is None
    assert after.storage_key == before.storage_key
    if "外語翻譯" in native.native_header:
        assert native.form == "foreign-assistance-master"
        assert "paper-form-foreign-assistance-master" in after.variant_ids
    parsed = ParsedPaper(
        category_raw=original.category_raw,
        category_code=native.category_code,
        subject_code=native.subject_code,
        subject_name_raw=native.subject_name_raw,
        files={"question": native.source_url},
        mirror_files={"question": {"checksum": checksum}},
    )
    (acquired,) = normalize_papers(
        native.source_exam_id,
        native.year_roc + 1911,
        native.exam_name_raw,
        [parsed],
        [],
        "",
        {},
        provider_id="wdasec_skill",
    ).papers
    assert acquired.file_type == after.file_type
    assert acquired.bundle_id == after.bundle_id
    assert acquired.source_material is None


@pytest.mark.parametrize(
    "change",
    [
        {"year_roc": 899},
        {"source_exam_id": "209901010001"},
        {"exam_name_raw": "New unreviewed source event"},
        {"category_code": "99999"},
        {"subject_code": "99999-class_b-question"},
        {"subject_name_raw": "New unreviewed source label"},
        {"download_url_source": "https://owinform.wdasec.gov.tw/unreviewed.pdf"},
        {"checksum": "0" * 64},
    ],
)
def test_multiple_answer_role_requires_every_original_context_dimension(change):
    native = next(
        p for p in owner.native_papers() if p.checksum == BOUNDARIES["115-assisted-cooking"]
    )
    original = replace(normalized(native), **change)
    result = renormalize_catalog(NormalizedCatalog([original], []), [])
    (paper,) = result.papers
    assert paper.file_type == "question"
    assert paper.source_material is None
    assert paper.classification_confidence == "review"
    assert result.review_queue
