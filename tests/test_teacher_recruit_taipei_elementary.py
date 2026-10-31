"""Tests for the teacher_recruit_taipei_elementary provider."""

import base64
import unittest
from urllib.parse import quote

from app.providers.teacher_recruit_taipei_elementary.client import (
    ARTICLE_URLS_BY_YEAR,
    TaipeiElementaryRecruitClient,
    parse_downloads,
)

ARTICLE_DOWNLOAD_URL_1 = (
    "https://www-ws.gov.taipei/Download.ashx?u=opaque-basic&n=MeWfuuekjumhnuenkee"
    "fpeiDvV%2flkKvnrZTmoYgucGRm&icon=..pdf"
)

ARTICLE_DOWNLOAD_URL_2 = (
    "https://www-ws.gov.taipei/Download.ashx?u=opaque-general&n=Mi4x5pmu6YCa56eRX"
    "%2bWQq%2betlOahiC5wZGY%3d&icon=..pdf"
)

ARTICLE_DOWNLOAD_URL_3 = (
    "https://www-ws.gov.taipei/Download.ashx?u=appeal-token&n=55aR576p6KGoLm9kdA%3d%3d&icon=..odt"
)

ARTICLE_HTML = f"""
<html>
  <body>
    <h1>公告114學年度臺北市公立國民小學教師聯合甄選初試試題含答案</h1>
    <a href="{ARTICLE_DOWNLOAD_URL_1}">pdf(419.43 KB)</a>
    <a href="{ARTICLE_DOWNLOAD_URL_2}">pdf(338.01 KB)</a>
    <a href="{ARTICLE_DOWNLOAD_URL_3}">odt(9.44 KB)</a>
  </body>
</html>
"""

EXPECTED_SUBJECT_CODES = {
    "basic-category-knowledge",
    "general",
    "english",
    "physical-education",
    "music",
    "visual-arts",
    "counseling",
    "information-technology",
    "taiwanese-minnan",
    "special-education-disability",
    "special-education-gifted",
    "science",
}


def _download_link(file_name: str, token: str) -> str:
    encoded_name = quote(base64.b64encode(file_name.encode("utf-8")).decode("ascii"), safe="")
    return f'<a href="https://www-ws.gov.taipei/Download.ashx?u={token}&n={encoded_name}&icon=..pdf">pdf</a>'


class TaipeiElementaryRecruitParserTests(unittest.TestCase):
    def test_parse_downloads_decodes_combined_question_answer_pdfs(self) -> None:
        downloads = parse_downloads(
            ARTICLE_HTML, page_url="https://www.gov.taipei/News_Content.aspx?s=example"
        )

        self.assertEqual(
            [download.file_type for download in downloads], ["question_answer", "question_answer"]
        )
        self.assertEqual(
            [download.subject_name for download in downloads], ["基礎類科知能", "普通科"]
        )
        self.assertEqual(
            [download.subject_code for download in downloads],
            ["basic-category-knowledge", "general"],
        )
        self.assertEqual(
            downloads[0].url,
            "https://www-ws.gov.taipei/Download.ashx?u=opaque-basic&n=MeWfuuekjumhnuenkeefpeiDvV%2flkKvnrZTmoYgucGRm&icon=..pdf",
        )

    def test_parse_downloads_keeps_all_reviewed_114_subjects(self) -> None:
        official_file_names = [
            "1基礎類科知能_含答案.pdf",
            "2.1普通科_含答案.pdf",
            "2.2英語科_含答案.pdf",
            "2.3體育科_含答案.pdf",
            "2.4音樂科_含答案.pdf",
            "2.5視覺藝術科_含答案.pdf",
            "2.6輔導科_含答案.pdf",
            "2.7資訊科技科_含答案.pdf",
            "2.8閩南語_含答案.pdf",
            "2.9特教科(身障)_含答案.pdf",
            "2.10特教科(資優)_含答案.pdf",
            "2.11自然科_含答案.pdf",
        ]
        html = (
            "<html><body>"
            + "".join(
                _download_link(file_name, f"token-{index}")
                for index, file_name in enumerate(official_file_names)
            )
            + "</body></html>"
        )

        downloads = parse_downloads(
            html, page_url="https://www.gov.taipei/News_Content.aspx?s=example"
        )

        self.assertEqual(len(downloads), 12)
        self.assertEqual({download.subject_code for download in downloads}, EXPECTED_SUBJECT_CODES)
        self.assertEqual({download.file_type for download in downloads}, {"question_answer"})

    def test_parse_downloads_ignores_unofficial_or_non_download_links(self) -> None:
        encoded_name = quote(
            base64.b64encode("1基礎類科知能_含答案.pdf".encode()).decode("ascii"), safe=""
        )
        html = f"""
        <html><body>
          <a href="https://mirror.example/1基礎類科知能_含答案.pdf">pdf</a>
          <a href="https://www-ws.gov.taipei/NotDownload.ashx?n={encoded_name}&icon=..pdf">pdf</a>
        </body></html>
        """

        downloads = parse_downloads(
            html, page_url="https://www.gov.taipei/News_Content.aspx?s=example"
        )

        self.assertEqual(downloads, [])


