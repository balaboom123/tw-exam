"""Tests for the teacher_recruit_tainan provider."""

import unittest
from unittest.mock import Mock
from urllib.parse import quote

from app.providers.teacher_recruit_tainan.client import (
    LISTING_URL,
    TainanTeacherRecruitClient,
    _request_url,
    parse_announcement_links,
    parse_available_years,
    parse_downloads,
)

LISTING_HTML = """
<html>
  <h1><span id="lblTitle">臺南市115學年度市立國民小學教師暨學前特教師聯合甄選</span></h1>
  <table>
    <tr><td><a href="view.aspx?id=10">公告甄選試題正確答案</a></td></tr>
    <tr><td><a href="view.aspx?id=9">公告複試試教題目</a></td></tr>
    <tr><td><a href="view.aspx?id=8">公告甄選試題及參考答案</a></td></tr>
    <tr><td><a href="view.aspx?id=1">公告簡章及臺南文史題庫</a></td></tr>
  </table>
</html>
"""


DETAIL_HTML = """
<html>
  <body>
    <a href="Default.aspx">首頁</a>
    <a href="./upload/TrJh70115學年度市立國民小學教師暨學前特教師聯合甄選試題.zip">
      TrJh70115學年度市立國民小學教師暨學前特教師聯合甄選試題.zip
    </a>
    <a href="./upload/TrJh71115學年度市立國民小學教師暨學前特教師聯合甄選試題參考答案.zip">
      TrJh71115學年度市立國民小學教師暨學前特教師聯合甄選試題參考答案.zip
    </a>
  </body>
</html>
"""


CORRECTED_DETAIL_HTML = """
<html>
  <body>
    <a href="./upload/TrJh90115學年度市立國民小學教師暨學前特教師聯合甄選試題答案.zip">
      TrJh90115學年度市立國民小學教師暨學前特教師聯合甄選試題答案.zip
    </a>
  </body>
</html>
"""


