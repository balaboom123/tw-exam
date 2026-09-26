import json
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace

from app.classification import classify_normalized_paper, identity_fields

from app.manifest import SourceManifest
from app.models import AliasRule, BundleAsset, NormalizedCatalog, NormalizedPaper, ReviewItem, SourceExamPage
from app.paths import provider_paths, site_paths
from app.publisher import publish_site, write_data_files, write_provider_state, write_site_state


def _v2_paper(**kwargs):
    paper = NormalizedPaper(**kwargs)
    return replace(paper, **identity_fields(classify_normalized_paper(paper)))


class PublisherTests(unittest.TestCase):
    def test_write_data_files(self) -> None:
        normalized = NormalizedCatalog(
            papers=[
                NormalizedPaper(
                    provider_id="moex",
                    canonical_id="nurse",
                    canonical_name="Nurse",
                    year_roc=115,
                    exam_name_raw="115 Nurse Exam",
                    category_raw="Nurse",
                    category_code="101",
                    source_exam_id="115030",
                    subject_code="0101",
                    subject_name_raw="Medical Basics",
                    paper_code="101-0101-question",
                    file_type="question",
                    download_url_source="https://wwwq.moex.gov.tw/example.pdf",
                    download_url_mirror="",
                    download_url_bundle="https://github.com/example/repo/releases/download/moex-bundles/nurse.zip",
                    storage_key="115/115030/101/0101/question.pdf",
                    checksum="abc123",
                )
            ],
            review_queue=[ReviewItem(raw_category="Nurse", normalized_candidate="Nurse", source_exam_id="115030", year_roc=115)],
        )
        raw_pages = [
            SourceExamPage(
                provider_id="moex",
                source_exam_id="115030",
                year_ad=2026,
                year_roc=115,
                exam_name_raw="115 Nurse Exam",
                attachments=[],
                papers=[],
            )
        ]
        aliases = [AliasRule(match_type="exact", raw_pattern="Nurse", canonical_id="nurse", canonical_name="Nurse")]
        bundles = [
            BundleAsset(
                canonical_id="nurse",
                canonical_name="Nurse",
                years=[115],
                file_count=1,
                storage_key="bundles/nurse.zip",
                asset_name="nurse.zip",
                release_tag="moex-bundles",
                download_url="https://github.com/example/repo/releases/download/moex-bundles/nurse.zip",
                legacy_asset_names=["nurse-legacy.zip"],
            )
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_data_files(root / "data", raw_pages, normalized, aliases, bundles, [])

            papers = json.loads((root / "data" / "papers" / "2026.json").read_text(encoding="utf-8"))
            self.assertEqual(papers[0]["canonical_name"], "Nurse")
            self.assertEqual(papers[0]["provider_id"], "moex")
            self.assertFalse((root / "data" / "papers.json").exists())
            self.assertFalse((root / "data" / "exams.raw.json").exists())
            self.assertEqual(
                papers[0]["download_url_bundle"],
                "https://github.com/example/repo/releases/download/moex-bundles/nurse.zip",
            )

            bundle_index = json.loads((root / "data" / "bundles.json").read_text(encoding="utf-8"))
            self.assertEqual(bundle_index[0]["canonical_id"], "nurse")

            release_assets = json.loads((root / "data" / "release-assets.json").read_text(encoding="utf-8"))
            self.assertEqual(
                release_assets,
                [
                    {
                        "storage_key": "bundles/nurse.zip",
                        "asset_name": "nurse.zip",
                        "checksum": "",
                        "legacy_asset_names": ["nurse-legacy.zip"],
                    },
                ],
            )

            review = json.loads((root / "data" / "review-queue.json").read_text(encoding="utf-8"))
            self.assertEqual(review[0]["normalized_candidate"], "Nurse")
            self.assertEqual(json.loads((root / "data" / "sync-failures.json").read_text(encoding="utf-8")), [])

    def test_write_provider_and_site_state_keep_outputs_scoped_only(self) -> None:
        raw_pages = [
            SourceExamPage(
                provider_id="moex",
                source_exam_id="115030",
                year_ad=2026,
                year_roc=115,
                exam_name_raw="115 Nurse Exam",
                attachments=[],
                papers=[],
            )
        ]
        normalized = NormalizedCatalog(
            papers=[
                NormalizedPaper(
                    provider_id="moex",
                    canonical_id="nurse",
                    canonical_name="Nurse",
                    year_roc=115,
                    exam_name_raw="115 Nurse Exam",
                    category_raw="Nurse",
                    subject_name_raw="Medical Basics",
                    paper_code="101-0101-question",
                    file_type="question",
                    download_url_source="https://wwwq.moex.gov.tw/example.pdf",
                    category_code="101",
                    source_exam_id="115030",
                    subject_code="0101",
                )
            ],
            review_queue=[ReviewItem(raw_category="Nurse", normalized_candidate="Nurse", source_exam_id="115030", year_roc=115)],
        )
        aliases = [AliasRule(match_type="exact", raw_pattern="Nurse", canonical_id="nurse", canonical_name="Nurse")]
        bundles = [
            BundleAsset(
                canonical_id="nurse",
                canonical_name="Nurse",
                years=[115],
                file_count=1,
                storage_key="bundles/sites/default/nurse.zip",
                asset_name="nurse.zip",
                release_tag="moex-bundles",
                download_url="https://example.test/nurse.zip",
            )
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            provider = provider_paths(root, "moex")
            site = site_paths(root, "default")
            write_provider_state(
                provider,
                raw_pages=raw_pages,
                normalized=normalized,
                aliases=aliases,
                failures=[],
                manifest=SourceManifest(provider_id="moex"),
            )
            write_site_state(
                site,
                bundles=bundles,
                frontend_bundles=[{"id": "nurse", "name": "Nurse", "years": [115], "fileCount": 1, "url": bundles[0].download_url}],
            )

            self.assertTrue(provider.exams_dir.exists())
            self.assertTrue(site.bundles_path.exists())
            self.assertTrue(provider.aliases_path.exists())
            self.assertTrue(site.release_assets_path.exists())
            self.assertFalse((root / "data" / "bundles.json").exists())
            self.assertFalse((root / "data" / "release-assets.json").exists())

    def test_publish_site_aggregates_provider_catalogs_into_default_site(self) -> None:
        moex_latest = _v2_paper(
            provider_id="moex",
            canonical_id="nurse",
            canonical_name="Nurse",
            year_roc=115,
            exam_name_raw="115 Nurse Exam",
            category_raw="Nurse",
            subject_name_raw="Medical Basics",
            paper_code="101-0101-question",
            file_type="question",
            download_url_source="https://example.test/moex/nurse.pdf",
            category_code="101",
            source_exam_id="115030",
            subject_code="0101",
            storage_key="115/115030/101/0101/question.pdf",
        )
        moex_prior = _v2_paper(
            provider_id="moex",
            canonical_id="nurse",
            canonical_name="Nurse",
            year_roc=114,
            exam_name_raw="114 Nurse Exam",
            category_raw="Nurse",
            subject_name_raw="Medical Basics",
            paper_code="101-0101-question",
            file_type="question",
            download_url_source="https://example.test/moex/nurse-114.pdf",
            category_code="101",
            source_exam_id="114030",
            subject_code="0101",
            storage_key="114/114030/101/0101/question.pdf",
        )
        ceec_latest = _v2_paper(
            provider_id="ceec_gsat",
            canonical_id="ceec-gsat",
            canonical_name="CEEC GSAT",
            year_roc=115,
            exam_name_raw="115 CEEC GSAT",
            category_raw="GSAT",
            subject_name_raw="General Subject",
            paper_code="101-0101-question",
            file_type="question",
            download_url_source="https://example.test/ceec_gsat/ceec-gsat.pdf",
            category_code="101",
            source_exam_id="gsat-115-guozong",
            subject_code="0101",
            storage_key="115/gsat-115-guozong/101/0101/question.pdf",
        )
        ceec_prior = _v2_paper(
            provider_id="ceec_gsat",
            canonical_id="ceec-gsat",
            canonical_name="CEEC GSAT",
            year_roc=114,
            exam_name_raw="114 CEEC GSAT",
            category_raw="GSAT",
            subject_name_raw="General Subject",
            paper_code="101-0101-question",
            file_type="question",
            download_url_source="https://example.test/ceec_gsat/ceec-gsat-114.pdf",
            category_code="101",
            source_exam_id="gsat-114-guozong",
            subject_code="0101",
            storage_key="114/gsat-114-guozong/101/0101/question.pdf",
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            for provider_id, paper in (
                ("moex", moex_latest),
                ("moex", moex_prior),
                ("ceec_gsat", ceec_latest),
                ("ceec_gsat", ceec_prior),
            ):
                mirror_path = provider_paths(root, provider_id).mirror_dir / paper.storage_key
                mirror_path.parent.mkdir(parents=True, exist_ok=True)
                mirror_path.write_bytes(b"%PDF-1.7 demo")

            write_provider_state(
                provider_paths(root, "moex"),
                raw_pages=[],
                normalized=NormalizedCatalog(papers=[moex_latest, moex_prior], review_queue=[]),
                aliases=[],
                failures=[],
                manifest=None,
            )
            write_provider_state(
                provider_paths(root, "ceec_gsat"),
                raw_pages=[],
                normalized=NormalizedCatalog(papers=[ceec_latest, ceec_prior], review_queue=[]),
                aliases=[],
                failures=[],
                manifest=None,
            )

            normalized, bundles = publish_site(root, site_id="default", repository="example/repo")

            self.assertEqual({paper.canonical_id for paper in normalized.papers}, {"nurse", "ceec-gsat"})
            self.assertEqual({bundle.canonical_id for bundle in bundles}, {"nurse", "ceec-gsat"})
            self.assertEqual(
                {bundle.storage_key for bundle in bundles},
                {
                    "bundles/sites/default/moex-professional-combined-not-applicable-nurse--a167604af56e.zip",
                    "bundles/sites/default/ceec-gsat-admission-gsat-not-applicable-ceec-gsat--468bb1ad9f50.zip",
                },
            )
            self.assertTrue(site_paths(root, "default").bundles_path.exists())
            self.assertFalse((root / "data" / "bundles.json").exists())
            self.assertTrue((site_paths(root, "default").bundle_dir / "moex-professional-combined-not-applicable-nurse--a167604af56e.zip").exists())
            self.assertTrue((site_paths(root, "default").bundle_dir / "ceec-gsat-admission-gsat-not-applicable-ceec-gsat--468bb1ad9f50.zip").exists())

            release_assets = json.loads(site_paths(root, "default").release_assets_path.read_text(encoding="utf-8"))
            self.assertEqual(
                {asset["storage_key"] for asset in release_assets["assets"]},
                {
                    "bundles/sites/default/moex-professional-combined-not-applicable-nurse--a167604af56e.zip",
                    "bundles/sites/default/ceec-gsat-admission-gsat-not-applicable-ceec-gsat--468bb1ad9f50.zip",
                },
            )

    def test_publish_site_excludes_single_year_bundles_from_public_site_state(self) -> None:
        nurse_latest = _v2_paper(
            provider_id="moex",
            canonical_id="nurse",
            canonical_name="Nurse",
            year_roc=115,
            exam_name_raw="115 Nurse Exam",
            category_raw="Nurse",
            subject_name_raw="Medical Basics",
            paper_code="101-0101-question",
            file_type="question",
            download_url_source="https://example.test/moex/nurse-115.pdf",
            category_code="101",
            source_exam_id="115030",
            subject_code="0101",
            storage_key="115/115030/101/0101/question.pdf",
        )
        nurse_prior = _v2_paper(
            provider_id="moex",
            canonical_id="nurse",
            canonical_name="Nurse",
            year_roc=114,
            exam_name_raw="114 Nurse Exam",
            category_raw="Nurse",
            subject_name_raw="Medical Basics",
            paper_code="101-0101-question",
            file_type="question",
            download_url_source="https://example.test/moex/nurse-114.pdf",
            category_code="101",
            source_exam_id="114030",
            subject_code="0101",
            storage_key="114/114030/101/0101/question.pdf",
        )
        ceec_single = _v2_paper(
            provider_id="ceec_gsat",
            canonical_id="ceec-gsat",
            canonical_name="CEEC GSAT",
            year_roc=115,
            exam_name_raw="115 CEEC GSAT",
            category_raw="GSAT",
            subject_name_raw="General Subject",
            paper_code="101-0101-question",
            file_type="question",
            download_url_source="https://example.test/ceec_gsat/ceec-gsat-115.pdf",
            category_code="101",
            source_exam_id="gsat-115-guozong",
            subject_code="0101",
            storage_key="115/gsat-115-guozong/101/0101/question.pdf",
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            for provider_id, paper in (("moex", nurse_latest), ("moex", nurse_prior), ("ceec_gsat", ceec_single)):
                mirror_path = provider_paths(root, provider_id).mirror_dir / paper.storage_key
                mirror_path.parent.mkdir(parents=True, exist_ok=True)
                mirror_path.write_bytes(b"%PDF-1.7 demo")

            write_provider_state(
                provider_paths(root, "moex"),
                raw_pages=[],
                normalized=NormalizedCatalog(papers=[nurse_latest, nurse_prior], review_queue=[]),
                aliases=[],
                failures=[],
                manifest=None,
            )
            write_provider_state(
                provider_paths(root, "ceec_gsat"),
                raw_pages=[],
                normalized=NormalizedCatalog(papers=[ceec_single], review_queue=[]),
                aliases=[],
                failures=[],
                manifest=None,
            )

            normalized, bundles = publish_site(root, site_id="default", repository="example/repo")

            self.assertEqual({paper.canonical_id for paper in normalized.papers}, {"nurse", "ceec-gsat"})
            self.assertEqual([bundle.canonical_id for bundle in bundles], ["nurse"])
            self.assertEqual(
                [paper.download_url_bundle for paper in normalized.papers if paper.canonical_id == "ceec-gsat"],
                [""],
            )

            site = site_paths(root, "default")
            site_bundles = json.loads(site.bundles_path.read_text(encoding="utf-8"))
            self.assertEqual([bundle["canonical_id"] for bundle in site_bundles["bundles"]], ["nurse"])

            frontend_bundles = json.loads(site.frontend_bundles_path.read_text(encoding="utf-8"))
            self.assertEqual([bundle["id"] for bundle in frontend_bundles["bundles"]], ["moex-professional-combined-not-applicable-nurse"])

            release_assets = json.loads(site.release_assets_path.read_text(encoding="utf-8"))
            self.assertEqual([asset["asset_name"] for asset in release_assets["assets"]], ["moex-professional-combined-not-applicable-nurse--a167604af56e.zip"])

    def test_publish_site_preserves_unaffected_existing_site_bundles_when_affected_ids_are_provided(self) -> None:
        nurse_latest = _v2_paper(
            provider_id="moex",
            canonical_id="nurse",
            canonical_name="Nurse",
            year_roc=115,
            exam_name_raw="115 Nurse Exam",
            category_raw="Nurse",
            subject_name_raw="Medical Basics",
            paper_code="101-0101-question",
            file_type="question",
            download_url_source="https://example.test/moex/nurse-115.pdf",
            category_code="101",
            source_exam_id="115030",
            subject_code="0101",
            storage_key="115/115030/101/0101/question.pdf",
        )
        nurse_prior = _v2_paper(
            provider_id="moex",
            canonical_id="nurse",
            canonical_name="Nurse",
            year_roc=114,
            exam_name_raw="114 Nurse Exam",
            category_raw="Nurse",
            subject_name_raw="Medical Basics",
            paper_code="101-0101-question",
            file_type="question",
            download_url_source="https://example.test/moex/nurse-114.pdf",
            category_code="101",
            source_exam_id="114030",
            subject_code="0101",
            storage_key="114/114030/101/0101/question.pdf",
        )
        doctor_latest = _v2_paper(
            provider_id="moex",
            canonical_id="doctor",
            canonical_name="Doctor",
            year_roc=115,
            exam_name_raw="115 Doctor Exam",
            category_raw="Doctor",
            subject_name_raw="Clinical Basics",
            paper_code="201-0201-question",
            file_type="question",
            download_url_source="https://example.test/moex/doctor-115.pdf",
            category_code="201",
            source_exam_id="115040",
            subject_code="0201",
            storage_key="115/115040/201/0201/question.pdf",
        )
        doctor_prior = _v2_paper(
            provider_id="moex",
            canonical_id="doctor",
            canonical_name="Doctor",
            year_roc=114,
            exam_name_raw="114 Doctor Exam",
            category_raw="Doctor",
            subject_name_raw="Clinical Basics",
            paper_code="201-0201-question",
            file_type="question",
            download_url_source="https://example.test/moex/doctor-114.pdf",
            category_code="201",
            source_exam_id="114040",
            subject_code="0201",
            storage_key="114/114040/201/0201/question.pdf",
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            for paper in (doctor_latest, doctor_prior):
                mirror_path = provider_paths(root, "moex").mirror_dir / paper.storage_key
                mirror_path.parent.mkdir(parents=True, exist_ok=True)
                mirror_path.write_bytes(b"%PDF-1.7 demo")

            write_provider_state(
                provider_paths(root, "moex"),
                raw_pages=[],
                normalized=NormalizedCatalog(
                    papers=[nurse_latest, nurse_prior, doctor_latest, doctor_prior],
                    review_queue=[],
                ),
                aliases=[],
                failures=[],
                manifest=None,
            )
            write_provider_state(
                provider_paths(root, "ceec_gsat"),
                raw_pages=[],
                normalized=NormalizedCatalog(papers=[], review_queue=[]),
                aliases=[],
                failures=[],
                manifest=None,
            )
            nurse_bundle = BundleAsset(
                canonical_id="nurse",
                canonical_name="Nurse",
                years=[115, 114],
                file_count=2,
                storage_key="bundles/sites/default/moex-professional-combined-not-applicable-nurse--a167604af56e.zip",
                asset_name="moex-professional-combined-not-applicable-nurse--a167604af56e.zip",
                release_tag="default-bundles-v2-001",
                download_url="https://github.com/example/repo/releases/download/default-bundles-v2-001/moex-professional-combined-not-applicable-nurse--a167604af56e.zip",
                schema_version=2,
                bundle_id=nurse_latest.bundle_id,
                catalog_version=nurse_latest.catalog_version,
                domain_id=nurse_latest.domain_id,
                exam_family_id=nurse_latest.exam_family_id,
                exam_series_id=nurse_latest.exam_series_id,
                level_id=nurse_latest.level_id,
                track_id=nurse_latest.track_id,
                stage_id=nurse_latest.stage_id,
                checksum="nurse-existing",
            )
            write_site_state(
                site_paths(root, "default"),
                bundles=[nurse_bundle],
                frontend_bundles=[
                    {
                        "id": nurse_latest.bundle_id,
                        "name": "Nurse",
                        "years": [115, 114],
                        "fileCount": 2,
                        "url": nurse_bundle.download_url,
                    }
                ],
            )

            normalized, bundles = publish_site(
                root,
                site_id="default",
                repository="example/repo",
                affected_canonical_ids={"doctor"},
            )

            self.assertEqual({paper.canonical_id for paper in normalized.papers}, {"nurse", "doctor"})
            self.assertEqual({bundle.canonical_id for bundle in bundles}, {"nurse", "doctor"})
            preserved_nurse = next(bundle for bundle in bundles if bundle.canonical_id == "nurse")
            self.assertEqual(preserved_nurse.checksum, "nurse-existing")
            self.assertEqual(preserved_nurse.storage_key, "bundles/sites/default/moex-professional-combined-not-applicable-nurse--a167604af56e.zip")
            self.assertFalse((site_paths(root, "default").bundle_dir / "moex-professional-combined-not-applicable-nurse--a167604af56e.zip").exists())
            self.assertTrue((site_paths(root, "default").bundle_dir / "moex-professional-combined-not-applicable-doctor--71f8362bd185.zip").exists())

    def test_publish_site_rejects_partial_publish_without_existing_site_bundle_metadata(self) -> None:
        doctor_latest = NormalizedPaper(
            provider_id="moex",
            canonical_id="doctor",
            canonical_name="Doctor",
            year_roc=115,
            exam_name_raw="115 Doctor Exam",
            category_raw="Doctor",
            subject_name_raw="Clinical Basics",
            paper_code="201-0201-question",
            file_type="question",
            download_url_source="https://example.test/moex/doctor-115.pdf",
            category_code="201",
            source_exam_id="115040",
            subject_code="0201",
            storage_key="115/115040/201/0201/question.pdf",
        )
        doctor_prior = NormalizedPaper(
            provider_id="moex",
            canonical_id="doctor",
            canonical_name="Doctor",
            year_roc=114,
            exam_name_raw="114 Doctor Exam",
            category_raw="Doctor",
            subject_name_raw="Clinical Basics",
            paper_code="201-0201-question",
            file_type="question",
            download_url_source="https://example.test/moex/doctor-114.pdf",
            category_code="201",
            source_exam_id="114040",
            subject_code="0201",
            storage_key="114/114040/201/0201/question.pdf",
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            for paper in (doctor_latest, doctor_prior):
                mirror_path = provider_paths(root, "moex").mirror_dir / paper.storage_key
                mirror_path.parent.mkdir(parents=True, exist_ok=True)
                mirror_path.write_bytes(b"%PDF-1.7 demo")

            write_provider_state(
                provider_paths(root, "moex"),
                raw_pages=[],
                normalized=NormalizedCatalog(papers=[doctor_latest, doctor_prior], review_queue=[]),
                aliases=[],
                failures=[],
                manifest=None,
            )
            write_provider_state(
                provider_paths(root, "ceec_gsat"),
                raw_pages=[],
                normalized=NormalizedCatalog(papers=[], review_queue=[]),
                aliases=[],
                failures=[],
                manifest=None,
            )

            with self.assertRaisesRegex(ValueError, "Partial publish requires existing site bundle metadata"):
                publish_site(
                    root,
                    site_id="default",
                    repository="example/repo",
                    affected_canonical_ids={"doctor"},
                )

            self.assertFalse(site_paths(root, "default").bundles_path.exists())

    def test_publish_site_fails_closed_when_bundle_build_has_failures(self) -> None:
        broken_latest = NormalizedPaper(
            provider_id="moex",
            canonical_id="nurse",
            canonical_name="Nurse",
            year_roc=115,
            exam_name_raw="115 Nurse Exam",
            category_raw="Nurse",
            subject_name_raw="Medical Basics",
            paper_code="101-0101-question",
            file_type="question",
            download_url_source="https://example.test/moex/nurse.pdf",
            category_code="101",
            source_exam_id="115030",
            subject_code="0101",
            storage_key="115/115030/101/0101/question.pdf",
        )
        broken_prior = NormalizedPaper(
            provider_id="moex",
            canonical_id="nurse",
            canonical_name="Nurse",
            year_roc=114,
            exam_name_raw="114 Nurse Exam",
            category_raw="Nurse",
            subject_name_raw="Medical Basics",
            paper_code="101-0101-question",
            file_type="question",
            download_url_source="https://example.test/moex/nurse-114.pdf",
            category_code="101",
            source_exam_id="114030",
            subject_code="0101",
            storage_key="114/114030/101/0101/question.pdf",
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_provider_state(
                provider_paths(root, "moex"),
                raw_pages=[],
                normalized=NormalizedCatalog(papers=[broken_latest, broken_prior], review_queue=[]),
                aliases=[],
                failures=[],
                manifest=None,
            )
            write_provider_state(
                provider_paths(root, "ceec_gsat"),
                raw_pages=[],
                normalized=NormalizedCatalog(papers=[], review_queue=[]),
                aliases=[],
                failures=[],
                manifest=None,
            )

            with self.assertRaises(ValueError):
                publish_site(root, site_id="default", repository="example/repo")

            self.assertFalse(site_paths(root, "default").bundles_path.exists())
            self.assertFalse(site_paths(root, "default").release_assets_path.exists())
            self.assertFalse((root / "data" / "bundles.json").exists())

if __name__ == "__main__":
    unittest.main()
