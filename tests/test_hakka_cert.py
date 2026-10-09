"""Tests for the hakka_cert provider."""

import json
import unittest
from pathlib import Path

import pytest

from app.providers.hakka_cert.client import HakkaCertClient, parse_downloads
from app.source_material import SourceDate


DOWNLOAD_HTML = """
<a href="/hakka/files/downloads/321.pdf">四縣 初級 題庫</a>
<a href="/hakka/files/downloads/324.pdf">四縣 初級 題庫（二）</a>
<a href="/hakka/files/downloads/322.zip">海陸 初級 題庫 音檔</a>
<a href="/hakka/files/downloads/323.ods">詞彙表</a>
<a href="/hakka/files/downloads/325.ods">四縣 初級 題庫答案</a>
<a href="/hakka/files/downloads/321.pdf">duplicate</a>
"""

PAGED_BASIC_HTML = """
<a href="/hakka/files/downloads/527.pdf">114 年度客語能力認證基礎級暨初級題庫 ( 四縣腔 ) PDF 下載</a>
<a href="/hakka/download-files?c=2&page=2">2</a>
"""

PAGED_BASIC_HTML_PAGE_2 = """
<a href="/hakka/files/downloads/352.pdf">112 年度客語能力認證基礎級暨初級題庫 ( 四縣腔 ) PDF 下載</a>
"""

PAGED_INTERMEDIATE_HTML = """
<a href="/hakka/files/downloads/548.pdf">114 年度客語能力認證中級暨中高級題庫（海陸腔-上）PDF 下載</a>
"""


class HakkaCertParserTests(unittest.TestCase):
    def test_official_sample_label_is_in_scope_without_collecting_vocabulary(self) -> None:
        downloads = parse_downloads(
            """
        <a href="/hakka/files/downloads/225.zip">107 年度客語能力認證中級暨中高級樣卷 ( 四縣腔 ) 下載</a>
        <a href="/hakka/files/downloads/225.zip">duplicate</a>
        <a href="/hakka/files/downloads/555.zip">107 年度客語詞彙音檔</a>
        """,
            level_code="intermediate-high-intermediate",
        )
        self.assertEqual(len(downloads), 1)
        self.assertEqual(downloads[0].file_type, "question")
        self.assertEqual(downloads[0].year_ad, 2018)
        self.assertEqual(downloads[0].category_code, "sixian")

    def test_archive_container_does_not_turn_question_packages_into_audio(self) -> None:
        html = """
        <a href="/hakka/files/downloads/129.zip">107 年度客語能力認證初級題庫及樣卷 ( 四縣腔 ) 下載</a>
        <a href="/hakka/files/downloads/791.zip">高級-口說測驗試題範例-四縣</a>
        <a href="/hakka/files/downloads/481.rar">高級-書寫測驗試題範例-四縣</a>
        <a href="/hakka/files/downloads/353.zip">112 年度客語能力認證基礎級暨初級題庫音檔 ( 四縣腔 ) 下載</a>
        <a href="/hakka/files/downloads/200.pdf">聽力測驗試題範例</a>
        <a href="/hakka/files/downloads/201.mp3">聽力測驗試題範例</a>
        """
        self.assertEqual(
            [download.file_type for download in parse_downloads(html)],
            ["question", "question", "question", "listening_audio", "question", "listening_audio"],
        )

    def test_parse_downloads_keeps_public_pdf_assets_once_with_dialect_code(self) -> None:
        downloads = parse_downloads(DOWNLOAD_HTML)

        self.assertEqual(len(downloads), 4)
        self.assertEqual(downloads[0].category_code, "sixian")
        self.assertEqual(downloads[0].file_type, "question")
        self.assertTrue(downloads[0].url.endswith("/hakka/files/downloads/321.pdf"))
        self.assertEqual(downloads[1].category_code, "sixian")
        self.assertTrue(downloads[1].url.endswith("/hakka/files/downloads/324.pdf"))
        self.assertEqual(downloads[2].category_code, "hailu")
        self.assertEqual(downloads[2].file_type, "listening_audio")
        self.assertTrue(downloads[2].url.endswith("/hakka/files/downloads/322.zip"))

        self.assertEqual(downloads[3].file_type, "answer")
        self.assertTrue(downloads[3].url.endswith("/hakka/files/downloads/325.ods"))


