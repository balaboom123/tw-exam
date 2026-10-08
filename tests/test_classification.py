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
    def test_ship_inspector_uses_its_own_level_in_a_cohosted_event(self) -> None:
        event = ("114年專門職業及技術人員高等考試大地工程技師考試分階段考試、驗船師、"
                 "引水人、第一次食品技師考試、高等暨普通考試消防設備人員考試、"
                 "普通考試地政士、專責報關人員考試、特種考試驗光人員考試")
        for category in ("驗船師高等考試_驗船師", "驗船師_驗船師", "驗船師"):
            with self.subTest(category=category):
                identity = classify(category, event)
                self.assertEqual(identity.exam_series_id, "professional-high")
                self.assertEqual(identity.level_id, "professional-high")
                self.assertEqual(identity.domain_id, "professional")
                self.assertEqual(identity.track_label, "驗船師")
                self.assertEqual(identity.confidence, "high")

    def test_ship_inspector_special_exam_does_not_inherit_legal_high_equivalence(self) -> None:
        event = ("097年第一次專門職業及技術人員高等暨普通考試消防設備人員考試、"
                 "普通考試不動產經紀人考試、97年特種考試中醫師、驗船師考試")
        identity = classify("驗船師", event)
        self.assertEqual(identity.exam_series_id, "professional-special")
        self.assertEqual(identity.level_id, "not-applicable")
        self.assertEqual(identity.confidence, "high")
        high = classify("驗船師", "098年專門職業及技術人員高等考試引水人、驗船師考試、特種考試中醫師考試")
        self.assertNotEqual(identity.bundle_id, high.bundle_id)

    def test_ship_inspector_does_not_consume_unrelated_or_unmarked_categories(self) -> None:
        for category, event in (("助理驗船師", "驗船師考試"),
                                ("驗船師", "驗船師測驗"),
                                ("驗船師", "驗船師升等考試"),
                                ("驗船師", "航海人員、驗船師考試")):
            with self.subTest(category=category, event=event):
                identity = classify(category, event)
                self.assertNotIn("native ship-inspector programme:", identity.reason)

    def test_ship_inspector_missing_event_wording_uses_exact_native_header_evidence(self) -> None:
        from app.moex_identity_evidence import moex_evidence_path, read_moex_category_identities
        from pathlib import Path

        facts = read_moex_category_identities(moex_evidence_path(Path(__file__).resolve().parents[1]))
        inspector_facts = [fact for fact in facts if fact.fact_id.endswith("ship-inspector-native-special")]
        self.assertEqual(len(inspector_facts), 3)
        for fact in inspector_facts:
            with self.subTest(source=fact.source_exam_id):
                identity = classify_paper(
                    provider_id="moex", source_exam_id=fact.source_exam_id,
                    year_ad=fact.year_ad, category_raw=fact.category_raw,
                    exam_name_raw=fact.exam_name_raw, category_code=fact.category_code,
                    canonical_id="ship-inspector", canonical_name="驗船師",
                )
                self.assertEqual(identity.exam_series_id, "professional-special")
                self.assertEqual(identity.level_id, "not-applicable")
                self.assertEqual(identity.confidence, "high")
                self.assertIn(fact.fact_id, identity.reason)

    def test_fishing_native_ranks_do_not_inherit_cohosted_navigation_or_legal_equivalence(self) -> None:
        event = "089年特種考試第二次航海人員、引水人、第二次船舶電信人員考試"
        identities = set()
        for rank in ("一級", "二級", "三級", "四級"):
            for role in ("漁航員", "輪機員"):
                with self.subTest(rank=rank, role=role):
                    identity = classify(rank + role, event)
                    self.assertEqual(identity.domain_id, "professional")
                    self.assertEqual(identity.exam_series_id, "professional-fishing-special")
                    self.assertEqual(identity.level_label, rank)
                    self.assertEqual(identity.track_label, role)
                    self.assertEqual(identity.bundle_name, f"漁船船員特考｜{rank}｜{role}")
                    self.assertEqual(identity.confidence, "high")
                    identities.add(identity.bundle_id)
        self.assertEqual(len(identities), 8)

    def test_fishing_reform_preserves_fifteen_native_qualifications(self) -> None:
        event = "091年第二次航海人員、漁船船員考試"
        categories = ("一等船長", "一等船副", "二等船長", "二等船副", "三等船長", "三等船副",
                      "一等輪機長", "一等大管輪", "一等管輪", "二等輪機長", "無線電子員",
                      "普通值機員", "限用值機員", "一級話務員", "二級話務員")
        identities = set()
        for category in categories:
            with self.subTest(category=category):
                identity = classify("漁船船員" + category, event)
                self.assertEqual(identity.exam_series_id, "professional-fishing-special")
                self.assertEqual(identity.domain_id, "professional")
                self.assertEqual(identity.confidence, "high")
                identities.add(identity.bundle_id)
        self.assertEqual(len(identities), 15)
        self.assertNotEqual(classify("一等船副", event).bundle_id,
                            classify("漁船船員一等船副", event).bundle_id)
        self.assertNotEqual(classify("普通值機員", "091年船舶電信人員、漁船船員考試").bundle_id,
                            classify("漁船船員普通值機員", event).bundle_id)

    def test_fishing_prefix_punctuation_and_compatibility_glyphs_do_not_fragment(self) -> None:
        event = "092年航海人員、驗船師、漁船船員考試"
        for categories in (("漁船船員_三等船副", "漁船船員三等船副"),
                           ("漁船船員一等輪機長", "漁船船員_一等輪機長"),
                           ("補考三級漁航員", "三級漁航員（補考）"),
                           ("一級漁航員(檢覈)", "一級漁航員（檢覈筆試）")):
            self.assertEqual(classify(categories[0], event).bundle_id, classify(categories[1], event).bundle_id)

    def test_fishing_screening_and_retake_remain_separate_from_regular_exams(self) -> None:
        event = "088年航海人員、漁船船員、船舶電信人員考試"
        ordinary = classify("一級漁航員", event)
        screening = classify("一級漁航員（檢覈）", event)
        self.assertEqual(screening.exam_series_id, "professional-fishing-screening")
        self.assertNotEqual(ordinary.bundle_id, screening.bundle_id)
        for category in ("三級漁航員", "二級輪機員", "製造主任技術員"):
            regular = classify(category, event)
            retake = classify("補考" + category, event)
            self.assertEqual(retake.variant_ids, ("subject-retake",))
            self.assertNotEqual(regular.bundle_id, retake.bundle_id)
        technical = classify("製造主任技術員", event)
        self.assertEqual(technical.level_id, "not-applicable")
        self.assertEqual(technical.level_label, "未分級")
        self.assertEqual(technical.track_label, "製造主任技術員")

    def test_fishing_does_not_consume_unsupported_native_combinations(self) -> None:
        for category, event in (("漁船船員三等管輪", "航海人員、漁船船員考試"),
                                ("漁船船員二等大管輪", "航海人員、漁船船員考試"),
                                ("漁船船員一等大副", "航海人員、漁船船員考試"),
                                ("三級漁航員(檢覈)", "漁船船員考試"),
                                ("漁船船員普通值機員", "漁船船員測驗"),
                                ("一級輪機員", "航海人員升等考試")):
            with self.subTest(category=category, event=event):
                self.assertFalse(classify(category, event).exam_series_id.startswith("professional-fishing-"))

    def test_navigation_uses_professional_programme_native_grade_and_occupation(self) -> None:
        event = "082年特種考試第一次航海人員驗船師考試"
        identities = set()
        for grade, roles in (("一等", ("船長", "大副", "船副", "輪機長", "大管輪", "管輪")),
                             ("二等", ("船長", "大副", "船副", "輪機長", "大管輪", "管輪")),
                             ("三等", ("船長", "船副", "輪機長", "管輪")),
                             ("正", ("駕駛", "司機")), ("副", ("駕駛", "司機"))):
            for role in roles:
                with self.subTest(grade=grade, role=role):
                    identity = classify(f"{grade}{role}", event)
                    self.assertEqual(identity.domain_id, "professional")
                    self.assertEqual(identity.exam_family_id, "professional-exam")
                    self.assertEqual(identity.exam_series_id, "professional-navigation-special")
                    self.assertEqual(identity.level_label, grade)
                    self.assertEqual(identity.track_label, role)
                    self.assertEqual(identity.confidence, "high")
                    self.assertEqual(identity.bundle_name, f"航海人員特考｜{grade}｜{role}")
                    identities.add(identity.bundle_id)
        self.assertEqual(len(identities), 20)

    def test_navigation_prefixes_unicode_and_route_spellings_do_not_fragment_bundles(self) -> None:
        event = "092年專門職業及技術人員特種考試不動產經紀人、航海人員、漁船船員考試"
        for spellings, variants in (
            (("一等管輪", "航海人員一等管輪", "航海人員_一等管輪"), ()),
            (("一等管輪(加註)", "一等管輪（加註）", "一等管輪(加註）", "加註一等管輪", "航海人員一等管輪（加註）"), ("engine-endorsement",)),
            (("海軍一等船副", "一等船副(海軍)", "航海人員_一等船副（海軍）"), ("navy-transfer",)),
            (("補考二等船副", "二等船副（補考）"), ("subject-retake",)),
        ):
            expected = classify(spellings[0], event)
            for category in spellings:
                with self.subTest(category=category):
                    actual = classify(category, event)
                    self.assertEqual(actual.bundle_id, expected.bundle_id)
                    self.assertEqual(actual.bundle_name, expected.bundle_name)
                    self.assertEqual(actual.variant_ids, variants)
        self.assertEqual(classify("一等大副(海軍補考)", event).variant_ids, ("navy-transfer", "subject-retake"))

    def test_navigation_engine_endorsement_and_naval_routes_keep_separate_papers(self) -> None:
        event = "088年第一次航海人員、驗船師、船舶電信人員考試"
        identities = [classify(c, event) for c in ("一等管輪", "一等管輪（加註）", "海軍一等管輪", "一等管輪（補考）")]
        self.assertEqual(len({i.bundle_id for i in identities}), 4)
        self.assertIn("主機加註", identities[1].bundle_name)
        self.assertIn("海軍轉任", identities[2].bundle_name)

    def test_navigation_special_high_ordinary_and_legacy_cases_keep_their_boundaries(self) -> None:
        special = "098年第二次專門職業及技術人員特種考試航海人員考試"
        combined = "098年第一次專門職業及技術人員高等暨普通考試航海人員考試"
        for grade, prefix, series in (("一等", "高考", "professional-navigation-high"), ("二等", "普考", "professional-navigation-ordinary")):
            for role in ("船副", "管輪"):
                with self.subTest(grade=grade, role=role):
                    old = classify(f"{grade}{role}", special)
                    current = classify(f"{grade}{role}", combined)
                    explicit = classify(f"專技{prefix}_{grade}{role}", combined)
                    abbreviated = classify(f"{prefix}_{grade}{role}", combined)
                    retake = classify(f"{prefix}_{grade}{role}", combined + "【舊案補考】")
                    self.assertEqual(current.exam_series_id, series)
                    self.assertEqual(current.level_label, grade)
                    self.assertEqual(current.bundle_id, explicit.bundle_id)
                    self.assertEqual(current.bundle_id, abbreviated.bundle_id)
                    self.assertNotEqual(old.bundle_id, current.bundle_id)
                    self.assertNotEqual(current.bundle_id, retake.bundle_id)
                    self.assertEqual(retake.variant_ids, ("legacy-case-retake",))
                    self.assertIn("舊案補考", retake.bundle_name)

    def test_navigation_does_not_consume_fishing_crew_promotion_or_unsupported_grades(self) -> None:
        for category, event in (
            ("漁船船員一等船副", "航海人員、漁船船員考試"),
            ("一級漁航員", "航海人員、漁船船員考試"),
            ("一級輪機員", "航海人員、漁船船員考試"),
            ("一等船副", "航海人員升等考試"),
            ("一等船副", "航海人員測驗"),
            ("一等船副", "其他人員考試"),
            ("三等大副", "航海人員考試"),
            ("一等駕駛", "航海人員考試"),
            ("正船副", "航海人員考試"),
            ("一等船副（加註）", "航海人員考試"),
            ("專技普考_一等管輪", "高等暨普通考試航海人員考試"),
        ):
            with self.subTest(category=category, event=event):
                self.assertFalse(classify(category, event).exam_series_id.startswith("professional-navigation-"))

    def test_ship_radio_uses_native_grades_and_occupations(self) -> None:
        event = "090年專門職業及技術人員特種考試航海人員、船舶電信人員、漁船船員考試"
        bundles = set()
        for category, level, track, native in (
            ("通用級報務員", "radio-general", "radio-telegraphist", "通用級"),
            ("一等報務員", "grade-1", "radio-telegraphist", "一等"),
            ("二等報務員", "grade-2", "radio-telegraphist", "二等"),
            ("特別級報務員", "radio-special", "radio-telegraphist", "特別級"),
            ("通用級話務員", "radio-general", "radio-telephonist", "通用級"),
            ("限用級話務員", "radio-limited", "radio-telephonist", "限用級"),
            ("一等無線電子員", "grade-1", "radio-electronic-operator", "一等"),
            ("二等無線電子員", "grade-2", "radio-electronic-operator", "二等"),
            ("普通值機員", "radio-ordinary", "radio-operator", "普通"),
            ("限用值機員", "radio-limited", "radio-operator", "限用"),
            ("通用值機員", "radio-general", "radio-operator", "通用"),
        ):
            with self.subTest(category=category):
                identity = classify(category, event)
                self.assertEqual(identity.domain_id, "professional")
                self.assertEqual(identity.exam_family_id, "professional-exam")
                self.assertEqual(identity.exam_series_id, "professional-ship-radio")
                self.assertEqual(identity.level_id, level)
                self.assertEqual(identity.level_label, native)
                self.assertEqual(identity.track_id, track)
                self.assertEqual(identity.confidence, "high")
                self.assertIn(f"船舶電信人員特考｜{native}｜", identity.bundle_name)
                bundles.add(identity.bundle_id)
        self.assertEqual(len(bundles), 11)

    def test_ship_radio_programme_does_not_inherit_a_cohosted_profession(self) -> None:
        historical = classify("二等無線電子員", "088年航海人員、驗船師、船舶電信人員考試")
        later = classify("船舶電信人員二等無線電子員", "095年中醫師、驗船師、船舶電信人員、漁船船員考試暨專技普通考試")
        self.assertEqual(historical.bundle_id, later.bundle_id)
        self.assertEqual(later.level_id, "grade-2")
        self.assertNotIn("ordinary", later.level_id)

    def test_radio_retake_spelling_is_equivalent_but_regular_papers_stay_separate(self) -> None:
        event = "087年航海人員、船舶電信人員考試"
        for category in ("補考二等報務員", "二等報務員(補考)", "二等報務員(補考）", "二等報務員（補考）"):
            with self.subTest(category=category):
                retake = classify(category, event)
                expected = classify("二等報務員(補考)", event)
                regular = classify("二等報務員", event)
                self.assertEqual(retake.bundle_id, expected.bundle_id)
                self.assertEqual(retake.variant_ids, ("subject-retake",))
                self.assertNotEqual(retake.bundle_id, regular.bundle_id)
                self.assertIn("補考", retake.bundle_name)

    def test_radio_navy_transfer_keeps_its_own_paper_set(self) -> None:
        event = "088年航海人員、船舶電信人員、漁船船員考試"
        navy = classify("海軍二等報務員", event)
        suffix = classify("二等報務員（海軍）", event)
        regular = classify("二等報務員", event)
        retake = classify("二等報務員（補考）", event)
        self.assertEqual(navy.bundle_id, suffix.bundle_id)
        self.assertEqual(navy.variant_ids, ("navy-transfer",))
        self.assertIn("海軍轉任", navy.bundle_name)
        self.assertEqual(len({navy.bundle_id, regular.bundle_id, retake.bundle_id}), 3)

    def test_ship_radio_does_not_consume_other_programmes_or_unsupported_categories(self) -> None:
        for category, event in (
            ("漁船船員普通值機員", "091年航海人員、漁船船員考試"),
            ("漁船船員普通值機員", "船舶電信人員、漁船船員考試"),
            ("報務員", "船舶電信人員考試"),
            ("三等報務員", "船舶電信人員考試"),
            ("特別級話務員", "船舶電信人員考試"),
            ("通用級報務員", "交通事業電信人員升資考試"),
            ("一等報務員", "其他人員考試"),
        ):
            with self.subTest(category=category, event=event):
                self.assertNotEqual(classify(category, event).exam_series_id, "professional-ship-radio")

    def test_taigi_forms_expose_native_proficiency_bands_and_separate_variants(self) -> None:
        identities = []
        for form, level in (("A", "cefr-a1-a2"), ("B", "cefr-b1-b2"), ("C", "cefr-c1-c2")):
            with self.subTest(form=form):
                identity = classify(f"臺灣台語官方試題範例_{form}卷", "官方範例", provider="taigi_cert")
                self.assertEqual(identity.level_id, level)
                self.assertEqual(identity.variant_ids, (f"paper-form-{form.lower()}",))
                self.assertEqual(identity.confidence, "high")
                self.assertIn(f"{form}卷", identity.bundle_name)
                self.assertNotIn("paper-", identity.level_id)
                identities.append(identity.bundle_id)
        self.assertEqual(len(set(identities)), 3)
        for category, subject in (("官方範例", ""), ("D卷", ""), ("A卷", "B卷音檔")):
            with self.subTest(category=category, subject=subject):
                identity = classify(category, "官方範例", provider="taigi_cert", subject=subject)
                self.assertEqual(identity.level_id, "unknown")
                self.assertEqual(identity.confidence, "review")
                self.assertEqual(identity.variant_ids, ())

    def test_security_programme_heading_is_not_a_second_track(self) -> None:
        for programme, event, track in (
            ("國家安全情報人員", "國家安全局國家安全情報人員考試", "資訊組"),
            ("調查人員", "法務部調查局調查人員考試", "調查工作組"),
        ):
            with self.subTest(programme=programme):
                historical = classify(f"{programme}考試三等考試{track}（選試英文）", event)
                current = classify(f"三等考試_{track}（選試英文）", event)
                self.assertEqual(historical.bundle_id, current.bundle_id)
                self.assertEqual(historical.bundle_name, current.bundle_name)
                self.assertEqual(historical.variant_ids, current.variant_ids)
                other = classify(f"三等考試_{track}（選試日文）", event)
                self.assertNotEqual(current.bundle_id, other.bundle_id)

    def test_police_programmes_do_not_merge_matching_levels_and_tracks(self) -> None:
        event = "115年公務人員特種考試警察人員考試、一般警察人員考試、國家安全情報人員考試"
        police = classify("警察人員考試三等考試_行政警察人員", event)
        general = classify("一般警察人員考試三等考試_行政警察人員", event)
        self.assertEqual(police.exam_series_id, "special-police")
        self.assertEqual(general.exam_series_id, "special-general-police")
        self.assertEqual(police.level_id, general.level_id)
        self.assertEqual(police.track_id, general.track_id)
        self.assertNotEqual(police.bundle_id, general.bundle_id)
        self.assertTrue(police.bundle_name.startswith("警察人員特考｜"))
        self.assertTrue(general.bundle_name.startswith("一般警察人員特考｜"))

    def test_general_police_only_event_can_resolve_unmarked_category(self) -> None:
        identity = classify("三等考試_行政警察人員", "公務人員特種考試一般警察人員考試")
        # An explicit programme heading or unambiguous event is required; a
        # police occupation alone cannot override the general-police event.
        self.assertEqual(identity.exam_series_id, "special-general-police")

    def test_unmarked_category_in_combined_police_event_is_review_isolated(self) -> None:
        event = "101年公務人員特種考試警察人員考試、一般警察人員考試、交通事業鐵路人員考試"
        identity = classify("三等考試_行政管理人員", event, source="101080")
        self.assertEqual(identity.exam_series_id, "moex-unknown")
        self.assertEqual(identity.confidence, "review")
        self.assertIn("event-101080", identity.bundle_id)
        self.assertIn("official programme cannot be resolved", identity.reason)

    def test_national_security_programme_does_not_inherit_cohosted_police_identity(self) -> None:
        event = "112年公務人員特種考試警察人員考試、一般警察人員考試、國家安全局國家安全情報人員考試"
        national = classify("國家安全情報人員考試三等考試_資訊組", event)
        police = classify("警察人員考試三等考試_資訊組", event)
        investigation = classify("三等考試_資訊組", "公務人員特種考試法務部調查局調查人員考試")
        self.assertEqual(national.exam_series_id, "special-national-security")
        self.assertEqual(investigation.exam_series_id, "special-investigation")
        self.assertEqual(len({national.bundle_id, police.bundle_id, investigation.bundle_id}), 3)

    def test_historical_national_security_uses_its_recorded_grade(self) -> None:
        identity = classify("乙等_情報科技組", "083年特種考試國家安全局國家安全情報人員考試", source="083160")
        self.assertEqual(identity.exam_series_id, "special-national-security")
        self.assertEqual(identity.level_id, "grade-b")

    def test_explicit_graded_security_programme_without_grade_requires_review(self) -> None:
        for category, event, series in (
            ("國家安全情報人員資訊組", "國家安全局國家安全情報人員考試", "special-national-security"),
            ("調查人員調查工作組", "國家安全情報人員及法務部調查局調查人員考試", "special-investigation"),
        ):
            with self.subTest(series=series):
                identity = classify(category, event)
                self.assertEqual(identity.exam_series_id, series)
                self.assertEqual(identity.level_id, "unknown")
                self.assertEqual(identity.confidence, "review")

    def test_mid_category_police_programme_heading_is_preserved(self) -> None:
        event = "100年公務人員特種考試一般警察人員考試、警察人員考試、交通事業鐵路人員考試"
        regular = classify("三等考試_警察特考_行政警察人員", event)
        general = classify("三等考試_一般警察人員_行政警察人員", event)
        self.assertEqual(regular.exam_series_id, "special-police")
        self.assertEqual(general.exam_series_id, "special-general-police")
        self.assertNotEqual(regular.bundle_id, general.bundle_id)

    def test_security_cohosted_qualification_is_not_intelligence_recruitment(self) -> None:
        event = "083年特種考試國家安全局國家安全情報人員考試、國家安全會議暨國家安全局現職文職人員任用資格考試"
        identity = classify("簡任行政", event, source="083130")
        self.assertEqual(identity.exam_series_id, "moex-unknown")
        self.assertEqual(identity.confidence, "review")

    def test_personnel_category_boilerplate_does_not_fragment_tracks(self) -> None:
        event = "公務人員特種考試警察人員考試、一般警察人員考試"
        for programme in ("警察人員", "一般警察人員"):
            for track in ("行政警察人員", "水上警察人員輪機組", "刑事警察人員數位鑑識組"):
                with self.subTest(programme=programme, track=track):
                    old = classify(f"{programme}考試三等考試_{track}", event, source="113060")
                    recent = classify(f"{programme}考試三等考試_{track.replace('人員', '人員類別')}", event, source="115060")
                    self.assertEqual(old.bundle_id, recent.bundle_id)
                    self.assertEqual(old.bundle_name, recent.bundle_name)
        navigation = classify("一般警察人員考試四等考試_水上警察人員類別航海組", event)
        engineering = classify("一般警察人員考試四等考試_水上警察人員類別輪機組", event)
        self.assertNotEqual(navigation.track_id, engineering.track_id)

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
        self.assertEqual(promotion.exam_series_id, "promotion-railway")
        self.assertEqual(promotion.level_id, "promotion-employee-to-senior")
        self.assertNotEqual(recruitment.bundle_id, promotion.bundle_id)

    def test_promotion_programme_precedes_a_recruitment_occupation_marker(self) -> None:
        for occupation in ("司法行政", "警察行政", "海巡行政", "移民行政", "外交事務", "原住民族行政"):
            with self.subTest(occupation=occupation):
                promoted = classify(f"公務人員升官等公務薦任_{occupation}", "公務人員、關務人員升官等考試")
                self.assertEqual(promoted.exam_series_id, "civil-promotion")
                self.assertEqual(promoted.exam_family_id, "civil-promotion")
                self.assertEqual(promoted.level_id, "recommended-rank")

    def test_police_promotion_is_separate_from_recruitment_and_civil_promotion(self) -> None:
        event = "111年警察人員升官等考試、交通事業郵政、公路人員升資考試"
        promoted = classify("警察人員警正_行政警察人員", event)
        recruited = classify("警察人員考試三等考試_行政警察人員", "警察人員特種考試")
        civil = classify("公務人員升官等公務薦任_警察行政", "公務人員升官等考試")
        self.assertEqual(promoted.exam_series_id, "promotion-police")
        self.assertEqual(promoted.level_id, "police-senior")
        self.assertTrue(promoted.bundle_name.startswith("警察人員升官等考試｜警正｜"))
        self.assertEqual(len({promoted.bundle_id, recruited.bundle_id, civil.bundle_id}), 3)
        commissioned = classify("警監_行政警察人員", "警察人員升官等考試")
        self.assertEqual(commissioned.level_label, "警監")
        self.assertNotEqual(commissioned.bundle_id, promoted.bundle_id)

    def test_transport_promotion_sectors_do_not_merge_equal_ranks_and_tracks(self) -> None:
        event = "交通事業鐵路、公路、港務、郵政人員升資考試"
        bundles = set()
        for sector, series in (("鐵路", "railway"), ("公路", "highway"), ("港務", "port"), ("郵政", "postal")):
            with self.subTest(sector=sector):
                identity = classify(f"交通事業{sector}人員佐級晉員級_業務類", event)
                self.assertEqual(identity.exam_series_id, f"promotion-{series}")
                self.assertEqual(identity.level_id, "promotion-associate-to-employee")
                bundles.add(identity.bundle_id)
        self.assertEqual(len(bundles), 4)
        unknown = classify("佐級晉員級_業務類", event)
        self.assertEqual(unknown.confidence, "review")
        self.assertEqual(unknown.exam_series_id, "moex-unknown")

    def test_historical_transport_sector_after_rank_remains_evidence(self) -> None:
        identity = classify("員級晉高員級_電信人員業務類報務", "082年交通事業電信、水運、民航人員升資考試")
        self.assertEqual(identity.exam_series_id, "promotion-telecommunications")
        self.assertEqual(identity.confidence, "high")
        spaced = classify("員級 晉高員級_鐵路人員技術類", "交通事業鐵路人員升資考試")
        compact = classify("員級晉高員級_鐵路人員技術類", "交通事業鐵路人員升資考試")
        self.assertEqual(spaced.level_id, "promotion-employee-to-senior")
        self.assertEqual(spaced.bundle_id, compact.bundle_id)

    def test_customs_and_civil_promotion_do_not_share_rank_identity(self) -> None:
        event = "公務人員升官等考試、關務人員升官等考試"
        civil = classify("公務薦任_技術類", event)
        customs = classify("關務薦任_技術類", event)
        unknown = classify("薦任_技術類", event)
        self.assertEqual(civil.exam_series_id, "civil-promotion")
        self.assertEqual(customs.exam_series_id, "promotion-customs")
        self.assertEqual(civil.level_id, customs.level_id)
        self.assertNotEqual(civil.bundle_id, customs.bundle_id)
        self.assertEqual(unknown.exam_series_id, "moex-unknown")
        self.assertEqual(unknown.confidence, "review")
        historical = classify("簡任升等_關務", "087年關務人員升等考試")
        self.assertEqual(historical.exam_series_id, "promotion-customs")

    def test_transport_promotion_destination_rank_preserves_official_transition(self) -> None:
        # ROC 93 official telecommunications headers spell out the transition
        # where the source category abbreviates its destination rank.
        for marker, transition in (("高級員", "employee-to-senior"), ("員級", "associate-to-employee"), ("佐級", "worker-to-associate")):
            with self.subTest(marker=marker):
                identity = classify(f"物料({marker})", "093年交通事業電信人員升資考試", source="093280")
                self.assertEqual(identity.exam_series_id, "promotion-telecommunications")
                self.assertEqual(identity.level_id, f"promotion-{transition}")
                self.assertEqual(identity.confidence, "high")
        recruited = classify("佐級_物料", "交通事業鐵路人員特種考試")
        self.assertEqual(recruited.level_id, "transport-associate")

    def test_transport_technical_topic_does_not_change_promotion_sector(self) -> None:
        for track in ("技術類電信機務", "技術類電信線務"):
            with self.subTest(track=track):
                port = classify(f"士級晉佐級_{track}", "083年交通事業港務人員升資考試", source="083280")
                self.assertEqual(port.exam_series_id, "promotion-port")
        railway = classify("佐級晉員級_公路工程", "交通事業鐵路人員升資考試")
        self.assertEqual(railway.exam_series_id, "promotion-railway")

    def test_transport_promotion_sector_suffix_is_distinct_from_a_technical_topic(self) -> None:
        event = "交通事業鐵路、公路、港務人員升資考試"
        for suffix, series in (("-公路", "highway"), ("(公路總局)", "highway"), ("(鐵路)", "railway"), ("-臺灣港務公司", "port"), ("(基隆港)", "port")):
            with self.subTest(suffix=suffix):
                identity = classify(f"佐級晉員級_業務類{suffix}", event)
                self.assertEqual(identity.exam_series_id, f"promotion-{series}")
        topic = classify("佐級晉員級_技術類(選試鐵路工程概要)", event)
        self.assertEqual(topic.exam_series_id, "moex-unknown")
        self.assertEqual(topic.confidence, "review")

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

        self.assertEqual(worker_promotion.exam_series_id, "promotion-railway")
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
