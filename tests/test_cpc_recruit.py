"""Tests for the cpc_recruit provider.

CPC Corporation (中油) publishes accepted exam papers on one ASP.NET
news-content page:
  - PhD exam papers:  https://www.cpc.com.tw/News_Content.aspx?n=32&s=826

The neighboring ``s=824`` page contains recruitment brochures and is excluded
from provider input. The pages render an inner content area
(``div.mcnTextContent`` or similar) that contains links pointing to the
download handler:
  https://ws.cpc.com.tw/Download.ashx?u=BASE64_PATH&n=BASE64_FILENAME

Fixtures below replicate the ASP.NET CMS HTML structure observed on those pages
and the known URL scheme (Download.ashx with base64 params).
"""
import unittest
from unittest.mock import patch

from app.providers.cpc_recruit.client import (
    CpcRecruitClient,
    CpcRecruitEntry,
    PHD_PAGE_URL,
    parse_employment_page,
)


# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------

# Typical ASP.NET CMS news-content page — the body content is rendered inside
# a <div class="mcnTextContent"> (MailChimp-style newsletter template used by
# CPC's CMS), containing a table with rows of anchor links.
PHD_PAGE_HTML = """
<!DOCTYPE html>
<html>
<head><title>博士論文-中油公司全球資訊網</title></head>
<body>
<form name="aspnetForm" id="aspnetForm" method="post">
<div id="wrapper">
  <div id="ContentPlaceHolder1_contentText" class="content-area">
    <div class="mcnTextContent">
      <table>
        <tbody>
          <tr>
            <td>
              <a href="https://ws.cpc.com.tw/Download.ashx?u=dXBsb2FkL0NQQy9FeGFtLzExM3BoZC5wZGY%3D&n=MTEz5bm05Y%2Bk5Y%2B35Lyg6Kmm6K235LiK6K235YWl5o6l.pdf">
                113年中油公司博士論文甄試試題及參考答案
              </a>
            </td>
          </tr>
          <tr>
            <td>
              <a href="https://ws.cpc.com.tw/Download.ashx?u=dXBsb2FkL0NQQy9FeGFtLzExMnBoZC5wZGY%3D&n=MTEy5bm05Y%2Bk5Y%2B35Lyg6Kmm6K235LiK6K235YWl5o6l.pdf">
                112年中油公司博士論文甄試試題及參考答案
              </a>
            </td>
          </tr>
          <tr>
            <td>
              <a href="https://ws.cpc.com.tw/Download.ashx?u=dXBsb2FkL0NQQy9FeGFtLzExMXBoZC5wZGY%3D&n=MTEx5bm05Y%2Bk5Y%2B35Lyg6Kmm6K235LiK6K235YWl5o6l.pdf">
                111年中油公司博士論文甄試試題及參考答案
              </a>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</div>
</form>
</body>
</html>
"""

PHD_PAGE_WITH_UNRELATED_DOWNLOAD_HTML = PHD_PAGE_HTML.replace(
    "</body>",
    """
<a href="https://ws.cpc.com.tw/Download.ashx?u=unrelated&n=unrelated.pdf">
  115年組織系統圖
</a>
</body>
""",
)


HIRING_PAGE_HTML = """
<!DOCTYPE html>
<html>
<head><title>新進人員甄試-中油公司全球資訊網</title></head>
<body>
<form name="aspnetForm" id="aspnetForm" method="post">
<div id="wrapper">
  <div id="ContentPlaceHolder1_contentText" class="content-area">
    <div class="mcnTextContent">
      <table>
        <tbody>
          <tr>
            <td>
              <a href="https://ws.cpc.com.tw/Download.ashx?u=dXBsb2FkL0NQQy9SZWNydWl0LzExM2hpcmUucGRm&n=MTEz5bm05bGH5Lq65ZWf5oiQ5ZGz5p%2Bl5o6l.pdf">
                113年中油公司新進人員甄試職類說明及應試科目
              </a>
            </td>
          </tr>
          <tr>
            <td>
              <a href="https://ws.cpc.com.tw/Download.ashx?u=dXBsb2FkL0NQQy9SZWNydWl0LzExMmhpcmUucGRm&n=MTEy5bm05bGH5Lq65ZWf5oiQ5ZGz5p%2Bl5o6l.pdf">
                112年中油公司新進人員甄試職類說明及應試科目
              </a>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</div>
</form>
</body>
</html>
"""

EMPTY_PAGE_HTML = """
<!DOCTYPE html>
<html><head><title>中油公司全球資訊網</title></head>
<body>
<div id="ContentPlaceHolder1_contentText" class="content-area">
  <div class="mcnTextContent">
    <p>暫無資料</p>
  </div>
</div>
</body>
</html>
"""

