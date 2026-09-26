import tempfile
import unittest
from pathlib import Path

from app.models import BundleAsset, NormalizedCatalog, NormalizedPaper, SourceExamPage
from app.paths import provider_paths, site_paths
from app.publisher import write_provider_state, write_site_state
from app.state import load_provider_state, load_site_bundles


class ScopedStateTests(unittest.TestCase):
    def test_legacy_model_constructors_remain_backward_compatible_without_provider_id(self) -> None:
        page = SourceExamPage(
            source_exam_id="115030",
            year_ad=2026,
            year_roc=115,
            exam_name_raw="115 Nurse Exam",
            attachments=[],
            papers=[],
        )
        paper = NormalizedPaper(
            canonical_id="nurse",
            canonical_name="Nurse",
            year_roc=115,
            exam_name_raw="115 Nurse Exam",
            category_raw="Nurse",
            subject_name_raw="Medical Basics",
            paper_code="101-0101-question",
            file_type="question",
            download_url_source="https://example.test/question.pdf",
        )

        self.assertEqual(page.provider_id, "")
        self.assertEqual(paper.provider_id, "")

    def test_provider_state_round_trips_under_scoped_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            provider = provider_paths(root, "moex")
            write_provider_state(
                provider,
                raw_pages=[],
                normalized=NormalizedCatalog(papers=[], review_queue=[]),
                aliases=[],
                failures=[],
                manifest=None,
            )

            raw_pages, catalog, failures = load_provider_state(provider)
            self.assertEqual(raw_pages, [])
            self.assertEqual(catalog.papers, [])
            self.assertEqual(failures, [])

    def test_site_bundles_load_from_wrapped_scoped_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            site = site_paths(root, "default")
            write_site_state(
                site,
                bundles=[
                    BundleAsset(
                        canonical_id="nurse",
                        canonical_name="Nurse",
                        years=[115],
                        file_count=1,
                        storage_key="bundles/sites/default/nurse.zip",
                        asset_name="nurse.zip",
                        release_tag="moex-bundles",
                        download_url="https://example.test/nurse.zip",
                        checksum="sha-1",
                    )
                ],
                frontend_bundles=[{"id": "nurse", "name": "Nurse", "years": [115], "fileCount": 1, "url": "https://example.test/nurse.zip"}],
            )

            bundles = load_site_bundles(site)
            self.assertEqual(bundles[0].release_tag, "moex-bundles")

    def test_site_bundles_reject_wrapped_payload_with_wrong_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            site = site_paths(root, "default")
            site.data_dir.mkdir(parents=True, exist_ok=True)
            site.bundles_path.write_text(
                '{"schema_version": 2, "site_id": "default", "bundles": []}',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Unsupported site bundles schema_version"):
                load_site_bundles(site)

    def test_site_bundles_reject_wrapped_payload_with_wrong_site_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            site = site_paths(root, "default")
            site.data_dir.mkdir(parents=True, exist_ok=True)
            site.bundles_path.write_text(
                '{"schema_version": 1, "site_id": "other", "bundles": []}',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Site bundles site_id mismatch"):
                load_site_bundles(site)


def test_provider_failure_ledger_rejects_non_record_json(tmp_path):
    import pytest
    from app.state import load_provider_failures

    provider = provider_paths(tmp_path, "ceec_ast")
    provider.data_dir.mkdir(parents=True)
    for malformed in ('null', '"invalid"', '{"stage": "download"}', '["invalid"]'):
        provider.sync_failures_path.write_text(malformed)
        with pytest.raises(ValueError, match="Expected a JSON"):
            load_provider_failures(provider)