class TaipeiElementaryRecruitClientTests(unittest.TestCase):
    def test_duplicate_article_links_do_not_duplicate_a_native_file(self) -> None:
        client = TaipeiElementaryRecruitClient(article_html_by_year={2025: ARTICLE_HTML * 2})
        page = client.fetch_exam_page("teacher-recruit-taipei-elementary-114", 2025)
        self.assertEqual(len(page.papers), 2)
        self.assertEqual(sum(len(p.files) for p in page.papers), 2)

    def test_competing_combined_files_stop_acquisition(self) -> None:
        competing = ARTICLE_DOWNLOAD_URL_1.replace("opaque-basic", "competing-basic")
        html = ARTICLE_HTML + f'<a href="{competing}">新版</a>'
        client = TaipeiElementaryRecruitClient(article_html_by_year={2025: html})
        with self.assertRaisesRegex(ValueError, "Competing Taipei downloads"):
            client.fetch_exam_page("teacher-recruit-taipei-elementary-114", 2025)

    def test_foreign_event_is_rejected_before_fetching_an_article(self) -> None:
        client = TaipeiElementaryRecruitClient()
        client._article_html = lambda year: self.fail("foreign event fetched an article")
        with self.assertRaisesRegex(ValueError, "Unexpected Taipei elementary discovery exam code"):
            client.fetch_exam_page("teacher-recruit-taipei-junior-114", 2025)

    def test_fetch_exam_page_builds_one_combined_paper_per_subject(self) -> None:
        client = TaipeiElementaryRecruitClient(article_html_by_year={2025: ARTICLE_HTML})

        page = client.fetch_exam_page("teacher-recruit-taipei-elementary-114", 2025)

        self.assertEqual(page.provider_id, "teacher_recruit_taipei_elementary")
        self.assertEqual(page.source_exam_id, "teacher-recruit-taipei-elementary-114")
        self.assertEqual(page.exam_name_raw, "114學年度臺北市國小教師甄試")
        self.assertEqual(len(page.papers), 2)
        self.assertEqual(page.papers[0].category_raw, "臺北市國小教師甄試")
        self.assertEqual(page.papers[0].subject_code, "basic-category-knowledge")
        self.assertEqual(page.papers[0].subject_name_raw, "基礎類科知能")
        self.assertEqual(
            page.papers[0].files,
            {
                "question_answer": "https://www-ws.gov.taipei/Download.ashx?u=opaque-basic&n=MeWfuuekjumhnuenkeefpeiDvV%2flkKvnrZTmoYgucGRm&icon=..pdf",
            },
        )

    def test_discover_years_uses_official_article_map(self) -> None:
        client = TaipeiElementaryRecruitClient(article_html_by_year={2025: ARTICLE_HTML})

        self.assertEqual(client.discover_available_years(), [2025])
        self.assertEqual(
            client.discover_exams(2025)[0].code, "teacher-recruit-taipei-elementary-114"
        )
        self.assertEqual(client.build_discovery_year_url(2025), ARTICLE_URLS_BY_YEAR[2025])
        self.assertEqual(
            client.build_discovery_exam_url("teacher-recruit-taipei-elementary-114", 2025),
            ARTICLE_URLS_BY_YEAR[2025],
        )
        with self.assertRaisesRegex(ValueError, "Unexpected Taipei elementary discovery exam code"):
            client.build_discovery_exam_url("teacher-recruit-taipei-elementary-113", 2025)


if __name__ == "__main__":
    unittest.main()
