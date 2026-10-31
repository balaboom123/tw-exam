"""Tests for the teacher_recruit_taipei_junior provider."""

import base64
import unittest
from urllib.parse import quote

from app.providers.teacher_recruit_taipei_junior.client import (
    ARTICLE_URLS_BY_YEAR,
    TaipeiJuniorRecruitClient,
    parse_downloads,
)

ARTICLE_DOWNLOAD_URL_1 = (
    "https://www-ws.gov.taipei/Download.ashx?u=question-token&n=5ZyL5paH6Kmm6aGML"
    "nBkZg%3d%3d&icon=..pdf"
)

ARTICLE_DOWNLOAD_URL_2 = (
    "https://www-ws.gov.taipei/Download.ashx?u=answer-token&n=5ZyL5paH562U5qGILnB"
    "kZg%3d%3d&icon=..pdf"
)

ARTICLE_DOWNLOAD_URL_3 = (
    "https://www-ws.gov.taipei/Download.ashx?u=appeal-token&n=6Kmm6aGM562U5qGI55a"
    "R576p6KGoLm9kdA%3d%3d&icon=..odt"
)

ARTICLE_HTML = f"""
<html>
  <body>
    <h1>公告114學年度市立國民中學正式教師聯合甄選 各類科初試試題、答案及試題答案疑義表</h1>
    <a href="{ARTICLE_DOWNLOAD_URL_1}">pdf(569.23 KB)</a>
    <a href="{ARTICLE_DOWNLOAD_URL_2}">pdf(72.66 KB)</a>
    <a href="{ARTICLE_DOWNLOAD_URL_3}">odt(9.44 KB)</a>
  </body>
</html>
"""


class TaipeiJuniorRecruitParserTests(unittest.TestCase):
    def test_current_native_final_draft_names_keep_complete_question_answer_pairs(self) -> None:
        # Official ROC115 filenames use both "試題完稿" and "答案 - 完稿".
        subjects = [
            "公民與社會科",
            "生活科技科",
            "地球科學科",
            "英語科",
            "家政科",
            "特殊教育－身心障礙科",
            "特殊教育－資優理化科",
            "特殊教育－資優數學科",
            "國文科",
            "專任輔導",
            "理化科",
            "童軍(雙語)",
            "童軍教育",
            "資訊科技",
            "輔導活動",
            "數學科",
            "體育(雙語)科",
            "體育科",
            "本土語－閩南語科",
        ]
        anchors = []
        for index, subject in enumerate(subjects):
            for suffix in ["試題完稿", "答案 - 完稿"]:
                name = quote(base64.b64encode(f"{subject}{suffix}.pdf".encode()).decode(), safe="")
                anchors.append(
                    f'<a href="https://www-ws.gov.taipei/Download.ashx?u={index}-{suffix}&n={name}">'
                    "PDF</a>"
                )
        client = TaipeiJuniorRecruitClient(
            article_urls_by_year={2026: "https://www.doe.gov.taipei/native115"},
            article_html_by_year={2026: "".join(anchors)},
        )
        page = client.fetch_exam_page("teacher-recruit-taipei-junior-115", 2026)
        self.assertEqual(len(page.papers), 19)
        self.assertEqual({p.subject_name_raw for p in page.papers}, set(subjects))
        for paper in page.papers:
            self.assertEqual(set(paper.files), {"question", "answer"})

    def test_foreign_download_links_cannot_supply_native_papers(self) -> None:
        html = ARTICLE_HTML.replace("www-ws.gov.taipei", "other.example")
        self.assertEqual(parse_downloads(html, page_url="https://www.doe.gov.taipei/"), [])

    def test_parse_downloads_decodes_official_question_and_answer_pdfs(self) -> None:
        downloads = parse_downloads(
            ARTICLE_HTML, page_url="https://www.doe.gov.taipei/News_Content.aspx?s=example"
        )

        self.assertEqual([download.file_type for download in downloads], ["question", "answer"])
        self.assertEqual([download.subject_name for download in downloads], ["國文", "國文"])
        self.assertEqual(
            downloads[0].url,
            "https://www-ws.gov.taipei/Download.ashx?u=question-token&n=5ZyL5paH6Kmm6aGMLnBkZg%3d%3d&icon=..pdf",
        )


