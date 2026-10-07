import unittest
from types import SimpleNamespace

from app.classification import _classify_paper_uncached, classify_normalized_paper, classify_paper


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
    def test_railway_recruitment_grades_do_not_borrow_cohosted_police_identity(self) -> None:
        event = "公務人員特種考試警察人員、一般警察人員及特種考試交通事業鐵路人員考試"
        for marker, level in (("高員三級", "transport-senior-3"), ("員級", "transport-employee"), ("佐級", "transport-associate")):
            for prefix in ("", "鐵路人員", "交通事業鐵路人員考試"):
                with self.subTest(marker=marker, prefix=prefix):
                    identity = classify(f"{prefix}{marker}考試_會計", event)
                    self.assertEqual(identity.exam_series_id, "special-railway")
                    self.assertEqual(identity.level_id, level)
                    self.assertEqual(identity.bundle_name, f"鐵路人員特考｜{marker}｜會計")
                    self.assertEqual(identity.confidence, "high")
        historic = classify("高員3級_會計", event, source="098110")
        current = classify("鐵路人員考試高員三級_會計", event, source="112070")
        self.assertEqual(historic.bundle_id, current.bundle_id)
        police = classify("警察三等考試_會計", event)
        self.assertEqual(police.exam_series_id, "special-police")
        self.assertNotEqual(police.bundle_id, current.bundle_id)

    def test_railway_recruitment_does_not_merge_with_transport_promotion(self) -> None:
        recruitment = classify("鐵路人員高員三級_會計", "特種考試交通事業鐵路人員考試")
        promotion = classify("交通事業鐵路人員員級晉高員_會計", "交通事業鐵路、公路、港務人員升資考試")
        self.assertEqual(recruitment.level_id, "transport-senior-3")
        self.assertEqual(promotion.exam_series_id, "civil-promotion")
        self.assertEqual(promotion.level_id, "promotion-employee-to-senior")
        self.assertNotEqual(recruitment.bundle_id, promotion.bundle_id)

    def test_cohosted_highway_and_railway_recruitment_remain_separate(self) -> None:
        event = "097年特種考試交通事業鐵路人員考試、97年特種考試交通事業公路人員考試"
        railway = classify("鐵路人員考試員級_土木工程", event)
        highway = classify("公路人員考試員級_土木工程", event)
        unresolved = classify("員級_土木工程", event)
        self.assertEqual(railway.exam_series_id, "special-railway")
        self.assertEqual(highway.exam_series_id, "special-highway")
        self.assertEqual(railway.level_id, "transport-employee")
        self.assertEqual(highway.level_id, "transport-employee")
        self.assertNotEqual(railway.bundle_id, highway.bundle_id)
        self.assertEqual(unresolved.confidence, "review")

    def test_explicit_railway_programme_with_missing_grade_requires_review(self) -> None:
        identity = classify("鐵路人員考試_會計", "警察人員及交通事業鐵路人員考試")
        self.assertEqual(identity.exam_series_id, "special-railway")
        self.assertEqual(identity.level_id, "unknown")
        self.assertEqual(identity.confidence, "review")

    def test_port_recruitment_grade_is_distinct_from_cohosted_patent_exam(self) -> None:
        event = "096年公務人員特種考試經濟部專利商標審查人員考試及96年特種考試交通事業港務人員考試"
        port = classify("輪機技術(佐級)", event, source="096270")
        patent = classify("生物技術(二等)", event, source="096270")
        self.assertEqual(port.exam_series_id, "special-port")
        self.assertEqual(port.level_id, "transport-associate")
        self.assertEqual(port.bundle_name, "港務人員特考｜佐級｜輪機技術")
        self.assertNotEqual(port.bundle_id, patent.bundle_id)

    def test_missing_level_evidence_is_reviewed_for_level_based_programmes(self) -> None:
        for provider in ("gept_cert", "jlpt_cert", "wdasec_skill"):
            with self.subTest(provider=provider):
                first = classify(
                    "官方試題", "官方測驗", provider=provider,
                    canonical="unresolved-level", subject="閱讀", source="event-first",
                )
                second = classify(
                    "官方試題", "官方測驗", provider=provider,
                    canonical="unresolved-level", subject="閱讀", source="event-second",
                )
                self.assertEqual(first.level_id, "unknown")
                self.assertEqual(first.confidence, "review")
                self.assertIn("missing official level", first.reason)
                self.assertNotEqual(first.bundle_id, second.bundle_id)

    def test_jlpt_out_of_range_level_is_unresolved(self) -> None:
        identity = classify("N6", "JLPT", provider="jlpt_cert", canonical="jlpt", subject="N6")
        self.assertEqual(identity.level_id, "unknown")
        self.assertEqual(identity.confidence, "review")

    def test_public_titles_use_source_labels_not_internal_ids(self) -> None:
        group = classify("一般行政（兩岸組一）", "115年公務人員高等考試三級", source="high-group-1")
        elective = classify("外交領事人員（選試日文）", "115年公務人員特種考試外交領事人員考試", source="diplomatic-115")
        staged = classify("律師", "115年專門職業及技術人員高等考試律師考試第二試", source="lawyer-115")

        self.assertIn("cross-strait-group-1", group.variant_ids)
        self.assertIn("cross-strait-group-1", group.bundle_id)
        self.assertTrue(group.bundle_name.endswith("｜兩岸組一"), group.bundle_name)
        self.assertTrue(any(v.startswith("elective-") for v in elective.variant_ids))
        self.assertIn("選試日文", elective.bundle_name)
        self.assertEqual(staged.stage_id, "stage-2")
        self.assertIn("stage-2", staged.bundle_id)
        self.assertTrue(staged.bundle_name.endswith("｜第二試"), staged.bundle_name)
        for identity in (group, elective, staged):
            self.assertNotRegex(identity.bundle_name, r"stage-\d|[a-z]{3,}-[a-z0-9]{2,}")

    def test_overlapping_variant_wording_appears_once(self) -> None:
        broadcast = classify("新聞廣播（選試日文、國語與閩南語播音）", "115年公務人員普通考試", source="ordinary-115")

        self.assertEqual(len(broadcast.variant_ids), 2)
        self.assertTrue(broadcast.bundle_name.endswith("｜選試日文、國語與閩南語播音"), broadcast.bundle_name)
        self.assertEqual(broadcast.bundle_name.count("選試"), 1)

    def test_whitespace_skill_subject_uses_its_recorded_subject_code(self) -> None:
        blank = classify("甲級", "技能檢定", provider="wdasec_skill", canonical="skill", subject="")
        whitespace = classify("甲級", "技能檢定", provider="wdasec_skill", canonical="skill", subject=" \t\u3000")
        self.assertEqual(whitespace, blank)
        self.assertEqual(whitespace.track_id, "0101")
        self.assertNotEqual(whitespace.confidence, "review")

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

        expected = _classify_paper_uncached(
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


def test_subject_track_titles_are_distinct_without_changing_identity():
    for provider, canonical, subjects in (
        ('ceec_ast', '分科測驗', ['物理', '化學']),
        ('ceec_gsat', '學科能力測驗', ['數學A', '數學B']),
    ):
        identities = [classify(canonical, f'115學年度{canonical}－{subject}',
                               provider=provider, canonical=canonical, subject=subject)
                      for subject in subjects]
        assert len({i.bundle_name for i in identities}) == 2
        for subject, identity in zip(subjects, identities):
            assert identity.bundle_name == f'{canonical}｜{subject}'
    skill = classify('乙級', '技能檢定', provider='wdasec_skill',
                     canonical='全國技術士技能檢定', subject='室內配線 乙級')
    assert skill.bundle_name == '全國技術士技能檢定｜乙級｜室內配線'
    tve = classify('統測', '115年統測', provider='tcte_tve',
                   canonical='四技二專統一入學測驗', subject='01機械群 專業科目(一)')
    assert tve.bundle_name == '四技二專統一入學測驗｜01機械群'


def test_mixed_script_occupation_names_cannot_collapse_to_ascii_fragment():
    pairs = [
        ('車床─CNC車床 乙級 學科', '銑床─CNC銑床 乙級 學科'),
        ('視覺傳達設計─平面設計PC 乙級 學科', '視覺傳達設計─包裝設計PC 乙級 學科'),
        ('視覺傳達設計─平面設計MAC 乙級 學科', '印前製程─MAC 乙級 學科'),
    ]
    for first, second in pairs:
        identities = [classify('乙級', '技能檢定', provider='wdasec_skill',
                               canonical='全國技術士技能檢定', subject=subject)
                      for subject in (first, second)]
        assert identities[0].bundle_id != identities[1].bundle_id
        assert all(identity.bundle_name.count('乙級') == 1 for identity in identities)
