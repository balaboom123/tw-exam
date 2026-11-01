"""Tests for the teacher_recruit_central_alliance provider."""

import unittest
from unittest.mock import patch

from app.providers.teacher_recruit_central_alliance.client import (
    BASE_URL,
    CentralAllianceRecruitClient,
    parse_final_answer_page,
    parse_subject_page,
)

SUBJECT_HTML = """
<table>
  <tr>
    <td>1</td><td>國語文</td>
    <td>試題：<a href="./download.php?seq=q1&type=question">115中策_國語文_試題.pdf</a></td>
    <td>參考解答：
      <a href="./download.php?seq=q1&type=referenceanswer">115中策_國語文答案.pdf</a></td>
  </tr>
</table>
"""

FINAL_HTML = """
<table>
  <tr>
    <td>1</td><td>國語文</td>
    <td><a href="./download.php?seq=f1&type=finalanswer">115中策_國語文答案.pdf</a></td>
  </tr>
</table>
"""


class CentralAllianceRecruitParserTests(unittest.TestCase):
    subject_url = f"{BASE_URL}/Subject/news.php?cate=B"
    final_url = f"{BASE_URL}/Ans2/news.php?cate=B"

    def test_repeated_identical_native_links_share_one_reference_per_role(self) -> None:
        papers = parse_subject_page(self.subject_url, "elementary", "國小", SUBJECT_HTML * 2)
        self.assertEqual(len(papers), 1)
        self.assertEqual(set(papers[0].downloads), {"question", "answer"})
        self.assertEqual(
            parse_final_answer_page(self.final_url, FINAL_HTML * 2),
            parse_final_answer_page(self.final_url, FINAL_HTML),
        )

    def test_competing_source_urls_cannot_silently_overwrite_a_native_role(self) -> None:
        for role in ["question", "referenceanswer"]:
            html = SUBJECT_HTML + SUBJECT_HTML.replace(f"seq=q1&type={role}", f"seq=q2&type={role}")
            with self.subTest(role=role):
                with self.assertRaisesRegex(ValueError, "Competing Central Alliance source URLs"):
                    parse_subject_page(self.subject_url, "elementary", "國小", html)
        with self.assertRaisesRegex(ValueError, "Competing Central Alliance source URLs"):
            parse_final_answer_page(
                self.final_url, FINAL_HTML + FINAL_HTML.replace("seq=f1", "seq=f2")
            )

    def test_source_role_query_and_owner_cannot_conflict(self) -> None:
        for href in [
            "https://unrelated.test/download.php?seq=q1&type=question",
            "./download.php?seq=q1&type=question&type=referenceanswer",
        ]:
            with self.subTest(href=href):
                with self.assertRaises(ValueError):
                    parse_subject_page(
                        self.subject_url,
                        "elementary",
                        "國小",
                        SUBJECT_HTML.replace("./download.php?seq=q1&type=question", href),
                    )

    def test_absolute_official_http_links_keep_their_original_source_url(self) -> None:
        source = BASE_URL.replace("https://", "http://")
        url = f"{source}/Subject/download.php?seq=q1&type=question"
        papers = parse_subject_page(
            self.subject_url,
            "elementary",
            "國小",
            SUBJECT_HTML.replace("./download.php?seq=q1&type=question", url),
        )
        self.assertEqual(papers[0].downloads["question"], url)

    def test_a_different_explicit_native_file_year_requires_review(self) -> None:
        for surface, html in [("subject", SUBJECT_HTML), ("final", FINAL_HTML)]:
            changed = html.replace("115中策_", "114中策_")
            with self.subTest(surface=surface):
                with self.assertRaisesRegex(ValueError, "another source year"):
                    if surface == "subject":
                        parse_subject_page(self.subject_url, "elementary", "國小", changed)
                    else:
                        parse_final_answer_page(self.final_url, changed)

    def test_an_empty_subject_is_not_a_new_public_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "lacks its subject label"):
            parse_subject_page(
                self.subject_url,
                "elementary",
                "國小",
                SUBJECT_HTML.replace("國語文", ""),
            )

    def test_parse_subject_page_pairs_question_and_reference_answer(self) -> None:
        papers = parse_subject_page(
            "https://qa115-tse-cl.twrecruit.com.tw/Subject/news.php?cate=B",
            "elementary",
            "國小",
            SUBJECT_HTML,
        )

        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].level_code, "elementary")
        self.assertEqual(papers[0].level_name, "國小")
        self.assertEqual(papers[0].subject_name, "國語文")
        self.assertTrue(papers[0].downloads["question"].endswith("type=question"))
        self.assertTrue(papers[0].downloads["answer"].endswith("type=referenceanswer"))

    def test_parse_final_answer_page_adds_corrected_answer(self) -> None:
        final_answers = parse_final_answer_page(
            "https://qa115-tse-cl.twrecruit.com.tw/Ans2/news.php?cate=B", FINAL_HTML
        )

        self.assertTrue(final_answers["國語文"].endswith("type=finalanswer"))


