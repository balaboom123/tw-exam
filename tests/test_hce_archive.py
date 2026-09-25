import ssl
import unittest
from unittest.mock import patch

from app.models import NormalizedCatalog
from app.normalizer import normalize_papers
from app.providers.base import SourceProvider
from app.providers.hce_archive import (
    HCE_CONFIGS,
    HceArchiveClient,
    _request_url,
    _ssl_context_for,
    parse_article_listing,
    parse_combined_pdf_listing,
    parse_listing_page_urls,
)
from app.providers.registry import get_provider


CMU_LISTING_HTML = """
<html><body>
  <a href="/?q=zh-hant/node/641">115學年度學士後中醫學系入學招生考試試題及參考答案公告</a>
</body></html>
"""

CMU_PAGINATION_HTML = """
<html><body>
  <a href="/?q=zh-hant/news_spbcm&amp;page=1">第 2 頁</a>
  <a href="/?q=zh-hant/news_spbcm&amp;page=1#main-content">第 2 頁內容</a>
  <a href="/?q=zh-hant/news_spbcm&amp;page=2">第 3 頁</a>
  <a href="/?q=zh-hant/news_spbcm&amp;topic=other&amp;page=3">Other archive</a>
  <a href="/?q=zh-hant/node/641">115公告</a>
</body></html>
"""

TCU_ARTICLE_HTML = """
<html><body>
  <a href="https://admissions.tcu.edu.tw/wp-content/uploads/2026/04/115國文試題.pdf">115後中醫_國文_試題</a>
  <a href="https://admissions.tcu.edu.tw/wp-content/uploads/2026/04/115國文_參考答案.pdf">115後中醫_國文_參考答案</a>
  <a href="https://admissions.tcu.edu.tw/wp-content/uploads/2026/04/115化學試題.pdf">115後中醫_化學_試題</a>
</body></html>
"""

TCU_LISTING_HTML = """
<html><body>
  <a href="/?p=26534">115學年度學士後中醫學系招生考試各科試題及參考答案</a>
</body></html>
"""

NTHU_ARTICLE_HTML = """
<html><body>
  <a href="/app/index.php?Action=downloadfile&amp;file=english&amp;fname=x">[下載] 115學士後醫試題【0101英文】.pdf</a>
  <a href="/app/index.php?Action=downloadfile&amp;file=biology&amp;fname=x">[下載] 115學士後醫試題【0102生物與生化】.pdf</a>
  <a href="/app/index.php?Action=downloadfile&amp;file=answers&amp;fname=x">[下載] 115各科試題參考答案(公告).pdf</a>
</body></html>
"""

NTHU_PAGINATION_HTML = """
<script>
var option = {
  urlPrefix: 'https://adms.site.nthu.edu.tw/p/403-1207-6125-PAGE.php?Lang=zh-tw',
  totalPage: 4
}
</script>
"""

NSYSU_LISTING_HTML = """
<html><body>
  <a href="https://www3.nsysu.edu.tw/exam/bachelor/med/pbm/pbm_115.pdf">115年</a>
  <a href="https://www3.nsysu.edu.tw/exam/bachelor/med/pbm/pbm_114.pdf">114年</a>
</body></html>
"""


