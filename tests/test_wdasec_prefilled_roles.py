"""A complete native answer sequence approves a role without changing identity."""

from dataclasses import replace

import pytest

from app.models import NormalizedCatalog, ParsedPaper
from app.normalizer import normalize_papers, renormalize_catalog
from app.providers.wdasec_skill import papers as owner
from app.publication_metadata import publication_paper_subject
from tests.test_wdasec_forms import normalized


@pytest.mark.parametrize("form", ["ordinary", "foreign-assistance-master"])
def test_prefilled_role_preserves_every_existing_identity_dimension(monkeypatch, form):
    native = next(
        row
        for row in owner.native_papers()
        if row.form == form and row.reviewed_role is not None and row.reviewed_grade is None
    )
    assert native.reviewed_role.question_count in {80, 100}
    original = normalized(native)
    without_role = replace(native, reviewed_role=None)
    monkeypatch.setattr(owner, "_index", lambda: {native.key: without_role})
    (before,) = renormalize_catalog(NormalizedCatalog([original], []), []).papers
    monkeypatch.setattr(owner, "_index", lambda: {native.key: native})
    (after,) = renormalize_catalog(NormalizedCatalog([original], []), []).papers
    assert before.file_type == "question"
    assert after.file_type == "question_answer"
    identity_fields = (
        "bundle_id",
        "level_id",
        "track_id",
        "variant_ids",
        "stage_id",
        "exam_event_id",
        "domain_id",
        "exam_family_id",
        "exam_series_id",
        "exam_class",
        "exam_subclass",
        "classification_confidence",
        "classification_reason",
    )
    assert {name: getattr(after, name) for name in identity_fields} == {
        name: getattr(before, name) for name in identity_fields
    }
    assert publication_paper_subject(after) == publication_paper_subject(before)
    assert after.subject_name_raw == original.subject_name_raw
    assert after.subject_code == original.subject_code
    assert after.download_url_source == original.download_url_source
    assert after.checksum == original.checksum

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
    assert acquired.file_type == after.file_type
    assert acquired.bundle_id == after.bundle_id
    assert set(parsed.files) == {"question"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_exam_id", "209901010001"),
        ("checksum", "0" * 64),
    ],
)
def test_role_only_facts_do_not_transfer_to_another_observation(field, value):
    native = next(
        row
        for row in owner.native_papers()
        if row.reviewed_role is not None and row.reviewed_grade is None
    )
    original = replace(normalized(native), **{field: value})
    result = renormalize_catalog(NormalizedCatalog([original], []), [])
    (paper,) = result.papers
    assert paper.file_type == "question"
    assert paper.classification_confidence == "review"
    assert result.review_queue
