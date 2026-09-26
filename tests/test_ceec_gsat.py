import unittest
from urllib.error import URLError
from unittest.mock import patch

from app.models import NormalizedCatalog
from app.normalizer import normalize_papers
from app.providers.base import SourceProvider
from app.providers.registry import get_provider

from app.providers.ceec_gsat.client import LISTING_URL, CeecGsatClient, parse_listing_page


LISTING_HTML = """
<html><body>
<h2>\u4e00\u822c\u8a66\u984c</h2>
<div>\u5171 19 \u9801 / 189 \u7b46</div>
<div>115-02-23 115\u5b78\u5e74\u5ea6\u5b78\u79d1\u80fd\u529b\u6e2c\u9a57\uff0d\u570b\u7d9c
  <a href="/files/a-question.pdf">\u8a66\u984c\u5167\u5bb9</a>
  <a href="/files/a-question-2.pdf">\u8a66\u984c\u5167\u5bb9</a>
  <a href="/files/a-sheet.pdf">\u7b54\u984c\u5377</a>
  <a href="/files/a-answer.pdf">\u9078\u64c7\u984c\u7b54\u6848</a>
  <a href="/files/a-guideline.pdf">\u975e\u9078\u64c7\u984c\u8a55\u5206\u539f\u5247</a>
</div>
<a href="/xmfile?page=2&amp;xsmsid=0J052424829869345634">\u4e0b\u4e00\u9801</a>
</body></html>
"""

LISTING_HTML_SPLIT_ROW = """
<html><body>
<h2>\u4e00\u822c\u8a66\u984c</h2>
<div>\u5171 19 \u9801 / 189 \u7b46</div>
<table>
  <tr><th>\u767c\u4f48\u65e5\u671f</th><th>\u6a19\u984c</th><th>\u4e0b\u8f09</th></tr>
  <tr>
    <td>115-02-23</td>
    <td>115\u5b78\u5e74\u5ea6\u5b78\u79d1\u80fd\u529b\u6e2c\u9a57\uff0d\u570b\u7d9c</td>
    <td>
      <a href="/files/a-question.pdf">\u8a66\u984c\u5167\u5bb9</a>
      <a href="/files/a-question-2.docx">\u8a66\u984c\u5167\u5bb9</a>
      <a href="/files/a-sheet.pdf">\u7b54\u984c\u5377</a>
      <a href="/files/a-answer.pdf">\u9078\u64c7\u984c\u7b54\u6848</a>
      <a href="/files/a-guideline.pdf">\u975e\u9078\u64c7\u984c\u8a55\u5206\u539f\u5247</a>
    </td>
  </tr>
</table>
</body></html>
"""

LISTING_HTML_TWO_DIGIT_ROC_YEAR = """
<html><body>
<div>共 19 頁 / 189 筆</div>
<table>
  <tr><th>發佈日期</th><th>標題</th><th>下載</th></tr>
  <tr>
    <td>086-02-24</td>
    <td>86學年度學科能力測驗－英文</td>
    <td>
      <a href="/files/86-english-question.pdf">試題內容</a>
      <a href="/files/86-english-answer.pdf">選擇題答案</a>
    </td>
  </tr>
</table>
</body></html>
"""


