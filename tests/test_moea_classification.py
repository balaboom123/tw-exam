"""Unreviewed source wording must not become a confident joint-job identity."""

from app.classification import classify_paper


def identity(category: str, *, event: str = "moea-recruit-114", year: int = 2025):
    return classify_paper(
        provider_id="moea_recruit",
        source_exam_id=event,
        year_ad=year,
        category_raw=category,
        exam_name_raw=f"{year - 1911}年經濟部所屬事業機構新進職員甄試",
        canonical_id="moea-recruit",
        canonical_name="經濟部所屬事業機構新進職員甄試",
        subject_name_raw="專業科目A",
        subject_code="joint-source",
        category_code=str(year - 1911),
    )


def test_unreviewed_moea_occupations_are_separate_within_one_event():
    records = [
        identity(category)
        for category in (
            "114年度共同科目",
            "114年度企管",
            "114年度人資",
            "114年度電機(甲)",
            "114年度電機(乙)",
        )
    ]
    assert len({record.bundle_id for record in records}) == len(records)
    assert len({record.track_id for record in records}) == len(records)
    assert all(record.confidence == "review" for record in records)
    assert all(record.level_id == "unknown" for record in records)
    assert all(record.stage_id == "unknown" for record in records)
    assert [record.track_label for record in records] == [
        "114年度共同科目",
        "114年度企管",
        "114年度人資",
        "114年度電機(甲)",
        "114年度電機(乙)",
    ]


def test_unreviewed_moea_category_does_not_merge_across_events():
    first = identity("企管", event="moea-recruit-100", year=2011)
    later = identity("企管", event="moea-recruit-114", year=2025)
    assert first.track_id == later.track_id
    assert first.bundle_id != later.bundle_id
    assert first.confidence == later.confidence == "review"


def test_moea_source_period_and_degree_words_do_not_resolve_native_identity():
    for category in ("2001", "專科以上 電機甲 科目A 第一試", "初試 人資", ""):
        record = identity(category)
        assert record.level_id == "unknown"
        assert record.stage_id == "unknown"
        assert record.confidence == "review"
        assert "reviewed native source evidence" in record.reason


def test_unicode_equivalent_unreviewed_categories_share_the_same_candidate():
    ascii_category = identity("114年度電機(甲)")
    full_width = identity("１１４年度電機（甲）")
    assert ascii_category.bundle_id == full_width.bundle_id
    assert ascii_category.track_id == full_width.track_id
