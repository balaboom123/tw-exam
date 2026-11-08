"""Tests for the teacher_recruit_taoyuan_elementary provider."""

import unittest

from app.providers.teacher_recruit_taoyuan_elementary.client import (
    ANSWER_URL,
    TaoyuanElementaryRecruitClient,
    parse_answer_page,
)

ANSWER_HTML = """
<html><body>
  <a href="download_file.aspx?ids=q1">115桃教育A-試題.pdf</a>
  <a href="download_file.aspx?ids=a1">115桃教育A_建議答案.pdf</a>
  <a href="download_file.aspx?ids=c1">115桃教育A_正確答案.pdf</a>
  <a href="download_file.aspx?ids=x1">115釋疑_疑義處理一覽表.pdf</a>
</body></html>
"""


class TaoyuanElementaryRecruitParserTests(unittest.TestCase):
    def test_parse_answer_page_keeps_questions_and_answers(self) -> None:
        papers = parse_answer_page("https://elementary.tyc.edu.tw/web/answer.aspx", ANSWER_HTML)

        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].subject_name, "教育A")
        self.assertEqual(
            papers[0].downloads["question"],
            "https://elementary.tyc.edu.tw/web/download_file.aspx?ids=q1",
        )
        self.assertEqual(
            papers[0].downloads["answer"],
            "https://elementary.tyc.edu.tw/web/download_file.aspx?ids=a1",
        )
        self.assertEqual(
            papers[0].downloads["corrected_answer"],
            "https://elementary.tyc.edu.tw/web/download_file.aspx?ids=c1",
        )


class TaoyuanElementaryRecruitClientTests(unittest.TestCase):
    def test_discovery_is_source_backed_and_preserves_the_official_page(self) -> None:
        client = TaoyuanElementaryRecruitClient(answer_html=ANSWER_HTML)

        self.assertEqual(client.discover_available_years(), [2026])
        self.assertEqual(client.build_discovery_year_url(2026), ANSWER_URL)
        self.assertEqual(
            client.build_discovery_exam_url("teacher-recruit-taoyuan-elementary-115", 2026),
            ANSWER_URL,
        )
        with self.assertRaisesRegex(ValueError, "Unexpected Taoyuan discovery exam code"):
            client.build_discovery_exam_url("teacher-recruit-taoyuan-elementary-114", 2026)

        self.assertEqual(
            TaoyuanElementaryRecruitClient(answer_html="<html></html>").discover_available_years(),
            [],
        )

    def test_fetch_exam_page_builds_subject_papers(self) -> None:
        client = TaoyuanElementaryRecruitClient(answer_html=ANSWER_HTML)

        page = client.fetch_exam_page("teacher-recruit-taoyuan-elementary-115", 2026)

        self.assertEqual(page.provider_id, "teacher_recruit_taoyuan_elementary")
        self.assertEqual(page.exam_name_raw, "115年度桃園市國小教師甄試")
        self.assertEqual(page.papers[0].category_raw, "桃園市國小教師甄試")
        self.assertEqual(page.papers[0].subject_name_raw, "教育A")
        self.assertEqual(set(page.papers[0].files), {"question", "answer", "corrected_answer"})


if __name__ == "__main__":
    unittest.main()
