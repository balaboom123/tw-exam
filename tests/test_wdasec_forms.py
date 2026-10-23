from dataclasses import replace

import pytest

from app.classification import classify_normalized_paper
from app.models import NormalizedPaper, ParsedPaper
from app.normalizer import normalize_papers
from app.providers.wdasec_skill.forms import FORM_LABELS, NativeForm, form_variant, native_forms


def normalized(form: NativeForm) -> NormalizedPaper:
    return NormalizedPaper(
        canonical_id="wdasec-skill",
        canonical_name="全國技術士技能檢定",
        year_roc=form.year_roc,
        source_exam_id=form.source_exam_id,
        exam_name_raw=form.exam_name_raw,
        category_raw="全國技術士技能檢定",
        category_code=form.category_code,
        subject_code=form.subject_code,
        subject_name_raw=form.subject_name_raw,
        paper_code=f"{form.category_code}-{form.subject_code}-question",
        file_type="question",
        download_url_source=form.source_url,
        checksum=form.checksum,
        provider_id="wdasec_skill",
    )


def test_distinct_native_questions_keep_occupation_and_grade_but_split_forms():
    assisted = next(form for form in native_forms() if form.form == "foreign-assistance-master")
    ordinary = next(
        form
        for form in native_forms()
        if form.form == "ordinary"
        and (form.source_exam_id, form.subject_code)
        == (assisted.source_exam_id, assisted.subject_code)
    )
    a = classify_normalized_paper(normalized(assisted))
    b = classify_normalized_paper(normalized(ordinary))
    assert a.level_id == b.level_id
    assert a.track_id == b.track_id
    assert a.stage_id == b.stage_id == "not-applicable"
    assert a.bundle_id != b.bundle_id
    assert a.variant_ids == (FORM_LABELS[assisted.form][0],)
    assert b.variant_ids == (FORM_LABELS[ordinary.form][0],)
    assert "外語輔助學科試卷（中文公告版）" in a.bundle_name
    assert "一般試卷" in b.bundle_name
    assert a.confidence == b.confidence == "high"


@pytest.mark.parametrize("form_name", list(FORM_LABELS))
@pytest.mark.parametrize(
    "field,value",
    [
        ("year_roc", 899),
        ("source_exam_id", "209901010001"),
        ("exam_name_raw", "Unreviewed new programme"),
        ("subject_name_raw", "Unreviewed native label"),
        ("download_url_source", "https://owinform.wdasec.gov.tw/new.pdf"),
        ("checksum", "0" * 64),
    ],
)
def test_new_source_context_or_bytes_cannot_inherit_an_approved_form(form_name, field, value):
    form = next(form for form in native_forms() if form.form == form_name)
    changed = replace(normalized(form), **{field: value})
    identity = classify_normalized_paper(changed)
    assert identity.confidence == "review"
    assert "paper-form-unknown" in identity.variant_ids


def test_ingestion_and_retained_classification_use_the_same_native_context():
    form = next(form for form in native_forms() if form.form == "foreign-assistance-master")
    raw = ParsedPaper(
        category_raw="全國技術士技能檢定",
        category_code=form.category_code,
        subject_code=form.subject_code,
        subject_name_raw=form.subject_name_raw,
        files={"question": form.source_url},
        mirror_files={"question": {"checksum": form.checksum, "storage_key": "native.pdf"}},
    )
    catalog = normalize_papers(
        source_exam_id=form.source_exam_id,
        year_ad=form.year_roc + 1911,
        exam_name_raw=form.exam_name_raw,
        papers=[raw],
        alias_rules=[],
        mirror_base_url="",
        mirror_metadata={},
        provider_id="wdasec_skill",
    )
    assert len(catalog.papers) == 1
    paper = catalog.papers[0]
    assert paper.variant_ids == [FORM_LABELS[form.form][0]]
    assert paper.bundle_id == classify_normalized_paper(normalized(form)).bundle_id


def test_new_occupation_cannot_gain_a_form_from_similar_wording():
    form = next(form for form in native_forms() if form.form == "foreign-assistance-master")
    assert (
        form_variant(
            year_roc=form.year_roc,
            source_exam_id=form.source_exam_id,
            exam_name_raw=form.exam_name_raw,
            category_code="99999",
            subject_code="99999-single-question",
            subject_name_raw=form.subject_name_raw,
            source_url=form.source_url,
            checksum=form.checksum,
        )
        == ("paper-form-unknown", "試卷形式待確認")
    )


@pytest.mark.repo_data
def test_native_form_migration_conserves_previously_public_paper_coverage():
    from app.bundler import public_bundle_ids
    from app.models import NormalizedCatalog
    from app.site_registry import get_site_config

    config = get_site_config("default")
    papers = []
    split_families = {
        form.family for form in native_forms() if form.form == "foreign-assistance-master"
    }
    for form in native_forms():
        if form.family not in split_families:
            continue
        paper = normalized(form)
        paper.bundle_id = classify_normalized_paper(paper).bundle_id
        papers.append(paper)
    public = public_bundle_ids(
        NormalizedCatalog(papers, []),
        min_years=config.public_min_years,
        min_years_by_canonical_prefix=config.public_min_years_by_canonical_prefix,
    )
    assert all(paper.bundle_id in public for paper in papers)
    held = replace(papers[0], checksum="0" * 64)
    held.bundle_id = classify_normalized_paper(held).bundle_id
    assert not public_bundle_ids(
        NormalizedCatalog([held], []),
        min_years=config.public_min_years,
        min_years_by_canonical_prefix=config.public_min_years_by_canonical_prefix,
    )
