import unittest
from urllib.error import URLError
from unittest.mock import patch

from app.models import NormalizedCatalog
from app.normalizer import normalize_papers
from app.providers.base import SourceProvider
from app.providers.registry import get_provider

from app.providers.ceec_ast.client import (
    AST_NOTICE_URL,
    LISTING_URL,
    CeecAstClient,
    parse_guideline_papers,
    parse_listing_page,
    parse_notice_listing,
    parse_notice_papers,
)


LISTING_HTML = """
<html><body>
<h2>一般試題</h2>
<div>共 25 頁 / 241 筆</div>
<table>
  <tr><th>發佈日期</th><th>標題</th><th>下載</th></tr>
  <tr>
    <td>114-08-12</td>
    <td>114學年度分科測驗－數學甲</td>
    <td>
      <a href="/files/math-a-question.pdf">試題內容</a>
      <a href="/files/math-a-question-2.pdf">試題內容</a>
      <a href="/files/math-a-sheet.pdf">答題卷</a>
      <a href="/files/math-a-answer.pdf">選擇(填)題答案</a>
      <a href="/files/math-a-guideline.pdf">非選擇題評分原則</a>
    </td>
  </tr>
</table>
</body></html>
"""


AST_NOTICE_LISTING_HTML = """
<html><body>
  <a href="/xmdoc/cont?xsmsid=0I363338985390931117&amp;sid=0Q167535468443608600">115學年度分科測驗試題/答題卷/參考答案</a>
</body></html>
"""

AST_NOTICE_PAGE_HTML = """
<table>
  <tr><th>科目</th><th>試題</th><th>答題卷</th><th>參考答案</th></tr>
  <tr><td>物理</td><td><a href="/files/physics-question.pdf">試題</a></td><td><a href="/files/physics-sheet.pdf">答題卷</a></td><td><a href="/files/physics-answer.pdf">答案</a></td></tr>
  <tr><td>化學</td><td><a href="/files/chemistry-question.pdf">試題</a></td><td><a href="/files/chemistry-sheet.pdf">答題卷</a></td><td><a href="/files/chemistry-answer.pdf">答案</a></td></tr>
  <tr><td>數學A</td><td><a href="/files/math-a-question.pdf">試題</a></td><td><a href="/files/math-a-sheet.pdf">答題卷</a></td><td><a href="/files/math-a-answer.pdf">答案</a></td></tr>
  <tr><td>生物</td><td><a href="/files/biology-question.pdf">試題</a></td><td><a href="/files/biology-sheet.pdf">答題卷</a></td><td><a href="/files/biology-answer.pdf">答案</a></td></tr>
  <tr><td>歷史</td><td><a href="/files/history-question.pdf">試題</a></td><td><a href="/files/history-sheet.pdf">答題卷</a></td><td><a href="/files/history-answer.pdf">答案</a></td></tr>
  <tr><td>地理</td><td><a href="/files/geography-question.pdf">試題</a></td><td><a href="/files/geography-sheet.pdf">答題卷</a></td><td><a href="/files/geography-answer.pdf">答案</a></td></tr>
  <tr><td>數學B</td><td><a href="/files/math-b-question.pdf">試題</a></td><td><a href="/files/math-b-sheet.pdf">答題卷</a></td><td><a href="/files/math-b-answer.pdf">答案</a></td></tr>
  <tr><td>公民與社會</td><td><a href="/files/civics-question.pdf">試題</a></td><td><a href="/files/civics-sheet.pdf">答題卷</a></td><td><a href="/files/civics-answer.pdf">答案</a></td></tr>
</table>
"""

AST_CURRENT_NOTICE_LISTING_HTML = """
<html><body>
  <a href="/xmdoc?xsmsid=exam&amp;sid=confirmed">115學年度分科測驗各考科選擇(填)題答案確定</a>
  <a href="/xmdoc?xsmsid=exam&amp;sid=guidelines">115學年度分科測驗各考科非選擇題評分原則</a>
</body></html>
"""

