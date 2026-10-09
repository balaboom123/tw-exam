import unittest
from unittest.mock import patch

from app.providers.taipower_recruit.client import (
    DISCOVERY_PAGE_SIZE,
    DOWNLOAD_URL,
    TaipowerRecruitClient,
    _quote_url_for_request,
    parse_hiring_page,
    parse_year_tabs,
)


HIRING_PAGE_HTML = """
<html>
<head><title>台電下載專區</title></head>
<body>
<ul>
  <li>
    <p class="title">113年新進僱用人員甄試試題解答</p>
    <div class="drawerBox">
      <ul class="fileDownload">
        <li>
          <span class="name">試題</span>
          <ul class="downloadFiles">
            <li><a download href="/media/5585/113nian_hiring_questions.pdf">下載</a></li>
          </ul>
        </li>
        <li>
          <span class="name">解答</span>
          <ul class="downloadFiles">
            <li><a download href="/media/5586/113nian_hiring_answers.pdf">下載</a></li>
          </ul>
        </li>
      </ul>
    </div>
  </li>
  <li>
    <p class="title">112年新進僱用人員甄試試題解答</p>
    <div class="drawerBox">
      <ul class="fileDownload">
        <li>
          <span class="name">試題</span>
          <ul class="downloadFiles">
            <li><a download href="/media/5223/112nian_hiring_questions.pdf">下載</a></li>
          </ul>
        </li>
        <li>
          <span class="name">解答</span>
          <ul class="downloadFiles">
            <li><a download href="/media/5224/112nian_hiring_answers.pdf">下載</a></li>
          </ul>
        </li>
      </ul>
    </div>
  </li>
  <li>
    <p class="title">107年12月新進僱用人員甄試試題解答</p>
    <div class="drawerBox">
      <ul class="fileDownload">
        <li>
          <span class="name">試題</span>
          <ul class="downloadFiles">
            <li><a download href="/media/3892/107dec_hiring_questions.pdf">下載</a></li>
          </ul>
        </li>
        <li>
          <span class="name">解答</span>
          <ul class="downloadFiles">
            <li><a download href="/media/3893/107dec_hiring_answers.pdf">下載</a></li>
          </ul>
        </li>
      </ul>
    </div>
  </li>
  <li>
    <p class="title">107年5月新進僱用人員甄試試題解答</p>
    <div class="drawerBox">
      <ul class="fileDownload">
        <li>
          <span class="name">試題</span>
          <ul class="downloadFiles">
            <li><a download href="/media/3790/107may_hiring_questions.pdf">下載</a></li>
          </ul>
        </li>
        <li>
          <span class="name">解答</span>
          <ul class="downloadFiles">
            <li><a download href="/media/3791/107may_hiring_answers.pdf">下載</a></li>
          </ul>
        </li>
      </ul>
    </div>
  </li>
  <li>
    <p class="title">106年新進僱用人員甄試試題解答</p>
    <div class="drawerBox">
      <ul class="fileDownload">
        <li>
          <span class="name">試題</span>
          <ul class="downloadFiles">
            <li><a download href="/media/3600/106nian_hiring_questions.pdf">下載</a></li>
          </ul>
        </li>
        <li>
          <span class="name">解答</span>
          <ul class="downloadFiles">
            <li><a download href="/media/3601/106nian_hiring_answers.pdf">下載</a></li>
          </ul>
        </li>
      </ul>
    </div>
  </li>
</ul>
</body>
</html>
"""

HIRING_PAGE_HTML_SINGLE_FILE = """
<html><body>
<ul>
  <li>
    <p class="title">111年新進僱用人員甄試試題解答</p>
    <div class="drawerBox">
      <ul class="fileDownload">
        <li>
          <span class="name">試題暨解答</span>
          <ul class="downloadFiles">
            <li><a download href="/media/4992/111nian_hiring.pdf">下載</a></li>
          </ul>
        </li>
      </ul>
    </div>
  </li>
</ul>
</body></html>
"""

HIRING_PAGE_HTML_NESTED_DIVS = """
<html><body>
<ul>
  <li>
    <p class="title"><span>Inner nested span</span> 114年新進僱用人員甄試試題解答</p>
    <div class="drawerBox">
      <ul class="fileDownload">
        <li>
          <span class="name">試題</span>
          <ul class="downloadFiles">
            <li><a download href="/media/5587/114nian_hiring_questions.pdf">下載</a></li>
          </ul>
        </li>
        <li>
          <span class="name">解答</span>
          <ul class="downloadFiles">
            <li><a download href="/media/5588/114nian_hiring_answers.pdf">下載</a></li>
          </ul>
        </li>
      </ul>
    </div>
  </li>
</ul>
</body></html>
"""

