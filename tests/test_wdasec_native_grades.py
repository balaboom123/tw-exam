"""Historical native grade corrections conserve exact acquisition references."""

from dataclasses import asdict, replace

import pytest

from app.bundler import _bundle_arcname, _bundle_manifest_data, _paper_bundle_key, _resolve_arcnames
from app.models import NormalizedCatalog, file_type_label
from app.normalizer import renormalize_catalog
from app.providers.wdasec_skill.papers import native_papers
from app.publication_metadata import publication_paper_subject, publication_paper_subject_code
from tests.test_wdasec_forms import normalized

REVIEWED = [
    paper
    for paper in native_papers()
    if paper.reviewed_grade is not None and paper.category_code != "06800"
]
TARGETS = {
    "09100": (
        "single",
        "wdasec-skill-skill-certification-single-skill-4d162efd7405-paper-form-ordinary",
    ),
    "15100": (
        "single",
        "wdasec-skill-skill-certification-single-skill-e54c91282b19-paper-form-ordinary",
    ),
    "00400": (
        "single",
        "wdasec-skill-skill-certification-single-skill-520d84214fb6-paper-form-ordinary",
    ),
    "02900": ("class-b", "wdasec-skill-skill-certification-class-b-skill-992e71552a91"),
    "07200": ("class-c", "wdasec-skill-skill-certification-class-c-skill-2e8275eb002a"),
}
SOURCE_FIELDS = (
    "year_roc",
    "source_exam_id",
    "exam_name_raw",
    "category_raw",
    "category_code",
    "subject_code",
    "subject_name_raw",
    "download_url_source",
    "checksum",
    "storage_key",
)


@pytest.mark.parametrize("native", REVIEWED, ids=lambda p: f"{p.year_roc}-{p.subject_code}")
def test_reviewed_grade_and_combined_role_rejoin_the_exact_existing_identity(native):
    assert len(REVIEWED) == 7
    original = replace(normalized(native), storage_key="native.pdf")
    result = renormalize_catalog(NormalizedCatalog([original], []), [])
    (paper,) = result.papers
    assert (paper.level_id, paper.bundle_id) == TARGETS[native.category_code]
    assert paper.file_type == "question_answer"
    assert paper.classification_confidence == "high" and not result.review_queue
    assert {key: getattr(paper, key) for key in SOURCE_FIELDS} == {
        key: getattr(original, key) for key in SOURCE_FIELDS
    }
    assert publication_paper_subject(paper) == native.reviewed_grade.subject_label
    assert (
        publication_paper_subject_code(paper)
        == f"{native.category_code}-{paper.level_id.replace('-', '_')}-question"
    )
    path = _bundle_arcname(paper)
    assert native.reviewed_grade.native_grade in path
    assert file_type_label("question_answer") in path
    assert asdict(renormalize_catalog(result, []).papers[0]) == asdict(paper)


def test_same_event_av_electronics_aliases_share_one_correctly_labelled_payload():
    native = [row for row in REVIEWED if row.category_code == "02900"]
    assert len(native) == 2
    papers = [
        replace(normalized(row), storage_key=f"native-{i}.pdf") for i, row in enumerate(native)
    ]
    current = renormalize_catalog(NormalizedCatalog(papers, []), []).papers
    assert len({p.source_exam_id for p in current}) == 1
    assert len({p.checksum for p in current}) == 1
    assert len({_paper_bundle_key(p) for p in current}) == 2
    assert {p.level_id for p in current} == {"class-b"}
    assert {p.file_type for p in current} == {"question_answer"}
    paths = _resolve_arcnames(current)
    assert paths[0] == paths[1]
    assert "乙級" in paths[0] and "甲級" not in paths[0]
    manifest = _bundle_manifest_data(
        current[0].bundle_id,
        current[0].bundle_name,
        current,
        dict(zip(map(_paper_bundle_key, current), paths, strict=True)),
    )
    assert manifest["file_count"] == 1
    assert len(manifest["papers"]) == 2
    assert {p["subject_code"] for p in manifest["papers"]} == {p.subject_code for p in papers}
    assert {p["download_url_source"] for p in manifest["papers"]} == {
        p.download_url_source for p in papers
    }


@pytest.mark.parametrize("native", REVIEWED, ids=lambda p: f"{p.year_roc}-{p.subject_code}")
def test_identical_payload_in_an_unreviewed_source_page_requires_review(native):
    original = replace(normalized(native), source_exam_id="209901010001", storage_key="native.pdf")
    result = renormalize_catalog(NormalizedCatalog([original], []), [])
    (paper,) = result.papers
    assert paper.file_type == "question"
    assert paper.classification_confidence == "review"
    assert result.review_queue


def test_older_argon_welding_native_c_grade_survives_adjacent_single_grade_reviews():
    native = [row for row in native_papers() if row.category_code == "09100" and row.year_roc == 92]
    assert len(native) == 2
    result = renormalize_catalog(NormalizedCatalog([normalized(row) for row in native], []), [])
    assert {paper.level_id for paper in result.papers} == {"class-c"}
    assert not result.review_queue
    assert all(row.reviewed_grade is None for row in native)
