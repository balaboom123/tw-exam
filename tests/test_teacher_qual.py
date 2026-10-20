"""Tests for the teacher_qual provider."""

import unittest
from urllib.parse import parse_qs
from unittest.mock import patch

from app.providers.teacher_qual.client import (
    LISTING_URL,
    TeacherQualClient,
    parse_available_years,
    parse_downloads,
    parse_order_options,
    parse_paper_downloads,
    listing_material,
    parse_subject_options,
)


TEACHER_LISTING_HTML = """
<select name="ctl00$ContentPlaceHolder1$schyy" id="ctl00_ContentPlaceHolder1_schyy">
  <option selected="selected" value=""></option>
  <option value="115">115</option>
  <option value="114">114</option>
  <option value="094">094</option>
</select>
<select name="ctl00$ContentPlaceHolder1$exid" id="ctl00_ContentPlaceHolder1_exid">
  <option selected="selected" value=""></option>
  <option value="10">幼兒園師資類科</option>
  <option value="99">全部類科</option>
</select>
<table>
  <tr><td>樣卷</td></tr>
  <tr><td>01_選擇題答案卡樣張<br><a href="ShowPicOut2.aspx?ASParam=sample">文件下載</a></td></tr>
  <tr><td>115年試題及參考答案</td></tr>
  <tr><td>考題及參考答案</td><td>全部下載<br><a href="ShowPicOut2.aspx?ASParam=all">文件下載</a></td></tr>
</table>
"""

ZERO_PADDED_YEAR_HTML = """
<table>
  <tr><td>樣卷</td></tr>
  <tr><td>01_選擇題答案卡樣張<br><a href="ShowPicOut2.aspx?ASParam=sample">文件下載</a></td></tr>
  <tr><td>099年試題及參考答案</td></tr>
  <tr><td>考題及參考答案</td><td>全部下載<br><a href="ShowPicOut2.aspx?ASParam=y099">文件下載</a></td></tr>
</table>
"""

SAMPLE_ONLY_YEAR_HTML = """
<table>
  <tr><td>樣卷</td></tr>
  <tr><td>01_選擇題答案卡樣張<br><a href="ShowPicOut2.aspx?ASParam=sample">文件下載</a></td></tr>
  <tr><td>107年僅有範例題</td></tr>
  <tr><td>範例題</td><td>全部下載<br><a href="ShowPicOut2.aspx?ASParam=y107">文件下載</a></td></tr>
</table>
"""

ORDER_SELECT_HTML = """
<select name="ctl00$ContentPlaceHolder1$ddlOrder" id="ctl00_ContentPlaceHolder1_ddlOrder">
  <option selected="selected" value=""></option>
  <option value="1">第一次考試</option>
  <option value="2">第二次考試</option>
</select>
"""


