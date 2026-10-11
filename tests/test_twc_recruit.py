"""Tests for the twc_recruit provider.

Taiwan Water Corporation (台灣自來水公司) publishes evaluation-position exam papers on:
  https://www.water.gov.tw/ch/Subject/Detail/59619?nodeId=715

The page renders download links for per-year ZIP bundles inside repeating
<div class="檔案下載"> elements. Each div contains two anchors:
  1. The ZIP download link: /ch/ServerFile/Get/[UUID]?nodeId=715
  2. A SHA256 verification link: /ch/ServerFile/ShowFileSHA256/[UUID]

Fixtures below replicate the observed HTML structure from the live site.
Archive spans ROC 103–114 (2014–2025), with year gaps (109, 113) which are
treated as normal (not errors).
"""
import re
import unittest
from unittest.mock import patch

from app.providers.twc_recruit.client import (
    DOWNLOAD_PAGE_URL,
    TwcRecruitClient,
    TwcRecruitEntry,
    parse_employment_detail,
)


# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------

# Representative subset of the real page structure — wraps download divs in the
# government CMS page layout with a subject-detail content area.
DETAIL_PAGE_HTML = """
<!DOCTYPE html>
<html>
<head><title>台灣自來水公司-評價職位人員甄試考古題</title></head>
<body>
<div id="wrapper">
  <div class="content-area">
    <div class="subject-detail">
      <h3>台灣自來水公司評價職位人員甄試考古題</h3>
      <div class="檔案下載">
        <a href="/ch/ServerFile/Get/f21b4843-4257-4f5b-ac85-f7463e03d250?nodeId=715"
           title="114年試題(解答).zip">114年試題(解答).zip(4.8M)</a>
        <a href="/ch/ServerFile/ShowFileSHA256/f21b4843-4257-4f5b-ac85-f7463e03d250">
          (SHA256驗證)
        </a>
      </div>
      <div class="檔案下載">
        <a href="/ch/ServerFile/Get/df60fb7a-cf5f-4045-b07e-84110d74a288?nodeId=715"
           title="112年試題(解答).zip">112年試題(解答).zip(5.7M)</a>
        <a href="/ch/ServerFile/ShowFileSHA256/df60fb7a-cf5f-4045-b07e-84110d74a288">
          (SHA256驗證)
        </a>
      </div>
      <div class="檔案下載">
        <a href="/ch/ServerFile/Get/6e89f5a8-74c2-4ad7-b421-bd0b18da5480?nodeId=715"
           title="111年試題(解答).zip">111年試題(解答).zip(11.8M)</a>
        <a href="/ch/ServerFile/ShowFileSHA256/6e89f5a8-74c2-4ad7-b421-bd0b18da5480">
          (SHA256驗證)
        </a>
      </div>
      <div class="檔案下載">
        <a href="/ch/ServerFile/Get/40fe1b28-e900-4a69-8fe1-4ea8471b6fb6?nodeId=715"
           title="110年試題(解答).zip">110年試題(解答).zip(8.2M)</a>
        <a href="/ch/ServerFile/ShowFileSHA256/40fe1b28-e900-4a69-8fe1-4ea8471b6fb6">
          (SHA256驗證)
        </a>
      </div>
    </div>
  </div>
</div>
</body>
</html>
"""

