import unittest
from unittest.mock import patch

from app.models import NormalizedCatalog
from app.normalizer import normalize_papers
from app.providers.base import SourceProvider
from app.providers.registry import get_provider

from app.providers.rcpet_cap.client import (
    MAIN_PAGE_URL,
    RcpetCapClient,
    _resolve_gdrive_confirm_url,
    _resolve_gdrive_url,
    parse_dropdown,
    parse_year_page,
)


MAIN_PAGE_HTML = """
<html><body>
<select id="exam" onchange="MM_jumpMenu('iframe',this,0)">
  <option value="exam/115/115exam.html" selected="selected">115年國中教育會考</option>
  <option value="exam/114/114exam.html">114年國中教育會考</option>
  <option value="exam/111c/111practice.html">111年參考試題本</option>
  <option value="exam/103/103exam.html">103年國中教育會考</option>
  <option value="exam/102/102exam.html">102年試辦國中教育會考</option>
  <option value="BCTESTexam.html">國中基測歷屆試題</option>
</select>
</body></html>
"""

YEAR_PAGE_HTML = """
<!DOCTYPE html>
<html lang="zh-Hant-TW">
<head><meta charset="utf-8" /><title>115年國中教育會考題本及相關檔案</title></head>
<body>
<h1 class="style1">115年國中教育會考題本及相關檔案</h1>
<ul>
  <li><a href="https://drive.google.com/file/d/answer-id/view" target="_blank">參考答案</a></li>
  <li><a href="https://drive.google.com/file/d/desc-id/view" target="_blank">試題說明</a></li>
  <li><a href="https://drive.google.com/file/d/writing-id/view" target="_blank">寫作測驗</a></li>
  <li><a href="https://drive.google.com/file/d/chinese-id/view" target="_blank">國文科</a></li>
  <li><a href="https://drive.google.com/file/d/eng-read-id/view" target="_blank">英語（閱讀）</a></li>
  <li><a href="https://drive.google.com/file/d/eng-listen-id/view" target="_blank">英語（聽力）</a></li>
  <li><a href="https://drive.google.com/file/d/math-id/view" target="_blank">數學科</a></li>
  <li><a href="https://drive.google.com/file/d/social-id/view" target="_blank">社會科</a></li>
  <li><a href="https://drive.google.com/file/d/science-id/view" target="_blank">自然科</a></li>
  <li><a href="115doubt.html">115年國中教育會考試題疑義新聞稿暨釋復內容</a></li>
  <li><a href="https://drive.google.com/file/d/stats-id/view" target="_blank">115年國中教育會考各科等級加標示與答對題數對照表</a></li>
</ul>
<div class="style1">寫作樣卷：
  <a href="https://drive.google.com/file/d/sample-6/view" target="_blank">六級分</a>
</div>
</body>
</html>
"""

YEAR_PAGE_HTML_LEGACY = """
<!DOCTYPE html>
<html lang="zh-Hant-TW">
<head><meta charset="utf-8" /><title>103年國中教育會考題本及相關檔案</title></head>
<body>
<h1 class="style1">103年國中教育會考題本及相關檔案</h1>
<ul>
  <li><a href="https://drive.google.com/file/d/answer-103/view" target="_blank">參考答案</a></li>
  <li><a href="https://drive.google.com/file/d/desc-103/view" target="_blank">試題說明</a></li>
  <li><a href="https://drive.google.com/file/d/writing-103/view" target="_blank">寫作測驗</a></li>
  <li><a href="https://drive.google.com/file/d/chinese-103/view" target="_blank">國文科</a></li>
  <li><a href="https://drive.google.com/file/d/eng-103/view" target="_blank">英語科</a></li>
  <li><a href="https://drive.google.com/file/d/eng-listen-103/view" target="_blank">英語科聽力語音檔(壓縮檔)-備註</a></li>
  <li><a href="https://drive.google.com/file/d/math-103/view" target="_blank">數學科</a></li>
  <li><a href="https://drive.google.com/file/d/social-103/view" target="_blank">社會科</a></li>
  <li><a href="https://drive.google.com/file/d/science-103/view" target="_blank">自然科</a></li>
</ul>
</body>
</html>
"""


