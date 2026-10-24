"""Common native theory headings do not erase qualification applicability."""

from dataclasses import replace

import pytest

from app.models import NormalizedCatalog, ParsedPaper
from app.normalizer import normalize_papers, renormalize_catalog
from app.providers.wdasec_skill import papers as owner
from app.source_material import publication_year_roc
from tests.test_wdasec_forms import normalized

HISTORICAL = [
    p
    for p in owner.native_papers()
    if p.source_exam_id == "201202290005"
    and p.category_code in {"01601", "14901"}
    and p.subject_code.endswith("-question")
]


def test_historical_code_review_covers_only_three_independent_combined_roles():
    assert {p.subject_code for p in HISTORICAL} == {
        "01601-class_c-question",
        "14901-class_b-question",
        "14901-class_c-question",
    }
    assert all(p.reviewed_role.question_count == 80 for p in HISTORICAL)
    assert all(p.reviewed_grade is None and p.reviewed_material is None for p in HISTORICAL)
    assert all(p.reviewed_component is None and p.form == "ordinary" for p in HISTORICAL)


@pytest.mark.parametrize("native", HISTORICAL, ids=lambda p: p.subject_code)
def test_independent_role_retains_raw_code_track_native_grade_and_annual_context(
    native, monkeypatch
):
    original = normalized(native)
    without_role = replace(native, reviewed_role=None)
    monkeypatch.setattr(owner, "_index", lambda: {without_role.key: without_role})
    (before,) = renormalize_catalog(NormalizedCatalog([original], []), []).papers
    monkeypatch.setattr(owner, "_index", lambda: {native.key: native})
    (after,) = renormalize_catalog(NormalizedCatalog([original], []), []).papers
    assert before.file_type == "question" and after.file_type == "question_answer"
    assert before.bundle_id == after.bundle_id and before.track_id == after.track_id
    assert before.level_id == after.level_id
    assert before.variant_ids == after.variant_ids == []
    assert after.source_material is None and after.schema_version == 2
    assert publication_year_roc(after) == after.year_roc == 99
    assert after.source_exam_id == original.source_exam_id
    assert after.subject_code == original.subject_code
    assert after.category_code == original.category_code
    assert after.subject_name_raw == original.subject_name_raw
    assert after.download_url_source == original.download_url_source
    assert after.checksum == original.checksum
    if native.category_code == "14901":
        assert "14900" in native.native_header
        assert "人工記帳" in after.bundle_name and after.track_id == before.track_id
    else:
        assert "01600" in native.native_header
        assert after.level_id == "class-c" and "自來水管配管" in after.bundle_name
    parsed = ParsedPaper(
        category_raw=original.category_raw,
        category_code=native.category_code,
        subject_code=native.subject_code,
        subject_name_raw=native.subject_name_raw,
        files={"question": native.source_url},
        mirror_files={"question": {"checksum": native.checksum}},
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
    assert acquired.bundle_id == after.bundle_id
    assert acquired.file_type == "question_answer"
    assert acquired.subject_code == after.subject_code
    assert acquired.category_code == after.category_code
    assert acquired.source_material is None


@pytest.mark.parametrize("native", HISTORICAL, ids=lambda p: p.subject_code)
@pytest.mark.parametrize("change", [{"checksum": "0" * 64}, {"source_exam_id": "209901010001"}])
def test_common_occupation_or_grade_cannot_approve_new_bytes_or_applicability(native, change):
    original = replace(normalized(native), **change)
    (paper,) = renormalize_catalog(NormalizedCatalog([original], []), []).papers
    assert paper.file_type == "question"
    assert paper.source_material is None
    assert paper.classification_confidence == "review"
    assert "paper-form-unknown" in paper.variant_ids