class TeacherQualParserTests(unittest.TestCase):
    def test_individual_cells_keep_subject_role_and_format_together(self) -> None:
        html = '''
        <span id="ctl00_ContentPlaceHolder1_frmGrd_DataGrid_ctl02_frmGrd_sbid03">
          學習者發展與適性輔導<br>考&nbsp;&nbsp;題
          <a href="ShowPicOut2.aspx?ASParam=q" title="下載pdf檔案">下載</a>
          <br>參考答案
          <a href="ShowPicOut2.aspx?ASParam=a" title="下載pdf檔案">下載</a>
        </span>
        <span id="ctl00_ContentPlaceHolder1_frmGrd_DataGrid_ctl02_frmGrd_sbid04">
          課程教學與評量<br>考 題
          <a href="ShowPicOut2.aspx?ASParam=next" title="下載docx檔案"></a>
        </span>'''
        papers = parse_paper_downloads(html)
        self.assertEqual(
            [(p.subject, p.file_role, p.file_format) for p in papers],
            [("學習者發展與適性輔導", "question", "pdf"),
             ("學習者發展與適性輔導", "answer", "pdf"),
             ("課程教學與評量", "question", "docx")],
        )
        self.assertTrue(papers[1].url.endswith("ASParam=a"))

    def test_individual_parser_rejects_unknown_roles_and_duplicate_candidates(self) -> None:
        cell = '''<span id="ctl00_ContentPlaceHolder1_frmGrd_DataGrid_ctl02_frmGrd_sbid01">
        國語文能力測驗<br>{role}<a href="ShowPicOut2.aspx?ASParam=q"
        title="下載pdf檔案"></a></span>'''
        with self.assertRaisesRegex(ValueError, "Unreviewed.*file role"):
            parse_paper_downloads(cell.format(role="尚未確認的類型"))
        with self.assertRaisesRegex(ValueError, "Duplicate.*subject/role/format"):
            parse_paper_downloads(cell.format(role="考題") * 2)

    def test_individual_parser_preserves_alternate_official_formats(self) -> None:
        cell = '''<span id="ctl00_ContentPlaceHolder1_frmGrd_DataGrid_ctl02_frmGrd_sbid01">
        國語文能力測驗<br>考題<a href="ShowPicOut2.aspx?ASParam=pdf"
        title="下載pdf檔案"></a><br>考題<a href="ShowPicOut2.aspx?ASParam=doc"
        title="下載doc檔案"></a></span>'''
        self.assertEqual([p.file_format for p in parse_paper_downloads(cell)], ["pdf", "doc"])

    def test_parse_available_years(self) -> None:
        self.assertEqual(parse_available_years(TEACHER_LISTING_HTML), [2026, 2025, 2005])

    def test_parse_subject_options(self) -> None:
        self.assertEqual(parse_subject_options(TEACHER_LISTING_HTML), [("10", "幼兒園師資類科"), ("99", "全部類科")])

    def test_parse_downloads_keeps_historical_all_bundle(self) -> None:
        downloads = parse_downloads(TEACHER_LISTING_HTML, year_roc=115)

        self.assertEqual(len(downloads), 1)
        self.assertEqual(downloads[0].label, "115年試題及參考答案 考題及參考答案 全部下載")
        self.assertTrue(downloads[0].url.endswith("ASParam=all"))

    def test_parse_downloads_keeps_zero_padded_roc_year_bundle(self) -> None:
        downloads = parse_downloads(ZERO_PADDED_YEAR_HTML, year_roc=99)

        self.assertEqual(len(downloads), 1)
        self.assertEqual(downloads[0].label, "099年試題及參考答案 考題及參考答案 全部下載")
        self.assertTrue(downloads[0].url.endswith("ASParam=y099"))

    def test_parse_downloads_keeps_sample_only_year_bundle(self) -> None:
        downloads = parse_downloads(SAMPLE_ONLY_YEAR_HTML, year_roc=107)

        self.assertEqual(len(downloads), 1)
        self.assertEqual(downloads[0].label, "107年僅有範例題 範例題 全部下載")
        self.assertTrue(downloads[0].url.endswith("ASParam=y107"))

    def test_parse_order_options(self) -> None:
        self.assertEqual(parse_order_options(ORDER_SELECT_HTML), [("1", "第一次考試"), ("2", "第二次考試")])


