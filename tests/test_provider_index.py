from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from jsonschema import Draft202012Validator

from app.bundler import public_bundle_ids, public_bundle_ids_from_indexes
from app.models import NormalizedCatalog, NormalizedPaper, SourceExamPage
from app.paths import provider_paths
from app.provider_index import build_provider_index_from_files, load_provider_index
from app.publisher import write_provider_state


ROOT = Path(__file__).resolve().parents[1]


class ProviderIndexTests(unittest.TestCase):
    def test_index_eligibility_matches_full_catalog_with_provider_year_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            papers = []
            indexes = []
            for provider_id, bundle_id in (("tii_cert", "tii-bundle"), ("ceec_gsat", "ceec-bundle")):
                provider = provider_paths(root, provider_id)
                paper = NormalizedPaper(
                    provider_id=provider_id, source_exam_id=f"{provider_id}-115", year_roc=115,
                    canonical_id=provider_id, canonical_name=provider_id, bundle_id=bundle_id,
                    schema_version=2, exam_name_raw=provider_id, category_raw=provider_id,
                    subject_name_raw="Subject", paper_code="paper-1", file_type="question",
                    download_url_source="https://example.test/paper.pdf",
                )
                write_provider_state(
                    provider, raw_pages=[],
                    normalized=NormalizedCatalog(papers=[paper], review_queue=[]),
                    aliases=[], failures=[], manifest=None,
                )
                papers.append(paper)
                indexes.append(load_provider_index(provider))

            policy = {"min_years": 2, "min_years_by_canonical_prefix": {"tii-": 1}}
            expected = public_bundle_ids(NormalizedCatalog(papers=papers, review_queue=[]), **policy)
            self.assertEqual(expected, {"tii-bundle"})
            self.assertEqual(public_bundle_ids_from_indexes(indexes, **policy), expected)

    def test_writer_index_matches_source_files_and_detects_a_stale_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            provider = provider_paths(Path(tmp_dir), "example")
            page = SourceExamPage(
                provider_id="example", source_exam_id="exam-115", year_ad=2026,
                year_roc=115, exam_name_raw="Example", attachments=[], papers=[],
            )
            paper = NormalizedPaper(
                provider_id="example", source_exam_id="exam-115", year_roc=115,
                canonical_id="example", canonical_name="Example", bundle_id="example-bundle",
                schema_version=2, exam_name_raw="Example", category_raw="Category",
                subject_name_raw="Subject", paper_code="paper-1", file_type="question",
                download_url_source="https://example.test/paper.pdf", storage_key="115/paper.pdf",
            )
            older_page = replace(page, source_exam_id="exam-114", year_ad=2025, year_roc=114)
            older_paper = replace(
                paper, source_exam_id="exam-114", year_roc=114, storage_key="114/paper.pdf",
            )
            write_provider_state(
                provider, raw_pages=[page, older_page],
                normalized=NormalizedCatalog(papers=[paper, older_paper], review_queue=[]),
                aliases=[], failures=[], manifest=None,
            )

            index = load_provider_index(provider)
            self.assertIsNotNone(index)
            self.assertEqual(index, build_provider_index_from_files(provider))
            self.assertEqual(
                index["raw_events"],
                [["exam-114", 2025, False], ["exam-115", 2026, False]],
            )
            self.assertEqual(index["bundle_ids"], ["example-bundle"])
            self.assertEqual(index["canonical_ids"], ["example"])
            self.assertEqual(index["papers"][0][4:7], [114, 0, 0])
            schema = json.loads((ROOT / "schemas/provider-index-v1.schema.json").read_text())
            Draft202012Validator(schema).validate(index)

            year_path = provider.papers_dir / "2026.json"
            records = json.loads(year_path.read_text(encoding="utf-8"))
            records[0]["paper_code"] = "paper-1-changed"
            year_path.write_text(json.dumps(records), encoding="utf-8")
            self.assertIsNone(load_provider_index(provider))

            index["papers"][0][4] = "115"
            provider.index_path.write_text(json.dumps(index), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "paper row is invalid"):
                load_provider_index(provider)


if __name__ == "__main__":
    unittest.main()