class TainanTeacherRecruitParserTests(unittest.TestCase):
    def test_parse_available_years_from_current_school_year_title(self) -> None:
        self.assertEqual(parse_available_years(LISTING_HTML), [2026])

    def test_discovery_ignores_years_in_old_notices(self) -> None:
        html = LISTING_HTML + '<a href="view.aspx?id=1">114學年度試題</a>'
        self.assertEqual(parse_available_years(html), [2026])

    def test_discovery_requires_one_native_programme_title(self) -> None:
        for html in [
            "<h1>115學年度教師甄選</h1>",
            LISTING_HTML.replace("115學年度", "114學年度暨115學年度", 1),
            LISTING_HTML.replace("臺南市", "其他縣市", 1),
            LISTING_HTML.replace("國民小學", "國民中學", 1),
            LISTING_HTML.replace("教師暨學前特教師", "教保員", 1),
        ]:
            with self.subTest(html=html):
                self.assertEqual(parse_available_years(html), [])

    def test_discovery_accepts_nested_title_markup(self) -> None:
        self.assertEqual(
            parse_available_years(LISTING_HTML.replace("臺南市115", "臺南市<b>115</b>")),
            [2026],
        )

    def test_announcement_links_stay_with_the_official_programme(self) -> None:
        html = (
            LISTING_HTML
            + """
        <a href="https://other.example/trexamps/view.aspx?id=8">試題</a>
        <a href="https://qualify.tn.edu.tw/other/view.aspx?id=8">試題</a>
        <a href="https://qualify.tn.edu.tw.evil.example/trexamps/view.aspx?id=8">試題</a>
        """
        )
        self.assertEqual(parse_announcement_links(html), parse_announcement_links(LISTING_HTML))

    def test_downloads_stay_with_the_official_upload_directory(self) -> None:
        html = (
            DETAIL_HTML
            + """
        <a href="https://other.example/trexamps/upload/115學年度試題.zip">115學年度試題</a>
        <a href="https://qualify.tn.edu.tw/other/upload/115學年度試題.zip">115學年度試題</a>
        """
        )
        self.assertEqual(
            parse_downloads(html, page_url=LISTING_URL),
            parse_downloads(DETAIL_HTML, page_url=LISTING_URL),
        )

    def test_request_url_preserves_encoded_native_file_names(self) -> None:
        raw = LISTING_URL + "upload/TrJh70115學年度試題.zip"
        encoded = quote(raw, safe=":/")
        self.assertEqual(_request_url(raw), encoded)
        self.assertEqual(_request_url(encoded), encoded)

    def test_official_resource_case_variants_preserve_original_links(self) -> None:
        notice = '<a href="View.aspx?id=8">115學年度試題</a>'
        self.assertEqual(parse_announcement_links(notice)[0].url, LISTING_URL + "View.aspx?id=8")
        detail = DETAIL_HTML.replace("./upload/", "./Upload/")
        downloads = parse_downloads(detail, page_url=LISTING_URL)
        self.assertEqual(len(downloads), 2)
        self.assertTrue(all("/Upload/" in download.url for download in downloads))

    def test_parse_announcement_links_keeps_question_and_answer_notices(self) -> None:
        links = parse_announcement_links(
            LISTING_HTML,
            page_url="https://qualify.tn.edu.tw/trexamps/",
        )

        self.assertEqual(
            [link.title for link in links], ["公告甄選試題正確答案", "公告甄選試題及參考答案"]
        )
        self.assertEqual(links[0].url, "https://qualify.tn.edu.tw/trexamps/view.aspx?id=10")
        self.assertEqual(links[1].url, "https://qualify.tn.edu.tw/trexamps/view.aspx?id=8")

    def test_parse_announcement_links_keeps_answer_only_notice_titles(self) -> None:
        links = parse_announcement_links(
            """
            <a href="view.aspx?id=12">公告甄選正確答案</a>
            <a href="view.aspx?id=13">公告錄取名單</a>
            """,
            page_url="https://qualify.tn.edu.tw/trexamps/",
        )

        self.assertEqual([link.title for link in links], ["公告甄選正確答案"])
        self.assertEqual(links[0].url, "https://qualify.tn.edu.tw/trexamps/view.aspx?id=12")

    def test_parse_downloads_classifies_official_upload_zip_links(self) -> None:
        downloads = parse_downloads(
            DETAIL_HTML,
            page_url="https://qualify.tn.edu.tw/trexamps/view.aspx?id=8",
        )

        self.assertEqual([download.file_type for download in downloads], ["question", "answer"])
        self.assertEqual(
            downloads[0].url,
            "https://qualify.tn.edu.tw/trexamps/upload/TrJh70115學年度市立國民小學教師暨學前特教師聯合甄選試題.zip",
        )

    def test_parse_downloads_keeps_answer_only_zip_labels(self) -> None:
        downloads = parse_downloads(
            """
            <a href="./upload/TrJh90115學年度市立國民小學教師暨學前特教師聯合甄選答案.zip">
              TrJh90115學年度市立國民小學教師暨學前特教師聯合甄選答案.zip
            </a>
            """,
            page_url="https://qualify.tn.edu.tw/trexamps/view.aspx?id=12",
        )

        self.assertEqual([download.file_type for download in downloads], ["corrected_answer"])