class TeacherQualClientTests(unittest.TestCase):
    def setUp(self) -> None:
        # These listing fixtures have no reviewed native container. Archive
        # projection is exercised with complete synthetic containers elsewhere.
        native = patch("app.providers.teacher_qual.archive.native_archive_for", return_value=None)
        native.start()
        self.addCleanup(native.stop)

    def test_category_postbacks_keep_their_cookie_session_and_latest_tokens(self) -> None:
        options = '''<select name="ctl00$ContentPlaceHolder1$exid">
        <option value="10">幼兒園</option><option value="99">全部類科</option></select>'''
        responses = [
            f'<input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="token-{i}">'
            + options for i in range(5)
        ]
        with patch("app.providers.teacher_qual.client.build_opener") as make_opener:
            opener = make_opener.return_value
            opener.open.return_value.__enter__.return_value.read.side_effect = [
                body.encode() for body in responses
            ]
            TeacherQualClient().category_listings(108, "2")
        make_opener.assert_called_once()
        requests = [call.args[0] for call in opener.open.call_args_list]
        self.assertIsNone(requests[0].data)
        forms = [parse_qs(request.data.decode(), keep_blank_values=True) for request in requests[1:]]
        self.assertEqual([form["__VIEWSTATE"][0] for form in forms],
                         ["token-0", "token-1", "token-2", "token-3"])
        self.assertEqual([form["ctl00$ContentPlaceHolder1$schyy"][0] for form in forms], ["108"] * 4)
        self.assertTrue(all(form["ctl00$ContentPlaceHolder1$ddlOrder"] == ["2"] for form in forms[1:]))
        self.assertEqual([form["ctl00$ContentPlaceHolder1$exid"][0] for form in forms[-2:]], ["10", "99"])

    def test_all_category_traversal_preserves_distinct_same_named_selectors(self) -> None:
        options = '''<select name="ctl00$ContentPlaceHolder1$exid">
          <option value="21">身心障礙組</option><option value="23">身心障礙組</option>
          <option value="99">全部類科</option></select>'''
        with patch("app.providers.teacher_qual.client._SelectionSession") as session_type:
            session = session_type.return_value
            session.html = options
            session.select.side_effect = ["first category", "second category", "original archive"]
            rows = TeacherQualClient().category_listings(115)
        self.assertEqual(rows, [("21", "身心障礙組", "first category"),
                                ("23", "身心障礙組", "second category"),
                                ("99", "全部類科", "original archive")])
        self.assertEqual([call.args[-1] for call in session.select.call_args_list], ["21", "23", "99"])

    def test_duplicate_selector_values_stop_category_acquisition(self) -> None:
        with patch("app.providers.teacher_qual.client._SelectionSession") as session_type:
            session_type.return_value.html = '''<select name="ctl00$ContentPlaceHolder1$exid">
            <option value="10">幼兒園</option><option value="10">另一類科</option></select>'''
            with self.assertRaisesRegex(ValueError, "repeats a category value"):
                TeacherQualClient().category_listings(115)

    def test_discovery_urls_preserve_selector_and_order_semantics(self) -> None:
        client = TeacherQualClient()

        self.assertEqual(client.build_discovery_year_url(2026), LISTING_URL)
        self.assertEqual(client.build_discovery_exam_url("teacher-qual-115", 2026), LISTING_URL)
        self.assertEqual(client.build_discovery_exam_url("teacher-qual-108-1", 2019), LISTING_URL)
        self.assertEqual(client.build_discovery_exam_url("teacher-qual-108-2", 2019), LISTING_URL)
        with self.assertRaisesRegex(ValueError, "Unexpected teacher qualification discovery order"):
            client.build_discovery_exam_url("teacher-qual-108", 2019)
        with self.assertRaisesRegex(ValueError, "Unexpected teacher qualification discovery order"):
            client.build_discovery_exam_url("teacher-qual-115-1", 2026)

    def test_fetch_exam_page_preserves_original_and_reads_individual_answers(self) -> None:
        client = TeacherQualClient()
        cell = '''<span id="ctl00_ContentPlaceHolder1_frmGrd_DataGrid_ctl02_frmGrd_sbid03">
        學習者發展與適性輔導<br>考題<a href="ShowPicOut2.aspx?ASParam=q"
        title="下載pdf檔案"></a><br>參考答案<a href="ShowPicOut2.aspx?ASParam=a"
        title="下載pdf檔案"></a></span>'''
        with patch.object(client, "category_listings", return_value=[
            ("10", "幼兒園師資類科", TEACHER_LISTING_HTML + cell),
            ("99", "全部類科", TEACHER_LISTING_HTML),
        ]):
            page = client.fetch_exam_page("teacher-qual-115", 2026)
        self.assertEqual(page.provider_id, "teacher_qual")
        self.assertEqual(page.source_exam_id, "teacher-qual-115")
        self.assertEqual(len(page.papers), 1)
        self.assertEqual(page.papers[0].category_code, "115-10")
        self.assertEqual(set(page.papers[0].files), {"question", "answer"})
        self.assertTrue(page.papers[0].files["answer"].endswith("ASParam=a"))
        self.assertEqual(page.attachments[0].file_type, "source_archive")
        self.assertTrue(page.attachments[0].download_url_source.endswith("ASParam=all"))
        self.assertEqual(page.source_material.kind, "administered")

    def test_sample_collection_has_no_synthetic_exam_or_publication_year(self) -> None:
        material = listing_material(SAMPLE_ONLY_YEAR_HTML, 107)
        self.assertEqual(material.kind, "sample")
        self.assertEqual(material.date.basis, "undated")
        self.assertIsNone(material.date.year_ad)
        with self.assertRaisesRegex(ValueError, "matching material heading"):
            listing_material(TEACHER_LISTING_HTML, 107)

    def test_discover_exams_splits_exam_order_when_source_requires_it(self) -> None:
        client = TeacherQualClient()
        client.discover_available_years = lambda: [2019]  # type: ignore[method-assign]
        client._year_selection_html = lambda year_roc: ORDER_SELECT_HTML  # type: ignore[method-assign]

        exams = client.discover_exams(2019)

        self.assertEqual([exam.code for exam in exams], ["teacher-qual-108-1", "teacher-qual-108-2"])
        self.assertEqual([exam.label for exam in exams], ["108年教師資格考試第一次考試歷屆試題及參考答案", "108年教師資格考試第二次考試歷屆試題及參考答案"])

    def test_fetch_exam_page_uses_exam_order_suffix(self) -> None:
        client = TeacherQualClient()
        cell = '''<span id="ctl00_ContentPlaceHolder1_frmGrd_DataGrid_ctl02_frmGrd_sbid01">
        國語文能力測驗<br>考題<a href="ShowPicOut2.aspx?ASParam=q"
        title="下載pdf檔案"></a></span>'''
        html = TEACHER_LISTING_HTML.replace("115年試題及參考答案", "108年試題及參考答案")
        with patch.object(client, "category_listings", return_value=[
            ("10", "幼兒園", html + cell), ("99", "全部類科", html)
        ]) as listings:
            page = client.fetch_exam_page("teacher-qual-108-2", 2019)
        listings.assert_called_once_with(108, "2")
        self.assertEqual(page.exam_name_raw, "108年教師資格考試第二次考試歷屆試題及參考答案")


if __name__ == "__main__":
    unittest.main()
