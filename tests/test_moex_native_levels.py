"""Native source levels and examination purposes must survive historical migration."""

import pytest

from app.classification import classify_paper, public_facets


def classify(
    category: str, event: str, *, year: int = 2026, source: str = "native-test", code: str = ""
):
    return classify_paper(
        provider_id="moex",
        source_exam_id=source,
        year_ad=year,
        category_raw=category,
        exam_name_raw=event,
        canonical_id="一般行政",
        canonical_name="一般行政",
        category_code=code,
    )


@pytest.mark.parametrize("number,native", [("1", "一"), ("2", "二"), ("3", "三")])
def test_numeric_native_levels_override_combined_event_order(number, native):
    event = "098年公務人員高等考試一級暨二級考試"
    numeric = classify(f"{number}級_一般行政", event)
    written = classify(f"高考{native}級_一般行政", event)
    assert numeric.level_id == f"grade-{number}"
    assert numeric.bundle_id == written.bundle_id
    assert numeric.level_label == f"{native}級"
    assert numeric.bundle_name == f"高等考試｜{native}級｜一般行政"


def test_judicial_administration_is_an_occupation_in_civil_high_exam():
    identity = classify("2級_司法行政(兩岸組)", "098年公務人員高等考試一級暨二級考試")
    assert identity.exam_series_id == "civil-high"
    assert identity.level_id == "grade-2"
    assert "司法特考" not in identity.bundle_name
    judicial = classify("三等考試_司法官", "司法人員特種考試暨專門職業及技術人員律師考試")
    assert judicial.exam_series_id == "special-judicial"


@pytest.mark.parametrize(
    "category,level,series",
    [
        ("高等_醫師(一)", "professional-high", "professional-high"),
        ("普通_護士", "professional-ordinary", "professional-ordinary"),
    ],
)
def test_legacy_professional_levels_do_not_borrow_civil_grades(category, level, series):
    identity = classify(category, "107年專門職業及技術人員高等暨普通考試")
    assert identity.domain_id == "professional"
    assert identity.level_id == level
    assert identity.exam_series_id == series
    assert "三等" not in identity.bundle_name
    assert "高考三級" not in identity.bundle_name


@pytest.mark.parametrize(
    "category,level", [("普_一般行政", "ordinary"), ("普通_一般行政", "ordinary")]
)
def test_ordinary_category_abbreviation_precedes_cohosted_high_grade(category, level):
    identity = classify(category, "097年公務人員高等考試三級考試暨普通考試")
    assert identity.level_id == level
    assert identity.exam_series_id == "civil-ordinary"


@pytest.mark.parametrize(
    "event",
    [
        "公務人員高等考試一級暨二級考試",
        "公務人員高等考試三級考試暨普通考試",
    ],
)
def test_unmarked_combined_event_cannot_choose_first_level(event):
    identity = classify("一般行政", event)
    assert identity.level_id == "unknown"
    assert identity.confidence == "review"
    assert "event-native-test" in identity.bundle_id


@pytest.mark.parametrize(
    "category,level",
    [("高等_普通行政類", "qualification-high"), ("普通_普通行政類", "qualification-ordinary")],
)
def test_eligibility_tests_share_purpose_across_chinese_medicine_cohosted_titles(category, level):
    plain = classify(category, "082年檢定考試", year=1993)
    cohosted = classify(category, "083年高等暨普通中醫師檢定考試", year=1994)
    assert plain.bundle_id == cohosted.bundle_id
    assert plain.exam_series_id == "eligibility-qualification"
    assert plain.domain_id == "qualification"
    assert plain.exam_family_id == "exam-eligibility"
    assert plain.level_id == level
    assert public_facets(plain)["exam_class"] == "應考資格檢定"