class CentralAllianceRecruitClientTests(unittest.TestCase):
    def test_invalid_annual_event_is_rejected_before_reading_any_source(self) -> None:
        invalid = [
            ("teacher-recruit-central-alliance-114-elementary", 2026),
            ("teacher-recruit-central-alliance-115-elementary", 2025),
            ("teacher-recruit-central-alliance-115-elementary", 2027),
            ("unrelated-elementary", 2026),
            ("teacher-recruit-central-alliance-115-senior", 2026),
        ]
        for event, year in invalid:
            with self.subTest(event=event, year=year):
                client = CentralAllianceRecruitClient()
                with patch.object(client, "_subject_html") as subject:
                    with patch.object(client, "_final_html") as final:
                        with self.assertRaises(ValueError):
                            client.fetch_exam_page(event, year)
                        subject.assert_not_called()
                        final.assert_not_called()

    def test_a_final_key_cannot_silently_disappear_without_a_matching_subject(self) -> None:
        client = CentralAllianceRecruitClient(
            subject_html_by_level={"elementary": SUBJECT_HTML},
            final_html_by_level={"elementary": FINAL_HTML.replace("國語文", "音樂")},
        )
        with self.assertRaisesRegex(ValueError, "lacks its matching native subject"):
            client.fetch_exam_page("teacher-recruit-central-alliance-115-elementary", 2026)

    def test_discovery_urls_preserve_the_official_level_mapping(self) -> None:
        client = CentralAllianceRecruitClient()

        self.assertEqual(client.build_discovery_year_url(2026), f"{BASE_URL}/Subject/news.php")
        self.assertEqual(
            client.build_discovery_exam_url(
                "teacher-recruit-central-alliance-115-kindergarten", 2026
            ),
            f"{BASE_URL}/Subject/news.php?cate=A",
        )
        self.assertEqual(
            client.build_discovery_exam_url(
                "teacher-recruit-central-alliance-115-elementary", 2026
            ),
            f"{BASE_URL}/Subject/news.php?cate=B",
        )
        self.assertEqual(
            client.build_discovery_exam_url("teacher-recruit-central-alliance-115-junior", 2026),
            f"{BASE_URL}/Subject/news.php?cate=C",
        )
        with self.assertRaisesRegex(ValueError, "Unexpected Central Alliance discovery level"):
            client.build_discovery_exam_url("teacher-recruit-central-alliance-115-senior", 2026)

    def test_fetch_exam_page_merges_subject_and_final_answer_downloads(self) -> None:
        client = CentralAllianceRecruitClient(
            subject_html_by_level={"elementary": SUBJECT_HTML},
            final_html_by_level={"elementary": FINAL_HTML},
        )

        page = client.fetch_exam_page("teacher-recruit-central-alliance-115-elementary", 2026)

        self.assertEqual(page.provider_id, "teacher_recruit_central_alliance")
        self.assertEqual(page.exam_name_raw, "115學年度中區策略聯盟教師甄試")
        self.assertEqual(page.papers[0].category_raw, "中區策略聯盟教師甄試")
        self.assertEqual(page.papers[0].subject_name_raw, "國小-國語文")
        self.assertEqual(set(page.papers[0].files), {"question", "answer", "corrected_answer"})


if __name__ == "__main__":
    unittest.main()
