import unittest

from app.publication_metadata import derive_public_metadata, publication_subject_label


class PublicationMetadataTests(unittest.TestCase):
    def test_taipower_file_prefix_does_not_claim_a_native_written_period(self) -> None:
        for raw in (
            "101年新進養成班試題科目A_基本電學",
            "101年新進養成班試題解答A_基本電學",
        ):
            with self.subTest(raw=raw):
                self.assertEqual(
                    publication_subject_label(raw, provider_id="taipower_recruit"), "基本電學"
                )

    def test_taipower_known_source_formats_keep_the_complete_subject(self) -> None:
        examples = {
            "99年新進養成班試題試題A_測量、土木、工程概要": "測量、土木、工程概要",
            "102年度新進僱用人員甄試科目試題__共同(國文、英文)": "共同(國文、英文)",
            "104企業管理概論、法律常識題目": "企業管理概論、法律常識",
            "104企業管理概論、法律常識答案": "企業管理概論、法律常識",
            "107年12月新進僱用人員甄試科目試題_分鏡腳本設計": "分鏡腳本設計",
            "111年度新進僱用人員甄試試題_輸配電學.pdf": "輸配電學",
            "115年度新進僱用人員甄試答案_行政學概要、法律常識": "行政學概要、法律常識",
            "115年度新進僱用人員甄試試題_主題_甲組": "主題_甲組",
        }
        for raw, expected in examples.items():
            with self.subTest(raw=raw):
                self.assertEqual(
                    publication_subject_label(raw, provider_id="taipower_recruit"), expected
                )

    def test_other_provider_and_unknown_taipower_formats_keep_their_wording(self) -> None:
        raw = "101年新進養成班試題科目A_基本電學"
        self.assertEqual(publication_subject_label(raw, provider_id="moea_recruit"), raw)
        unknown = "參考資料_基本電學_第一階段.pdf"
        self.assertEqual(
            publication_subject_label(unknown, provider_id="taipower_recruit"), unknown
        )

    def test_whole_taipower_programme_keeps_historical_subjects_searchable(self) -> None:
        subjects = [f"distinct subject {index}" for index in range(54)]
        papers = [
            {
                "provider_id": "taipower_recruit",
                "subject_name_raw": f"115年度新進僱用人員甄試試題_{subject}",
                "exam_name_raw": "115年度台電新進僱用人員甄試",
            }
            for subject in subjects
        ]
        aliases, labels = derive_public_metadata(
            papers,
            bundle_id="taipower-recruit-employment-recruitment-not-applicable-taipower-recruit",
            canonical_name="台電新進僱用人員甄試",
        )
        self.assertTrue(set(subjects).issubset(aliases))
        self.assertLessEqual(len(aliases), 64)
        self.assertEqual(labels, [])
        self.assertEqual(papers[0]["subject_name_raw"], f"115年度新進僱用人員甄試試題_{subjects[0]}")

    def test_generic_skill_bundle_exposes_clean_subject_label_and_codes(self) -> None:
        aliases, labels = derive_public_metadata(
            [
                {
                    "subject_name_raw": "冷凍空調裝修 丙級 學科",
                    "category_raw": "全國技術士技能檢定",
                    "exam_name_raw": "115年度全國技術士技能檢定第1梯次學科試題暨答案",
                    "category_code": "09501",
                    "subject_code": "09501-class_c-question",
                }
            ],
            bundle_id="wdasec-skill-skill-certification-class-c-example",
            canonical_name="全國技術士技能檢定｜丙級",
        )

        self.assertEqual(labels, ["冷凍空調裝修"])
        self.assertIn("冷凍空調裝修", aliases)
        self.assertIn("09501", aliases)

    def test_admission_labels_split_source_subjects_and_strip_file_descriptors(self) -> None:
        aliases, labels = derive_public_metadata(
            [
                {
                    "subject_name_raw": "數學A 試題內容",
                    "category_raw": "學科能力測驗",
                    "exam_name_raw": "115學年度學科能力測驗",
                    "category_code": "",
                    "subject_code": "math-a",
                }
            ],
            bundle_id="ceec-gsat-admission-gsat-not-applicable-a",
            canonical_name="學科能力測驗",
        )

        self.assertEqual(labels, ["數學A"])
        self.assertIn("數學A", aliases)

    def test_non_generic_bundle_keeps_aliases_searchable_without_row_labels(self) -> None:
        aliases, labels = derive_public_metadata(
            [{"subject_name_raw": "行政法", "category_raw": "一般行政", "exam_name_raw": "高等考試"}],
            bundle_id="moex-civil-high-grade-3-general-administration",
            canonical_name="高等考試｜三等／高考三級｜一般行政",
        )

        self.assertIn("行政法", aliases)
        self.assertEqual(labels, [])


if __name__ == "__main__":
    unittest.main()