class DropdownParserTests(unittest.TestCase):
    def test_parse_dropdown_extracts_exam_entries(self) -> None:
        entries = parse_dropdown(MAIN_PAGE_HTML)

        self.assertEqual(len(entries), 5)
        self.assertEqual(entries[0].year_roc, 115)
        self.assertEqual(entries[0].year_ad, 2026)
        self.assertEqual(entries[0].page_url, "exam/115/115exam.html")
        self.assertEqual(entries[0].label, "115年國中教育會考")

    def test_parse_dropdown_skips_bctest(self) -> None:
        entries = parse_dropdown(MAIN_PAGE_HTML)

        labels = [e.label for e in entries]
        self.assertNotIn("國中基測歷屆試題", labels)

    def test_parse_dropdown_handles_reference_test(self) -> None:
        entries = parse_dropdown(MAIN_PAGE_HTML)

        ref_entry = next(e for e in entries if e.year_dir == "111c")
        self.assertEqual(ref_entry.year_roc, 111)
        self.assertEqual(ref_entry.year_ad, 2022)
        self.assertEqual(ref_entry.label, "111年參考試題本")

    def test_parse_dropdown_handles_pilot_year(self) -> None:
        entries = parse_dropdown(MAIN_PAGE_HTML)

        pilot = next(e for e in entries if e.year_roc == 102)
        self.assertEqual(pilot.year_ad, 2013)
        self.assertEqual(pilot.label, "102年試辦國中教育會考")


class GdriveUrlTests(unittest.TestCase):
    def test_resolve_viewer_url(self) -> None:
        url = "https://drive.google.com/file/d/1aBcDeFgHiJk/view"
        self.assertEqual(
            _resolve_gdrive_url(url),
            "https://drive.google.com/uc?id=1aBcDeFgHiJk&export=download",
        )

    def test_resolve_viewer_url_with_query(self) -> None:
        url = "https://drive.google.com/file/d/1aBcDeFgHiJk/view?usp=sharing"
        self.assertEqual(
            _resolve_gdrive_url(url),
            "https://drive.google.com/uc?id=1aBcDeFgHiJk&export=download",
        )

    def test_non_gdrive_url_unchanged(self) -> None:
        url = "https://cap.rcpet.edu.tw/exam/115/115P_Chinese.pdf"
        self.assertEqual(_resolve_gdrive_url(url), url)

    def test_resolve_confirm_url_from_google_drive_warning_form(self) -> None:
        html = """
        <form id="download-form" action="https://drive.usercontent.google.com/download" method="get">
          <input type="hidden" name="id" value="file-id">
          <input type="hidden" name="export" value="download">
          <input type="hidden" name="confirm" value="t">
          <input type="hidden" name="uuid" value="abc">
        </form>
        """

        resolved = _resolve_gdrive_confirm_url(html, "https://drive.google.com/uc?id=file-id&export=download")

        self.assertIn("drive.usercontent.google.com/download", resolved)
        self.assertIn("confirm=t", resolved)
        self.assertIn("uuid=abc", resolved)

    def test_resolve_confirm_url_handles_relative_warning_form_action(self) -> None:
        html = """
        <form id="download-form" action="/download" method="get">
          <input type="hidden" name="id" value="file-id">
          <input type="hidden" name="confirm" value="t">
        </form>
        """

        resolved = _resolve_gdrive_confirm_url(html, "https://drive.usercontent.google.com/uc?id=file-id")

        self.assertTrue(resolved.startswith("https://drive.usercontent.google.com/download?"))
        self.assertIn("confirm=t", resolved)


