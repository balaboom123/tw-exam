import unittest

from app.providers.base import ResponseMetadata
from app.providers.moex.client import make_result_url
from app.manifest import SourceManifest
from app.models import ExamOption, ParsedPaper, SourceExamPage
from app.probe import hash_exam_codes, hash_paper_urls, probe_latest
from app.providers.registry import get_provider


class FakeProbeClient:
    def __init__(self) -> None:
        self.available_years = [2026, 2025]
        self.exams_by_year: dict[int, list[ExamOption]] = {}
        self.head_lengths: dict[str, int] = {}
        self.pages: dict[str, SourceExamPage] = {}
        self.discovered_exam_years: list[int] = []
        self.fetched_exam_codes: list[str] = []
        self.head_urls: list[str] = []

    def discover_available_years(self) -> list[int]:
        return self.available_years

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        self.discovered_exam_years.append(year_ad)
        return self.exams_by_year.get(year_ad, [])

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        self.fetched_exam_codes.append(exam_code)
        return self.pages[exam_code]

    def head(self, url: str) -> ResponseMetadata:
        self.head_urls.append(url)
        return ResponseMetadata(url=url, status=200, content_length=self.head_lengths[url])


def _exam(code: str, year_ad: int = 2026) -> ExamOption:
    return ExamOption(code=code, year_ad=year_ad, year_roc=year_ad - 1911, label=f"Exam {code}")


def _page(code: str, subject_code: str = "0101") -> SourceExamPage:
    return SourceExamPage(
        source_exam_id=code,
        year_ad=2026,
        year_roc=115,
        exam_name_raw=f"Exam {code}",
        attachments=[],
        papers=[
            ParsedPaper(
                category_raw="Category",
                category_code="101",
                subject_code=subject_code,
                subject_name_raw="Subject",
                files={"question": f"https://example.test/{code}/{subject_code}/question.pdf"},
            )
        ],
    )


