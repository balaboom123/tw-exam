import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

from app.providers.wdasec_skill.client import (
    DetailRow,
    ListingRow,
    PAGE_URL,
    WdasecSkillClient,
    parse_detail_rows,
    parse_hidden_fields,
    parse_listing_rows,
    parse_page_count,
)


class _FakeResponse:
    headers = {}
    status = 200

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return INITIAL_PAGE_HTML.encode("utf-8")


class _CapturingOpener:
    def __init__(self) -> None:
        self.data = b""

    def open(self, request, timeout: int = 60) -> _FakeResponse:  # noqa: ANN001
        self.data = request.data
        return _FakeResponse()


INITIAL_PAGE_HTML = """\
<html><body>
<form method="post" action="./PastQuestions.aspx" id="form1">
<div class="aspNetHidden">
<input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="FAKE_VS_1" />
</div>
<div class="aspNetHidden">
<input type="hidden" name="__VIEWSTATEGENERATOR" id="__VIEWSTATEGENERATOR" value="43FD7A7D" />
<input type="hidden" name="__EVENTVALIDATION" id="__EVENTVALIDATION" value="FAKE_EV_1" />
</div>
<div id="Panel1">
<input type="submit" name="btnSelectA" value="全國技能檢定各梯次試題及答案" id="btnSelectA" class="button2" />
<input type="submit" name="btnSelectB" value="乙級專案檢定試題及答案" id="btnSelectB" class="button2" />
<input type="submit" name="btnSelectC" value="在校生丙級專案檢定" id="btnSelectC" class="button2" />
</div>
<input type="hidden" name="hdfType" id="hdfType" />
<input type="hidden" name="Hiddyyyy" id="Hiddyyyy" />
<input type="hidden" name="hiddkey" id="hiddkey" />
</form>
</body></html>"""


CATEGORY_LISTING_HTML = """\
<html><body>
<form method="post" action="./PastQuestions.aspx" id="form1">
<div class="aspNetHidden">
<input type="hidden" name="__EVENTTARGET" id="__EVENTTARGET" value="" />
<input type="hidden" name="__EVENTARGUMENT" id="__EVENTARGUMENT" value="" />
<input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="FAKE_VS_2" />
</div>
<div class="aspNetHidden">
<input type="hidden" name="__VIEWSTATEGENERATOR" id="__VIEWSTATEGENERATOR" value="43FD7A7D" />
<input type="hidden" name="__EVENTVALIDATION" id="__EVENTVALIDATION" value="FAKE_EV_2" />
</div>
<div id="Panel2">
<input type="submit" name="btnBackSelect" value="回上頁" id="btnBackSelect" class="button" />
<div>
<table cellspacing="0" cellpadding="0" rules="all" id="gvData" style="width:100%;">
<tr>
<th class="gvtitle" align="center" scope="col">年度</th><th class="gvtitle" align="center" scope="col">主題</th><th class="gvtitle" align="center" scope="col">發布日期</th><th class="gvtitle" align="center" scope="col">點閱率</th><th class="gvtitle" scope="col" style="border-width:0px;width:0px;">&nbsp;</th><th class="gvtitle" align="center" scope="col">&nbsp;</th>
</tr><tr>
<td class="gvdataset" align="center">115</td><td class="gvdataset" align="left">115年度全國技術士技能檢定第1梯次學科試題暨答案</td><td class="gvdataset" align="center">115.03.16</td><td class="gvdataset" align="center">10621</td><td style="border-width:0px;width:0px;">
    <input type="hidden" name="gvData$ctl02$hdfPLAID" id="gvData_hdfPLAID_0" value="202603160001" />
</td><td class="gvdataset" style="width:30px;"><input type="button" value="瀏覽" onclick="javascript:__doPostBack(&#39;gvData&#39;,&#39;order$0&#39;)" class="button" /></td>
</tr><tr>
<td class="gvdataset" align="center">114</td><td class="gvdataset" align="left">114年度全國技術士技能檢定第3梯次學科試題暨答案</td><td class="gvdataset" align="center">114.11.03</td><td class="gvdataset" align="center">15655</td><td style="border-width:0px;width:0px;">
    <input type="hidden" name="gvData$ctl03$hdfPLAID" id="gvData_hdfPLAID_1" value="202511030001" />
</td><td class="gvdataset" style="width:30px;"><input type="button" value="瀏覽" onclick="javascript:__doPostBack(&#39;gvData&#39;,&#39;order$1&#39;)" class="button" /></td>
</tr><tr>
<td class="gvdataset" align="center">114</td><td class="gvdataset" align="left">114年度全國技術士技能檢定第2梯次學科試題暨答案</td><td class="gvdataset" align="center">114.07.07</td><td class="gvdataset" align="center">13955</td><td style="border-width:0px;width:0px;">
    <input type="hidden" name="gvData$ctl04$hdfPLAID" id="gvData_hdfPLAID_2" value="202507070001" />
</td><td class="gvdataset" style="width:30px;"><input type="button" value="瀏覽" onclick="javascript:__doPostBack(&#39;gvData&#39;,&#39;order$2&#39;)" class="button" /></td>
</tr><tr class="pgr">
<td colspan="6"><table>
<tr>
<td><span>1</span></td><td><a href="javascript:__doPostBack(&#39;gvData&#39;,&#39;Page$2&#39;)">2</a></td><td><a href="javascript:__doPostBack(&#39;gvData&#39;,&#39;Page$3&#39;)">3</a></td>
</tr>
</table></td>
</tr>
</table>
</div>
</div>
<input type="hidden" name="hdfType" id="hdfType" value="201111010001" />
<input type="hidden" name="Hiddyyyy" id="Hiddyyyy" />
<input type="hidden" name="hiddkey" id="hiddkey" />
</form>
</body></html>"""


