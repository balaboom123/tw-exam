"""Software booklets with atypical counts retain tracks and embedded answers."""

from dataclasses import asdict, replace

import pytest

from app.models import NormalizedCatalog
from app.normalizer import renormalize_catalog
from app.providers.wdasec_skill import papers as owner
from tests.test_wdasec_forms import normalized


@pytest.mark.parametrize(
    ("year", "category", "question_count"),
    [(92, "11901", 140), (92, "11902", 140)]
    + [(year, category, 70) for year in (93, 96) for category in ("11900", "11901", "11902")],
)
def test_reviewed_software_role_preserves_booklets_and_qualification_tracks(
    monkeypatch, year, category, question_count
):
    native = next(
        p
        for p in owner.native_papers()
        if p.year_roc == year
        and p.category_code == category
        and p.source_exam_id in {"201201050005", "201201050007", "201201050016"}
    )
    assert native.reviewed_role is not None
    assert native.reviewed_role.question_count == question_count
    assert native.reviewed_grade is native.reviewed_component is native.reviewed_material is None
    source = normalized(native)
    monkeypatch.setattr(owner, "_index", lambda: {native.key: replace(native, reviewed_role=None)})
    (before,) = renormalize_catalog(NormalizedCatalog([source], []), []).papers
    monkeypatch.setattr(owner, "_index", lambda: {native.key: native})
    (after,) = renormalize_catalog(NormalizedCatalog([source], []), []).papers
    assert {k for k in asdict(before) if asdict(before)[k] != asdict(after)[k]} == {
        "file_type",
        "paper_code",
    }
    assert before.file_type == "question" and after.file_type == "question_answer"
    assert after.level_id == "class-b" and after.year_roc == year
    assert after.source_material is None
    if category == "11901":
        assert "JAVA" in after.bundle_name
    elif category == "11902":
        assert "C++" in after.bundle_name
    else:
        assert "JAVA" not in after.bundle_name and "C++" not in after.bundle_name
    changed = replace(source, checksum="0" * 64)
    result = renormalize_catalog(NormalizedCatalog([changed], []), [])
    assert result.papers[0].file_type == "question"
    assert result.papers[0].classification_confidence == "review"
    assert result.review_queue