class CeecParserTests(unittest.TestCase):
    def test_listing_retries_only_the_page_that_times_out(self) -> None:
        html = LISTING_HTML.replace("共 19 頁", "共 2 頁").encode("utf-8")
        second_page_url = f"{LISTING_URL}&page=2"
        requested = []

        class Response:
            status = 200
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                return html

        def open_page(request, _timeout):
            requested.append(request.full_url)
            if request.full_url == second_page_url and requested.count(second_page_url) == 1:
                raise URLError("TLS handshake timed out")
            return Response()

        client = CeecGsatClient()
        with patch.object(client._listing_http, "_open", side_effect=open_page), patch("app.providers.http.time.sleep"):
            self.assertEqual(len(client.discover_exams(2026)), 1)

        self.assertEqual(requested, [LISTING_URL, second_page_url, second_page_url])
        self.assertEqual(client.http.max_attempts, 1)

    def test_failed_listing_is_not_exposed_and_completed_pages_survive_discovery_retry(self) -> None:
        first_html = '<div>選擇年度 115 86 ※本試題為PDF</div>' + LISTING_HTML.replace("共 19 頁", "共 3 頁")
        second_html = LISTING_HTML_TWO_DIGIT_ROC_YEAR
        third_html = LISTING_HTML_TWO_DIGIT_ROC_YEAR.replace("86", "85")
        requested = []
        fail = True

        def fetch(url):
            requested.append(url)
            if url == LISTING_URL:
                return first_html
            if url.endswith("page=2"):
                if fail:
                    raise URLError("Connection reset by peer")
                return second_html
            return third_html

        client = CeecGsatClient()
        with patch.object(client, "_fetch_text", side_effect=fetch):
            self.assertEqual(client.discover_available_years(), [2026, 1997])
            with self.assertRaises(URLError):
                client.discover_exams(2026)
            self.assertIsNone(client._entries_cache)
            fail = False
            self.assertEqual(len(client.discover_exams(2026)), 1)
            self.assertEqual(len(client.discover_exams(1997)), 1)
            self.assertEqual(len(client.discover_exams(1996)), 1)
            self.assertEqual(client.build_discovery_year_url(2026), LISTING_URL)

        self.assertEqual(requested, [LISTING_URL, f"{LISTING_URL}&page=2", f"{LISTING_URL}&page=2", f"{LISTING_URL}&page=3"])

    def test_parse_listing_page_extracts_total_pages_and_exam_row(self) -> None:
        page = parse_listing_page(LISTING_HTML)

        self.assertEqual(page.total_pages, 19)
        self.assertEqual(page.entries[0].year_ad, 2026)
        self.assertEqual(page.entries[0].source_exam_id, "gsat-115-guozong")
        self.assertEqual(page.entries[0].title, "115\u5b78\u5e74\u5ea6\u5b78\u79d1\u80fd\u529b\u6e2c\u9a57\uff0d\u570b\u7d9c")
        self.assertEqual(
            [item.label for item in page.entries[0].downloads],
            [
                "\u8a66\u984c\u5167\u5bb9",
                "\u8a66\u984c\u5167\u5bb9",
                "\u7b54\u984c\u5377",
                "\u9078\u64c7\u984c\u7b54\u6848",
                "\u975e\u9078\u64c7\u984c\u8a55\u5206\u539f\u5247",
            ],
        )

    def test_parse_listing_page_extracts_exam_row_when_date_and_title_are_split(self) -> None:
        page = parse_listing_page(LISTING_HTML_SPLIT_ROW)

        self.assertEqual(page.total_pages, 19)
        self.assertEqual(len(page.entries), 1)
        self.assertEqual(page.entries[0].year_ad, 2026)
        self.assertEqual(page.entries[0].source_exam_id, "gsat-115-guozong")
        self.assertEqual(page.entries[0].title, "115\u5b78\u5e74\u5ea6\u5b78\u79d1\u80fd\u529b\u6e2c\u9a57\uff0d\u570b\u7d9c")
        self.assertEqual(
            [item.label for item in page.entries[0].downloads],
            [
                "\u8a66\u984c\u5167\u5bb9",
                "\u8a66\u984c\u5167\u5bb9",
                "\u7b54\u984c\u5377",
                "\u9078\u64c7\u984c\u7b54\u6848",
                "\u975e\u9078\u64c7\u984c\u8a55\u5206\u539f\u5247",
            ],
        )

    def test_parse_listing_page_keeps_two_digit_roc_year_titles(self) -> None:
        page = parse_listing_page(LISTING_HTML_TWO_DIGIT_ROC_YEAR)

        self.assertEqual(len(page.entries), 1)
        self.assertEqual(page.entries[0].year_ad, 1997)
        self.assertEqual(page.entries[0].source_exam_id, "gsat-86-english")
        self.assertEqual([item.label for item in page.entries[0].downloads], ["試題內容", "選擇題答案"])

    def test_fetch_exam_page_turns_one_listing_row_into_many_single_file_papers(self) -> None:
        with patch.object(CeecGsatClient, "_fetch_text", return_value=LISTING_HTML):
            client = CeecGsatClient()
            page = client.fetch_exam_page("gsat-115-guozong", 2026)

        self.assertEqual(page.provider_id, "ceec_gsat")
        self.assertEqual(page.exam_name_raw, "115\u5b78\u5e74\u5ea6\u5b78\u79d1\u80fd\u529b\u6e2c\u9a57\uff0d\u570b\u7d9c")
        self.assertEqual({paper.category_raw for paper in page.papers}, {"\u5b78\u79d1\u80fd\u529b\u6e2c\u9a57"})
        self.assertEqual(
            {file_type for paper in page.papers for file_type in paper.files},
            {"question", "question_alt", "answer_sheet", "answer", "corrected_answer"},
        )
        self.assertEqual(client.build_discovery_year_url(2026), LISTING_URL)
        self.assertEqual(client.build_discovery_exam_url("gsat-115-guozong", 2026), LISTING_URL)

    def test_registry_returns_ceec_provider(self) -> None:
        provider = get_provider("ceec_gsat")

        self.assertIsInstance(provider, SourceProvider)
        self.assertEqual(provider.provider_id, "ceec_gsat")

    def test_ceec_normalization_uses_stable_canonical_bundle_identity(self) -> None:
        with patch.object(CeecGsatClient, "_fetch_text", return_value=LISTING_HTML):
            client = CeecGsatClient()
            page = client.fetch_exam_page("gsat-115-guozong", 2026)

        mirror_metadata = {}
        for paper in page.papers:
            for file_type in paper.files:
                mirror_metadata[(paper.category_code, paper.subject_code, file_type)] = {
                    "checksum": f"{paper.subject_code}-{file_type}",
                    "storage_key": f"providers/ceec_gsat/115/{page.source_exam_id}/{paper.category_code}/{paper.subject_code}/{file_type}.pdf",
                }

        normalized = normalize_papers(
            source_exam_id=page.source_exam_id,
            year_ad=page.year_ad,
            exam_name_raw=page.exam_name_raw,
            papers=page.papers,
            alias_rules=[],
            mirror_base_url="",
            mirror_metadata=mirror_metadata,
        )

        self.assertIsInstance(normalized, NormalizedCatalog)
        self.assertEqual({paper.canonical_id for paper in normalized.papers}, {"ceec-gsat"})
        self.assertEqual({paper.canonical_name for paper in normalized.papers}, {"學科能力測驗"})
        self.assertEqual(normalized.review_queue, [])


if __name__ == "__main__":
    unittest.main()
