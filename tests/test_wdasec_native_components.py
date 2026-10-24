"""A practical booklet with response sheets keeps its question role and native identity."""

from dataclasses import asdict, replace

import pytest

from app.models import NormalizedCatalog, ParsedPaper
from app.normalizer import normalize_papers, renormalize_catalog
from app.providers.wdasec_skill import papers as owner
from app.publication_metadata import publication_paper_subject, publication_paper_subject_code
from tests.test_wdasec_forms import normalized

PRACTICAL = [
    paper
    for paper in owner.native_papers()
    if paper.source_exam_id == "201211120001"
    and paper.category_code == "14901"
    and paper.reviewed_component is not None
    and paper.reviewed_component.component_id == "practical"
]


def test_both_reviewed_manual_bookkeeping_grades_have_their_native_component_facts():
    assert {paper.subject_code for paper in PRACTICAL} == {
        "14901-class_b-question",
        "14901-class_c-question",
    }


@pytest.mark.parametrize("native", PRACTICAL, ids=lambda p: p.subject_code)
def test_native_practical_projection_preserves_theory_source_key_and_question_role(
    native, monkeypatch
):
    assert native.year_roc == 101
    assert native.reviewed_grade is None and native.reviewed_role is None
    original = normalized(native)
    monkeypatch.setattr(
        owner, "_index", lambda: {native.key: replace(native, reviewed_component=None)}
    )
    (before,) = renormalize_catalog(NormalizedCatalog([original], []), []).papers
    assert publication_paper_subject(before).endswith("學科")
    monkeypatch.setattr(owner, "_index", lambda: {native.key: native})
    (after,) = renormalize_catalog(NormalizedCatalog([original], []), []).papers
    assert asdict(after) == asdict(before)
    assert after.file_type == "question"
    assert after.subject_code.endswith("-question")
    assert after.subject_name_raw.endswith("學科")
    assert publication_paper_subject_code(after) == f"{native.family[1]}-practical"
    assert (
        publication_paper_subject(after) == original.subject_name_raw.removesuffix("學科") + "術科"
    )

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
    assert acquired.file_type == "question"
    assert acquired.subject_code == original.subject_code
    assert acquired.checksum == original.checksum
    assert publication_paper_subject(acquired) == publication_paper_subject(after)
    assert publication_paper_subject_code(acquired) == publication_paper_subject_code(after)
    assert set(parsed.files) == {"question"}


@pytest.mark.parametrize("native", PRACTICAL, ids=lambda p: p.subject_code)
@pytest.mark.parametrize(
    "field,value", [("source_exam_id", "209901010001"), ("checksum", "0" * 64)]
)
def test_unknown_context_cannot_inherit_the_practical_component(native, field, value):
    paper = replace(normalized(native), **{field: value})
    assert publication_paper_subject(paper) == paper.subject_name_raw
    assert publication_paper_subject_code(paper) == paper.subject_code
    assert owner.native_file_role(paper) == "question"
