"""Tests for the jlpt_cert provider."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from app.providers.jlpt_cert.client import JlptCertClient, parse_downloads
from app.classification import classify_paper


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
    def test_unsupported_or_conflicting_source_files_require_review(self) -> None:
        for source in [
            SAMPLE_HTML.replace("N1V.pdf", "N10V.pdf", 1),
            SAMPLE_HTML.replace("N1V.pdf", "N1unknown.pdf", 1),
            SAMPLE_HTML.replace("N1V.pdf", "N1Q1.pdf", 1),
            SAMPLE_HTML.replace("N1Q1.mp3", "N1script.mp3", 1),
            SAMPLE_HTML.replace("<th>N1</th>", "<th>N2</th>", 1),
            SAMPLE_HTML.replace("<th>N1</th>", "", 1),
            SAMPLE_HTML.replace(
                "../../samples/sample2018/pdf/N1V.pdf", "https://example.com/samples/N1V.pdf", 1
            ),
        ]:
            with self.subTest(source=source[:80]), self.assertRaises(ValueError):
                parse_downloads(source)

    def test_missing_or_duplicate_edition_markers_cannot_become_year_zero(self) -> None:
        for source in [
            '<table><tr><th>N1</th><td><a href="/samples/sample2018/pdf/N1V.pdf">PDF</a></td></tr></table>',
            SAMPLE_HTML.replace("book2012.gif", "book2018.gif"),
            "<p>The listing is temporarily unavailable.</p>",
        ]:
            with (
                self.subTest(source=source[:80]),
                self.assertRaisesRegex(ValueError, "edition headings"),
            ):
                parse_downloads(source)

    def test_shared_URL_keeps_each_explicit_edition_reference(self) -> None:
        row = '<tr><th>N1</th><td><a href="/samples/sample2012/pdf/N1V.pdf">PDF</a></td></tr>'
        source = (
            '<img src="book2018.gif"><table>'
            + row
            + row
            + '</table><img src="book2012.gif"><table>'
            + row
            + "</table>"
        )
        downloads = parse_downloads(source)
        self.assertEqual([r.year_ad for r in downloads], [2018, 2012])
        self.assertEqual(len({r.url for r in downloads}), 1)

    def test_two_source_URLs_cannot_overwrite_the_same_native_part(self) -> None:
        source = '<img src="book2018.gif"><table><tr><th>N1</th><td><a href="/samples/sample2018/pdf/N1V.pdf">PDF</a></td><td><a href="/samples/other/pdf/N1V.pdf">PDF</a></td></tr></table>'
        with self.assertRaisesRegex(ValueError, "conflicting URLs"):
            parse_downloads(source)

    def test_parse_downloads_keeps_workbook_year_for_sample2017_audio_link(self) -> None:
        downloads = parse_downloads(
            SAMPLE_HTML, base_url="https://www.jlpt.jp/e/samples/sampleindex.html"
        )

        self.assertEqual(
            [download.year_ad for download in downloads], [2018, 2018, 2018, 2012, 2012, 2012]
        )
        self.assertEqual(downloads[0].level_code, "n1")
        self.assertEqual(downloads[0].file_type, "question")
        self.assertEqual(downloads[1].file_type, "answer")
        self.assertEqual(downloads[2].file_type, "listening_audio")
        self.assertEqual(downloads[4].year_ad, 2012)
        self.assertTrue(downloads[4].url.endswith("/samples/sample2017/mp3/N2Q2.mp3"))
        self.assertEqual(downloads[5].file_type, "listening_transcript")


class JlptCertClientTests(unittest.TestCase):
    def test_unreviewed_endpoints_stop_before_transport(self) -> None:
        client = JlptCertClient()
        for source in [
            "https://example.com/samples/sample2018/pdf/N1V.pdf",
            "https://www.jlpt.jp/samples/../outside/N1V.pdf",
            "https://www.jlpt.jp/samples/sample2018/pdf/N10V.pdf",
            "https://www.jlpt.jp/samples/sample2018/pdf/N1V.pdf#other",
        ]:
            for method in ["head", "download_file"]:
                with (
                    self.subTest(source=source, method=method),
                    patch.object(client.http, "head") as head,
                    patch.object(client.http, "download") as download,
                ):
                    with self.assertRaises(ValueError):
                        getattr(client, method)(source)
                    head.assert_not_called()
                    download.assert_not_called()

    def test_fetch_exam_page_builds_papers_for_requested_workbook_year(self) -> None:
        client = JlptCertClient()
        client._fetch_text = lambda url: SAMPLE_HTML  # type: ignore[method-assign]

        self.assertEqual(client.discover_available_years(), [2018, 2012])
        self.assertEqual(
            [exam.code for exam in client.discover_exams(2018)], ["jlpt-cert-practice-2018"]
        )

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


class JlptLevelEvidenceTests(unittest.TestCase):
    def test_structured_level_cannot_contradict_source_labels(self) -> None:
        for code in ["n2", "n10", "legacy"]:
            identity = classify_paper(
                provider_id="jlpt_cert",
                source_exam_id="jlpt-cert-practice-2018",
                year_ad=2018,
                category_raw="JLPT_N1",
                category_code=code,
                exam_name_raw="JLPT Official Practice Workbook",
                canonical_id="jlpt-cert",
                canonical_name="JLPT",
                subject_name_raw="N1 vocabulary question",
            )
            self.assertEqual((identity.level_id, identity.confidence), ("unknown", "review"))

    def classify(self, category: str, subject: str):
        return classify_paper(
            provider_id="jlpt_cert",
            source_exam_id="jlpt-cert-practice-2018",
            year_ad=2018,
            category_raw=category,
            exam_name_raw="JLPT Official Practice Workbook",
            canonical_id="jlpt-cert",
            canonical_name="JLPT",
            subject_name_raw=subject,
        )

    def test_unsupported_and_conflicting_levels_are_not_N1(self) -> None:
        for category, subject in [
            ("JLPT_N10", "N10 vocabulary question"),
            ("JLPT_N1", "N2 vocabulary question"),
            ("JLPT_N1", "N10 vocabulary question"),
            ("JLPT_N0", "N0 vocabulary question"),
            ("JLPT_XN1", "XN1 vocabulary question"),
        ]:
            with self.subTest(category=category, subject=subject):
                identity = self.classify(category, subject)
                self.assertEqual((identity.level_id, identity.confidence), ("unknown", "review"))

    def test_each_native_level_is_preserved_case_insensitively(self) -> None:
        for level in range(1, 6):
            identity = self.classify(f"JLPT_N{level}", f"n{level} vocabulary question")
            self.assertEqual((identity.level_id, identity.confidence), (f"n{level}", "high"))


@pytest.mark.repo_data
def test_retained_native_review_anchors_match_every_current_workbook_file() -> None:
    from app.paths import provider_paths
    from app.state import load_provider_state

    root = Path(__file__).resolve().parents[1]
    provider = provider_paths(root, "jlpt_cert")
    _pages, catalog, _failures = load_provider_state(provider)
    manifest = json.loads(provider.source_manifest_path.read_text())
    assert set(manifest["files"]) == {p.storage_key for p in catalog.papers}
    for paper in catalog.papers:
        file = manifest["files"][paper.storage_key]
        native = file["native_review"]
        assert file["source_url"] == paper.download_url_source
        assert file["sha256"] == native["reviewed_sha256"] == paper.checksum
        assert file["source_exam_id"] == paper.source_exam_id
        assert native["level_code"] == paper.category_code == paper.level_id
        assert native["file_type"] == paper.file_type
        assert native["edition_year"] == paper.source_material.date.year_ad
        assert paper.subject_code == native["level_code"] + "-" + native["part_code"]
        if paper.file_type == "listening_audio":
            assert native["PDF_page_count"] is None
            assert native["native_role_heading"] is None
        else:
            assert native["PDF_page_count"] > 0
            assert native["native_role_heading"]
    audit = manifest["probe_policy"]["native_source_review"]
    assert audit["native_level_groups_reviewed"] == 5
    assert audit["level_edition_contexts_reviewed"] == 10
    assert audit["source_asset_requests"] == 0
    assert audit["ordinary_unreferenced_mirror_files"] == 0
    assert audit["protected_source_revision_blobs"] == 10


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
            "provider_id",
            "source_exam_id",
            "category_code",
            "subject_code",
            "download_url_source",
            "checksum",
        ):
            assert previous[field] == current[field]
        assert current["schema_version"] == 3
        assert current["bundle_id"] == previous["bundle_id"] + "-material-practice-collection"
        assert current["source_material"]["kind"] == "practice_collection"
        assert entry["record_type"] == "paper"
        assert entry["reason"] == "source_reference_retired"
        assert entry["blob_storage_key"].startswith(
            "providers/jlpt_cert/recovery/source-revisions/"
        )


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
    assert Counter(paper.source_material.date.year_ad for paper in catalog.papers) == {
        2012: 58,
        2018: 58,
    }
    for paper in catalog.papers:
        assert paper.schema_version == 3
        assert paper.source_material.kind == "practice_collection"
        assert paper.source_material.date.basis == "edition_year"
        assert paper.source_material.date.year_roc == paper.year_roc
        assert not paper.source_material.needs_review
        assert "material-practice-collection" in paper.variant_ids
    index = load_provider_index(provider)
    assert index["schema_version"] == 3
    assert Counter(paper_index_source_year_roc(index, row) for row in index["papers"]) == {
        101: 58,
        107: 58,
    }
    assert "jlpt_cert" in quarantined_provider_ids(root, site_id="default")


if __name__ == "__main__":
    unittest.main()
