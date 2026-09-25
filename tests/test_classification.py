import unittest
from types import SimpleNamespace

from app.classification import classify_normalized_paper, classify_paper


def classify(category: str, event: str, *, source: str = "event-115", canonical: str = "一般行政", provider: str = "moex", subject: str = ""):
    return classify_paper(
        provider_id=provider,
        source_exam_id=source,
        year_ad=2026,
        category_raw=category,
        exam_name_raw=event,
        canonical_id=canonical,
        canonical_name=canonical,
        subject_name_raw=subject,
        subject_code="0101",
    )


class ExamIdentityClassificationTests(unittest.TestCase):
    def test_moex_papers_in_one_category_share_identity_across_subjects(self) -> None:
        base = dict(
            provider_id="moex",
            source_exam_id="115030",
            year_roc=115,
            category_raw="一般行政",
            exam_name_raw="115年公務人員高等考試三級",
            canonical_id="general-administration",
            canonical_name="一般行政",
        )
        first = SimpleNamespace(**base, subject_name_raw="國文", subject_code="0101")
        second = SimpleNamespace(**base, subject_name_raw="法學知識", subject_code="0102")

        expected = classify_paper(
            provider_id="moex",
            source_exam_id=base["source_exam_id"],
            year_ad=2026,
            category_raw=base["category_raw"],
            exam_name_raw=base["exam_name_raw"],
            canonical_id=base["canonical_id"],
            canonical_name=base["canonical_name"],
            subject_name_raw=second.subject_name_raw,
            subject_code=second.subject_code,
        )
        self.assertEqual(classify_normalized_paper(first), expected)
        self.assertEqual(classify_normalized_paper(second), expected)

    def test_same_track_is_separated_by_civil_service_level_and_series(self) -> None:
        high = classify("一般行政", "115年公務人員高等考試三級", source="high-115")
        ordinary = classify("一般行政", "115年公務人員普通考試", source="ordinary-115")
        elementary = classify("一般行政", "115年公務人員初等考試", source="elementary-115")
        local = classify("一般行政（三等）", "115年地方特考", source="local-115")
        local_fourth = classify("一般行政（四等）", "115年地方特考", source="local-4-115")

        self.assertEqual(high.exam_series_id, "civil-high")
        self.assertEqual(high.level_id, "grade-3")
        self.assertEqual(ordinary.exam_series_id, "civil-ordinary")
        self.assertEqual(ordinary.level_id, "ordinary")
        self.assertEqual(elementary.exam_series_id, "civil-elementary")
        self.assertEqual(elementary.level_id, "elementary")
        self.assertEqual(local.exam_series_id, "special-local-government")
        self.assertEqual(local.level_id, "grade-3")
        self.assertEqual(local_fourth.level_id, "grade-4")
        self.assertEqual(high.track_id, "general-administration")
        self.assertEqual(len({high.bundle_id, ordinary.bundle_id, elementary.bundle_id, local.bundle_id, local_fourth.bundle_id}), 5)

    def test_promotion_and_variant_markers_are_not_merged(self) -> None:
        promotion = classify("一般行政（員級晉高員級）", "115年升官等考試", source="promotion-115")
        group_one = classify("一般行政（兩岸組一）", "115年公務人員高等考試三級", source="high-group-1")
        group_two = classify("一般行政（兩岸組二）", "115年公務人員高等考試三級", source="high-group-2")

        self.assertEqual(promotion.exam_series_id, "civil-promotion")
        self.assertEqual(promotion.level_id, "promotion-employee-to-senior")
        self.assertIn("cross-strait-group-1", group_one.variant_ids)
        self.assertIn("cross-strait-group-2", group_two.variant_ids)
        self.assertNotEqual(group_one.bundle_id, group_two.bundle_id)

    def test_source_event_marker_resolves_worker_promotion_level(self) -> None:
        worker_promotion = classify(
            "常務工",
            "082年交通事業鐵路人員差工晉升士級考試",
            source="082040",
        )

        self.assertEqual(worker_promotion.exam_series_id, "civil-promotion")
        self.assertEqual(worker_promotion.level_id, "promotion-worker-rank")
        self.assertEqual(worker_promotion.confidence, "medium")
        self.assertIn("source event marker", worker_promotion.reason)

    def test_non_moex_levels_use_provider_specific_hierarchy(self) -> None:
        gept = classify("中高級", "全民英檢", provider="gept_cert", canonical="gept-cert", subject="中高級")
        jlpt = classify("N2", "日本語能力試驗", provider="jlpt_cert", canonical="jlpt-cert", subject="N2")
        skill = classify("甲級", "技能檢定", provider="wdasec_skill", canonical="skill", subject="甲級")

        self.assertEqual(gept.level_id, "high-intermediate")
        self.assertEqual(gept.exam_series_id, "language-gept")
        self.assertEqual(jlpt.level_id, "n2")
        self.assertEqual(jlpt.exam_series_id, "language-jlpt")
        self.assertEqual(skill.level_id, "class-a")
        self.assertEqual(skill.exam_series_id, "skill-certification")

    def test_historical_tcte_classes_are_professional_tracks(self) -> None:
        identity = classify(
            "四技二專統一入學測驗",
            "90學年度四技二專統一入學測驗",
            source="tcte-tve-90",
            provider="tcte_tve",
            canonical="tcte-tve",
            subject="01機械類 專業科目(一)",
        )

        self.assertEqual(identity.exam_series_id, "admission-tcte")
        self.assertEqual(identity.track_id, "tcte-group-01")
        self.assertEqual(identity.track_label, "01機械類")

    def test_ast_multi_subject_notices_reuse_historical_subject_tracks(self) -> None:
        historical = classify(
            "分科測驗",
            "114學年度分科測驗－物理",
            source="ceec-ast-114-physics",
            provider="ceec_ast",
            canonical="ceec-ast",
            subject="物理 試題內容",
        )
        confirmed = classify(
            "分科測驗",
            "115學年度分科測驗各考科選擇(填)題答案確定",
            source="ceec-ast-confirmed-115",
            provider="ceec_ast",
            canonical="ceec-ast",
            subject="物理",
        )
        guidelines = classify(
            "分科測驗",
            "115學年度分科測驗各考科非選擇題評分原則",
            source="ceec-ast-guidelines-115",
            provider="ceec_ast",
            canonical="ceec-ast",
            subject="物理",
        )

        self.assertEqual(confirmed.track_id, historical.track_id)
        self.assertEqual(guidelines.bundle_id, historical.bundle_id)

    def test_ambiguous_level_is_review_isolated_per_source_event(self) -> None:
        first = classify("一般行政", "其他特種考試", source="unknown-1")
        second = classify("一般行政", "其他特種考試", source="unknown-2")

        self.assertEqual(first.confidence, "review")
        self.assertEqual(second.confidence, "review")
        self.assertNotEqual(first.bundle_id, second.bundle_id)
        self.assertIn("no authoritative level marker", first.reason)

    def test_professional_combined_event_has_explicit_combined_identity(self) -> None:
        combined = classify(
            "專門職業及技術人員高等暨普通考試",
            "專門職業及技術人員高等暨普通考試",
            source="professional-combined",
            canonical="doctor",
        )

        self.assertEqual(combined.exam_series_id, "professional-combined")
        self.assertEqual(combined.level_id, "combined")
        self.assertEqual(combined.confidence, "high")


if __name__ == "__main__":
    unittest.main()