class TainanTeacherRecruitClientTests(unittest.TestCase):
    def client_with_pages(
        self, listing: str = LISTING_HTML, detail: str = DETAIL_HTML
    ) -> TainanTeacherRecruitClient:
        client = TainanTeacherRecruitClient()
        pages = {
            LISTING_URL: listing,
            LISTING_URL + "view.aspx?id=8": detail,
            LISTING_URL + "view.aspx?id=10": CORRECTED_DETAIL_HTML,
        }
        client._fetch_text = Mock(side_effect=lambda url: pages[url])  # type: ignore[method-assign]
        return client

    def test_fetch_rejects_a_foreign_event_before_network_access(self) -> None:
        client = self.client_with_pages()
        with self.assertRaisesRegex(ValueError, "Unexpected Tainan discovery exam code"):
            client.fetch_exam_page("foreign-event", 2026)
        client._fetch_text.assert_not_called()

    def test_fetch_never_relabels_current_files_as_a_historical_year(self) -> None:
        client = self.client_with_pages()
        with self.assertRaisesRegex(ValueError, "does not represent the requested year"):
            client.fetch_exam_page("teacher-recruit-tainan-114", 2025)
        self.assertEqual(client._fetch_text.call_count, 1)

    def test_old_notice_is_not_downloaded_as_a_current_event(self) -> None:
        client = self.client_with_pages(
            LISTING_HTML + '<a href="view.aspx?id=114">114學年度試題及答案</a>'
        )
        page = client.fetch_exam_page("teacher-recruit-tainan-115", 2026)
        self.assertEqual(len(page.papers[0].files), 3)
        self.assertNotIn(
            LISTING_URL + "view.aspx?id=114",
            [call.args[0] for call in client._fetch_text.call_args_list],
        )

    def test_download_year_must_match_in_both_label_and_source_file_name(self) -> None:
        for detail in [
            DETAIL_HTML.replace("115學年度", "114學年度"),
            DETAIL_HTML.replace("115學年度", "114學年度", 1),
            DETAIL_HTML.replace("115學年度", ""),
        ]:
            with self.subTest(detail=detail):
                client = self.client_with_pages(detail=detail)
                with self.assertRaisesRegex(ValueError, "download does not match"):
                    client.fetch_exam_page("teacher-recruit-tainan-115", 2026)

    def test_competing_role_archives_require_review_instead_of_first_link_wins(self) -> None:
        detail = (
            DETAIL_HTML
            + """
        <a href="upload/alternative-115學年度試題.zip">115學年度試題</a>
        """
        )
        client = self.client_with_pages(detail=detail)
        with self.assertRaisesRegex(ValueError, "competing source archives"):
            client.fetch_exam_page("teacher-recruit-tainan-115", 2026)

    def test_repeated_exact_archive_link_does_not_create_another_reference(self) -> None:
        client = self.client_with_pages(detail=DETAIL_HTML + DETAIL_HTML)
        page = client.fetch_exam_page("teacher-recruit-tainan-115", 2026)
        self.assertEqual(len(page.papers[0].files), 3)

    def test_discovery_urls_preserve_the_official_current_year_listing(self) -> None:
        client = TainanTeacherRecruitClient()

        self.assertEqual(client.build_discovery_year_url(2026), LISTING_URL)
        self.assertEqual(
            client.build_discovery_exam_url("teacher-recruit-tainan-115", 2026),
            LISTING_URL,
        )
        with self.assertRaisesRegex(ValueError, "Unexpected Tainan discovery exam code"):
            client.build_discovery_exam_url("teacher-recruit-tainan-114", 2026)

    def test_fetch_exam_page_builds_current_year_recruitment_paper(self) -> None:
        client = TainanTeacherRecruitClient()
        pages = {
            "https://qualify.tn.edu.tw/trexamps/": LISTING_HTML,
            "https://qualify.tn.edu.tw/trexamps/view.aspx?id=8": DETAIL_HTML,
            "https://qualify.tn.edu.tw/trexamps/view.aspx?id=10": CORRECTED_DETAIL_HTML,
        }
        client._fetch_text = lambda url: pages[url]  # type: ignore[method-assign]

        page = client.fetch_exam_page("teacher-recruit-tainan-115", 2026)

        self.assertEqual(page.provider_id, "teacher_recruit_tainan")
        self.assertEqual(page.source_exam_id, "teacher-recruit-tainan-115")
        self.assertEqual(page.exam_name_raw, "115學年度臺南市國小教師甄試")
        self.assertEqual(len(page.papers), 1)
        self.assertEqual(page.papers[0].category_raw, "臺南市國小教師甄試")
        self.assertEqual(
            page.papers[0].files,
            {
                "corrected_answer": "https://qualify.tn.edu.tw/trexamps/upload/TrJh90115學年度市立國民小學教師暨學前特教師聯合甄選試題答案.zip",
                "question": "https://qualify.tn.edu.tw/trexamps/upload/TrJh70115學年度市立國民小學教師暨學前特教師聯合甄選試題.zip",
                "answer": "https://qualify.tn.edu.tw/trexamps/upload/TrJh71115學年度市立國民小學教師暨學前特教師聯合甄選試題參考答案.zip",
            },
        )


if __name__ == "__main__":
    unittest.main()
