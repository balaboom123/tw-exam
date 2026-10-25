"""Printed source counts and omitted items do not redefine a file's role."""

from dataclasses import asdict, replace

import pytest

from app.models import NormalizedCatalog
from app.normalizer import renormalize_catalog
from app.providers.wdasec_skill import papers as owner
from tests.test_wdasec_forms import normalized


@pytest.mark.parametrize(
    ("checksum", "actual_count"),
    [
        ("267a52f8da9d575b55ab004580325d5766bf40198ed0bed4855a7096de09b194", 100),
        ("efb451d2a0c9e7eb83eadb4c392e178136de950cd00c9d4176ba983b87c555f3", 79),
        ("12e5ec0a934c11bc207d87d76d16106451d6046c4dac795f37f89c2452fb8544", 100),
        ("579b99318dc73b363b9da2dd681d7327efec46f57b4f8ec97bc891c34ec5cd0e", 100),
    ],
)
def test_exact_native_role_uses_reviewed_present_questions_and_preserves_other_facts(
    monkeypatch, checksum, actual_count
):
    native = next(p for p in owner.native_papers() if p.checksum == checksum)
    assert native.reviewed_role is not None
    assert native.reviewed_role.question_count == actual_count
    if actual_count == 79:
        assert "question 45" in native.reviewed_role.evidence
        assert "unresolved" in native.reviewed_role.evidence
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
    changed = replace(source, checksum="0" * 64)
    result = renormalize_catalog(NormalizedCatalog([changed], []), [])
    assert result.papers[0].file_type == "question"
    assert result.papers[0].classification_confidence == "review"
    assert result.review_queue