HIRING_PAGE_HTML_MULTI_SUBJECT = """
<html><body>
<ul>
  <li>
    <p class="title">112年度共同科目</p>
    <div class="drawerBox">
      <ul class="fileDownload">
        <li>
          <span class="name">共同科目試題</span>
          <ul class="downloadFiles">
            <li><a download href="/media/1001/common_q.pdf">下載</a></li>
          </ul>
        </li>
        <li>
          <span class="name">共同科目解答</span>
          <ul class="downloadFiles">
            <li><a download href="/media/1002/common_a.pdf">下載</a></li>
          </ul>
        </li>
      </ul>
    </div>
  </li>
  <li>
    <p class="title">112年度企業管理概論</p>
    <div class="drawerBox">
      <ul class="fileDownload">
        <li>
          <span class="name">企業管理概論試題</span>
          <ul class="downloadFiles">
            <li><a download href="/media/1003/biz_q.pdf">下載</a></li>
          </ul>
        </li>
        <li>
          <span class="name">企業管理概論解答</span>
          <ul class="downloadFiles">
            <li><a download href="/media/1004/biz_a.pdf">下載</a></li>
          </ul>
        </li>
      </ul>
    </div>
  </li>
</ul>
</body></html>
"""


EVENT_TABS_HTML = """
<html><body>
<a href="/2289/2544/2554/2557/?Page=1&amp;PageSize=10&amp;q_attribute=4256">113年度</a>
<a href="/2289/2544/2554/2557/?Page=1&amp;PageSize=10&amp;q_attribute=2659">107年12月</a>
<a href="/2289/2544/2554/2557/?Page=1&amp;PageSize=10&amp;q_attribute=1611">107年5月</a>
</body></html>
"""


def _event_page_html(
    year_roc: int,
    month: int | None = None,
    *,
    next_page: bool = False,
) -> str:
    prefix = f"{year_roc}年{month}月" if month is not None else f"{year_roc}年度"
    suffix = f"-{month}" if month is not None else ""
    pagination = (
        f'<a href="?Page=2&amp;PageSize={DISCOVERY_PAGE_SIZE}&amp;q_attribute=4256">2</a>'
        if next_page
        else ""
    )
    return f"""
<html><body>
<ul><li>
  <p class="title">{prefix}共同科目</p>
  <div class="drawerBox"><ul class="fileDownload">
    <li><span class="name">{prefix}新進僱用人員甄試試題_共同科目</span>
      <ul class="downloadFiles"><li><a download href="/media/{year_roc}{suffix}/question.pdf">下載</a></li></ul>
    </li>
    <li><span class="name">{prefix}新進僱用人員甄試答案_共同科目</span>
      <ul class="downloadFiles"><li><a download href="/media/{year_roc}{suffix}/answer.pdf">下載</a></li></ul>
    </li>
  </ul></div>
</li></ul>
{pagination}
</body></html>
"""


def _fake_archive_fetch(url: str) -> str:
    if url == DOWNLOAD_URL:
        return EVENT_TABS_HTML
    event_by_attribute = {
        "4256": (113, None),
        "2659": (107, 12),
        "1611": (107, 5),
    }
    for attribute, (year_roc, month) in event_by_attribute.items():
        if f"q_attribute={attribute}" in url:
            if f"PageSize={DISCOVERY_PAGE_SIZE}" not in url:
                raise AssertionError(f"unbounded event URL: {url}")
            return _event_page_html(year_roc, month)
    raise AssertionError(f"unexpected URL: {url}")