DETAIL_PAGE_HTML = """\
<html><body>
<form method="post" action="./PastQuestions.aspx?yserno=202603160001" id="form1">
<div class="aspNetHidden">
<input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="FAKE_VS_3" />
</div>
<div class="aspNetHidden">
<input type="hidden" name="__VIEWSTATEGENERATOR" id="__VIEWSTATEGENERATOR" value="43FD7A7D" />
<input type="hidden" name="__EVENTVALIDATION" id="__EVENTVALIDATION" value="FAKE_EV_3" />
</div>
<div id="Panel3">
<div>
<table cellspacing="0" cellpadding="0" rules="all" id="gvFile" style="width:100%;">
<tr>
<th class="gvtitle" scope="col">編號</th><th class="gvtitle" scope="col">職類項目</th><th class="gvtitle" scope="col">檢定日期</th><th class="gvtitle" scope="col">級別</th><th class="gvtitle" scope="col">學科</th><th class="gvtitle" scope="col">術科</th>
</tr><tr>
<td class="gvdataset vGvFontSize" align="center">00100</td><td class="gvdataset vGvFontSize" align="center">冷凍空調裝修</td><td class="gvdataset vGvFontSize" align="center">115.03.15</td><td class="gvdataset vGvFontSize" align="center">甲級</td><td class="gvdataset vGvFontSize" align="center"><a href='../owInform/DLowFileQ/202603160001/15/001001-1.pdf?18' target='_blank'><img src='/ExamNet/img/download.png' width='30px'></a></td><td class="gvdataset vGvFontSize" align="center">&nbsp;</td>
</tr><tr>
<td class="gvdataset vGvFontSize" align="center"></td><td class="gvdataset vGvFontSize" align="center"></td><td class="gvdataset vGvFontSize" align="center">115.03.15</td><td class="gvdataset vGvFontSize" align="center">乙級</td><td class="gvdataset vGvFontSize" align="center"><a href='../owInform/DLowFileQ/202603160001/15/001002-1.pdf?18' target='_blank'><img src='/ExamNet/img/download.png' width='30px'></a></td><td class="gvdataset vGvFontSize" align="center">&nbsp;</td>
</tr><tr>
<td class="gvdataset vGvFontSize" align="center"></td><td class="gvdataset vGvFontSize" align="center"></td><td class="gvdataset vGvFontSize" align="center">115.03.15</td><td class="gvdataset vGvFontSize" align="center">丙級</td><td class="gvdataset vGvFontSize" align="center"><a href='../owInform/DLowFileQ/202603160001/15/001003-1.pdf?18' target='_blank'><img src='/ExamNet/img/download.png' width='30px'></a></td><td class="gvdataset vGvFontSize" align="center"><a href='../owInform/DLowFileQ/202603160001/0316094745B2.pdf?18' target='_blank'><img src='/ExamNet/img/download.png' width='30px'></a></td>
</tr><tr style="border-top: 1px dashed #666666;">
<td class="gvdataset vGvFontSize" align="center">00700</td><td class="gvdataset vGvFontSize" align="center">室內配線─屋內線路裝修</td><td class="gvdataset vGvFontSize" align="center">115.03.15</td><td class="gvdataset vGvFontSize" align="center">甲級</td><td class="gvdataset vGvFontSize" align="center"><a href='../owInform/DLowFileQ/202603160001/15/007001-1.pdf?18' target='_blank'><img src='/ExamNet/img/download.png' width='30px'></a></td><td class="gvdataset vGvFontSize" align="center">&nbsp;</td>
</tr><tr>
<td class="gvdataset vGvFontSize" align="center"></td><td class="gvdataset vGvFontSize" align="center"></td><td class="gvdataset vGvFontSize" align="center">115.03.15</td><td class="gvdataset vGvFontSize" align="center">乙級</td><td class="gvdataset vGvFontSize" align="center"><a href='../owInform/DLowFileQ/202603160001/15/007002-1.pdf?18' target='_blank'><img src='/ExamNet/img/download.png' width='30px'></a></td><td class="gvdataset vGvFontSize" align="center">&nbsp;</td>
</tr>
</table>
</div>
</div>
<input type="hidden" name="hdfType" id="hdfType" value="201111010001" />
</form>
</body></html>"""


