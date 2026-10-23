import unittest
from unittest.mock import patch

from app.models import NormalizedCatalog
from app.normalizer import normalize_papers
from app.providers.base import SourceProvider
from app.providers.registry import get_provider

from app.providers.post_recruit.client import PostRecruitClient, parse_history_page, parse_year_page, source_file_code


YEAR_HTML = """
<html><body>
<h2>考古題年度選取</h2>
<a href="/115post02/Paper/History?EPID=10315">114年</a>
<a href="/115post02/Paper/History?EPID=10316">113年</a>
</body></html>
"""

HISTORY_HTML = """
<html><body>
<h2>考古題</h2>
114年-營運職-法務-臺北地區(A11108101)
<a href="/_File/Download/115post02/HistoryPaper/one.pdf">專業科目(1)</a>
<a href="/_File/Download/115post02/HistoryPaper/two.pdf">專業科目(2)</a>
<a href="/Home/Index">首頁</a>
</body></html>
"""

PRIOR_YEAR_HTML = """
<html><body>
<h2>考古題年度選取</h2>
<a href="/114post01/Paper/History?EPID=10274">113年</a>
<a href="/114post01/Paper/History?EPID=10272">111年</a>
</body></html>
"""


class PostRecruitParserTests(unittest.TestCase):
    def test_parse_year_page_extracts_history_links(self) -> None:
        years = parse_year_page(YEAR_HTML, "https://svc.tabf.org.tw/115post02//Paper/Year")

        self.assertEqual(len(years), 2)
        self.assertEqual(years[0].year_ad, 2025)
        self.assertEqual(years[0].url, "https://svc.tabf.org.tw/115post02/Paper/History?EPID=10315")
        self.assertEqual(years[0].listing_url, "https://svc.tabf.org.tw/115post02//Paper/Year")

    def test_discovery_merges_live_windows_with_newest_host_precedence(self) -> None:
        def fetch(url: str) -> str:
            return PRIOR_YEAR_HTML if "114post01" in url else YEAR_HTML

        with patch.object(PostRecruitClient, "_fetch_text", side_effect=fetch):
            client = PostRecruitClient()
            years = client._years()

        self.assertEqual([year.year_ad for year in years], [2025, 2024, 2022])
        self.assertEqual(years[1].url, "https://svc.tabf.org.tw/115post02/Paper/History?EPID=10316")
        self.assertEqual(years[2].url, "https://svc.tabf.org.tw/114post01/Paper/History?EPID=10272")

    def test_discovery_manifest_urls_preserve_listing_and_event_provenance(self) -> None:
        def fetch(url: str) -> str:
            return PRIOR_YEAR_HTML if "114post01" in url else YEAR_HTML

        with patch.object(PostRecruitClient, "_fetch_text", side_effect=fetch):
            client = PostRecruitClient()
            self.assertEqual(
                client.build_discovery_year_url(2022),
                "https://svc.tabf.org.tw/114post01//Paper/Year",
            )
            self.assertEqual(
                client.build_discovery_exam_url("post-recruit-111", 2022),
                "https://svc.tabf.org.tw/114post01/Paper/History?EPID=10272",
            )

    def test_parse_history_page_extracts_official_pdf_links(self) -> None:
        papers = parse_history_page(HISTORY_HTML, "https://svc.tabf.org.tw/115post02/Paper/History?EPID=10315")

        self.assertEqual(len(papers), 2)
        self.assertEqual(papers[0].category_code, "post-recruit")
        self.assertEqual(papers[0].subject_code, source_file_code(papers[0].files["question"]))
        self.assertEqual(papers[0].category_raw, "營運職-法務-臺北地區(A11108101)")
        self.assertEqual(papers[0].subject_name_raw, "專業科目(1)")
        self.assertEqual(papers[0].files["question"], "https://svc.tabf.org.tw/_File/Download/115post02/HistoryPaper/one.pdf")

    def test_fetch_exam_page_turns_year_into_source_page(self) -> None:
        with patch.object(PostRecruitClient, "_fetch_text", side_effect=lambda url: YEAR_HTML if "Paper/Year" in url else HISTORY_HTML):
            client = PostRecruitClient()
            page = client.fetch_exam_page("post-recruit-114", 2025)

        self.assertEqual(page.provider_id, "post_recruit")
        self.assertEqual(page.source_exam_id, "post-recruit-114")
        self.assertEqual(page.exam_name_raw, "114年中華郵政職階人員甄試")
        self.assertEqual(len(page.papers), 2)

    def test_registry_returns_post_recruit_provider(self) -> None:
        provider = get_provider("post_recruit")

        self.assertIsInstance(provider, SourceProvider)
        self.assertEqual(provider.provider_id, "post_recruit")

    def test_post_recruit_normalization_uses_stable_canonical_bundle_identity(self) -> None:
        with patch.object(PostRecruitClient, "_fetch_text", side_effect=lambda url: YEAR_HTML if "Paper/Year" in url else HISTORY_HTML):
            client = PostRecruitClient()
            page = client.fetch_exam_page("post-recruit-114", 2025)

        mirror_metadata = {}
        for paper in page.papers:
            for file_type in paper.files:
                mirror_metadata[(paper.category_code, paper.subject_code, file_type)] = {
                    "checksum": f"{paper.subject_code}-{file_type}",
                    "storage_key": f"providers/post_recruit/114/{page.source_exam_id}/{paper.category_code}/{paper.subject_code}/{file_type}.pdf",
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
        self.assertEqual({paper.canonical_id for paper in normalized.papers}, {"post-recruit"})
        self.assertEqual({paper.canonical_name for paper in normalized.papers}, {"中華郵政職階人員甄試"})
        self.assertEqual(normalized.review_queue, [])


if __name__ == "__main__":
    unittest.main()