def test_chinese_medicine_eligibility_is_ungraded_and_separate_from_licensing():
    eligibility = classify("中醫師", "082年檢定考試", year=1993)
    licensing = classify("專技特考_中醫師", "專門職業及技術人員特種考試中醫師考試")
    assert eligibility.exam_series_id == "chinese-medicine-eligibility"
    assert eligibility.level_id == "not-applicable"
    assert eligibility.domain_id == "qualification"
    assert licensing.domain_id == "professional"
    assert licensing.bundle_id != eligibility.bundle_id


def test_missing_eligibility_level_is_reviewed_instead_of_assumed_ordinary():
    missing = classify("財務行政類", "090年高等暨普通中醫師檢定考試", year=2001)
    assert missing.level_id == "unknown"
    assert missing.confidence == "review"


@pytest.mark.parametrize(
    "source,code,category,year,level",
    [
        ("091010", "101", "公職獸醫師", 2002, "grade-1"),
        ("091010", "201", "一般民政", 2002, "grade-2"),
        # A native code beginning with 1 is not evidence of first grade.
        ("094180", "101", "人事行政", 2005, "grade-2"),
    ],
)
def test_checksum_anchored_headers_resolve_unmarked_combined_categories(
    source, code, category, year, level
):
    identity = classify(
        category,
        f"{source[:3]}年公務人員高等考試一級暨二級考試",
        year=year,
        source=source,
        code=code,
    )
    assert identity.level_id == level
    assert identity.confidence == "high"
    assert identity.reason.startswith("reviewed official question headers:")


def test_native_special_grade_is_not_displayed_as_civil_high_equivalence():
    identity = classify("三等_一般行政", "地方政府公務人員特種考試")
    assert identity.level_label == "三等"
    assert identity.bundle_name == "地方特考｜三等｜一般行政"


def test_high_grade_reform_preserves_native_number_but_separates_historical_system():
    old = classify("高考一級_一般行政", "公務人員高等考試一級考試", year=1995)
    new = classify("高考一級_一般行政", "公務人員高等考試一級考試", year=1996)
    assert old.level_id == new.level_id == "grade-1"
    assert old.level_label == new.level_label == "一級"
    assert old.bundle_id != new.bundle_id
    assert "85年改制前" in old.bundle_name
    assert old.variant_ids == ("civil-high-pre-1996",)
    assert new.variant_ids == ()


def test_numeric_civil_level_pattern_does_not_consume_native_ship_rank():
    identity = classify("一級輪機員", "專門職業及技術人員特種考試航海人員考試")
    assert identity.level_id == "maritime-rank-1"


def test_parenthetical_grade_and_high_abbreviation_preserve_native_levels():
    combined = "公務人員高等考試一級暨二級考試"
    assert classify("人事行政(一級)", combined).level_id == "grade-1"
    assert classify("人事行政(二級)", combined).level_id == "grade-2"
    assert classify("高三_一般行政", "公務人員高等考試三級考試暨普通考試").level_id == "grade-3"


def test_judicial_affairs_officer_heading_is_distinct_from_administration_track():
    event = "公務人員特種考試關務人員、司法人員考試"
    identity = classify("3等_司法事務官營繕工程事務組", event)
    assert identity.exam_series_id == "special-judicial"


def test_exact_ordinary_header_fact_overrides_combined_event_title_order():
    identity = classify_paper(
        provider_id="moex",
        source_exam_id="096100",
        year_ad=2007,
        category_code="408",
        category_raw="新聞廣播（選試英文、國語播音與閩南語播音）",
        exam_name_raw="096年公務人員高等考試三級考試暨普通考試",
        canonical_id="新聞廣播",
        canonical_name="新聞廣播",
    )
    assert identity.exam_series_id == "civil-ordinary"
    assert identity.level_id == "ordinary"
    assert identity.confidence == "high"


def test_cohosted_eligibility_title_cannot_assign_screening_grade_to_unmarked_profession():
    identity = classify("護理師", "中醫師檢定、第一次專技醫事人員暨醫師檢覈筆試")
    assert identity.level_id == "unknown"
    assert identity.confidence == "review"