class TaipowerRecruitParserTests(unittest.TestCase):
    def test_quote_url_for_request_percent_encodes_non_ascii_path_and_preserves_query(self) -> None:
        url = "https://www.taipower.com.tw/media/demo/115年度新進僱用人員甄試試題.pdf?mediaDL=true"

        quoted = _quote_url_for_request(url)

        self.assertEqual(
            quoted,
            "https://www.taipower.com.tw/media/demo/115%E5%B9%B4%E5%BA%A6%E6%96%B0%E9%80%B2%E5%83%B1%E7%94%A8%E4%BA%BA%E5%93%A1%E7%94%84%E8%A9%A6%E8%A9%A6%E9%A1%8C.pdf?mediaDL=true",
        )

    def test_parse_hiring_page_extracts_entries(self) -> None:
        entries = parse_hiring_page(HIRING_PAGE_HTML)

        self.assertEqual(len(entries), 5)

    def test_parse_hiring_page_extracts_year_roc(self) -> None:
        entries = parse_hiring_page(HIRING_PAGE_HTML)

        self.assertEqual(entries[0].year_roc, 113)
        self.assertEqual(entries[1].year_roc, 112)
        self.assertEqual(entries[2].year_roc, 107)
        self.assertEqual(entries[3].year_roc, 107)
        self.assertEqual(entries[4].year_roc, 106)

    def test_parse_hiring_page_computes_year_ad(self) -> None:
        entries = parse_hiring_page(HIRING_PAGE_HTML)

        self.assertEqual(entries[0].year_ad, 2024)
        self.assertEqual(entries[1].year_ad, 2023)
        self.assertEqual(entries[2].year_ad, 2018)
        self.assertEqual(entries[3].year_ad, 2018)
        self.assertEqual(entries[4].year_ad, 2017)

    def test_parse_hiring_page_extracts_title(self) -> None:
        entries = parse_hiring_page(HIRING_PAGE_HTML)

        self.assertEqual(entries[0].title, "113年新進僱用人員甄試試題解答")

    def test_parse_hiring_page_extracts_downloads(self) -> None:
        entries = parse_hiring_page(HIRING_PAGE_HTML)

        self.assertEqual(len(entries[0].downloads), 2)
        self.assertEqual(entries[0].downloads[0].label, "試題")
        self.assertEqual(
            entries[0].downloads[0].url,
            "https://www.taipower.com.tw/media/5585/113nian_hiring_questions.pdf",
        )
        self.assertEqual(entries[0].downloads[1].label, "解答")
        self.assertEqual(
            entries[0].downloads[1].url,
            "https://www.taipower.com.tw/media/5586/113nian_hiring_answers.pdf",
        )

    def test_multi_session_year_has_month_in_exam_code(self) -> None:
        entries = parse_hiring_page(HIRING_PAGE_HTML)
        year_107_entries = [e for e in entries if e.year_roc == 107]

        self.assertEqual(len(year_107_entries), 2)
        self.assertIsNotNone(year_107_entries[0].month)
        self.assertIsNotNone(year_107_entries[1].month)

    def test_multi_session_year_detects_month_values(self) -> None:
        entries = parse_hiring_page(HIRING_PAGE_HTML)
        year_107_entries = [e for e in entries if e.year_roc == 107]
        months = {e.month for e in year_107_entries}

        self.assertEqual(months, {5, 12})

    def test_single_session_year_has_no_month(self) -> None:
        entries = parse_hiring_page(HIRING_PAGE_HTML)
        year_113_entry = next(e for e in entries if e.year_roc == 113)

        self.assertIsNone(year_113_entry.month)

    def test_parse_hiring_page_handles_single_file_entry(self) -> None:
        entries = parse_hiring_page(HIRING_PAGE_HTML_SINGLE_FILE)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].year_roc, 111)
        self.assertEqual(len(entries[0].downloads), 1)
        self.assertEqual(entries[0].downloads[0].label, "試題暨解答")

    def test_parse_hiring_page_handles_nested_divs(self) -> None:
        entries = parse_hiring_page(HIRING_PAGE_HTML_NESTED_DIVS)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].year_roc, 114)
        self.assertEqual(entries[0].year_ad, 2025)
        self.assertEqual(len(entries[0].downloads), 2)
        self.assertEqual(entries[0].downloads[0].label, "試題")
        self.assertEqual(entries[0].downloads[1].label, "解答")
        self.assertIn("114年新進僱用人員甄試試題解答", entries[0].title)

    def test_parse_hiring_page_empty_html_returns_empty_list(self) -> None:
        entries = parse_hiring_page("<html><body></body></html>")

        self.assertEqual(entries, [])

    def test_fetch_exam_page_builds_source_exam_page(self) -> None:
        with patch.object(TaipowerRecruitClient, "_iter_entries", return_value=parse_hiring_page(HIRING_PAGE_HTML)):
            client = TaipowerRecruitClient()
            page = client.fetch_exam_page("taipower-recruit-113", 2024)

        self.assertEqual(page.provider_id, "taipower_recruit")
        self.assertEqual(page.source_exam_id, "taipower-recruit-113")
        self.assertEqual(page.year_ad, 2024)
        self.assertEqual(page.year_roc, 113)
        self.assertEqual(page.exam_name_raw, "113年度台電新進僱用人員甄試")
        self.assertEqual(len(page.papers), 2)
        self.assertEqual({paper.category_raw for paper in page.papers}, {"台電新進僱用人員甄試"})

    def test_fetch_exam_page_multi_session_year(self) -> None:
        with patch.object(TaipowerRecruitClient, "_iter_entries", return_value=parse_hiring_page(HIRING_PAGE_HTML)):
            client = TaipowerRecruitClient()
            page = client.fetch_exam_page("taipower-recruit-107-12", 2018)

        self.assertEqual(page.source_exam_id, "taipower-recruit-107-12")
        self.assertEqual(page.year_roc, 107)

    def test_fetch_exam_page_assigns_question_and_answer_file_types(self) -> None:
        with patch.object(TaipowerRecruitClient, "_iter_entries", return_value=parse_hiring_page(HIRING_PAGE_HTML)):
            client = TaipowerRecruitClient()
            page = client.fetch_exam_page("taipower-recruit-113", 2024)

        file_types = {file_type for paper in page.papers for file_type in paper.files}
        self.assertIn("question", file_types)
        self.assertIn("answer", file_types)

    def test_discover_available_years_returns_sorted_descending(self) -> None:
        with patch.object(TaipowerRecruitClient, "_iter_entries", return_value=parse_hiring_page(HIRING_PAGE_HTML)):
            client = TaipowerRecruitClient()
            years = client.discover_available_years()

        self.assertEqual(years, [2024, 2023, 2018, 2017])

    def test_discover_exams_returns_exam_options_for_single_session_year(self) -> None:
        with patch.object(TaipowerRecruitClient, "_iter_entries", return_value=parse_hiring_page(HIRING_PAGE_HTML)):
            client = TaipowerRecruitClient()
            exams = client.discover_exams(2024)

        self.assertEqual(len(exams), 1)
        self.assertEqual(exams[0].code, "taipower-recruit-113")
        self.assertEqual(exams[0].year_ad, 2024)
        self.assertEqual(exams[0].year_roc, 113)
        self.assertEqual(exams[0].label, "113年度台電新進僱用人員甄試")

    def test_discover_exams_returns_two_options_for_multi_session_year(self) -> None:
        with patch.object(TaipowerRecruitClient, "_iter_entries", return_value=parse_hiring_page(HIRING_PAGE_HTML)):
            client = TaipowerRecruitClient()
            exams = client.discover_exams(2018)

        self.assertEqual(len(exams), 2)
        codes = {e.code for e in exams}
        self.assertIn("taipower-recruit-107-12", codes)
        self.assertIn("taipower-recruit-107-5", codes)

    def test_discover_exams_deduplicates_multi_subject_entries(self) -> None:
        with patch.object(TaipowerRecruitClient, "_iter_entries", return_value=parse_hiring_page(HIRING_PAGE_HTML_MULTI_SUBJECT)):
            client = TaipowerRecruitClient()
            exams = client.discover_exams(2023)

        self.assertEqual(len(exams), 1)
        self.assertEqual(exams[0].code, "taipower-recruit-112")

    def test_fetch_exam_page_aggregates_multi_subject_entries(self) -> None:
        with patch.object(TaipowerRecruitClient, "_iter_entries", return_value=parse_hiring_page(HIRING_PAGE_HTML_MULTI_SUBJECT)):
            client = TaipowerRecruitClient()
            page = client.fetch_exam_page("taipower-recruit-112", 2023)

        self.assertEqual(len(page.papers), 4)
        urls = {url for paper in page.papers for url in paper.files.values()}
        self.assertEqual(len(urls), 4)
        codes = [paper.subject_code for paper in page.papers]
        self.assertEqual(codes, ["hiring-01", "hiring-02", "hiring-03", "hiring-04"])
        file_types = [ft for paper in page.papers for ft in paper.files]
        self.assertEqual(file_types, ["question", "answer", "question", "answer"])


    def test_parse_event_tabs_preserves_two_sessions_in_one_year(self) -> None:
        tabs = parse_year_tabs(EVENT_TABS_HTML)

        self.assertEqual(
            tabs,
            [
                (113, None, "/2289/2544/2554/2557/?Page=1&PageSize=10&q_attribute=4256"),
                (107, 12, "/2289/2544/2554/2557/?Page=1&PageSize=10&q_attribute=2659"),
                (107, 5, "/2289/2544/2554/2557/?Page=1&PageSize=10&q_attribute=1611"),
            ],
        )

    def test_archive_discovery_fetches_every_event_at_bounded_page_size(self) -> None:
        with patch.object(
            TaipowerRecruitClient,
            "_fetch_text",
            side_effect=_fake_archive_fetch,
        ) as fetch:
            client = TaipowerRecruitClient()
            years = client.discover_available_years()
            year_url = client.build_discovery_year_url(2018)
            event_url = client.build_discovery_exam_url(
                "taipower-recruit-107-12",
                2018,
            )
            exams = client.discover_exams(2018)

        self.assertEqual(years, [2024, 2018])
        self.assertEqual(year_url, DOWNLOAD_URL)
        self.assertEqual(
            event_url,
            "https://www.taipower.com.tw/2289/2544/2554/2557/"
            "?Page=1&PageSize=60&q_attribute=2659",
        )
        self.assertEqual(
            [exam.code for exam in exams],
            ["taipower-recruit-107-12", "taipower-recruit-107-5"],
        )
        self.assertEqual(fetch.call_count, 4)

    def test_archive_discovery_rejects_missing_event_tabs(self) -> None:
        with patch.object(TaipowerRecruitClient, "_fetch_text", return_value="<html></html>"):
            with self.assertRaisesRegex(ValueError, "no official event tabs"):
                TaipowerRecruitClient().discover_available_years()

    def test_archive_discovery_rejects_residual_pagination(self) -> None:
        one_tab = EVENT_TABS_HTML.replace(
            '<a href="/2289/2544/2554/2557/?Page=1&amp;PageSize=10&amp;q_attribute=2659">107年12月</a>',
            "",
        ).replace(
            '<a href="/2289/2544/2554/2557/?Page=1&amp;PageSize=10&amp;q_attribute=1611">107年5月</a>',
            "",
        )

        def fake_fetch(url: str) -> str:
            return one_tab if url == DOWNLOAD_URL else _event_page_html(113, next_page=True)

        with patch.object(TaipowerRecruitClient, "_fetch_text", side_effect=fake_fetch):
            with self.assertRaisesRegex(ValueError, "still paginates"):
                TaipowerRecruitClient().discover_available_years()

    def test_archive_discovery_rejects_duplicate_asset_urls(self) -> None:
        one_tab = EVENT_TABS_HTML.replace(
            '<a href="/2289/2544/2554/2557/?Page=1&amp;PageSize=10&amp;q_attribute=2659">107年12月</a>',
            "",
        ).replace(
            '<a href="/2289/2544/2554/2557/?Page=1&amp;PageSize=10&amp;q_attribute=1611">107年5月</a>',
            "",
        )

        def fake_fetch(url: str) -> str:
            if url == DOWNLOAD_URL:
                return one_tab
            return _event_page_html(113).replace("/answer.pdf", "/question.pdf")

        with patch.object(TaipowerRecruitClient, "_fetch_text", side_effect=fake_fetch):
            with self.assertRaisesRegex(ValueError, "repeats download URL"):
                TaipowerRecruitClient().discover_available_years()

    def test_archive_discovery_rejects_cross_event_entries(self) -> None:
        one_tab = EVENT_TABS_HTML.replace(
            '<a href="/2289/2544/2554/2557/?Page=1&amp;PageSize=10&amp;q_attribute=2659">107年12月</a>',
            "",
        ).replace(
            '<a href="/2289/2544/2554/2557/?Page=1&amp;PageSize=10&amp;q_attribute=1611">107年5月</a>',
            "",
        )

        def fake_fetch(url: str) -> str:
            return one_tab if url == DOWNLOAD_URL else _event_page_html(112)

        with patch.object(TaipowerRecruitClient, "_fetch_text", side_effect=fake_fetch):
            with self.assertRaisesRegex(ValueError, "cross-event entries"):
                TaipowerRecruitClient().discover_available_years()

    def test_archive_discovery_rejects_duplicate_event_tabs(self) -> None:
        duplicate_tabs = EVENT_TABS_HTML.replace(
            "</body>",
            '<a href="/2289/2544/2554/2557/?Page=1&amp;PageSize=10&amp;q_attribute=9999">113年度</a></body>',
        )
        with patch.object(
            TaipowerRecruitClient,
            "_fetch_text",
            return_value=duplicate_tabs,
        ):
            with self.assertRaisesRegex(ValueError, "duplicate event tabs"):
                TaipowerRecruitClient().discover_available_years()

    def test_archive_discovery_rejects_wrong_listing_route(self) -> None:
        wrong_route = EVENT_TABS_HTML.replace("/2557/", "/2556/", 1)
        with patch.object(
            TaipowerRecruitClient,
            "_fetch_text",
            return_value=wrong_route,
        ):
            with self.assertRaisesRegex(ValueError, "Unexpected Taipower archive"):
                TaipowerRecruitClient().discover_available_years()

    def test_discovery_urls_reject_unknown_year_and_exam(self) -> None:
        with patch.object(
            TaipowerRecruitClient,
            "_fetch_text",
            side_effect=_fake_archive_fetch,
        ):
            client = TaipowerRecruitClient()
            with self.assertRaisesRegex(ValueError, "Unknown Taipower recruitment discovery year"):
                client.build_discovery_year_url(2025)
            with self.assertRaisesRegex(ValueError, "Unknown Taipower recruitment discovery exam"):
                client.build_discovery_exam_url("taipower-recruit-107", 2018)

    def test_registry_returns_taipower_recruit_provider(self) -> None:
        from app.providers.registry import get_provider
        from app.providers.base import SourceProvider

        provider = get_provider("taipower_recruit")

        self.assertIsInstance(provider, SourceProvider)
        self.assertEqual(provider.provider_id, "taipower_recruit")

    def test_default_sync_refreshes_a_valid_old_pdf_and_preserves_its_bytes(self) -> None:
        import hashlib
        import tempfile
        from pathlib import Path

        from app.models import ParsedPaper, SourceExamPage
        from app.providers.base import DownloadedFile
        from app.providers.taipower_recruit.provider import TaipowerRecruitProvider
        from app.source_revisions import revision_blob_key
        from app.storage import MirrorStore
        from app.sync import sync_exam_pages

        url = "https://www.taipower.com.tw/media/common.pdf?mediaDL=true"
        page = SourceExamPage(
            source_exam_id="taipower-recruit-113",
            year_ad=2024,
            year_roc=113,
            exam_name_raw="113年度台電新進僱用人員甄試",
            attachments=[],
            papers=[
                ParsedPaper(
                    category_raw="台電新進僱用人員甄試",
                    category_code="113",
                    subject_code="hiring-01",
                    subject_name_raw="國文及英文",
                    files={"question": url},
                )
            ],
            provider_id="taipower_recruit",
        )
        old = b"%PDF-1.7 retained MOEA common paper"
        fresh = b"%PDF-1.7 current Taipower hiring paper"
        key = "providers/taipower_recruit/113/taipower-recruit-113/113/hiring-01/question.pdf"
        with tempfile.TemporaryDirectory() as temporary:
            store = MirrorStore(Path(temporary))
            store.write_bytes(key, old)
            with (
                patch.object(TaipowerRecruitClient, "fetch_exam_page", return_value=page),
                patch.object(
                    TaipowerRecruitClient,
                    "download_file",
                    return_value=DownloadedFile(
                        data=fresh, content_type="application/pdf", file_name="common.pdf"
                    ),
                ) as download,
            ):
                _, catalog, failures = sync_exam_pages(
                    client=TaipowerRecruitProvider(),
                    exam_codes=[("taipower-recruit-113", 2024)],
                    mirror_store=store,
                    alias_rules=[],
                    mirror_base_url="",
                    download_attachments=False,
                )
            self.assertEqual(failures, [])
            download.assert_called_once_with(url)
            self.assertEqual((store.root / key).read_bytes(), fresh)
            self.assertEqual(catalog.papers[0].checksum, hashlib.sha256(fresh).hexdigest())
            blob = revision_blob_key("taipower_recruit", hashlib.sha256(old).hexdigest(), ".pdf")
            self.assertEqual((store.root / blob).read_bytes(), old)



if __name__ == "__main__":
    unittest.main()