NESTED_CONTENT_HTML = """
<!DOCTYPE html>
<html>
<body>
<div id="ContentPlaceHolder1_contentText" class="content-area">
  <div class="mcnTextContent">
    <p>以下為博士論文甄試相關資料：</p>
    <table>
      <tbody>
        <tr>
          <td>
            <span>
              <a href="https://ws.cpc.com.tw/Download.ashx?u=dXBsb2FkL0NQQy9FeGFtLzExNHBoZC5wZGY%3D&n=MTEy5bm05Y%2Bk.pdf">
                114年博士論文甄試試題
              </a>
            </span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</div>
</body>
</html>
"""

# Page with links that lack a year — should be skipped
NO_YEAR_PAGE_HTML = """
<!DOCTYPE html>
<html>
<body>
<div id="ContentPlaceHolder1_contentText" class="content-area">
  <div class="mcnTextContent">
    <table>
      <tbody>
        <tr>
          <td>
            <a href="https://ws.cpc.com.tw/Download.ashx?u=abc&n=xyz.pdf">
              無年份資訊的連結
            </a>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Parser unit tests
# ---------------------------------------------------------------------------

class CpcRecruitParserTests(unittest.TestCase):

    def test_parse_phd_page_extracts_entries(self) -> None:
        entries = parse_employment_page(PHD_PAGE_HTML)

        self.assertEqual(len(entries), 3)

    def test_parse_phd_page_extracts_year_roc(self) -> None:
        entries = parse_employment_page(PHD_PAGE_HTML)

        self.assertEqual(entries[0].year_roc, 113)
        self.assertEqual(entries[1].year_roc, 112)
        self.assertEqual(entries[2].year_roc, 111)

    def test_parse_phd_page_computes_year_ad(self) -> None:
        entries = parse_employment_page(PHD_PAGE_HTML)

        self.assertEqual(entries[0].year_ad, 2024)
        self.assertEqual(entries[1].year_ad, 2023)
        self.assertEqual(entries[2].year_ad, 2022)

    def test_parse_phd_page_extracts_label(self) -> None:
        entries = parse_employment_page(PHD_PAGE_HTML)

        self.assertIn("113", entries[0].label)
        self.assertIn("博士", entries[0].label)

    def test_parse_phd_page_preserves_download_ashx_url(self) -> None:
        entries = parse_employment_page(PHD_PAGE_HTML)

        url = entries[0].url
        self.assertIn("Download.ashx", url)
        self.assertIn("u=", url)
        self.assertIn("n=", url)
        self.assertTrue(url.startswith("https://ws.cpc.com.tw/"))

    def test_parse_hiring_page_extracts_entries(self) -> None:
        entries = parse_employment_page(HIRING_PAGE_HTML)

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].year_roc, 113)
        self.assertEqual(entries[1].year_roc, 112)

    def test_parse_empty_page_returns_empty_list(self) -> None:
        entries = parse_employment_page(EMPTY_PAGE_HTML)

        self.assertEqual(entries, [])

    def test_parse_nested_content_extracts_entry(self) -> None:
        """Links inside extra wrapper elements (span etc.) should be found."""
        entries = parse_employment_page(NESTED_CONTENT_HTML)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].year_roc, 114)

    def test_parse_skips_links_without_year(self) -> None:
        entries = parse_employment_page(NO_YEAR_PAGE_HTML)

        self.assertEqual(entries, [])

    def test_parse_plain_html_returns_empty(self) -> None:
        entries = parse_employment_page("<html><body></body></html>")

        self.assertEqual(entries, [])


# ---------------------------------------------------------------------------
# CpcRecruitEntry dataclass tests
# ---------------------------------------------------------------------------

class CpcRecruitEntryTests(unittest.TestCase):

    def test_entry_fields(self) -> None:
        entry = CpcRecruitEntry(
            year_roc=113,
            year_ad=2024,
            label="113年中油公司博士論文甄試試題及參考答案",
            url="https://ws.cpc.com.tw/Download.ashx?u=abc&n=xyz.pdf",
            source="phd",
        )

        self.assertEqual(entry.year_roc, 113)
        self.assertEqual(entry.year_ad, 2024)
        self.assertEqual(entry.source, "phd")


# ---------------------------------------------------------------------------
# Client integration tests (mocked _fetch_text)
# ---------------------------------------------------------------------------

class CpcRecruitClientTests(unittest.TestCase):

    def _make_client(self) -> CpcRecruitClient:
        return CpcRecruitClient()

    def test_discover_available_years_returns_sorted_descending(self) -> None:
        with patch.object(CpcRecruitClient, "_fetch_text", return_value=PHD_PAGE_HTML) as fetch:
            client = self._make_client()
            years = client.discover_available_years()

        self.assertEqual(years, [2024, 2023, 2022])
        fetch.assert_called_once_with(PHD_PAGE_URL)

    def test_provider_filters_unrelated_year_bearing_download_links(self) -> None:
        with patch.object(
            CpcRecruitClient,
            "_fetch_text",
            return_value=PHD_PAGE_WITH_UNRELATED_DOWNLOAD_HTML,
        ):
            years = self._make_client().discover_available_years()

        self.assertEqual(years, [2024, 2023, 2022])

    def test_discovery_urls_preserve_official_page_provenance(self) -> None:
        client = self._make_client()

        self.assertEqual(client.build_discovery_year_url(2024), PHD_PAGE_URL)
        self.assertEqual(
            client.build_discovery_exam_url("cpc-recruit-113", 2024),
            PHD_PAGE_URL,
        )

    def test_discover_exams_returns_options_for_year(self) -> None:
        def fake_fetch(url: str) -> str:
            if "s=826" in url:
                return PHD_PAGE_HTML
            return HIRING_PAGE_HTML

        with patch.object(CpcRecruitClient, "_fetch_text", side_effect=fake_fetch):
            client = self._make_client()
            exams = client.discover_exams(2024)

        self.assertTrue(len(exams) >= 1)
        codes = [e.code for e in exams]
        self.assertIn("cpc-recruit-113", codes)
        self.assertEqual(exams[0].label, "113年中油公司新進博士級人員甄試")

    def test_fetch_exam_page_builds_source_exam_page(self) -> None:
        with patch.object(CpcRecruitClient, "_fetch_text", return_value=PHD_PAGE_HTML) as fetch:
            client = self._make_client()
            page = client.fetch_exam_page("cpc-recruit-113", 2024)

        self.assertEqual(page.provider_id, "cpc_recruit")
        self.assertEqual(page.source_exam_id, "cpc-recruit-113")
        self.assertEqual(page.year_ad, 2024)
        self.assertEqual(page.year_roc, 113)
        self.assertEqual(page.exam_name_raw, "113年中油公司新進博士級人員甄試")
        self.assertEqual(len(page.papers), 1)
        self.assertEqual(page.papers[0].category_raw, "中油新進博士級人員甄試")
        fetch.assert_called_once_with(PHD_PAGE_URL)

    def test_fetch_exam_page_papers_have_question_file_type(self) -> None:
        def fake_fetch(url: str) -> str:
            if "s=826" in url:
                return PHD_PAGE_HTML
            return HIRING_PAGE_HTML

        with patch.object(CpcRecruitClient, "_fetch_text", side_effect=fake_fetch):
            client = self._make_client()
            page = client.fetch_exam_page("cpc-recruit-113", 2024)

        file_types = {ft for paper in page.papers for ft in paper.files}
        # This fixture explicitly says 試題及參考答案. Its questions must remain
        # discoverable instead of the complete package becoming answer-only.
        self.assertEqual(file_types, {"question"})

    def test_brochure_mentioning_questions_is_not_an_exam_package(self) -> None:
        html = PHD_PAGE_HTML.replace("試題及參考答案", "甄試簡章（含試題說明）")
        with patch.object(CpcRecruitClient, "_fetch_text", return_value=html):
            client = self._make_client()
            self.assertEqual(client.discover_available_years(), [])
            self.assertEqual(client.fetch_exam_page("cpc-recruit-113", 2024).papers, [])

    def test_fetch_exam_page_preserves_download_ashx_url_in_papers(self) -> None:
        def fake_fetch(url: str) -> str:
            if "s=826" in url:
                return PHD_PAGE_HTML
            return HIRING_PAGE_HTML

        with patch.object(CpcRecruitClient, "_fetch_text", side_effect=fake_fetch):
            client = self._make_client()
            page = client.fetch_exam_page("cpc-recruit-113", 2024)

        for paper in page.papers:
            for url in paper.files.values():
                self.assertIn("Download.ashx", url)

    def test_fetch_exam_page_category_raw(self) -> None:
        def fake_fetch(url: str) -> str:
            if "s=826" in url:
                return PHD_PAGE_HTML
            return HIRING_PAGE_HTML

        with patch.object(CpcRecruitClient, "_fetch_text", side_effect=fake_fetch):
            client = self._make_client()
            page = client.fetch_exam_page("cpc-recruit-113", 2024)

        categories = {paper.category_raw for paper in page.papers}
        self.assertTrue(all("中油" in cat for cat in categories))


# ---------------------------------------------------------------------------
# Registry test
# ---------------------------------------------------------------------------

class CpcRecruitRegistryTests(unittest.TestCase):

    def test_registry_returns_cpc_recruit_provider(self) -> None:
        from app.providers.registry import get_provider
        from app.providers.base import SourceProvider

        provider = get_provider("cpc_recruit")

        self.assertIsInstance(provider, SourceProvider)
        self.assertEqual(provider.provider_id, "cpc_recruit")


if __name__ == "__main__":
    unittest.main()