# Full archive fixture including gap years (109, 113 absent) to test gap handling
FULL_ARCHIVE_HTML = """
<!DOCTYPE html>
<html>
<head><title>台灣自來水公司-評價職位人員甄試考古題</title></head>
<body>
<div class="檔案下載">
  <a href="/ch/ServerFile/Get/f21b4843-4257-4f5b-ac85-f7463e03d250?nodeId=715"
     title="114年試題(解答).zip">114年試題(解答).zip(4.8M)</a>
  <a href="/ch/ServerFile/ShowFileSHA256/f21b4843-4257-4f5b-ac85-f7463e03d250">(SHA256驗證)</a>
</div>
<div class="檔案下載">
  <a href="/ch/ServerFile/Get/df60fb7a-cf5f-4045-b07e-84110d74a288?nodeId=715"
     title="112年試題(解答).zip">112年試題(解答).zip(5.7M)</a>
  <a href="/ch/ServerFile/ShowFileSHA256/df60fb7a-cf5f-4045-b07e-84110d74a288">(SHA256驗證)</a>
</div>
<div class="檔案下載">
  <a href="/ch/ServerFile/Get/6e89f5a8-74c2-4ad7-b421-bd0b18da5480?nodeId=715"
     title="111年試題(解答).zip">111年試題(解答).zip(11.8M)</a>
  <a href="/ch/ServerFile/ShowFileSHA256/6e89f5a8-74c2-4ad7-b421-bd0b18da5480">(SHA256驗證)</a>
</div>
<div class="檔案下載">
  <a href="/ch/ServerFile/Get/40fe1b28-e900-4a69-8fe1-4ea8471b6fb6?nodeId=715"
     title="110年試題(解答).zip">110年試題(解答).zip(8.2M)</a>
  <a href="/ch/ServerFile/ShowFileSHA256/40fe1b28-e900-4a69-8fe1-4ea8471b6fb6">(SHA256驗證)</a>
</div>
<div class="檔案下載">
  <a href="/ch/ServerFile/Get/f04a1355-016d-4f0f-aea2-1bcbbb736e13?nodeId=715"
     title="108年試題(解答).zip">108年試題(解答).zip(7.4M)</a>
  <a href="/ch/ServerFile/ShowFileSHA256/f04a1355-016d-4f0f-aea2-1bcbbb736e13">(SHA256驗證)</a>
</div>
<div class="檔案下載">
  <a href="/ch/ServerFile/Get/fc134ba2-f89b-478a-a003-d77c6be12771?nodeId=715"
     title="107年試題(解答).zip">107年試題(解答).zip(6.9M)</a>
  <a href="/ch/ServerFile/ShowFileSHA256/fc134ba2-f89b-478a-a003-d77c6be12771">(SHA256驗證)</a>
</div>
<div class="檔案下載">
  <a href="/ch/ServerFile/Get/69dedaed-5aae-4bc3-ab82-6c7e39d971ed?nodeId=715"
     title="106年試題(解答).zip">106年試題(解答).zip(5.1M)</a>
  <a href="/ch/ServerFile/ShowFileSHA256/69dedaed-5aae-4bc3-ab82-6c7e39d971ed">(SHA256驗證)</a>
</div>
<div class="檔案下載">
  <a href="/ch/ServerFile/Get/977326a1-ebeb-4549-a31a-4a7c0f93c12f?nodeId=715"
     title="105年試題(解答).zip">105年試題(解答).zip(4.3M)</a>
  <a href="/ch/ServerFile/ShowFileSHA256/977326a1-ebeb-4549-a31a-4a7c0f93c12f">(SHA256驗證)</a>
</div>
<div class="檔案下載">
  <a href="/ch/ServerFile/Get/3dc49939-b596-457f-a590-072036d2d161?nodeId=715"
     title="104年試題(解答).zip">104年試題(解答).zip(3.8M)</a>
  <a href="/ch/ServerFile/ShowFileSHA256/3dc49939-b596-457f-a590-072036d2d161">(SHA256驗證)</a>
</div>
<div class="檔案下載">
  <a href="/ch/ServerFile/Get/e7ac95c8-84cb-417a-a29d-d16cdb5527f2?nodeId=715"
     title="103年試題(解答).zip">103年試題(解答).zip(3.2M)</a>
  <a href="/ch/ServerFile/ShowFileSHA256/e7ac95c8-84cb-417a-a29d-d16cdb5527f2">(SHA256驗證)</a>
</div>
"""

EMPTY_PAGE_HTML = """
<!DOCTYPE html>
<html><head><title>台灣自來水公司</title></head>
<body>
<div class="content-area">
  <p>暫無資料</p>
</div>
</body>
</html>
"""