class HceArchiveParserTests(unittest.TestCase):
    def test_parse_article_listing_extracts_official_article_page(self) -> None:
        pages = parse_article_listing(CMU_LISTING_HTML, "https://adm21.cmu.edu.tw/?q=news_spbcm", HCE_CONFIGS["hce_cmu"])

        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].year_ad, 2026)
        self.assertEqual(pages[0].year_roc, 115)
        self.assertEqual(pages[0].url, "https://adm21.cmu.edu.tw/?q=zh-hant/node/641")

    def test_parse_listing_page_urls_keeps_only_cmu_archive_pagination(self) -> None:
        urls = parse_listing_page_urls(CMU_PAGINATION_HTML, HCE_CONFIGS["hce_cmu"].listing_url, HCE_CONFIGS["hce_cmu"])

        self.assertEqual(
            urls,
            [
                "https://adm21.cmu.edu.tw/?q=zh-hant/news_spbcm&page=1",
                "https://adm21.cmu.edu.tw/?q=zh-hant/news_spbcm&page=2",
            ],
        )

    def test_parse_listing_page_urls_reads_bounded_nthu_embedded_pagination(self) -> None:
        urls = parse_listing_page_urls(
            NTHU_PAGINATION_HTML,
            HCE_CONFIGS["hce_nthu"].listing_url,
            HCE_CONFIGS["hce_nthu"],
        )

        self.assertEqual(
            urls,
            [
                "https://adms.site.nthu.edu.tw/p/403-1207-6125-2.php?Lang=zh-tw",
                "https://adms.site.nthu.edu.tw/p/403-1207-6125-3.php?Lang=zh-tw",
                "https://adms.site.nthu.edu.tw/p/403-1207-6125-4.php?Lang=zh-tw",
            ],
        )

    def test_nthu_embedded_pagination_rejects_source_growth_beyond_bound(self) -> None:
        html = NTHU_PAGINATION_HTML.replace("totalPage: 4", "totalPage: 9")

        with self.assertRaisesRegex(ValueError, "exceeds configured bound"):
            parse_listing_page_urls(
                html,
                HCE_CONFIGS["hce_nthu"].listing_url,
                HCE_CONFIGS["hce_nthu"],
            )

    def test_nthu_embedded_pagination_requires_complete_metadata(self) -> None:
        html = NTHU_PAGINATION_HTML.replace("totalPage: 4", "")

        with self.assertRaisesRegex(ValueError, "Missing embedded listing pagination metadata"):
            parse_listing_page_urls(
                html,
                HCE_CONFIGS["hce_nthu"].listing_url,
                HCE_CONFIGS["hce_nthu"],
            )

    def test_cmu_short_historical_file_labels_become_questions_and_all_answers(self) -> None:
        html = """
        <a href="/sites/default/files/112%E5%9C%8B%E6%96%87.pdf">112國文.pdf</a>
        <a href="/sites/default/files/112%E5%8C%96%E5%AD%B8.pdf">112化學.pdf</a>
        <a href="/sites/default/files/answers.pdf">公告用-112後中參考答案.pdf</a>
        """
        papers = HCE_CONFIGS["hce_cmu"].parse_papers(html, "https://adm21.cmu.edu.tw/?q=zh-hant/node/325")
        by_subject = {paper.subject_code: paper for paper in papers}

        self.assertEqual(by_subject["chinese"].files["question"], "https://adm21.cmu.edu.tw/sites/default/files/112%E5%9C%8B%E6%96%87.pdf")
        self.assertEqual(by_subject["chemistry"].files["question"], "https://adm21.cmu.edu.tw/sites/default/files/112%E5%8C%96%E5%AD%B8.pdf")
        self.assertEqual(by_subject["all"].files["all_answers"], "https://adm21.cmu.edu.tw/sites/default/files/answers.pdf")

    def test_tcu_subject_file_page_extracts_question_and_answer_assets(self) -> None:
        papers = HCE_CONFIGS["hce_tcu"].parse_papers(
            TCU_ARTICLE_HTML,
            "https://admissions.tcu.edu.tw/?p=26534",
        )

        self.assertEqual(len(papers), 2)
        self.assertEqual(papers[0].category_code, "post-bacc-chinese-medicine")
        self.assertEqual(papers[0].subject_code, "chinese")
        self.assertEqual(papers[0].files["question"], "https://admissions.tcu.edu.tw/wp-content/uploads/2026/04/115國文試題.pdf")
        self.assertEqual(papers[0].files["answer"], "https://admissions.tcu.edu.tw/wp-content/uploads/2026/04/115國文_參考答案.pdf")
        self.assertEqual(papers[1].subject_code, "chemistry")
        self.assertEqual(papers[1].files["question"], "https://admissions.tcu.edu.tw/wp-content/uploads/2026/04/115化學試題.pdf")

    def test_tcu_article_listing_extracts_current_official_event(self) -> None:
        pages = parse_article_listing(
            TCU_LISTING_HTML,
            HCE_CONFIGS["hce_tcu"].listing_url,
            HCE_CONFIGS["hce_tcu"],
        )

        self.assertEqual(
            [(page.year_ad, page.url) for page in pages],
            [(2026, "https://admissions.tcu.edu.tw/?p=26534")],
        )

    def test_nthu_subject_file_page_keeps_all_answers_asset(self) -> None:
        papers = HCE_CONFIGS["hce_nthu"].parse_papers(
            NTHU_ARTICLE_HTML,
            "https://adms.site.nthu.edu.tw/p/406-1207-305076,r6125.php?Lang=zh-tw",
        )

        by_subject = {paper.subject_code: paper for paper in papers}
        self.assertEqual(by_subject["english"].files["question"], "https://adms.site.nthu.edu.tw/app/index.php?Action=downloadfile&file=english&fname=x")
        self.assertEqual(by_subject["biology-biochemistry"].files["question"], "https://adms.site.nthu.edu.tw/app/index.php?Action=downloadfile&file=biology&fname=x")
        self.assertEqual(by_subject["all"].files["all_answers"], "https://adms.site.nthu.edu.tw/app/index.php?Action=downloadfile&file=answers&fname=x")

    def test_parse_combined_pdf_listing_extracts_year_files(self) -> None:
        pages = parse_combined_pdf_listing(NSYSU_LISTING_HTML, "https://lis.nsysu.edu.tw/p/412-1001-23442.php", HCE_CONFIGS["hce_nsysu"])

        self.assertEqual([page.year_roc for page in pages], [115, 114])
        self.assertEqual(pages[0].url, "https://www3.nsysu.edu.tw/exam/bachelor/med/pbm/pbm_115.pdf")

    def test_parse_combined_pdf_listing_ignores_footer_pdf_numbers(self) -> None:
        html = """
        <html><body>
          <a href="https://www3.nsysu.edu.tw/exam/bachelor/med/pbm/pbm_115.pdf">115年</a>
          <a href="https://lis.nsysu.edu.tw/var/file/1/1001/img/39/266894846.pdf">隱私權政策聲明</a>
          <a href="https://lis.nsysu.edu.tw/var/file/1/1001/img/1132/386200698.pdf">個人資料保護管理政策</a>
        </body></html>
        """

        pages = parse_combined_pdf_listing(html, "https://lis.nsysu.edu.tw/p/412-1001-23442.php", HCE_CONFIGS["hce_nsysu"])

        self.assertEqual([page.year_roc for page in pages], [115])

    def test_unverified_tls_context_is_limited_to_failing_cmu_archive_host(self) -> None:
        context = _ssl_context_for("https://adm21.cmu.edu.tw/?q=zh-hant/news_spbcm")
        self.assertIsNotNone(context)
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertIsNone(_ssl_context_for("https://spbcm.cmu.edu.tw/page/384"))
        self.assertIsNone(_ssl_context_for("https://example.cmu.edu.tw/"))

    def test_request_url_quotes_non_ascii_download_paths(self) -> None:
        self.assertEqual(
            _request_url("https://example.edu/files/115國文試題.pdf"),
            "https://example.edu/files/115%E5%9C%8B%E6%96%87%E8%A9%A6%E9%A1%8C.pdf",
        )