class HiddenFieldParserTests(unittest.TestCase):
    def test_parse_initial_page_fields(self) -> None:
        fields = parse_hidden_fields(INITIAL_PAGE_HTML)

        self.assertEqual(fields["__VIEWSTATE"], "FAKE_VS_1")
        self.assertEqual(fields["__VIEWSTATEGENERATOR"], "43FD7A7D")
        self.assertEqual(fields["__EVENTVALIDATION"], "FAKE_EV_1")

    def test_parse_updates_viewstate_after_postback(self) -> None:
        fields = parse_hidden_fields(CATEGORY_LISTING_HTML)

        self.assertEqual(fields["__VIEWSTATE"], "FAKE_VS_2")
        self.assertEqual(fields["hdfType"], "201111010001")

    def test_parse_keeps_listing_row_hidden_fields(self) -> None:
        fields = parse_hidden_fields(CATEGORY_LISTING_HTML)

        self.assertEqual(fields["gvData$ctl02$hdfPLAID"], "202603160001")


class WdasecSkillClientPostTests(unittest.TestCase):
    def test_post_replays_listing_row_hidden_fields(self) -> None:
        opener = _CapturingOpener()
        client = WdasecSkillClient()
        client._opener = opener
        client._hidden_fields = parse_hidden_fields(CATEGORY_LISTING_HTML)

        client._post({"__EVENTTARGET": "gvData", "__EVENTARGUMENT": "order$0"})

        payload = parse_qs(opener.data.decode("utf-8"))
        self.assertEqual(payload["gvData$ctl02$hdfPLAID"], ["202603160001"])


class ListingParserTests(unittest.TestCase):
    def test_parse_listing_rows_extracts_sessions(self) -> None:
        rows = parse_listing_rows(CATEGORY_LISTING_HTML)

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0].year_roc, 115)
        self.assertEqual(rows[0].plaid, "202603160001")
        self.assertIn("第1梯次", rows[0].title)
        self.assertEqual(rows[0].row_index, 0)

    def test_parse_listing_rows_extracts_multiple_years(self) -> None:
        rows = parse_listing_rows(CATEGORY_LISTING_HTML)

        self.assertEqual(rows[1].year_roc, 114)
        self.assertEqual(rows[1].plaid, "202511030001")
        self.assertIn("第3梯次", rows[1].title)

    def test_parse_listing_preserves_row_indices(self) -> None:
        rows = parse_listing_rows(CATEGORY_LISTING_HTML)

        self.assertEqual([r.row_index for r in rows], [0, 1, 2])


class PaginationParserTests(unittest.TestCase):
    def test_parse_page_count_from_paginated_listing(self) -> None:
        count = parse_page_count(CATEGORY_LISTING_HTML)

        self.assertEqual(count, 3)

    def test_parse_page_count_defaults_to_one(self) -> None:
        count = parse_page_count(INITIAL_PAGE_HTML)

        self.assertEqual(count, 1)