# Anchors that don't match the ServerFile/Get pattern or lack a year — should be skipped
NO_YEAR_PAGE_HTML = """
<!DOCTYPE html>
<html>
<body>
<div class="檔案下載">
  <a href="/ch/ServerFile/Get/aabbccdd-0000-0000-0000-000000000001?nodeId=715">
    無年份資訊的連結
  </a>
</div>
</body>
</html>
"""

# SHA256 links only — should all be skipped (no ServerFile/Get links)
SHA256_ONLY_HTML = """
<!DOCTYPE html>
<html>
<body>
<div class="檔案下載">
  <a href="/ch/ServerFile/ShowFileSHA256/f21b4843-4257-4f5b-ac85-f7463e03d250">
    (SHA256驗證)
  </a>
</div>
</body>
</html>
"""

# Nested divs inside 檔案下載 — parser should still find the ZIP link
NESTED_DIVS_HTML = """
<!DOCTYPE html>
<html>
<body>
<div class="檔案下載">
  <div class="inner-wrapper">
    <span>
      <a href="/ch/ServerFile/Get/f21b4843-4257-4f5b-ac85-f7463e03d250?nodeId=715"
         title="114年試題(解答).zip">114年試題(解答).zip(4.8M)</a>
    </span>
  </div>
  <a href="/ch/ServerFile/ShowFileSHA256/f21b4843-4257-4f5b-ac85-f7463e03d250">
    (SHA256驗證)
  </a>
</div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Parser unit tests
# ---------------------------------------------------------------------------

class TwcRecruitParserTests(unittest.TestCase):

    def test_parse_detail_page_extracts_entries(self) -> None:
        entries = parse_employment_detail(DETAIL_PAGE_HTML)

        self.assertEqual(len(entries), 4)

    def test_generic_download_titles_use_the_visible_year_and_file_label(self) -> None:
        html = re.sub(r'title="\d{3}年試題\(解答\)\.zip"', 'title="(檔案下載)"', DETAIL_PAGE_HTML)
        entries = parse_employment_detail(html)
        self.assertEqual([e.year_roc for e in entries], [114, 112, 111, 110])
        self.assertEqual(entries[0].label, "114年試題(解答).zip(4.8M)")
        self.assertEqual(entries[0].url, parse_employment_detail(DETAIL_PAGE_HTML)[0].url)

    def test_generic_title_without_a_visible_year_remains_unresolved(self) -> None:
        html = NO_YEAR_PAGE_HTML.replace('?nodeId=715">', '?nodeId=715" title="(檔案下載)">')
        self.assertEqual(parse_employment_detail(html), [])
        with patch.object(TwcRecruitClient, "_fetch_text", return_value=html):
            with self.assertRaisesRegex(ValueError, "candidate/parsed download mismatch: 1 != 0"):
                TwcRecruitClient().discover_available_years()

    def test_parse_detail_page_extracts_year_roc(self) -> None:
        entries = parse_employment_detail(DETAIL_PAGE_HTML)

        years_roc = [e.year_roc for e in entries]
        self.assertIn(114, years_roc)
        self.assertIn(112, years_roc)
        self.assertIn(111, years_roc)
        self.assertIn(110, years_roc)

    def test_parse_detail_page_computes_year_ad(self) -> None:
        entries = parse_employment_detail(DETAIL_PAGE_HTML)

        years_ad = {e.year_roc: e.year_ad for e in entries}
        self.assertEqual(years_ad[114], 2025)
        self.assertEqual(years_ad[112], 2023)
        self.assertEqual(years_ad[111], 2022)
        self.assertEqual(years_ad[110], 2021)

    def test_parse_detail_page_extracts_zip_url(self) -> None:
        entries = parse_employment_detail(DETAIL_PAGE_HTML)

        # Find 114 year entry
        entry_114 = next(e for e in entries if e.year_roc == 114)
        self.assertIn("ServerFile/Get", entry_114.url)
        self.assertIn("f21b4843-4257-4f5b-ac85-f7463e03d250", entry_114.url)
        self.assertIn("nodeId=715", entry_114.url)
        self.assertTrue(entry_114.url.startswith("https://www.water.gov.tw/"))

    def test_parse_detail_page_extracts_label(self) -> None:
        entries = parse_employment_detail(DETAIL_PAGE_HTML)

        entry_114 = next(e for e in entries if e.year_roc == 114)
        self.assertIn("114", entry_114.label)
        self.assertIn("試題", entry_114.label)

    def test_parse_detail_page_skips_sha256_links(self) -> None:
        """SHA256 verification links should not become entries."""
        entries = parse_employment_detail(DETAIL_PAGE_HTML)

        for entry in entries:
            self.assertNotIn("ShowFileSHA256", entry.url)

    def test_parse_full_archive_extracts_ten_entries(self) -> None:
        entries = parse_employment_detail(FULL_ARCHIVE_HTML)

        # 103,104,105,106,107,108,110,111,112,114 = 10 years (109 and 113 absent)
        self.assertEqual(len(entries), 10)

    def test_parse_full_archive_gap_years_absent(self) -> None:
        """Years 109 and 113 are absent from archive — should not appear."""
        entries = parse_employment_detail(FULL_ARCHIVE_HTML)

        years_roc = [e.year_roc for e in entries]
        self.assertNotIn(109, years_roc)
        self.assertNotIn(113, years_roc)

    def test_parse_empty_page_returns_empty_list(self) -> None:
        entries = parse_employment_detail(EMPTY_PAGE_HTML)

        self.assertEqual(entries, [])

    def test_parse_skips_links_without_year(self) -> None:
        entries = parse_employment_detail(NO_YEAR_PAGE_HTML)

        self.assertEqual(entries, [])

    def test_parse_sha256_only_returns_empty(self) -> None:
        entries = parse_employment_detail(SHA256_ONLY_HTML)

        self.assertEqual(entries, [])

    def test_parse_plain_html_returns_empty(self) -> None:
        entries = parse_employment_detail("<html><body></body></html>")

        self.assertEqual(entries, [])

    def test_parse_nested_divs_extracts_entry(self) -> None:
        """ZIP links inside nested elements within 檔案下載 should be found."""
        entries = parse_employment_detail(NESTED_DIVS_HTML)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].year_roc, 114)
        self.assertIn("f21b4843-4257-4f5b-ac85-f7463e03d250", entries[0].url)


# ---------------------------------------------------------------------------
# TwcRecruitEntry dataclass tests
# ---------------------------------------------------------------------------

class TwcRecruitEntryTests(unittest.TestCase):

    def test_entry_fields(self) -> None:
        entry = TwcRecruitEntry(
            year_roc=114,
            year_ad=2025,
            label="114年試題(解答).zip",
            url="https://www.water.gov.tw/ch/ServerFile/Get/f21b4843-4257-4f5b-ac85-f7463e03d250?nodeId=715",
        )

        self.assertEqual(entry.year_roc, 114)
        self.assertEqual(entry.year_ad, 2025)
        self.assertEqual(entry.label, "114年試題(解答).zip")
        self.assertTrue(entry.url.startswith("https://"))


# ---------------------------------------------------------------------------
# Client integration tests (mocked _fetch_text)
# ---------------------------------------------------------------------------

class TwcRecruitClientTests(unittest.TestCase):

    def test_discover_available_years_returns_sorted_descending(self) -> None:
        with patch.object(TwcRecruitClient, "_fetch_text", return_value=DETAIL_PAGE_HTML):
            client = TwcRecruitClient()
            years = client.discover_available_years()

        # 114→2025, 112→2023, 111→2022, 110→2021, sorted descending
        self.assertEqual(years, [2025, 2023, 2022, 2021])

    def test_discovery_caches_the_bounded_archive(self) -> None:
        client = TwcRecruitClient()
        with patch.object(client, "_fetch_text", return_value=DETAIL_PAGE_HTML) as fetch:
            client.discover_available_years()
            client.discover_exams(2025)
            client.fetch_exam_page("twc-recruit-114", 2025)

        fetch.assert_called_once_with(DOWNLOAD_PAGE_URL)

    def test_discovery_urls_are_stable_and_source_owned(self) -> None:
        client = TwcRecruitClient()
        with patch.object(client, "_fetch_text", return_value=DETAIL_PAGE_HTML):
            self.assertEqual(client.build_discovery_year_url(2025), DOWNLOAD_PAGE_URL)
            self.assertEqual(
                client.build_discovery_exam_url("twc-recruit-114", 2025),
                DOWNLOAD_PAGE_URL,
            )

    def test_discovery_urls_reject_unknown_event(self) -> None:
        client = TwcRecruitClient()
        with patch.object(client, "_fetch_text", return_value=DETAIL_PAGE_HTML):
            with self.assertRaisesRegex(ValueError, "Unknown Taiwan Water"):
                client.build_discovery_exam_url("twc-recruit-115", 2026)

    def test_discovery_rejects_candidate_parse_mismatch(self) -> None:
        client = TwcRecruitClient()
        with patch.object(client, "_fetch_text", return_value=NO_YEAR_PAGE_HTML):
            with self.assertRaisesRegex(
                ValueError, "candidate/parsed download mismatch"
            ):
                client.discover_available_years()

    def test_discovery_rejects_empty_archive(self) -> None:
        client = TwcRecruitClient()
        with patch.object(client, "_fetch_text", return_value=EMPTY_PAGE_HTML):
            with self.assertRaisesRegex(ValueError, "contains no paper downloads"):
                client.discover_available_years()

    def test_discovery_rejects_duplicate_year(self) -> None:
        html = DETAIL_PAGE_HTML.replace('title="112年', 'title="114年')
        client = TwcRecruitClient()
        with patch.object(client, "_fetch_text", return_value=html):
            with self.assertRaisesRegex(ValueError, "repeats ROC year"):
                client.discover_available_years()

    def test_discovery_rejects_duplicate_url(self) -> None:
        html = DETAIL_PAGE_HTML.replace(
            "df60fb7a-cf5f-4045-b07e-84110d74a288",
            "f21b4843-4257-4f5b-ac85-f7463e03d250",
        )
        client = TwcRecruitClient()
        with patch.object(client, "_fetch_text", return_value=html):
            with self.assertRaisesRegex(ValueError, "repeats paper URL"):
                client.discover_available_years()

    def test_discovery_rejects_external_download_host(self) -> None:
        html = DETAIL_PAGE_HTML.replace(
            'href="/ch/ServerFile/Get/f21b4843',
            'href="https://example.test/ch/ServerFile/Get/f21b4843',
        )
        client = TwcRecruitClient()
        with patch.object(client, "_fetch_text", return_value=html):
            with self.assertRaisesRegex(
                ValueError, "Unexpected Taiwan Water paper URL"
            ):
                client.discover_available_years()

    def test_discovery_rejects_unbounded_archive_growth(self) -> None:
        client = TwcRecruitClient()
        with patch.object(client, "_fetch_text", return_value=DETAIL_PAGE_HTML * 26):
            with self.assertRaisesRegex(ValueError, "exceeds 100 paper downloads"):
                client.discover_available_years()

    def test_discover_exams_returns_exam_options_for_year(self) -> None:
        with patch.object(TwcRecruitClient, "_fetch_text", return_value=DETAIL_PAGE_HTML):
            client = TwcRecruitClient()
            exams = client.discover_exams(2025)

        self.assertEqual(len(exams), 1)
        self.assertEqual(exams[0].code, "twc-recruit-114")
        self.assertEqual(exams[0].year_ad, 2025)
        self.assertEqual(exams[0].year_roc, 114)

    def test_discover_exams_returns_empty_for_gap_year(self) -> None:
        with patch.object(TwcRecruitClient, "_fetch_text", return_value=FULL_ARCHIVE_HTML):
            client = TwcRecruitClient()
            # 109 is a gap year — no entries expected
            exams = client.discover_exams(2020)

        self.assertEqual(exams, [])

    def test_fetch_exam_page_builds_source_exam_page(self) -> None:
        with patch.object(TwcRecruitClient, "_fetch_text", return_value=DETAIL_PAGE_HTML):
            client = TwcRecruitClient()
            page = client.fetch_exam_page("twc-recruit-114", 2025)

        self.assertEqual(page.provider_id, "twc_recruit")
        self.assertEqual(page.source_exam_id, "twc-recruit-114")
        self.assertEqual(page.year_ad, 2025)
        self.assertEqual(page.year_roc, 114)

    def test_fetch_exam_page_has_exactly_one_paper(self) -> None:
        """Each year produces exactly one ParsedPaper (the ZIP bundle)."""
        with patch.object(TwcRecruitClient, "_fetch_text", return_value=DETAIL_PAGE_HTML):
            client = TwcRecruitClient()
            page = client.fetch_exam_page("twc-recruit-114", 2025)

        self.assertEqual(len(page.papers), 1)

    def test_fetch_exam_page_paper_has_accessible_bundle_file_type(self) -> None:
        with patch.object(TwcRecruitClient, "_fetch_text", return_value=DETAIL_PAGE_HTML):
            client = TwcRecruitClient()
            page = client.fetch_exam_page("twc-recruit-114", 2025)

        paper = page.papers[0]
        self.assertIn("accessible_bundle", paper.files)

    def test_fetch_exam_page_paper_url_points_to_zip(self) -> None:
        with patch.object(TwcRecruitClient, "_fetch_text", return_value=DETAIL_PAGE_HTML):
            client = TwcRecruitClient()
            page = client.fetch_exam_page("twc-recruit-114", 2025)

        paper = page.papers[0]
        url = paper.files["accessible_bundle"]
        self.assertIn("ServerFile/Get", url)
        self.assertIn("nodeId=715", url)

    def test_fetch_exam_page_paper_category_raw(self) -> None:
        with patch.object(TwcRecruitClient, "_fetch_text", return_value=DETAIL_PAGE_HTML):
            client = TwcRecruitClient()
            page = client.fetch_exam_page("twc-recruit-114", 2025)

        paper = page.papers[0]
        self.assertEqual(paper.category_raw, "台水評價職位人員甄試")

    def test_fetch_exam_page_exam_name_raw_contains_year(self) -> None:
        with patch.object(TwcRecruitClient, "_fetch_text", return_value=DETAIL_PAGE_HTML):
            client = TwcRecruitClient()
            page = client.fetch_exam_page("twc-recruit-114", 2025)

        self.assertIn("114", page.exam_name_raw)

    def test_fetch_exam_page_rejects_unknown_event(self) -> None:
        client = TwcRecruitClient()
        with patch.object(client, "_fetch_text", return_value=DETAIL_PAGE_HTML):
            with self.assertRaisesRegex(
                ValueError, "No Taiwan Water recruitment event"
            ):
                client.fetch_exam_page("twc-recruit-115", 2026)

    def test_download_rejects_unapproved_url_before_network(self) -> None:
        client = TwcRecruitClient()

        with self.assertRaisesRegex(ValueError, "Unexpected Taiwan Water paper URL"):
            client.download_file("https://example.test/paper.zip")


# ---------------------------------------------------------------------------
# Registry test
# ---------------------------------------------------------------------------

class TwcRecruitRegistryTests(unittest.TestCase):

    def test_registry_returns_twc_recruit_provider(self) -> None:
        from app.providers.registry import get_provider
        from app.providers.base import SourceProvider

        provider = get_provider("twc_recruit")

        self.assertIsInstance(provider, SourceProvider)
        self.assertEqual(provider.provider_id, "twc_recruit")


if __name__ == "__main__":
    unittest.main()
