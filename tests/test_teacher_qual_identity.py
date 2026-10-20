import pytest

from app.classification import classify_paper
from app.providers.teacher_qual.client import LISTING_URL
from app.source_material import SourceDate, SourceMaterial


def identity(code, *, year=115, sample=False, event=None, label="official category"):
    return classify_paper(
        provider_id="teacher_qual",
        source_exam_id=event or f"teacher-qual-{year}",
        year_ad=year + 1911,
        category_raw=label,
        exam_name_raw=f"{year}年教師資格考試歷屆試題及參考答案",
        canonical_id="teacher-qual", canonical_name="教師資格考試",
        category_code=f"{year}-{code}",
        subject_code="subject-test", subject_name_raw="國語文能力測驗",
        source_material=SourceMaterial(
            "sample" if sample else "administered",
            SourceDate("undated", None) if sample else SourceDate("exam_year", year + 1911),
            LISTING_URL, "official sample/past-paper heading",
        ),
    )


@pytest.mark.parametrize(
    "code,title,variant",
    [("10", "幼兒園", None), ("21", "身心障礙組", "disability"),
     ("22", "資賦優異組", "gifted"), ("30", "國民小學", None), ("40", "中等學校", None)],
)
def test_official_routes_are_qualifications_not_school_or_numerical_levels(code, title, variant):
    result = identity(code)
    assert result.domain_id == "teacher"
    assert result.exam_series_id == "teacher-qualification"
    assert result.level_id == result.stage_id == "not-applicable"
    assert result.confidence == "high"
    assert title in result.bundle_name
    if variant:
        assert variant in result.variant_ids


def test_all_five_routes_and_their_sample_collections_have_distinct_identities():
    codes = ("10", "21", "22", "30", "40")
    results = [identity(code, sample=sample) for code in codes for sample in (False, True)]
    assert len({r.bundle_id for r in results}) == 10
    for code in codes:
        assert identity(code).bundle_id == identity(code, year=94).bundle_id
        assert "material-sample" in identity(code, sample=True).variant_ids


def test_sittings_stay_separate_events_within_the_same_cross_year_identity():
    first = identity("30", year=108, event="teacher-qual-108-1")
    second = identity("30", year=108, event="teacher-qual-108-2")
    assert first.bundle_id == second.bundle_id
    assert first.exam_event_id != second.exam_event_id
    assert first.stage_id == second.stage_id == "not-applicable"


def test_same_display_label_cannot_approve_an_unknown_selector_value():
    approved = identity("21", label="身心障礙組")
    unknown = identity("23", label="身心障礙組")
    assert unknown.confidence == "review"
    assert unknown.bundle_id != approved.bundle_id
    assert "event-teacher-qual-115" in unknown.bundle_id