class TaipeiJuniorRecruitClientTests(unittest.TestCase):
    def test_duplicate_article_links_do_not_duplicate_native_papers(self) -> None:
        client = TaipeiJuniorRecruitClient(article_html_by_year={2025: ARTICLE_HTML * 2})
        page = client.fetch_exam_page("teacher-recruit-taipei-junior-114", 2025)
        self.assertEqual(len(page.papers), 1)
        self.assertEqual(set(page.papers[0].files), {"question", "answer"})

    def test_competing_question_or_answer_files_stop_acquisition(self) -> None:
        for original in [ARTICLE_DOWNLOAD_URL_1, ARTICLE_DOWNLOAD_URL_2]:
            competing = original.replace("-token", "-competing-token")
            html = ARTICLE_HTML + f'<a href="{competing}">新版</a>'
            client = TaipeiJuniorRecruitClient(article_html_by_year={2025: html})
            with self.subTest(url=original):
                with self.assertRaisesRegex(ValueError, "Competing Taipei downloads"):
                    client.fetch_exam_page("teacher-recruit-taipei-junior-114", 2025)

    def test_foreign_event_is_rejected_before_fetching_an_article(self) -> None:
        client = TaipeiJuniorRecruitClient()
        client._article_html = lambda year: self.fail("foreign event fetched an article")
        with self.assertRaisesRegex(ValueError, "Unexpected Taipei junior discovery exam code"):
            client.fetch_exam_page("teacher-recruit-taipei-elementary-114", 2025)

    def test_fetch_exam_page_builds_subject_paper_pairs(self) -> None:
        client = TaipeiJuniorRecruitClient(article_html_by_year={2025: ARTICLE_HTML})

        page = client.fetch_exam_page("teacher-recruit-taipei-junior-114", 2025)

        self.assertEqual(page.provider_id, "teacher_recruit_taipei_junior")
        self.assertEqual(page.source_exam_id, "teacher-recruit-taipei-junior-114")
        self.assertEqual(page.exam_name_raw, "114學年度臺北市國中教師甄試")
        self.assertEqual(len(page.papers), 1)
        self.assertEqual(page.papers[0].category_raw, "臺北市國中教師甄試")
        self.assertEqual(page.papers[0].subject_code, "guo-wen")
        self.assertEqual(page.papers[0].subject_name_raw, "國文")
        self.assertEqual(
            page.papers[0].files,
            {
                "question": "https://www-ws.gov.taipei/Download.ashx?u=question-token&n=5ZyL5paH6Kmm6aGMLnBkZg%3d%3d&icon=..pdf",
                "answer": "https://www-ws.gov.taipei/Download.ashx?u=answer-token&n=5ZyL5paH562U5qGILnBkZg%3d%3d&icon=..pdf",
            },
        )

    def test_discover_years_uses_official_article_map(self) -> None:
        client = TaipeiJuniorRecruitClient(
            article_html_by_year={2025: ARTICLE_HTML, 2024: ARTICLE_HTML}
        )

        self.assertEqual(client.discover_available_years(), [2026, 2025, 2024])
        self.assertEqual(client.discover_exams(2026)[0].code, "teacher-recruit-taipei-junior-115")
        self.assertEqual(client.discover_exams(2025)[0].code, "teacher-recruit-taipei-junior-114")
        self.assertEqual(client.build_discovery_year_url(2024), ARTICLE_URLS_BY_YEAR[2024])
        self.assertEqual(
            client.build_discovery_exam_url("teacher-recruit-taipei-junior-114", 2025),
            ARTICLE_URLS_BY_YEAR[2025],
        )
        with self.assertRaisesRegex(ValueError, "Unexpected Taipei junior discovery exam code"):
            client.build_discovery_exam_url("teacher-recruit-taipei-junior-113", 2025)


if __name__ == "__main__":
    unittest.main()