class HceArchiveProviderTests(unittest.TestCase):
    def test_cmu_client_fetches_source_page_from_listing(self) -> None:
        def fake_fetch(url: str) -> str:
            if "news_spbcm" in url:
                return CMU_LISTING_HTML
            return TCU_ARTICLE_HTML

        with patch.object(HceArchiveClient, "_fetch_text", side_effect=fake_fetch):
            page = HceArchiveClient(HCE_CONFIGS["hce_cmu"]).fetch_exam_page("hce-cmu-115", 2026)

        self.assertEqual(page.provider_id, "hce_cmu")
        self.assertEqual(page.source_exam_id, "hce-cmu-115")
        self.assertEqual(page.year_roc, 115)

    def test_cmu_client_follows_pagination_once_and_caches_year_pages(self) -> None:
        page_one = """
        <a href="/?q=zh-hant/node/640">114學年度學士後中醫學系入學招生考試試題及參考答案公告</a>
        <a href="/?q=zh-hant/news_spbcm&amp;page=2">第 3 頁</a>
        """
        page_two = """
        <a href="/?q=zh-hant/node/639">113學年度學士後中醫學系入學招生考試試題及參考答案公告</a>
        """
        responses = {
            HCE_CONFIGS["hce_cmu"].listing_url: CMU_LISTING_HTML + CMU_PAGINATION_HTML,
            "https://adm21.cmu.edu.tw/?q=zh-hant/news_spbcm&page=1": page_one,
            "https://adm21.cmu.edu.tw/?q=zh-hant/news_spbcm&page=2": page_two,
        }
        calls: list[str] = []

        def fake_fetch(url: str) -> str:
            calls.append(url)
            return responses[url]

        client = HceArchiveClient(HCE_CONFIGS["hce_cmu"])
        with patch.object(client, "_fetch_text", side_effect=fake_fetch):
            self.assertEqual(client.discover_available_years(), [2026, 2025, 2024])
            self.assertEqual([exam.code for exam in client.discover_exams(2025)], ["hce-cmu-114"])

        self.assertEqual(len(calls), 3)

    def test_cmu_discovery_builders_reuse_cached_year_pages(self) -> None:
        with patch.object(HceArchiveClient, "_fetch_text", return_value=CMU_LISTING_HTML) as fetch:
            client = HceArchiveClient(HCE_CONFIGS["hce_cmu"])

            self.assertEqual(
                client.build_discovery_year_url(2026),
                "https://adm21.cmu.edu.tw/?q=zh-hant/node/641",
            )
            self.assertEqual(
                client.build_discovery_exam_url("hce-cmu-115", 2026),
                "https://adm21.cmu.edu.tw/?q=zh-hant/node/641",
            )
            self.assertEqual(client.discover_exams(2026)[0].code, "hce-cmu-115")

        self.assertEqual(fetch.call_count, 1)

    def test_cmu_discovery_builders_reject_unknown_events(self) -> None:
        with patch.object(HceArchiveClient, "_fetch_text", return_value=CMU_LISTING_HTML):
            client = HceArchiveClient(HCE_CONFIGS["hce_cmu"])
            with self.assertRaisesRegex(ValueError, "Unknown hce_cmu discovery year"):
                client.build_discovery_year_url(2025)
            with self.assertRaisesRegex(ValueError, "Unknown hce_cmu discovery exam"):
                client.build_discovery_exam_url("hce-cmu-114", 2026)

    def test_cmu_client_respects_robots_crawl_delay(self) -> None:
        client = HceArchiveClient(HCE_CONFIGS["hce_cmu"])
        client._last_request_at = 100.0

        with patch("app.providers.hce_archive.time.monotonic", return_value=103.0), patch(
            "app.providers.hce_archive.time.sleep"
        ) as sleep:
            client._wait_for_request_slot()

        sleep.assert_called_once_with(7.0)
        self.assertEqual(client._last_request_at, 110.0)

    def test_nsysu_provider_exposes_manifest_discovery_urls(self) -> None:
        with patch.object(HceArchiveClient, "_fetch_text", return_value=NSYSU_LISTING_HTML) as fetch:
            provider = get_provider("hce_nsysu")

            self.assertEqual(
                provider.build_discovery_year_url(2026),
                "https://www3.nsysu.edu.tw/exam/bachelor/med/pbm/pbm_115.pdf",
            )
            self.assertEqual(
                provider.build_discovery_exam_url("hce-nsysu-115", 2026),
                "https://www3.nsysu.edu.tw/exam/bachelor/med/pbm/pbm_115.pdf",
            )

        self.assertEqual(fetch.call_count, 1)

    def test_tcu_provider_exposes_manifest_discovery_urls(self) -> None:
        with patch.object(HceArchiveClient, "_fetch_text", return_value=TCU_LISTING_HTML) as fetch:
            provider = get_provider("hce_tcu")

            self.assertEqual(
                provider.build_discovery_year_url(2026),
                "https://admissions.tcu.edu.tw/?p=26534",
            )
            self.assertEqual(
                provider.build_discovery_exam_url("hce-tcu-115", 2026),
                "https://admissions.tcu.edu.tw/?p=26534",
            )

        self.assertEqual(fetch.call_count, 1)

    def test_nsysu_client_uses_combined_pdf_as_one_paper(self) -> None:
        with patch.object(HceArchiveClient, "_fetch_text", return_value=NSYSU_LISTING_HTML):
            page = HceArchiveClient(HCE_CONFIGS["hce_nsysu"]).fetch_exam_page("hce-nsysu-115", 2026)

        self.assertEqual(len(page.papers), 1)
        self.assertEqual(page.papers[0].category_code, "post-bacc-medicine")
        self.assertEqual(page.papers[0].subject_code, "all")
        self.assertEqual(page.papers[0].files["question_answer"], "https://www3.nsysu.edu.tw/exam/bachelor/med/pbm/pbm_115.pdf")

    def test_nthu_config_uses_live_bounded_pagination(self) -> None:
        config = HCE_CONFIGS["hce_nthu"]

        self.assertEqual(config.historical_year_pages, ())
        self.assertEqual(config.embedded_pagination_placeholder, "PAGE")
        self.assertEqual(config.max_listing_pages, 8)

    def test_registry_returns_hce_providers(self) -> None:
        for provider_id in ("hce_cmu", "hce_tcu", "hce_nsysu", "hce_nthu"):
            with self.subTest(provider_id=provider_id):
                provider = get_provider(provider_id)
                self.assertIsInstance(provider, SourceProvider)
                self.assertEqual(provider.provider_id, provider_id)

    def test_hce_normalization_uses_stable_school_bundle_identity(self) -> None:
        with patch.object(HceArchiveClient, "_fetch_text", return_value=NSYSU_LISTING_HTML):
            page = HceArchiveClient(HCE_CONFIGS["hce_nsysu"]).fetch_exam_page("hce-nsysu-115", 2026)

        mirror_metadata = {
            ("post-bacc-medicine", "all", "question_answer"): {
                "checksum": "checksum",
                "storage_key": "providers/hce_nsysu/115/hce-nsysu-115/post-bacc-medicine/all/question_answer.pdf",
            }
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
        self.assertEqual({paper.canonical_id for paper in normalized.papers}, {"hce-nsysu"})
        self.assertEqual({paper.canonical_name for paper in normalized.papers}, {"國立中山大學學士後醫學系"})
        self.assertEqual(normalized.review_queue, [])


if __name__ == "__main__":
    unittest.main()