class HakkaCertClientTests(unittest.TestCase):
    def test_samples_keep_edition_years_and_do_not_inherit_the_storage_fallback(self) -> None:
        client = HakkaCertClient()

        def fake_fetch(url: str) -> str:
            if "c=3" in url:
                return '<a href="/hakka/files/downloads/225.zip">107 年度客語能力認證中級暨中高級樣卷 ( 四縣腔 ) 下載</a>'
            if "c=5" in url:
                return '<a href="/hakka/files/downloads/477.pdf">高級-閱讀測驗試題範例-海陸</a>'
            return '<a href="/hakka/files/downloads/129.zip">107 年度客語能力認證初級題庫及樣卷 ( 四縣腔 ) 下載</a>'

        client._fetch_text = fake_fetch  # type: ignore[method-assign]
        sample = client.fetch_exam_page(
            "hakka-cert-intermediate-high-intermediate-2018", 2018
        ).papers[0]
        self.assertEqual(sample.source_material.kind, "sample")
        self.assertEqual(sample.source_material.date, SourceDate("edition_year", 2018))
        self.assertTrue(sample.source_material.evidence_url.endswith("?c=3"))
        advanced = client.fetch_exam_page("hakka-cert-advanced-2026", 2026)
        self.assertEqual(advanced.year_ad, 2026)  # compatibility partition only
        self.assertEqual(advanced.papers[0].source_material.date, SourceDate("undated", None))
        bank = client.fetch_exam_page("hakka-cert-basic-elementary-2018", 2018).papers[0]
        self.assertIsNone(bank.source_material)

    def test_discovery_uses_material_year_for_labels_without_year(self) -> None:
        client = HakkaCertClient()

        def fake_fetch(url: str) -> str:
            if "c=3" in url or "c=5" in url:
                return ""
            return DOWNLOAD_HTML

        client._fetch_text = fake_fetch  # type: ignore[method-assign]

        self.assertEqual(client.discover_available_years(), [2026])
        self.assertEqual(
            [exam.code for exam in client.discover_exams(2026)],
            ["hakka-cert-basic-elementary-2026"],
        )
        self.assertEqual(client.discover_exams(2027), [])

    def test_discovery_uses_official_level_category_and_label_years(self) -> None:
        client = HakkaCertClient()

        def fake_fetch(url: str) -> str:
            if "page=2" in url:
                return PAGED_BASIC_HTML_PAGE_2
            if "c=3" in url:
                return PAGED_INTERMEDIATE_HTML
            if "c=5" in url:
                return ""
            return PAGED_BASIC_HTML

        client._fetch_text = fake_fetch  # type: ignore[method-assign]

        self.assertEqual(client.discover_available_years(), [2025, 2023])
        self.assertEqual(
            [exam.code for exam in client.discover_exams(2025)],
            ["hakka-cert-basic-elementary-2025", "hakka-cert-intermediate-high-intermediate-2025"],
        )

    def test_fetch_exam_page_filters_hakka_by_level_category_and_year(self) -> None:
        client = HakkaCertClient()

        def fake_fetch(url: str) -> str:
            if "page=2" in url:
                return PAGED_BASIC_HTML_PAGE_2
            if "c=3" in url:
                return PAGED_INTERMEDIATE_HTML
            if "c=5" in url:
                return ""
            return PAGED_BASIC_HTML

        client._fetch_text = fake_fetch  # type: ignore[method-assign]

        page = client.fetch_exam_page("hakka-cert-basic-elementary-2023", 2023)

        self.assertEqual(page.source_exam_id, "hakka-cert-basic-elementary-2023")
        self.assertEqual(len(page.papers), 1)
        self.assertEqual(
            page.papers[0].subject_name_raw,
            "112 年度客語能力認證基礎級暨初級題庫 ( 四縣腔 ) PDF 下載",
        )

    def test_fetch_exam_page_builds_question_papers(self) -> None:
        client = HakkaCertClient()
        client._fetch_text = lambda url: "" if "c=3" in url or "c=5" in url else DOWNLOAD_HTML  # type: ignore[method-assign]

        page = client.fetch_exam_page("hakka-cert-basic-elementary-2026", 2026)

        self.assertEqual(page.provider_id, "hakka_cert")
        self.assertEqual(page.exam_name_raw, "客語能力認證官方教材及試題 基礎級暨初級")
        self.assertEqual(len(page.papers), 4)
        self.assertIn("question", page.papers[0].files)
        self.assertIn("listening_audio", page.papers[2].files)
        self.assertEqual(len({paper.subject_code for paper in page.papers}), len(page.papers))

    def test_download_listing_is_cached_across_discovery_and_fetch(self) -> None:
        client = HakkaCertClient()
        calls: list[str] = []

        def fake_fetch(url: str) -> str:
            calls.append(url)
            return "" if "c=3" in url or "c=5" in url else DOWNLOAD_HTML

        client._fetch_text = fake_fetch  # type: ignore[method-assign]

        client.discover_available_years()
        client.fetch_exam_page("hakka-cert-basic-elementary-2026", 2026)

        self.assertEqual(len(calls), 3)