class DetailParserTests(unittest.TestCase):
    def test_parse_detail_rows_extracts_trades(self) -> None:
        rows = parse_detail_rows(DETAIL_PAGE_HTML)

        self.assertEqual(len(rows), 5)

    def test_parse_detail_first_row(self) -> None:
        rows = parse_detail_rows(DETAIL_PAGE_HTML)

        self.assertEqual(rows[0].trade_code, "00100")
        self.assertEqual(rows[0].trade_name, "冷凍空調裝修")
        self.assertEqual(rows[0].level, "甲級")
        self.assertIn("001001-1.pdf", rows[0].question_url)
        self.assertEqual(rows[0].practical_url, "")

    def test_parse_detail_inherits_trade_code_from_previous_row(self) -> None:
        rows = parse_detail_rows(DETAIL_PAGE_HTML)

        self.assertEqual(rows[1].trade_code, "00100")
        self.assertEqual(rows[1].trade_name, "冷凍空調裝修")
        self.assertEqual(rows[1].level, "乙級")

    def test_parse_detail_row_with_both_links(self) -> None:
        rows = parse_detail_rows(DETAIL_PAGE_HTML)

        row_c = rows[2]
        self.assertEqual(row_c.level, "丙級")
        self.assertIn("001003-1.pdf", row_c.question_url)
        self.assertIn("0316094745B2.pdf", row_c.practical_url)

    def test_parse_detail_new_trade_group(self) -> None:
        rows = parse_detail_rows(DETAIL_PAGE_HTML)

        self.assertEqual(rows[3].trade_code, "00700")
        self.assertEqual(rows[3].trade_name, "室內配線─屋內線路裝修")
        self.assertEqual(rows[3].level, "甲級")

    def test_parse_detail_second_trade_inherits_code(self) -> None:
        rows = parse_detail_rows(DETAIL_PAGE_HTML)

        self.assertEqual(rows[4].trade_code, "00700")
        self.assertEqual(rows[4].level, "乙級")


class WdasecSkillClientDiscoveryTests(unittest.TestCase):
    def test_listing_discovery_is_cached_within_one_client(self) -> None:
        client = WdasecSkillClient()
        with (
            patch.object(client, "fetch_category_listing", return_value=CATEGORY_LISTING_HTML) as fetch,
            patch("app.providers.wdasec_skill.client.parse_page_count", return_value=1),
        ):
            self.assertEqual(len(client.discover_all_listing_rows()), 3)
            self.assertEqual(len(client.discover_all_listing_rows()), 3)

        fetch.assert_called_once_with()

    def test_discovery_urls_use_main_listing_and_stable_detail_route(self) -> None:
        client = WdasecSkillClient()
        client._listing_rows_cache = (
            ListingRow(
                year_roc=115,
                title="115年度全國技術士技能檢定第1梯次學科試題暨答案",
                plaid="202603160001",
                row_index=0,
            ),
        )

        self.assertEqual(client.build_discovery_year_url(2026), PAGE_URL)
        self.assertEqual(
            client.build_discovery_exam_url("202603160001", 2026),
            f"{PAGE_URL}?yserno=202603160001",
        )

    def test_fetch_exam_page_uses_direct_detail_route(self) -> None:
        client = WdasecSkillClient()
        client._listing_rows_cache = (
            ListingRow(
                year_roc=115,
                title="115年度全國技術士技能檢定第1梯次學科試題暨答案",
                plaid="202603160001",
                row_index=0,
            ),
        )
        with patch.object(client, "_get", return_value=DETAIL_PAGE_HTML) as fetch:
            page = client.fetch_exam_page("202603160001", 2026)

        fetch.assert_called_once_with(f"{PAGE_URL}?yserno=202603160001")
        self.assertEqual(page.source_exam_id, "202603160001")
        self.assertEqual(page.year_ad, 2026)
        self.assertEqual(len(page.papers), 6)


class WdasecSkillProviderTests(unittest.TestCase):
    def test_provider_id(self) -> None:
        from app.providers.wdasec_skill.provider import WdasecSkillProvider

        provider = WdasecSkillProvider()
        self.assertEqual(provider.provider_id, "wdasec_skill")


if __name__ == "__main__":
    unittest.main()


def test_hidden_fields_and_listing_inputs_handle_valueless_attributes():
    assert parse_hidden_fields('<input type="hidden" name="token" value>') == {"token": ""}
    assert parse_hidden_fields('<input type="hidden" name value="ignored">') == {}
    assert parse_listing_rows('<tr><td><input name value></td></tr>') == []