class ProbeTests(unittest.TestCase):
    def test_probe_latest_skips_fetching_pages_when_manifest_fingerprints_match(self) -> None:
        client = FakeProbeClient()
        year_url = "https://wwwq.moex.gov.tw/exam/wFrmExamQandASearch.aspx?y=2026"
        exam_codes = ["115040", "115030"]
        client.head_lengths = {
            year_url: 800,
            make_result_url("115040", 2026): 500,
            make_result_url("115030", 2026): 300,
        }
        manifest = SourceManifest(
            years={
                "2026": {
                    "year_ad": 2026,
                    "year_roc": 115,
                    "search_url": year_url,
                    "head_content_length": 800,
                    "exam_codes": exam_codes,
                    "exam_codes_hash": hash_exam_codes(exam_codes),
                }
            },
            exams={
                "115040": {"source_exam_id": "115040", "year_ad": 2026, "head_content_length": 500},
                "115030": {"source_exam_id": "115030", "year_ad": 2026, "head_content_length": 300},
            },
        )

        result = probe_latest(client=client, manifest=manifest, year_window=1, now="2026-05-20T00:00:00+08:00")

        self.assertFalse(result.should_sync)
        self.assertEqual(result.changed_exam_codes, [])
        self.assertEqual(result.unchanged_exam_codes, ["115040", "115030"])
        self.assertEqual(client.discovered_exam_years, [])
        self.assertEqual(client.fetched_exam_codes, [])
        self.assertEqual(result.request_counts["year_head_count"], 1)
        self.assertEqual(result.request_counts["exam_head_count"], 2)

    def test_probe_latest_recovers_empty_moex_manifest_even_when_head_length_matches(self) -> None:
        client = FakeProbeClient()
        year_url = "https://wwwq.moex.gov.tw/exam/wFrmExamQandASearch.aspx?y=2026"
        client.exams_by_year[2026] = [_exam("115040")]
        client.head_lengths = {
            year_url: 800,
            make_result_url("115040", 2026): 500,
        }
        client.pages["115040"] = _page("115040")
        manifest = SourceManifest(
            provider_id="moex",
            years={
                "2026": {
                    "year_ad": 2026,
                    "year_roc": 115,
                    "search_url": year_url,
                    "head_content_length": 800,
                    "exam_codes": [],
                    "exam_codes_hash": hash_exam_codes([]),
                }
            },
        )

        result = probe_latest(client=client, manifest=manifest, year_window=1, now="2026-05-20T00:00:00+08:00")

        self.assertEqual(client.discovered_exam_years, [2026])
        self.assertEqual(result.updated_manifest.years["2026"]["exam_codes"], ["115040"])
        self.assertEqual(result.changed_exam_codes, ["115040"])

    def test_probe_latest_fetches_changed_exam_page_and_updates_hashes(self) -> None:
        client = FakeProbeClient()
        year_url = "https://wwwq.moex.gov.tw/exam/wFrmExamQandASearch.aspx?y=2026"
        client.head_lengths = {
            year_url: 800,
            make_result_url("115040", 2026): 501,
        }
        client.pages["115040"] = _page("115040")
        manifest = SourceManifest(
            years={
                "2026": {
                    "year_ad": 2026,
                    "year_roc": 115,
                    "search_url": year_url,
                    "head_content_length": 800,
                    "exam_codes": ["115040"],
                    "exam_codes_hash": hash_exam_codes(["115040"]),
                }
            },
            exams={"115040": {"source_exam_id": "115040", "year_ad": 2026, "head_content_length": 500}},
        )

        result = probe_latest(client=client, manifest=manifest, year_window=1, now="2026-05-20T00:00:00+08:00")

        self.assertTrue(result.should_sync)
        self.assertEqual(result.changed_exam_codes, ["115040"])
        self.assertEqual(client.fetched_exam_codes, ["115040"])
        self.assertEqual(
            result.updated_manifest.exams["115040"]["paper_url_hash"],
            hash_paper_urls(_page("115040")),
        )
        self.assertEqual(result.request_counts["exam_get_count"], 1)

    def test_probe_latest_discovers_new_and_removed_exams_when_year_changes(self) -> None:
        client = FakeProbeClient()
        year_url = "https://wwwq.moex.gov.tw/exam/wFrmExamQandASearch.aspx?y=2026"
        client.exams_by_year[2026] = [_exam("115040"), _exam("115010")]
        client.head_lengths = {
            year_url: 900,
            make_result_url("115040", 2026): 500,
            make_result_url("115010", 2026): 250,
        }
        client.pages["115010"] = _page("115010")
        manifest = SourceManifest(
            years={
                "2026": {
                    "year_ad": 2026,
                    "year_roc": 115,
                    "search_url": year_url,
                    "head_content_length": 800,
                    "exam_codes": ["115040", "115030"],
                    "exam_codes_hash": hash_exam_codes(["115040", "115030"]),
                }
            },
            exams={
                "115040": {"source_exam_id": "115040", "year_ad": 2026, "head_content_length": 500},
                "115030": {"source_exam_id": "115030", "year_ad": 2026, "head_content_length": 300},
            },
        )

        result = probe_latest(client=client, manifest=manifest, year_window=1, now="2026-05-20T00:00:00+08:00")

        self.assertTrue(result.should_sync)
        self.assertEqual(result.changed_years, [2026])
        self.assertEqual(result.changed_exam_codes, ["115010"])
        self.assertEqual(result.removed_exam_codes, ["115030"])
        self.assertEqual(result.unchanged_exam_codes, ["115040"])
        self.assertEqual(client.discovered_exam_years, [2026])
        self.assertEqual(client.fetched_exam_codes, ["115010"])

    def test_probe_latest_fails_clearly_for_provider_without_probe_url_model(self) -> None:
        class CeecLikeProbeClient(FakeProbeClient):
            provider_id = "ceec_gsat"

        client = CeecLikeProbeClient()
        manifest = SourceManifest(provider_id="ceec_gsat")

        with self.assertRaisesRegex(NotImplementedError, "ceec_gsat"):
            probe_latest(client=client, manifest=manifest, year_window=1, now="2026-05-20T00:00:00+08:00")


class ProviderRegistryTests(unittest.TestCase):
    def test_get_provider_returns_moex_provider(self) -> None:
        provider = get_provider("moex")

        self.assertEqual(provider.provider_id, "moex")
        self.assertTrue(hasattr(provider, "discover_available_years"))


if __name__ == "__main__":
    unittest.main()