AST_GUIDELINE_PAGE_HTML = """
<table>
  <tr><th>科目</th><th>評分原則</th></tr>
  <tr><td>物理</td><td><a href="/files/physics-guideline.pdf">物理</a></td></tr>
  <tr><td>化學</td><td><a href="/files/chemistry-guideline.pdf">化學</a></td></tr>
</table>
"""


class CeecAstParserTests(unittest.TestCase):
    def test_listing_retries_only_the_page_that_times_out(self) -> None:
        html = LISTING_HTML.replace("共 25 頁", "共 2 頁").encode("utf-8")
        second_page_url = f"{LISTING_URL}&page=2"
        requested: list[str] = []

        class Response:
            status = 200
            headers: dict[str, str] = {}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self) -> bytes:
                return html

        def open_page(request, _timeout):
            requested.append(request.full_url)
            if request.full_url == second_page_url and requested.count(second_page_url) == 1:
                raise URLError("TLS handshake timed out")
            return Response()

        client = CeecAstClient()
        with patch.object(client._http, "_open", side_effect=open_page), patch("app.providers.http.time.sleep"):
            entries = client._iter_entries()

        self.assertEqual(len(entries), 1)
        self.assertEqual(requested, [LISTING_URL, second_page_url, second_page_url])

    def test_parse_listing_page_extracts_total_pages_and_ast_row(self) -> None:
        page = parse_listing_page(LISTING_HTML)

        self.assertEqual(page.total_pages, 25)
        self.assertEqual(len(page.entries), 1)
        self.assertEqual(page.entries[0].year_ad, 2025)
        self.assertEqual(page.entries[0].source_exam_id, "ceec-ast-114-math-a")
        self.assertEqual(page.entries[0].title, "114學年度分科測驗－數學甲")
        self.assertEqual(
            [item.label for item in page.entries[0].downloads],
            ["試題內容", "試題內容", "答題卷", "選擇(填)題答案", "非選擇題評分原則"],
        )

    def test_parse_notice_listing_and_subject_table_extracts_current_ast_materials(self) -> None:
        notices = parse_notice_listing(AST_NOTICE_LISTING_HTML)
        papers = parse_notice_papers(AST_NOTICE_PAGE_HTML, base_url=notices[0].url, year_ad=2026)

        self.assertEqual([(notice.source_exam_id, notice.year_ad) for notice in notices], [("ceec-ast-notice-115", 2026)])
        self.assertEqual(len(papers), 8)
        self.assertEqual({paper.subject_code for paper in papers}, {"physics", "chemistry", "math-a", "biology", "history", "geography", "math-b", "civics-society"})
        self.assertEqual({file_type for paper in papers for file_type in paper.files}, {"question", "answer_sheet", "answer"})

    def test_client_prefers_current_official_notice_over_generic_listing(self) -> None:
        notice_url = "https://www.ceec.edu.tw/xmdoc/cont?xsmsid=0I363338985390931117&sid=0Q167535468443608600"

        def fake_fetch(url: str) -> str:
            if url == AST_NOTICE_URL:
                return AST_NOTICE_LISTING_HTML
            if url == notice_url:
                return AST_NOTICE_PAGE_HTML
            return LISTING_HTML

        client = CeecAstClient()
        client._fetch_text = fake_fetch  # type: ignore[method-assign]

        self.assertEqual(client.discover_available_years(), [2026, 2025])
        self.assertEqual([exam.code for exam in client.discover_exams(2026)], ["ceec-ast-notice-115"])
        page = client.fetch_exam_page("ceec-ast-notice-115", 2026)

        self.assertEqual(page.exam_name_raw, "115學年度分科測驗試題/答題卷/參考答案")
        self.assertEqual(len(page.papers), 8)

    def test_client_discovers_current_confirmed_answers_and_scoring_principles(self) -> None:
        confirmed_url = "https://www.ceec.edu.tw/xmdoc/cont?sid=confirmed&xsmsid=exam"
        guidelines_url = "https://www.ceec.edu.tw/xmdoc/cont?sid=guidelines&xsmsid=exam"

        def fake_fetch(url: str) -> str:
            if url == AST_NOTICE_URL:
                return AST_CURRENT_NOTICE_LISTING_HTML
            if url == confirmed_url:
                return AST_NOTICE_PAGE_HTML
            if url == guidelines_url:
                return AST_GUIDELINE_PAGE_HTML
            return LISTING_HTML

        client = CeecAstClient()
        client._fetch_text = fake_fetch  # type: ignore[method-assign]

        self.assertEqual(
            [exam.code for exam in client.discover_exams(2026)],
            ["ceec-ast-confirmed-115", "ceec-ast-guidelines-115"],
        )
        self.assertEqual(client.build_discovery_year_url(2026), AST_NOTICE_URL)
        self.assertEqual(client.build_discovery_year_url(2025), LISTING_URL)
        self.assertEqual(client.build_discovery_exam_url("ceec-ast-confirmed-115", 2026), confirmed_url)
        self.assertEqual(client.build_discovery_exam_url("ceec-ast-114-math-a", 2025), LISTING_URL)
        page = client.fetch_exam_page("ceec-ast-guidelines-115", 2026)

        self.assertEqual(len(page.papers), 2)
        self.assertEqual({file_type for paper in page.papers for file_type in paper.files}, {"corrected_answer"})

    def test_parse_guideline_papers_requires_and_extracts_scoring_principles(self) -> None:
        papers = parse_guideline_papers(AST_GUIDELINE_PAGE_HTML, base_url=AST_NOTICE_URL, year_ad=2026)

        self.assertEqual([paper.subject_code for paper in papers], ["physics", "chemistry"])
        self.assertEqual({file_type for paper in papers for file_type in paper.files}, {"corrected_answer"})

    def test_fetch_exam_page_turns_one_listing_row_into_many_single_file_papers(self) -> None:
        with patch.object(CeecAstClient, "_fetch_text", return_value=LISTING_HTML):
            client = CeecAstClient()
            page = client.fetch_exam_page("ceec-ast-114-math-a", 2025)

        self.assertEqual(page.provider_id, "ceec_ast")
        self.assertEqual(page.exam_name_raw, "114學年度分科測驗－數學甲")
        self.assertEqual({paper.category_raw for paper in page.papers}, {"分科測驗"})
        self.assertEqual(
            {file_type for paper in page.papers for file_type in paper.files},
            {"question", "question_alt", "answer_sheet", "answer", "corrected_answer"},
        )

    def test_registry_returns_ceec_ast_provider(self) -> None:
        provider = get_provider("ceec_ast")

        self.assertIsInstance(provider, SourceProvider)
        self.assertEqual(provider.provider_id, "ceec_ast")

    def test_ceec_ast_normalization_uses_stable_canonical_bundle_identity(self) -> None:
        with patch.object(CeecAstClient, "_fetch_text", return_value=LISTING_HTML):
            client = CeecAstClient()
            page = client.fetch_exam_page("ceec-ast-114-math-a", 2025)

        mirror_metadata = {}
        for paper in page.papers:
            for file_type in paper.files:
                mirror_metadata[(paper.category_code, paper.subject_code, file_type)] = {
                    "checksum": f"{paper.subject_code}-{file_type}",
                    "storage_key": f"providers/ceec_ast/114/{page.source_exam_id}/{paper.category_code}/{paper.subject_code}/{file_type}.pdf",
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
        self.assertEqual({paper.canonical_id for paper in normalized.papers}, {"ceec-ast"})
        self.assertEqual({paper.canonical_name for paper in normalized.papers}, {"分科測驗"})
        self.assertEqual(normalized.review_queue, [])


if __name__ == "__main__":
    unittest.main()
