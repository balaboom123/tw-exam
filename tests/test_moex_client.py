import unittest
from unittest.mock import patch

from app.providers.moex.client import (
    MoexClient, MoexSourceQualityError, make_download_url, make_result_url,
    parse_result_page, parse_search_page,
)


SEARCH_HTML = """
<html><body>
  <select name="ctl00$holderContent$wUctlExamYearStart$ddlExamYear">
    <option value="2026">115</option>
    <option selected="selected" value="2025">114</option>
    <option value="2024">113</option>
  </select>
  <select name="ctl00$holderContent$ddlExamCode" id="ctl00_holderContent_ddlExamCode">
    <option value="">請選擇考試...</option>
    <option value="114170">114年專技高考護理師</option>
    <option value="114160">114年專技高考藥師</option>
  </select>
</body></html>
"""


RESULT_HTML = """
<html><body>
  <table id="ctl00_holderContent_tblExamQand">
    <tr>
      <td></td>
      <td>115年專技高考護理師</td>
      <td><a href="wHandExamQandA_File.ashx?t=B&amp;code=115030">無障礙題本</a></td>
      <td><a href="wHandExamQandA_File.ashx?t=A&amp;code=115030">全部答案</a></td>
    </tr>
    <tr>
      <td></td><td></td><td></td><td>護理師</td>
    </tr>
    <tr>
      <td></td><td></td><td></td>
      <td><a href="wHandExamQandA_File.ashx?t=Q&amp;code=115030&amp;c=101&amp;s=0101&amp;q=1">基礎醫學試題答案</a></td>
      <td><a href="wHandExamQandA_File.ashx?t=Q&amp;code=115030&amp;c=101&amp;s=0101&amp;q=1">試題</a></td>
      <td><a href="wHandExamQandA_File.ashx?t=S&amp;code=115030&amp;c=101&amp;s=0101&amp;q=1">答案</a></td>
      <td></td>
    </tr>
    <tr>
      <td></td><td></td><td></td>
      <td><a href="wHandExamQandA_File.ashx?t=Q&amp;code=115030&amp;c=101&amp;s=0102&amp;q=1">基本護理學試題答案更正答案</a></td>
      <td><a href="wHandExamQandA_File.ashx?t=Q&amp;code=115030&amp;c=101&amp;s=0102&amp;q=1">試題</a></td>
      <td><a href="wHandExamQandA_File.ashx?t=S&amp;code=115030&amp;c=101&amp;s=0102&amp;q=1">答案</a></td>
      <td><a href="wHandExamQandA_File.ashx?t=M&amp;code=115030&amp;c=101&amp;s=0102&amp;q=1">更正答案</a></td>
    </tr>
  </table>
</body></html>
"""


class ParseSearchPageTests(unittest.TestCase):
    def test_parse_search_page_extracts_years_and_exam_options(self) -> None:
        page = parse_search_page(SEARCH_HTML)

        self.assertEqual(page.available_years, [2026, 2025, 2024])
        self.assertEqual(page.exams[0].code, "114170")
        self.assertEqual(page.exams[0].year_ad, 2025)
        self.assertEqual(page.exams[0].year_roc, 114)
        self.assertIn("護理師", page.exams[0].label)


    def test_required_search_structure_rejects_placeholder_html(self) -> None:
        with self.assertRaises(MoexSourceQualityError):
            parse_search_page("<html><body>temporarily unavailable</body></html>", require_year_select=True)

        with self.assertRaises(MoexSourceQualityError):
            parse_search_page(
                '<select name="ctl00$holderContent$wUctlExamYearStart$ddlExamYear"><option value="2026">115</option></select>',
                require_exam_select=True,
                require_exams=True,
            )


class ParseResultPageTests(unittest.TestCase):
    def test_parse_result_page_extracts_attachments_and_subject_files(self) -> None:
        parsed = parse_result_page(RESULT_HTML, exam_code="115030", year_ad=2026)

        self.assertEqual(parsed.exam_name_raw, "115年專技高考護理師")
        self.assertEqual(len(parsed.attachments), 2)
        self.assertEqual(parsed.attachments[0].file_type, "accessible_bundle")
        self.assertEqual(parsed.attachments[1].file_type, "all_answers")
        self.assertEqual(len(parsed.papers), 2)

        first_paper = parsed.papers[0]
        self.assertEqual(first_paper.category_raw, "護理師")
        self.assertEqual(first_paper.category_code, "101")
        self.assertEqual(first_paper.subject_code, "0101")
        self.assertEqual(first_paper.subject_name_raw, "基礎醫學")
        self.assertEqual(set(first_paper.files), {"question", "answer"})

        second_paper = parsed.papers[1]
        self.assertEqual(second_paper.subject_name_raw, "基本護理學")
        self.assertEqual(set(second_paper.files), {"question", "answer", "corrected_answer"})

    def test_required_result_table_rejects_placeholder_html(self) -> None:
        with self.assertRaises(MoexSourceQualityError):
            parse_result_page("<html><body>blocked</body></html>", exam_code="115030", year_ad=2026, require_table=True)

    def test_url_builders_return_live_http_entrypoints(self) -> None:
        self.assertEqual(
            make_result_url("115030", 2026),
            "https://wwwq.moex.gov.tw/exam/wFrmExamQandASearch.aspx?e=115030&y=2026",
        )
        self.assertEqual(
            make_download_url("wHandExamQandA_File.ashx?t=Q&code=115030&c=101&s=0101&q=1"),
            "https://wwwq.moex.gov.tw/exam/wHandExamQandA_File.ashx?t=Q&code=115030&c=101&s=0101&q=1",
        )


class FakeTextResponse:
    def __init__(self, body: bytes, content_type: str) -> None:
        self._body = body
        self.headers = {"Content-Type": content_type}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeTextResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class FetchTextDecodingTests(unittest.TestCase):
    def test_fetch_text_falls_back_to_meta_charset(self) -> None:
        html = '<html><head><meta charset="big5"></head><body>護理師</body></html>'
        response = FakeTextResponse(body=html.encode("big5"), content_type="text/html")

        with patch("app.providers.moex.client.urlopen", return_value=response):
            client = MoexClient(ssl_context=object())
            text = client._fetch_text("https://example.test")

        self.assertIn("護理師", text)

    def test_fetch_text_falls_back_to_cp950_when_headers_are_missing(self) -> None:
        response = FakeTextResponse(body="藥師".encode("cp950"), content_type="text/html")

        with patch("app.providers.moex.client.urlopen", return_value=response):
            client = MoexClient(ssl_context=object())
            text = client._fetch_text("https://example.test")

        self.assertEqual(text, "藥師")


if __name__ == "__main__":
    unittest.main()


def test_search_options_with_valueless_value_are_ignored():
    page = parse_search_page('''
        <select name="ctl00$holderContent$wUctlExamYearStart$ddlExamYear">
          <option value>請選擇</option><option value="2026">115</option>
        </select>
        <select name="ctl00$holderContent$ddlExamCode"><option value>請選擇</option></select>
    ''')
    assert page.available_years == [2026]
    assert page.exams == []