@pytest.mark.repo_data
def test_retained_question_packages_keep_prior_audio_role_references() -> None:
    provider = Path(__file__).resolve().parents[1] / "data/providers/hakka_cert"
    papers = [
        paper
        for path in (provider / "papers").glob("*.json")
        for paper in json.loads(path.read_text(encoding="utf-8"))
    ]
    current = {paper["download_url_source"]: paper for paper in papers}
    journal = json.loads((provider / "source-revisions.json").read_text(encoding="utf-8"))
    # Historical backfills also retain older payload versions and delisted
    # sources. Isolate the corrections that changed only an audio role.
    retired = [
        entry
        for entry in journal["revisions"]
        if entry["source_record"]["file_type"] == "listening_audio"
        and entry["reason"] == "source_reference_retired"
        and entry["source_record"]["download_url_source"] in current
        and current[entry["source_record"]["download_url_source"]]["file_type"] == "question"
        and current[entry["source_record"]["download_url_source"]]["checksum"]
        == entry["source_record"]["checksum"]
    ]
    assert len(retired) == 15
    for entry in retired:
        previous = entry["source_record"]
        corrected = current[previous["download_url_source"]]
        assert corrected["file_type"] == "question"
        for field in (
            "provider_id",
            "year_roc",
            "source_exam_id",
            "category_code",
            "subject_code",
            "subject_name_raw",
            "checksum",
        ):
            assert previous[field] == corrected[field]
        # Immutable history preserves the earlier classification. Current
        # sample material adds an identity variant without revising its source.
        if corrected.get("source_material") is not None:
            assert "material-sample" in corrected["variant_ids"]
        else:
            assert previous["bundle_id"] == corrected["bundle_id"]
        assert entry["blob_storage_key"].startswith(
            "providers/hakka_cert/recovery/source-revisions/"
        )


@pytest.mark.parametrize(
    "event,year",
    [
        ("hakka-cert-unknown-2026", 2026),
        ("hakka-cert-advanced-2025", 2026),
        ("another-provider-2026", 2026),
    ],
)
def test_invalid_event_keys_cannot_merge_other_hakka_levels(event, year):
    client = HakkaCertClient()
    client._fetch_text = lambda url: pytest.fail("Invalid event must be rejected before discovery")
    with pytest.raises(ValueError, match="Unknown Hakka event"):
        client.fetch_exam_page(event, year)


def test_absent_listing_event_is_rejected_instead_of_returning_empty_success():
    client = HakkaCertClient()
    client._fetch_text = lambda url: ""
    with pytest.raises(ValueError, match="absent from the official listing"):
        client.fetch_exam_page("hakka-cert-advanced-2026", 2026)


if __name__ == "__main__":
    unittest.main()