class YearPageParserTests(unittest.TestCase):
    def test_parse_year_page_extracts_all_subjects(self) -> None:
        result = parse_year_page(YEAR_PAGE_HTML, year_roc=115)

        subject_codes = [p.subject_code for p in result.papers]
        self.assertIn("writing", subject_codes)
        self.assertIn("chinese", subject_codes)
        self.assertIn("english-reading", subject_codes)
        self.assertIn("english-listening", subject_codes)
        self.assertIn("math", subject_codes)
        self.assertIn("social", subject_codes)
        self.assertIn("science", subject_codes)

    def test_parse_year_page_extracts_answer(self) -> None:
        result = parse_year_page(YEAR_PAGE_HTML, year_roc=115)

        answer_papers = [p for p in result.papers if "answer" in p.files]
        self.assertEqual(len(answer_papers), 1)
        self.assertIn("answer-id", answer_papers[0].files["answer"])

    def test_parse_year_page_extracts_question_files(self) -> None:
        result = parse_year_page(YEAR_PAGE_HTML, year_roc=115)

        chinese = next(p for p in result.papers if p.subject_code == "chinese")
        self.assertIn("question", chinese.files)
        self.assertIn("chinese-id", chinese.files["question"])

    def test_parse_year_page_skips_supplementary_items(self) -> None:
        result = parse_year_page(YEAR_PAGE_HTML, year_roc=115)

        subject_names = [p.subject_name_raw for p in result.papers]
        for name in subject_names:
            self.assertNotIn("等級加標示", name)
            self.assertNotIn("試題疑義", name)

    def test_parse_year_page_skips_writing_samples(self) -> None:
        result = parse_year_page(YEAR_PAGE_HTML, year_roc=115)

        urls = []
        for p in result.papers:
            urls.extend(p.files.values())
        self.assertFalse(any("sample-6" in u for u in urls))

    def test_parse_year_page_sets_metadata(self) -> None:
        result = parse_year_page(YEAR_PAGE_HTML, year_roc=115)

        self.assertEqual(result.provider_id, "rcpet_cap")
        self.assertEqual(result.source_exam_id, "cap-115")
        self.assertEqual(result.year_ad, 2026)
        self.assertEqual(result.year_roc, 115)
        self.assertIn("115年國中教育會考", result.exam_name_raw)

    def test_parse_year_page_sets_category(self) -> None:
        result = parse_year_page(YEAR_PAGE_HTML, year_roc=115)

        for paper in result.papers:
            self.assertEqual(paper.category_raw, "國中教育會考")
            self.assertEqual(paper.category_code, "115")

    def test_parse_year_page_legacy_subject_names(self) -> None:
        result = parse_year_page(YEAR_PAGE_HTML_LEGACY, year_roc=103)

        subject_codes = [p.subject_code for p in result.papers]
        self.assertIn("english-reading", subject_codes)
        self.assertIn("english-listening", subject_codes)

    def test_parse_year_page_legacy_mp3_listening_uses_audio_file_type(self) -> None:
        html = """
        <html><body>
        <h1>102年國中教育會考題本及相關檔案</h1>
        <ul>
          <li><a href="https://drive.google.com/file/d/audio-id/view">英語科聽力語音檔(mp3)-備註</a></li>
        </ul>
        </body></html>
        """

        result = parse_year_page(html, year_roc=102)
        listening = next(p for p in result.papers if p.subject_code == "english-listening")

        self.assertIn("listening_audio", listening.files)

    def test_parse_year_page_resolves_urls_with_base(self) -> None:
        html = """
        <html><body>
        <h1>114年國中教育會考題本及相關檔案</h1>
        <ul>
          <li><a href="114P_Chinese.pdf">國文科</a></li>
        </ul>
        </body></html>
        """
        result = parse_year_page(html, year_roc=114, base_url="https://cap.rcpet.edu.tw/exam/114/114exam.html")

        chinese = next(p for p in result.papers if p.subject_code == "chinese")
        self.assertTrue(chinese.files["question"].startswith("https://cap.rcpet.edu.tw/exam/114/"))

    def test_parse_year_page_includes_description(self) -> None:
        result = parse_year_page(YEAR_PAGE_HTML, year_roc=115)

        desc_papers = [p for p in result.papers if "question_alt" in p.files]
        self.assertEqual(len(desc_papers), 1)

    def test_parse_year_page_111c_gets_unique_id(self) -> None:
        result = parse_year_page(YEAR_PAGE_HTML_LEGACY, year_roc=111, year_dir="111c")

        self.assertEqual(result.source_exam_id, "cap-111c")
        for paper in result.papers:
            self.assertEqual(paper.category_code, "111c")

    def test_111_and_111c_coexist(self) -> None:
        regular = parse_year_page(YEAR_PAGE_HTML_LEGACY, year_roc=111, year_dir="111")
        reference = parse_year_page(YEAR_PAGE_HTML_LEGACY, year_roc=111, year_dir="111c")

        self.assertEqual(regular.source_exam_id, "cap-111")
        self.assertEqual(reference.source_exam_id, "cap-111c")
        self.assertNotEqual(regular.source_exam_id, reference.source_exam_id)


