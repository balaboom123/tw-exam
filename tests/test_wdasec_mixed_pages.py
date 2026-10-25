"""A text answer key can accompany scanned questions in the same native PDF."""

from dataclasses import asdict, replace

from app.models import NormalizedCatalog
from app.normalizer import renormalize_catalog
from app.providers.wdasec_skill import papers as owner
from tests.test_wdasec_forms import normalized


def test_mixed_page_original_uses_independent_full_document_role_review(monkeypatch):
    native = next(
        p
        for p in owner.native_papers()
        if p.checksum == "d3a61323b10ccb456c66b47bd8267a707ee4d4acdbef6a25f0328fa3cacf80df"
    )
    assert native.reviewed_role is not None
    assert native.reviewed_role.question_count == 100
    assert "five scanned question pages" in native.reviewed_role.evidence
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
    assert after.source_material is None and after.year_roc == 94
    assert after.level_id == "class-a" and "汽車修護" in after.bundle_name
    changed = replace(source, checksum="0" * 64)
    result = renormalize_catalog(NormalizedCatalog([changed], []), [])
    assert result.papers[0].file_type == "question"
    assert result.papers[0].classification_confidence == "review"
    assert result.review_queue
