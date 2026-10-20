from app.classification import classify_paper
from app.source_material import SourceDate, SourceMaterial


def classify(
    code,
    *,
    event="gept-cert-advanced-2022",
    text="GEPT全民英檢官方練習資料_高級",
    kind="practice_collection",
):
    return classify_paper(
        provider_id="gept_cert",
        source_exam_id=event,
        year_ad=2022,
        category_raw=text,
        category_code=code,
        exam_name_raw="GEPT全民英檢 高級",
        canonical_id="gept-cert",
        canonical_name="GEPT全民英檢 高級",
        subject_name_raw="第一節，5級分範例",
        source_material=SourceMaterial(
            kind,
            SourceDate("undated", None),
            "https://www.gept.org.tw/Exam_Intro/t04_introduction.asp",
            "官方範例",
        ),
    )


def test_practice_set_is_a_content_variant_not_a_proficiency_grade_or_selection_stage():
    first, second = classify("advanced-practice-1"), classify("advanced-practice-2")
    assert first.level_id == second.level_id == "advanced"
    assert first.stage_id == second.stage_id == "not-applicable"
    assert first.bundle_id != second.bundle_id
    assert "practice-set-1" in first.variant_ids and "practice-set-2" in second.variant_ids
    assert first.bundle_name == "GEPT全民英檢｜高級｜練習題1｜練習問題集"
    assert second.bundle_name == "GEPT全民英檢｜高級｜練習題2｜練習問題集"


def test_structured_source_level_wins_over_incidental_grade_words_and_score_numbers():
    item = classify("elementary", event="gept-cert-elementary-2022", text="優級，5級分範例")
    assert item.level_id == "elementary" and item.confidence == "high"


def test_unknown_structured_grade_or_mismatched_event_is_isolated_for_review():
    for code, event in [
        ("score-5", "gept-cert-advanced-2022"),
        ("elementary", "gept-cert-advanced-2022"),
    ]:
        item = classify(code, event=event)
        assert item.level_id == "unknown" and item.confidence == "review"


def test_native_level_is_named_once_even_with_legacy_level_in_the_canonical_name():
    item = classify("high-intermediate", event="gept-cert-high-intermediate-2022", kind="reference")
    assert item.bundle_name == "GEPT全民英檢｜中高級｜參考資料"
