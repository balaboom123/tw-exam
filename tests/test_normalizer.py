import unittest

from app.models import AliasRule, NormalizedCatalog, NormalizedPaper, ParsedPaper, ReviewItem
from app.normalizer import normalize_papers, renormalize_catalog


class NormalizePapersTests(unittest.TestCase):
    def test_ascii_official_records_receive_v2_identity_on_ingest_and_migration(self) -> None:
        parsed = ParsedPaper(
            category_raw="GSAT", category_code="101", subject_code="0101",
            subject_name_raw="English", files={"question": "https://official.example/question.pdf"},
        )
        aliases = [AliasRule(
            match_type="exact", raw_pattern="CEEC GSAT", canonical_id="ceec-gsat", canonical_name="CEEC GSAT",
        )]
        result = normalize_papers(
            source_exam_id="gsat-115-english", year_ad=2026, exam_name_raw="115 CEEC GSAT",
            papers=[parsed], alias_rules=aliases, mirror_base_url="", mirror_metadata={},
            provider_id="ceec_gsat",
        )
        paper = result.papers[0]
        self.assertEqual(paper.schema_version, 2)
        self.assertEqual(paper.exam_series_id, "admission-gsat")
        self.assertEqual(paper.bundle_id, "ceec-gsat-admission-gsat-not-applicable-ceec-gsat")
        self.assertEqual(paper.classification_confidence, "medium")

        legacy = NormalizedPaper(
            provider_id="ceec_gsat", canonical_id="ceec-gsat", canonical_name="CEEC GSAT",
            year_roc=115, exam_name_raw="115 CEEC GSAT", category_raw="GSAT",
            category_code="101", source_exam_id="gsat-115-english", subject_code="0101",
            subject_name_raw="English", paper_code="101-0101-question", file_type="question",
            download_url_source="https://official.example/question.pdf",
        )
        migrated = renormalize_catalog(NormalizedCatalog([legacy], []), aliases).papers[0]
        self.assertEqual(migrated.schema_version, 2)
        self.assertEqual(migrated.bundle_id, paper.bundle_id)
        self.assertEqual(migrated.canonical_id, legacy.canonical_id)

    def test_renormalization_keeps_ambiguous_canonicalization_review_until_resolved(self) -> None:
        parsed = ParsedPaper(
            category_raw="甲組暨乙組", category_code="301", subject_code="0101",
            subject_name_raw="國文", files={"question": "https://official.example/question.pdf"},
        )
        original = normalize_papers(
            source_exam_id="115030", year_ad=2026, exam_name_raw="專門職業及技術人員高等考試",
            papers=[parsed], alias_rules=[], mirror_base_url="", mirror_metadata={},
            provider_id="moex",
        )
        first = renormalize_catalog(original, alias_rules=[])
        second = renormalize_catalog(first, alias_rules=[])
        self.assertEqual(first, original)
        self.assertEqual(second, first)
        self.assertEqual(len(first.review_queue), 1)
        self.assertIn("legacy canonicalization requires review", first.review_queue[0].reason)

        resolved = renormalize_catalog(first, alias_rules=[AliasRule(
            match_type="exact", raw_pattern="甲組暨乙組", canonical_id="reviewed-program",
            canonical_name="甲組暨乙組",
        )])
        self.assertEqual(resolved.papers[0].canonical_id, first.papers[0].canonical_id)
        self.assertEqual(resolved.review_queue, [])

        historical = renormalize_catalog(NormalizedCatalog(papers=first.papers, review_queue=[]), alias_rules=[])
        self.assertEqual(historical.review_queue, [])

    def test_renormalization_rebuilds_review_queue_from_current_papers(self) -> None:
        resolved = NormalizedPaper(
            canonical_id="general-administration",
            canonical_name="一般行政",
            year_roc=115,
            exam_name_raw="115年公務人員高等考試三級考試",
            category_raw="三等_一般行政類科",
            subject_name_raw="行政學",
            paper_code="301-0608-question",
            file_type="question",
            download_url_source="https://example.test/resolved.pdf",
            category_code="301",
            source_exam_id="115010",
            subject_code="0608",
            provider_id="moex",
        )
        still_review = NormalizedPaper(
            canonical_id="司法官",
            canonical_name="司法官",
            year_roc=115,
            exam_name_raw="115年司法官考試",
            category_raw="司法官",
            subject_name_raw="刑法",
            paper_code="401-0101-question",
            file_type="question",
            download_url_source="https://example.test/review.pdf",
            category_code="401",
            source_exam_id="115020",
            subject_code="0101",
            provider_id="moex",
        )
        stale = ReviewItem(
            raw_category=resolved.category_raw,
            normalized_candidate=resolved.canonical_name,
            source_exam_id=resolved.source_exam_id,
            year_roc=resolved.year_roc,
            provider_id="moex",
            reason="stale review row",
        )

        rebuilt = renormalize_catalog(
            NormalizedCatalog(papers=[resolved, still_review], review_queue=[stale]),
            alias_rules=[],
        )

        self.assertEqual(len(rebuilt.review_queue), 1)
        self.assertEqual(rebuilt.review_queue[0].source_exam_id, "115020")
        self.assertEqual(rebuilt.review_queue[0].raw_category, "司法官")
        self.assertNotIn("stale review row", {item.reason for item in rebuilt.review_queue})

        preserved = renormalize_catalog(
            NormalizedCatalog(papers=[resolved, still_review], review_queue=[stale]),
            alias_rules=[],
            collect_reviews=False,
        )
        self.assertEqual(len(preserved.review_queue), 1)
        self.assertEqual(preserved.review_queue[0].reason, "stale review row")

    def test_alias_rules_override_general_canonicalization(self) -> None:
        papers = [
            ParsedPaper(
                category_raw="高等考試_護理師",
                category_code="101",
                subject_code="0101",
                subject_name_raw="基礎醫學",
                files={
                    "question": "https://example.test/q.pdf",
                    "answer": "https://example.test/a.pdf",
                },
            ),
        ]
        aliases = [
            AliasRule(
                match_type="exact",
                raw_pattern="高等考試_護理師",
                canonical_id="nurse",
                canonical_name="護理師",
            )
        ]

        normalized = normalize_papers(
            source_exam_id="115030",
            year_ad=2026,
            exam_name_raw="115年第一次專門職業及技術人員高等考試營養師、護理師、社會工作師考試",
            papers=papers,
            alias_rules=aliases,
            mirror_base_url="https://mirror.example/releases/download/moex",
            mirror_metadata={
                ("101", "0101", "question"): {"checksum": "abc123", "storage_key": "115/115030/101/0101/question.pdf"},
                ("101", "0101", "answer"): {"checksum": "def456", "storage_key": "115/115030/101/0101/answer.pdf"},
            },
        )

        self.assertEqual(len(normalized.papers), 2)
        self.assertEqual(normalized.review_queue, [])
        self.assertEqual(normalized.papers[0].canonical_id, "nurse")
        self.assertEqual(normalized.papers[0].canonical_name, "護理師")
        self.assertTrue(normalized.papers[0].download_url_mirror.endswith("/115/115030/101/0101/question.pdf"))

    def test_general_rules_reduce_known_exam_prefixes_without_aliases(self) -> None:
        papers = [
            ParsedPaper(
                category_raw="專技高考_社會工作師",
                category_code="103",
                subject_code="0301",
                subject_name_raw="社會工作",
                files={"question": "https://example.test/q.pdf"},
            )
        ]

        normalized = normalize_papers(
            source_exam_id="115030",
            year_ad=2026,
            exam_name_raw="115年第一次專門職業及技術人員高等考試營養師、護理師、社會工作師考試",
            papers=papers,
            alias_rules=[],
            mirror_base_url="",
            mirror_metadata={("103", "0301", "question"): {"checksum": "abc123", "storage_key": "115/115030/103/0301/question.pdf"}},
        )

        self.assertEqual(normalized.papers[0].canonical_name, "社會工作師")
        self.assertEqual(normalized.papers[0].canonical_id, "social-worker")
        self.assertEqual(normalized.review_queue, [])

    def test_ambiguous_categories_are_queued_for_review(self) -> None:
        papers = [
            ParsedPaper(
                category_raw="專門職業及技術人員高等考試護理師、心理師考試",
                category_code="999",
                subject_code="0001",
                subject_name_raw="綜合科目",
                files={"question": "https://example.test/q.pdf"},
            )
        ]

        normalized = normalize_papers(
            source_exam_id="114170",
            year_ad=2025,
            exam_name_raw="114年專門職業及技術人員高等考試護理師、心理師考試",
            papers=papers,
            alias_rules=[],
            mirror_base_url="",
            mirror_metadata={("999", "0001", "question"): {"checksum": "abc123", "storage_key": "114/114170/999/0001/question.pdf"}},
        )

        self.assertEqual(normalized.papers[0].canonical_name, "專門職業及技術人員高等考試護理師、心理師考試")
        self.assertEqual(len(normalized.review_queue), 1)
        self.assertEqual(normalized.review_queue[0]["raw_category"], "專門職業及技術人員高等考試護理師、心理師考試")


    def test_fallback_canonical_ids_use_full_string_hashes_instead_of_prefixes(self) -> None:
        papers = [
            ParsedPaper(
                category_raw="abcdefghX",
                category_code="101",
                subject_code="0101",
                subject_name_raw="Subject A",
                files={"question": "https://example.test/a.pdf"},
            ),
            ParsedPaper(
                category_raw="abcdefghY",
                category_code="102",
                subject_code="0101",
                subject_name_raw="Subject B",
                files={"question": "https://example.test/b.pdf"},
            ),
        ]

        normalized = normalize_papers(
            source_exam_id="115999",
            year_ad=2026,
            exam_name_raw="115 demo exam",
            papers=papers,
            alias_rules=[],
            mirror_base_url="",
            mirror_metadata={
                ("101", "0101", "question"): {"checksum": "aaa", "storage_key": "115/115999/101/0101/question.pdf"},
                ("102", "0101", "question"): {"checksum": "bbb", "storage_key": "115/115999/102/0101/question.pdf"},
            },
        )

        self.assertEqual(len(normalized.papers), 2)
        self.assertTrue(all(paper.canonical_id.startswith("canonical-") for paper in normalized.papers))
        self.assertNotEqual(normalized.papers[0].canonical_id, normalized.papers[1].canonical_id)

    def test_requested_topic_providers_use_stable_canonical_ids(self) -> None:
        cases = [
            ("teacher-qual-115", "教師資格考試", "teacher-qual", "教師資格考試"),
            ("teacher-recruit-newtaipei-115-junior", "新北市教師甄試", "teacher-recruit-newtaipei", "新北市教師甄試"),
            (
                "teacher-recruit-taoyuan-elementary-115",
                "桃園市國小教師甄試",
                "teacher-recruit-taoyuan-elementary",
                "桃園市國小教師甄試",
            ),
            ("teacher-recruit-kaohsiung-115-elementary", "高雄市教師甄試", "teacher-recruit-kaohsiung", "高雄市教師甄試"),
            (
                "teacher-recruit-central-alliance-115-elementary",
                "中區策略聯盟教師甄試",
                "teacher-recruit-central-alliance",
                "中區策略聯盟教師甄試",
            ),
            ("teacher-recruit-tainan-115", "臺南市國小教師甄試", "teacher-recruit-tainan", "臺南市國小教師甄試"),
            ("teacher-recruit-taipei-junior-114", "臺北市國中教師甄試", "teacher-recruit-taipei-junior", "臺北市國中教師甄試"),
            (
                "teacher-recruit-taipei-elementary-114",
                "臺北市國小教師甄試",
                "teacher-recruit-taipei-elementary",
                "臺北市國小教師甄試",
            ),
            ("gept-cert-materials", "GEPT全民英檢官方練習資料_初級", "gept-cert", "GEPT全民英檢"),
            ("gept-cert-elementary-2022", "GEPT全民英檢官方練習資料_初級", "gept-cert-elementary", "GEPT全民英檢 初級"),
            ("gept-cert-intermediate-2022", "GEPT全民英檢官方練習資料_中級", "gept-cert-intermediate", "GEPT全民英檢 中級"),
            ("jlpt-cert-practice-2018", "JLPT Japanese-Language Proficiency Test_N1", "jlpt-cert", "JLPT Japanese-Language Proficiency Test"),
            ("tocfl-cert-materials", "TOCFL華語文能力測驗官方參考資料", "tocfl-cert", "TOCFL華語文能力測驗"),
            ("tocfl-cert-2024", "TOCFL華語文能力測驗官方參考資料", "tocfl-cert", "TOCFL華語文能力測驗"),
            ("hakka-cert-materials", "客語能力認證官方教材及試題_四縣", "hakka-cert", "客語能力認證"),
            (
                "hakka-cert-basic-elementary-2025",
                "客語能力認證官方教材及試題_四縣",
                "hakka-cert-basic-elementary",
                "客語能力認證 基礎級暨初級",
            ),
            (
                "hakka-cert-intermediate-high-intermediate-2025",
                "客語能力認證官方教材及試題_海陸",
                "hakka-cert-intermediate-high-intermediate",
                "客語能力認證 中級暨中高級",
            ),
            ("taigi-cert-materials", "臺灣台語語言能力認證官方試題範例", "taigi-cert", "臺灣台語語言能力認證"),
            ("taigi-cert-a-2026", "臺灣台語語言能力認證官方試題範例", "taigi-cert-a", "臺灣台語語言能力認證 A卷"),
            ("taigi-cert-b-2026", "臺灣台語語言能力認證官方試題範例", "taigi-cert-b", "臺灣台語語言能力認證 B卷"),
            ("tqc-cert-samples", "TQC範例試卷_專業知識領域類", "tqc-cert", "TQC電腦技能基金會認證"),
            ("ipas-cert-ise-2026", "iPAS產業人才能力鑑定官方下載_ISE", "ipas-cert-ise", "iPAS 資訊安全工程師"),
            ("ipas-cert-oia-2026", "iPAS產業人才能力鑑定官方下載_OIA", "ipas-cert-oia", "iPAS 營運智慧分析師"),
            ("ipas-cert-aiap-2026", "iPAS產業人才能力鑑定官方下載_AIAP", "ipas-cert-aiap", "iPAS AI應用規劃師"),
            ("ipas-cert-aiot-2026", "iPAS產業人才能力鑑定官方下載_AIOT", "ipas-cert-aiot", "iPAS AIoT應用工程師"),
            ("ipas-cert-downloads", "iPAS產業人才能力鑑定官方下載_ISE", "ipas-cert", "iPAS產業人才能力鑑定"),
        ]
        for source_exam_id, category_raw, expected_id, expected_name in cases:
            with self.subTest(source_exam_id=source_exam_id):
                normalized = normalize_papers(
                    source_exam_id=source_exam_id,
                    year_ad=2026,
                    exam_name_raw=category_raw,
                    papers=[
                        ParsedPaper(
                            category_raw=category_raw,
                            category_code="topic",
                            subject_code="download",
                            subject_name_raw="download",
                            files={"question": "https://example.test/file.pdf"},
                        )
                    ],
                    alias_rules=[],
                    mirror_base_url="",
                    mirror_metadata={("topic", "download", "question"): {"checksum": "abc", "storage_key": "topic/download/question.pdf"}},
                )

                self.assertEqual(normalized.papers[0].canonical_id, expected_id)
                self.assertEqual(normalized.papers[0].canonical_name, expected_name)
                self.assertEqual(normalized.review_queue, [])


if __name__ == "__main__":
    unittest.main()