class RcpetCapClientTests(unittest.TestCase):
    @patch.object(RcpetCapClient, "_fetch_text", return_value=YEAR_PAGE_HTML)
    def test_fetch_exam_page_returns_source_exam_page(self, mock_fetch) -> None:
        client = RcpetCapClient()
        with patch.object(client, "_get_dropdown_entries") as mock_dd:
            from app.providers.rcpet_cap.client import DropdownEntry
            mock_dd.return_value = [
                DropdownEntry(year_dir="115", page_url="exam/115/115exam.html", label="115年國中教育會考", year_roc=115, year_ad=2026),
            ]

            page = client.fetch_exam_page("cap-115", year_ad=2026)

        self.assertEqual(page.provider_id, "rcpet_cap")
        self.assertEqual(page.year_ad, 2026)
        self.assertEqual(page.year_roc, 115)
        self.assertTrue(len(page.papers) > 0)


    def test_discover_exams_111_and_111c_distinct(self) -> None:
        client = RcpetCapClient()
        with patch.object(client, "_get_dropdown_entries") as mock_dd:
            from app.providers.rcpet_cap.client import DropdownEntry
            mock_dd.return_value = [
                DropdownEntry(year_dir="111", page_url="exam/111/111exam.html", label="111年國中教育會考", year_roc=111, year_ad=2022),
                DropdownEntry(year_dir="111c", page_url="exam/111c/111practice.html", label="111年參考試題本", year_roc=111, year_ad=2022),
            ]

            exams = client.discover_exams(year_ad=2022)
            year_url = client.build_discovery_year_url(2022)
            exam_url = client.build_discovery_exam_url("cap-111c", 2022)

        codes = [e.code for e in exams]
        self.assertEqual(len(codes), 2)
        self.assertIn("cap-111", codes)
        self.assertIn("cap-111c", codes)
        self.assertEqual(year_url, MAIN_PAGE_URL)
        self.assertEqual(
            exam_url,
            "https://cap.rcpet.edu.tw/exam/111c/111practice.html",
        )

    def test_dropdown_discovery_is_cached_within_one_client(self) -> None:
        client = RcpetCapClient()
        with patch.object(client, "_fetch_text", return_value=MAIN_PAGE_HTML) as fetch:
            self.assertIn(2026, client.discover_available_years())
            self.assertEqual([exam.code for exam in client.discover_exams(2026)], ["cap-115"])

        fetch.assert_called_once_with(MAIN_PAGE_URL)


class RegistryTests(unittest.TestCase):
    def test_registry_returns_rcpet_cap_provider(self) -> None:
        provider = get_provider("rcpet_cap")

        self.assertIsInstance(provider, SourceProvider)
        self.assertEqual(provider.provider_id, "rcpet_cap")


class NormalizerTests(unittest.TestCase):
    def test_cap_papers_normalize_to_canonical(self) -> None:
        result = parse_year_page(YEAR_PAGE_HTML, year_roc=115)

        catalog = normalize_papers(
            source_exam_id=result.source_exam_id,
            year_ad=result.year_ad,
            exam_name_raw=result.exam_name_raw,
            papers=result.papers,
            alias_rules=[],
            mirror_base_url="",
            mirror_metadata={},
        )

        self.assertTrue(len(catalog.papers) > 0)
        for paper in catalog.papers:
            self.assertEqual(paper.canonical_id, "rcpet-cap")
            self.assertEqual(paper.canonical_name, "國中教育會考")
        self.assertEqual(len(catalog.review_queue), 0)


    def test_cap_111c_normalizes_to_canonical(self) -> None:
        result = parse_year_page(YEAR_PAGE_HTML_LEGACY, year_roc=111, year_dir="111c")

        catalog = normalize_papers(
            source_exam_id=result.source_exam_id,
            year_ad=result.year_ad,
            exam_name_raw=result.exam_name_raw,
            papers=result.papers,
            alias_rules=[],
            mirror_base_url="",
            mirror_metadata={},
        )

        self.assertTrue(len(catalog.papers) > 0)
        for paper in catalog.papers:
            self.assertEqual(paper.canonical_id, "rcpet-cap")
            self.assertEqual(paper.canonical_name, "國中教育會考")


if __name__ == "__main__":
    unittest.main()


def test_dropdown_ignores_valueless_option_attributes():
    from app.providers.rcpet_cap.client import parse_dropdown

    assert parse_dropdown('<select><option value>請選擇</option></select>') == []
