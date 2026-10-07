"""Tests for the jlpt_cert provider."""

import json
import unittest
from pathlib import Path

import pytest

from app.providers.jlpt_cert.client import JlptCertClient, parse_downloads


SAMPLE_HTML = """
<p><img src="img/book2018.gif" alt="JLPT Official Practice Workbook Vol. 2 (published 2018)" /></p>
<table>
<tr><th>N1</th>
<td><a href="../../samples/sample2018/pdf/N1V.pdf"><img alt="PDF" /></a></td>
<td><a href="../../samples/sample2018/pdf/N1answer.pdf"><img alt="PDF" /></a></td>
<td><a href="../../samples/sample2018/mp3/N1Q1.mp3">Q1<img alt="MP3" /></a></td>
</tr>
</table>
<p><img src="img/book2012.gif" alt="JLPT Official Practice Workbook (published 2012)" /></p>
<table>
<tr><th>N2</th>
<td><a href="../../samples/sample2012/pdf/N2L.pdf"><img alt="PDF" /></a></td>
<td><a href="../../samples/sample2017/mp3/N2Q2.mp3">Q2<img alt="MP3" /></a></td>
<td><a href="../../samples/sample2012/pdf/N2script.pdf"><img alt="PDF" /></a></td>
</tr>
</table>
"""


class JlptCertParserTests(unittest.TestCase):
    def test_parse_downloads_keeps_workbook_year_for_sample2017_audio_link(self) -> None:
        downloads = parse_downloads(SAMPLE_HTML, base_url="https://www.jlpt.jp/e/samples/sampleindex.html")

        self.assertEqual([download.year_ad for download in downloads], [2018, 2018, 2018, 2012, 2012, 2012])
        self.assertEqual(downloads[0].level_code, "n1")
        self.assertEqual(downloads[0].file_type, "question")
        self.assertEqual(downloads[1].file_type, "answer")
        self.assertEqual(downloads[2].file_type, "listening_audio")
        self.assertEqual(downloads[4].year_ad, 2012)
        self.assertTrue(downloads[4].url.endswith("/samples/sample2017/mp3/N2Q2.mp3"))
        self.assertEqual(downloads[5].file_type, "listening_transcript")


class JlptCertClientTests(unittest.TestCase):
    def test_fetch_exam_page_builds_papers_for_requested_workbook_year(self) -> None:
        client = JlptCertClient()
        client._fetch_text = lambda url: SAMPLE_HTML  # type: ignore[method-assign]

        self.assertEqual(client.discover_available_years(), [2018, 2012])
        self.assertEqual([exam.code for exam in client.discover_exams(2018)], ["jlpt-cert-practice-2018"])

        page = client.fetch_exam_page("jlpt-cert-practice-2012", 2012)

        self.assertEqual(page.provider_id, "jlpt_cert")
        self.assertEqual(page.source_exam_id, "jlpt-cert-practice-2012")
        self.assertEqual(len(page.papers), 3)
        self.assertIn("question", page.papers[0].files)
        self.assertIn("listening_audio", page.papers[1].files)
        self.assertIn("listening_transcript", page.papers[2].files)
        self.assertEqual(len({paper.subject_code for paper in page.papers}), len(page.papers))
        self.assertEqual(page.source_material.kind, "practice_collection")
        self.assertEqual(page.source_material.date.basis, "edition_year")
        self.assertEqual(page.source_material.date.year_ad, 2012)

    def test_discovery_and_fetch_share_one_official_listing_snapshot(self) -> None:
        client = JlptCertClient()
        calls = []

        def fetch(url):
            calls.append(url)
            return SAMPLE_HTML

        client._fetch_text = fetch
        client.discover_available_years()
        client.discover_exams(2012)
        client.fetch_exam_page("jlpt-cert-practice-2012", 2012)
        self.assertEqual(len(calls), 1)
        with self.assertRaisesRegex(ValueError, "official listing"):
            client.fetch_exam_page("jlpt-cert-practice-2027", 2027)
        with self.assertRaisesRegex(ValueError, "official listing"):
            client.fetch_exam_page("wrong-programme", 2012)


@pytest.mark.repo_data
def test_retained_workbook_transcripts_keep_retired_roles_and_source_context() -> None:
    root = Path(__file__).resolve().parents[1]
    provider = root / "data/providers/jlpt_cert"
    papers = [
        paper
        for path in sorted((provider / "papers").glob("*.json"))
        for paper in json.loads(path.read_text(encoding="utf-8"))
    ]
    transcripts = [paper for paper in papers if paper["file_type"] == "listening_transcript"]
    assert {(paper["year_roc"], paper["level_id"]) for paper in transcripts} == {
        (year, f"n{level}") for year in (101, 107) for level in range(1, 6)
    }
    assert not any(paper["file_type"] == "question_alt" for paper in papers)
    journal = json.loads((provider / "source-revisions.json").read_text(encoding="utf-8"))
    retired = {
        (entry["source_record"]["year_roc"], entry["source_record"]["level_id"]): entry
        for entry in journal["revisions"]
        if entry["source_record"]["file_type"] == "question_alt"
    }
    for current in transcripts:
        entry = retired[(current["year_roc"], current["level_id"])]
        previous = entry["source_record"]
        for field in (
            "provider_id", "source_exam_id", "category_code", "subject_code",
            "download_url_source", "checksum",
        ):
            assert previous[field] == current[field]
        assert current["schema_version"] == 3
        assert current["bundle_id"] == previous["bundle_id"] + "-material-practice-collection"
        assert current["source_material"]["kind"] == "practice_collection"
        assert entry["record_type"] == "paper"
        assert entry["reason"] == "source_reference_retired"
        assert entry["blob_storage_key"].startswith("providers/jlpt_cert/recovery/source-revisions/")


@pytest.mark.repo_data
def test_retained_workbooks_have_official_edition_facts_and_remain_withheld() -> None:
    from collections import Counter
    from app.paths import provider_paths
    from app.provider_index import load_provider_index, paper_index_source_year_roc
    from app.publication_quarantine import quarantined_provider_ids
    from app.state import load_provider_state

    root = Path(__file__).resolve().parents[1]
    provider = provider_paths(root, "jlpt_cert")
    pages, catalog, failures = load_provider_state(provider)
    assert not failures
    assert {page.source_material.date.year_ad for page in pages} == {2012, 2018}
    assert Counter(paper.source_material.date.year_ad for paper in catalog.papers) == {2012: 58, 2018: 58}
    for paper in catalog.papers:
        assert paper.schema_version == 3
        assert paper.source_material.kind == "practice_collection"
        assert paper.source_material.date.basis == "edition_year"
        assert paper.source_material.date.year_roc == paper.year_roc
        assert not paper.source_material.needs_review
        assert "material-practice-collection" in paper.variant_ids
    index = load_provider_index(provider)
    assert index["schema_version"] == 3
    assert Counter(paper_index_source_year_roc(index, row) for row in index["papers"]) == {101: 58, 107: 58}
    assert "jlpt_cert" in quarantined_provider_ids(root, site_id="default")


if __name__ == "__main__":
    unittest.main()
