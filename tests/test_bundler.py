import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from dataclasses import asdict
from pathlib import Path

from jsonschema import Draft202012Validator

from app.bundler import build_bundles, public_bundle_ids
from app.models import NormalizedCatalog, NormalizedPaper


ARCHIVE_SCHEMA = Draft202012Validator(json.loads(
    (Path(__file__).resolve().parents[1] / "schemas/bundle-archive-manifest-v2.schema.json").read_text()
))


def make_paper(
    *,
    canonical_id: str,
    canonical_name: str,
    year_roc: int,
    source_exam_id: str,
    subject_code: str,
    storage_key: str,
    file_type: str = "question",
    subject_name_raw: str = "subject",
    category_raw: str = "category",
) -> NormalizedPaper:
    return NormalizedPaper(
        canonical_id=canonical_id,
        canonical_name=canonical_name,
        year_roc=year_roc,
        exam_name_raw=f"exam-{year_roc}",
        category_raw=category_raw,
        subject_name_raw=subject_name_raw,
        paper_code=f"101-{subject_code}-{file_type}",
        file_type=file_type,
        download_url_source=f"https://source.example/{source_exam_id}-{subject_code}-{file_type}.pdf",
        category_code="101",
        source_exam_id=source_exam_id,
        subject_code=subject_code,
        download_url_mirror="",
        download_url_bundle="",
        storage_key=storage_key,
        checksum=f"sum-{year_roc}-{subject_code}-{file_type}",
    )


class BundlerTests(unittest.TestCase):
    def test_build_bundles_groups_multiple_years_under_one_stable_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            mirror_dir = root / "mirror"
            bundles_dir = root / "bundles"
            (mirror_dir / "115/115030/101/0101").mkdir(parents=True)
            (mirror_dir / "114/114030/101/0101").mkdir(parents=True)
            (mirror_dir / "115/115030/101/0101/question.pdf").write_bytes(b"%PDF-1.7 latest")
            (mirror_dir / "114/114030/101/0101/question.pdf").write_bytes(b"%PDF-1.7 prior")

            catalog = NormalizedCatalog(
                papers=[
                    make_paper(
                        canonical_id="nurse",
                        canonical_name="Nurse",
                        year_roc=115,
                        source_exam_id="115030",
                        subject_code="0101",
                        subject_name_raw="Anatomy",
                        storage_key="115/115030/101/0101/question.pdf",
                    ),
                    make_paper(
                        canonical_id="nurse",
                        canonical_name="Nurse",
                        year_roc=114,
                        source_exam_id="114030",
                        subject_code="0101",
                        subject_name_raw="Anatomy",
                        storage_key="114/114030/101/0101/question.pdf",
                    ),
                ],
                review_queue=[],
            )

            result = build_bundles(
                bundle_dir=bundles_dir,
                mirror_dir=mirror_dir,
                normalized=catalog,
                bundle_base_url="https://ignored.example",
            )

            self.assertEqual(len(result.bundles), 1)
            actual = result.bundles[0]
            self.assertEqual(actual.canonical_id, "nurse")
            self.assertEqual(actual.canonical_name, "Nurse")
            self.assertEqual(actual.years, [115, 114])
            self.assertEqual(actual.file_count, 2)
            self.assertEqual(actual.storage_key, "bundles/nurse.zip")
            self.assertEqual(actual.asset_name, "nurse.zip")
            self.assertEqual(actual.release_tag, "")
            self.assertEqual(actual.download_url, "")
            self.assertEqual(actual.legacy_asset_names, ["Nurse__nurse.zip"])

            bundle_zip = bundles_dir / "nurse.zip"
            self.assertTrue(bundle_zip.exists())
            with zipfile.ZipFile(bundle_zip) as archive:
                names = archive.namelist()
                self.assertIn("115/101_0101_Anatomy_試題.pdf", names)
                self.assertIn("114/101_0101_Anatomy_試題.pdf", names)
                manifest = json.loads(archive.read("bundle.json").decode("utf-8"))
                self.assertEqual(manifest["canonical_id"], "nurse")
                self.assertEqual(manifest["years"], [115, 114])
                ARCHIVE_SCHEMA.validate(manifest)

            self.assertTrue(all(paper.download_url_bundle == "" for paper in catalog.papers))
            self.assertEqual(result.failures, [])

    def test_build_bundles_reuses_existing_bundle_entries_and_skips_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            mirror_dir = root / "mirror"
            bundles_dir = root / "bundles"
            bundles_dir.mkdir()
            existing_bundle = bundles_dir / "nurse.zip"
            with zipfile.ZipFile(existing_bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("114/exam-old/category/0101_Anatomy/question.pdf", b"%PDF-1.7 old")
                archive.writestr("bundle.json", json.dumps({"canonical_id": "nurse"}, ensure_ascii=False))

            (mirror_dir / "115/exam-new/101/0101").mkdir(parents=True)
            (mirror_dir / "115/exam-new/101/0101/question.pdf").write_bytes(b"%PDF-1.7 new")

            catalog = NormalizedCatalog(
                papers=[
                    make_paper(
                        canonical_id="nurse",
                        canonical_name="Nurse",
                        year_roc=114,
                        source_exam_id="exam-old",
                        subject_code="0101",
                        subject_name_raw="Anatomy",
                        storage_key="114/exam-old/101/0101/question.pdf",
                    ),
                    make_paper(
                        canonical_id="nurse",
                        canonical_name="Nurse",
                        year_roc=115,
                        source_exam_id="exam-new",
                        subject_code="0101",
                        subject_name_raw="Anatomy",
                        storage_key="115/exam-new/101/0101/question.pdf",
                    ),
                    make_paper(
                        canonical_id="nurse",
                        canonical_name="Nurse",
                        year_roc=115,
                        source_exam_id="exam-new",
                        subject_code="0102",
                        subject_name_raw="missing-subject",
                        storage_key="115/exam-new/101/0102/question.pdf",
                    ),
                ],
                review_queue=[],
            )

            result = build_bundles(
                bundle_dir=bundles_dir,
                mirror_dir=mirror_dir,
                normalized=catalog,
                bundle_base_url="https://ignored.example",
            )

            self.assertEqual(len(result.bundles), 1)
            with zipfile.ZipFile(bundles_dir / "nurse.zip") as archive:
                names = archive.namelist()
                self.assertIn("114/101_0101_Anatomy_試題.pdf", names)
                self.assertIn("115/101_0101_Anatomy_試題.pdf", names)
                self.assertNotIn("115/101_0102_missing-subject_試題.pdf", names)
            self.assertEqual(len(result.failures), 1)
            self.assertEqual(result.failures[0]["paper_code"], "101-0102-question")
            self.assertEqual(catalog.papers[0].download_url_bundle, "")
            self.assertEqual(catalog.papers[1].download_url_bundle, "")
            self.assertEqual(catalog.papers[2].download_url_bundle, "")

    def test_build_bundles_preserves_migrated_canonical_alias_asset_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            bundles_dir = root / "bundles"
            bundles_dir.mkdir()
            old_canonical_id = "canonical-old-nurse"
            old_bundle = bundles_dir / f"{old_canonical_id}.zip"
            archive_entry = "114/101_0101_Anatomy_試題.pdf"
            with zipfile.ZipFile(old_bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(archive_entry, b"%PDF-1.7 migrated-old")
                archive.writestr(
                    "bundle.json",
                    json.dumps(
                        {
                            "canonical_id": old_canonical_id,
                            "canonical_name": "Old Nurse",
                            "years": [114],
                            "file_count": 1,
                            "papers": [],
                        },
                        ensure_ascii=False,
                    ),
                )

            catalog = NormalizedCatalog(
                papers=[
                    make_paper(
                        canonical_id="nurse",
                        canonical_name="Nurse",
                        year_roc=114,
                        source_exam_id="114030",
                        subject_code="0101",
                        subject_name_raw="Anatomy",
                        storage_key="114/114030/101/0101/question.pdf",
                    ),
                ],
                review_queue=[],
            )

            result = build_bundles(
                bundle_dir=bundles_dir,
                mirror_dir=root / "mirror",
                normalized=catalog,
                bundle_base_url="https://ignored.example",
                canonical_aliases={"nurse": [old_canonical_id]},
            )

            self.assertEqual(result.failures, [])
            self.assertEqual(
                result.bundles[0].legacy_asset_names,
                ["Nurse__nurse.zip", f"{old_canonical_id}.zip"],
            )
            with zipfile.ZipFile(bundles_dir / "nurse.zip") as archive:
                self.assertEqual(archive.read(archive_entry), b"%PDF-1.7 migrated-old")

    def test_build_bundles_uses_explicit_bundle_entry_instead_of_manifest_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            bundles_dir = root / "bundles"
            bundles_dir.mkdir()
            existing_bundle = bundles_dir / "nurse.zip"
            with zipfile.ZipFile(existing_bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("114/exam-old/101_0101_question.pdf", b"%PDF-1.7 first")
                archive.writestr("114/exam-old/101_0102_question.pdf", b"%PDF-1.7 second")
                archive.writestr(
                    "bundle.json",
                    json.dumps(
                        {
                            "canonical_id": "nurse",
                            "canonical_name": "Nurse",
                            "years": [114],
                            "file_count": 2,
                            "papers": [
                                {
                                    "source_exam_id": "exam-old",
                                    "category_code": "101",
                                    "subject_code": "0102",
                                    "file_type": "question",
                                    "bundle_entry": "114/exam-old/101_0102_question.pdf",
                                },
                                {
                                    "source_exam_id": "exam-old",
                                    "category_code": "101",
                                    "subject_code": "0101",
                                    "file_type": "question",
                                    "bundle_entry": "114/exam-old/101_0101_question.pdf",
                                },
                            ],
                        },
                        ensure_ascii=False,
                    ),
                )

            catalog = NormalizedCatalog(
                papers=[
                    make_paper(
                        canonical_id="nurse",
                        canonical_name="Nurse",
                        year_roc=114,
                        source_exam_id="exam-old",
                        subject_code="0101",
                        storage_key="114/exam-old/101/0101/question.pdf",
                    ),
                    make_paper(
                        canonical_id="nurse",
                        canonical_name="Nurse",
                        year_roc=114,
                        source_exam_id="exam-old",
                        subject_code="0102",
                        storage_key="114/exam-old/101/0102/question.pdf",
                    ),
                ],
                review_queue=[],
            )

            result = build_bundles(
                bundle_dir=bundles_dir,
                mirror_dir=root / "mirror",
                normalized=catalog,
                bundle_base_url="https://ignored.example",
            )

            self.assertEqual(result.failures, [])
            with zipfile.ZipFile(bundles_dir / "nurse.zip") as archive:
                self.assertEqual(archive.read("114/101_0101_subject_試題.pdf"), b"%PDF-1.7 first")
                self.assertEqual(archive.read("114/101_0102_subject_試題.pdf"), b"%PDF-1.7 second")

    def test_build_bundles_disambiguates_duplicate_arcnames(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            mirror_dir = root / "mirror"
            bundles_dir = root / "bundles"
            (mirror_dir / "86/086010/104/2001").mkdir(parents=True)
            (mirror_dir / "86/086020/104/2001").mkdir(parents=True)
            (mirror_dir / "86/086010/104/2001/question.pdf").write_bytes(b"%PDF exam1")
            (mirror_dir / "86/086020/104/2001/question.pdf").write_bytes(b"%PDF exam2")

            catalog = NormalizedCatalog(
                papers=[
                    make_paper(
                        canonical_id="marine",
                        canonical_name="Marine",
                        year_roc=86,
                        source_exam_id="086010",
                        subject_code="2001",
                        subject_name_raw="Navigation",
                        storage_key="86/086010/104/2001/question.pdf",
                    ),
                    make_paper(
                        canonical_id="marine",
                        canonical_name="Marine",
                        year_roc=86,
                        source_exam_id="086020",
                        subject_code="2001",
                        subject_name_raw="Navigation",
                        storage_key="86/086020/104/2001/question.pdf",
                    ),
                ],
                review_queue=[],
            )

            result = build_bundles(
                bundle_dir=bundles_dir,
                mirror_dir=mirror_dir,
                normalized=catalog,
                bundle_base_url="https://ignored.example",
            )

            self.assertEqual(len(result.bundles), 1)
            self.assertEqual(result.bundles[0].file_count, 2)
            with zipfile.ZipFile(bundles_dir / "marine.zip") as archive:
                names = [name for name in archive.namelist() if name != "bundle.json"]
                self.assertEqual(len(names), 2)
                self.assertEqual(len(set(names)), 2, f"Duplicate arcnames found: {names}")
                self.assertIn("86/101_2001_Navigation_試題_086010.pdf", names)
                self.assertIn("86/101_2001_Navigation_試題_086020.pdf", names)
            self.assertEqual(result.failures, [])

    def test_build_bundles_allows_single_year_for_configured_canonical_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            mirror_dir = root / "mirror"
            bundles_dir = root / "bundles"
            tii_dir = mirror_dir / "providers" / "tii_cert" / "115" / "tii-cert-aml-2026-1" / "aml" / "main"
            ceec_dir = mirror_dir / "providers" / "ceec_gsat" / "115" / "gsat-115" / "101" / "0101"
            tii_dir.mkdir(parents=True)
            ceec_dir.mkdir(parents=True)
            (tii_dir / "question.pdf").write_bytes(b"%PDF-1.7 tii")
            (ceec_dir / "question.pdf").write_bytes(b"%PDF-1.7 ceec")

            tii_paper = make_paper(
                canonical_id="tii-aml",
                canonical_name="TII AML",
                year_roc=115,
                source_exam_id="tii-cert-aml-2026-1",
                category_raw="TII AML",
                subject_code="main",
                storage_key="providers/tii_cert/115/tii-cert-aml-2026-1/aml/main/question.pdf",
            )
            tii_paper.provider_id = "tii_cert"
            ceec_paper = make_paper(
                canonical_id="ceec-gsat",
                canonical_name="CEEC GSAT",
                year_roc=115,
                source_exam_id="gsat-115",
                category_raw="GSAT",
                subject_code="0101",
                storage_key="providers/ceec_gsat/115/gsat-115/101/0101/question.pdf",
            )
            ceec_paper.provider_id = "ceec_gsat"

            result = build_bundles(
                bundle_dir=bundles_dir,
                mirror_dir=mirror_dir,
                normalized=NormalizedCatalog(papers=[tii_paper, ceec_paper], review_queue=[]),
                bundle_base_url="",
                min_years=2,
                min_years_by_canonical_prefix={"tii-": 1},
            )

            self.assertEqual([bundle.canonical_id for bundle in result.bundles], ["tii-aml"])
            self.assertEqual(
                public_bundle_ids(
                    NormalizedCatalog(papers=[tii_paper, ceec_paper], review_queue=[]),
                    min_years=2,
                    min_years_by_canonical_prefix={"tii-": 1},
                ),
                {"tii-aml"},
            )
            self.assertTrue((bundles_dir / "tii-aml.zip").exists())
            self.assertFalse((bundles_dir / "ceec-gsat.zip").exists())
            self.assertEqual(result.failures, [])

    def test_build_bundles_splits_oversized_archive_into_manifested_parts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            mirror_dir = root / "mirror"
            bundles_dir = root / "bundles"
            papers = []
            for index in range(3):
                storage_key = f"115/exam-{index}/101/010{index}/question.bin"
                source = mirror_dir / storage_key
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(os.urandom(5500))
                papers.append(
                    make_paper(
                        canonical_id="large",
                        canonical_name="Large",
                        year_roc=115,
                        source_exam_id=f"exam-{index}",
                        subject_code=f"010{index}",
                        storage_key=storage_key,
                    )
                )

            result = build_bundles(
                bundle_dir=bundles_dir,
                mirror_dir=mirror_dir,
                normalized=NormalizedCatalog(papers=papers, review_queue=[]),
                bundle_base_url="",
                max_bundle_bytes=12_000,
            )

            self.assertEqual(len(result.bundles), 3)
            self.assertEqual({bundle.bundle_id for bundle in result.bundles}, {""})
            self.assertEqual({bundle.part_count for bundle in result.bundles}, {3})
            self.assertEqual([bundle.part_index for bundle in result.bundles], [1, 2, 3])
            self.assertFalse((bundles_dir / "large.zip").exists())
            for bundle in result.bundles:
                archive_path = bundles_dir / bundle.asset_name
                self.assertLess(archive_path.stat().st_size, 12_000)
                with zipfile.ZipFile(archive_path) as archive:
                    manifest = json.loads(archive.read("bundle.json").decode("utf-8"))
                    self.assertEqual(manifest["part_count"], 3)
                    self.assertEqual(manifest["part_index"], bundle.part_index)
                    self.assertEqual(manifest["file_count"], 1)
                    ARCHIVE_SCHEMA.validate(manifest)
            self.assertEqual(result.failures, [])

    def test_oversized_entry_preserves_existing_assets_before_failing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            mirror_dir = root / "mirror"
            bundles_dir = root / "bundles"
            bundles_dir.mkdir()
            existing_bundle = bundles_dir / "large.zip"
            existing_bundle.write_bytes(b"existing-bundle")
            stale_part = bundles_dir / "large--part-01-of-02.zip"
            stale_part.write_bytes(b"existing-part")

            paper = make_paper(
                canonical_id="large",
                canonical_name="Large",
                year_roc=115,
                source_exam_id="exam-oversized",
                subject_code="0100",
                storage_key="115/exam-oversized/101/0100/question.bin",
            )
            source = mirror_dir / paper.storage_key
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"oversized-entry")

            with self.assertRaisesRegex(ValueError, "exceeds the 10-byte multipart target"):
                build_bundles(
                    bundle_dir=bundles_dir,
                    mirror_dir=mirror_dir,
                    normalized=NormalizedCatalog(papers=[paper], review_queue=[]),
                    bundle_base_url="",
                    max_bundle_bytes=10,
                )

            self.assertEqual(existing_bundle.read_bytes(), b"existing-bundle")
            self.assertEqual(stale_part.read_bytes(), b"existing-part")

    def test_structured_asset_names_remain_unique_after_readable_prefix_truncation(self) -> None:
        from app.bundler import _bundle_asset_name

        prefix = "moex-" + "x" * 120
        first = prefix + "-grade-3"
        second = prefix + "-grade-4"

        first_name = _bundle_asset_name(first, structured=True)
        second_name = _bundle_asset_name(second, structured=True)

        self.assertNotEqual(first_name, second_name)
        self.assertLessEqual(len(first_name), 255)
        self.assertLessEqual(len(second_name), 255)


    def test_v2_bundle_id_splits_same_legacy_track_into_separate_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            mirror_dir = root / "mirror"
            bundles_dir = root / "bundles"
            high = make_paper(
                canonical_id="一般行政",
                canonical_name="一般行政",
                year_roc=115,
                source_exam_id="high-115",
                subject_code="0101",
                storage_key="115/high-115/101/0101/question.pdf",
            )
            ordinary = make_paper(
                canonical_id="一般行政",
                canonical_name="一般行政",
                year_roc=115,
                source_exam_id="ordinary-115",
                subject_code="0101",
                storage_key="115/ordinary-115/101/0101/question.pdf",
            )
            for paper, bundle_id, bundle_name, series_id, level_id in (
                (high, "moex-civil-high-grade-3-general-administration", "高等考試｜三等｜一般行政", "civil-high", "grade-3"),
                (ordinary, "moex-civil-ordinary-ordinary-general-administration", "普通考試｜普通｜一般行政", "civil-ordinary", "ordinary"),
            ):
                paper.provider_id = "moex"
                paper.schema_version = 2
                paper.catalog_version = "exam-identity-v2"
                paper.domain_id = "civil-service"
                paper.exam_family_id = "civil-service-exam"
                paper.exam_series_id = series_id
                paper.level_id = level_id
                paper.track_id = "general-administration"
                paper.stage_id = "not-applicable"
                paper.bundle_id = bundle_id
                paper.bundle_name = bundle_name
                paper.bundle_policy_id = "default-bundle-policy-v2"
                paper.classification_confidence = "high"
                paper.classification_reason = "test fixture"
                paper.exam_class = "公職考試"
                paper.exam_subclass = "公職／公務人員"
                source = mirror_dir / paper.storage_key
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(b"%PDF-1.7 identity")

            result = build_bundles(
                bundle_dir=bundles_dir,
                mirror_dir=mirror_dir,
                normalized=NormalizedCatalog(papers=[high, ordinary], review_queue=[]),
                bundle_base_url="",
            )

            self.assertEqual(len(result.bundles), 2)
            self.assertEqual({bundle.bundle_id for bundle in result.bundles}, {high.bundle_id, ordinary.bundle_id})
            self.assertEqual({bundle.canonical_id for bundle in result.bundles}, {"一般行政"})
            for bundle in result.bundles:
                with zipfile.ZipFile(bundles_dir / bundle.asset_name) as archive:
                    manifest = json.loads(archive.read("bundle.json").decode("utf-8"))
                    self.assertEqual(manifest["bundle_id"], bundle.bundle_id)
                    self.assertEqual(manifest["exam_series_id"], bundle.exam_series_id)
                    self.assertEqual(manifest["level_id"], bundle.level_id)

    def test_rebuilding_unchanged_sources_reproduces_identical_archive_bytes(self) -> None:
        # The recorded checksum is the download contract published to users and
        # the signal the release upload uses to decide what to re-publish, so a
        # rebuild that changes nothing must not change the archive bytes - not
        # across time, and not between a workstation and CI.
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            mirror_dir = root / "mirror"
            (mirror_dir / "115/115030/101/0101").mkdir(parents=True)
            (mirror_dir / "114/114030/101/0101").mkdir(parents=True)
            source_paths = [
                mirror_dir / "115/115030/101/0101/question.pdf",
                mirror_dir / "114/114030/101/0101/question.pdf",
            ]
            source_paths[0].write_bytes(b"%PDF-1.7 latest")
            source_paths[1].write_bytes(b"%PDF-1.7 prior")

            catalog = NormalizedCatalog(
                papers=[
                    make_paper(
                        canonical_id="nurse",
                        canonical_name="Nurse",
                        year_roc=115,
                        source_exam_id="115030",
                        subject_code="0101",
                        storage_key="115/115030/101/0101/question.pdf",
                    ),
                    make_paper(
                        canonical_id="nurse",
                        canonical_name="Nurse",
                        year_roc=114,
                        source_exam_id="114030",
                        subject_code="0101",
                        storage_key="114/114030/101/0101/question.pdf",
                    ),
                ],
                review_queue=[],
            )

            def build(bundle_dir: Path) -> dict[str, str]:
                result = build_bundles(
                    bundle_dir=bundle_dir,
                    mirror_dir=mirror_dir,
                    normalized=catalog,
                    bundle_base_url="",
                )
                self.assertEqual(result.failures, [])
                return {bundle.asset_name: bundle.checksum for bundle in result.bundles}

            first = build(root / "bundles-first")
            self.assertTrue(first)

            # A re-mirrored file keeps its bytes but gets a fresh mtime, and a
            # different umask gives it different permission bits.
            for source_path in source_paths:
                os.utime(source_path, (1_609_459_200, 1_609_459_200))
                source_path.chmod(0o600)

            second = build(root / "bundles-second")
            self.assertEqual(first, second)

            for asset_name, checksum in first.items():
                first_bytes = (root / "bundles-first" / asset_name).read_bytes()
                second_bytes = (root / "bundles-second" / asset_name).read_bytes()
                self.assertEqual(first_bytes, second_bytes)
                self.assertEqual(hashlib.sha256(second_bytes).hexdigest(), checksum)

    def test_unchanged_single_part_bundle_is_reused_until_manifest_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            mirror_dir = root / "mirror"
            source = mirror_dir / "115/exam-115/101/0101/question.pdf"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"%PDF-1.7 question")
            paper = make_paper(
                canonical_id="nurse", canonical_name="Nurse", year_roc=115,
                source_exam_id="exam-115", subject_code="0101",
                storage_key="115/exam-115/101/0101/question.pdf",
            )
            catalog = NormalizedCatalog(papers=[paper], review_queue=[])
            bundle_dir = root / "bundles"

            first = build_bundles(bundle_dir, mirror_dir, catalog, "")
            self.assertEqual(first.failures, [])
            archive_path = bundle_dir / first.bundles[0].asset_name
            original_bytes = archive_path.read_bytes()
            marker = 946_684_800_000_000_000
            os.utime(archive_path, ns=(marker, marker))

            second = build_bundles(bundle_dir, mirror_dir, catalog, "")
            self.assertEqual(second.failures, [])
            self.assertEqual(second.bundles[0].checksum, first.bundles[0].checksum)
            self.assertEqual(archive_path.read_bytes(), original_bytes)
            self.assertEqual(archive_path.stat().st_mtime_ns, marker)

            paper.classification_reason = "reclassified"
            third = build_bundles(bundle_dir, mirror_dir, catalog, "")
            self.assertEqual(third.failures, [])
            self.assertNotEqual(archive_path.stat().st_mtime_ns, marker)
            self.assertNotEqual(third.bundles[0].checksum, first.bundles[0].checksum)

    def test_old_full_manifest_reuses_archive_after_provider_only_metadata_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source = root / "mirror/question.pdf"
            source.parent.mkdir()
            payload = b"%PDF-1.7 old release payload"
            source.write_bytes(payload)
            paper = make_paper(
                canonical_id="nurse", canonical_name="Nurse", year_roc=115,
                source_exam_id="exam-115", subject_code="0101", storage_key="question.pdf",
            )
            paper.checksum = hashlib.sha256(payload).hexdigest()
            catalog = NormalizedCatalog(papers=[paper], review_queue=[])
            first = build_bundles(root / "bundles", root / "mirror", catalog, "")
            archive_path = root / "bundles" / first.bundles[0].asset_name
            with zipfile.ZipFile(archive_path) as archive:
                manifest = json.loads(archive.read("bundle.json"))
                entry = manifest["papers"][0]["bundle_entry"]
            manifest.pop("manifest_version")
            manifest["papers"] = [{**asdict(paper), "bundle_entry": entry}]
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(entry, payload)
                archive.writestr("bundle.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            old_bytes = archive_path.read_bytes()
            marker = 946_684_800_000_000_000
            os.utime(archive_path, ns=(marker, marker))
            source.unlink()
            paper.download_url_source = "https://source.example/new-location.pdf"
            paper.download_url_mirror = "https://mirror.example/question.pdf"

            reused = build_bundles(root / "bundles", root / "mirror", catalog, "")

            self.assertEqual(reused.failures, [])
            self.assertEqual(archive_path.read_bytes(), old_bytes)
            self.assertEqual(archive_path.stat().st_mtime_ns, marker)
            self.assertEqual(reused.bundles[0].checksum, hashlib.sha256(old_bytes).hexdigest())

    def test_manifest_paper_order_does_not_force_rewriting_an_unchanged_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            mirror = root / "mirror"
            mirror.mkdir()
            papers = []
            for index in range(2):
                source = mirror / f"{index}.pdf"
                source.write_bytes(f"%PDF-1.7 paper {index}".encode())
                paper = make_paper(
                    canonical_id="nurse", canonical_name="Nurse", year_roc=115,
                    source_exam_id="exam-115", subject_code=str(index), storage_key=source.name,
                )
                paper.checksum = hashlib.sha256(source.read_bytes()).hexdigest()
                papers.append(paper)
            catalog = NormalizedCatalog(papers=papers, review_queue=[])
            first = build_bundles(root / "bundles", mirror, catalog, "")
            path = root / "bundles" / first.bundles[0].asset_name
            with zipfile.ZipFile(path) as archive:
                manifest = json.loads(archive.read("bundle.json"))
                payloads = {row["bundle_entry"]: archive.read(row["bundle_entry"]) for row in manifest["papers"]}
            manifest["papers"].reverse()
            with zipfile.ZipFile(path, "w") as archive:
                for name, payload in payloads.items():
                    archive.writestr(name, payload)
                archive.writestr("bundle.json", json.dumps(manifest))
            old_bytes = path.read_bytes()
            marker = 946_684_800_000_000_000
            os.utime(path, ns=(marker, marker))
            for source in mirror.iterdir():
                source.unlink()

            reused = build_bundles(root / "bundles", mirror, catalog, "")

            self.assertEqual(reused.failures, [])
            self.assertEqual(path.stat().st_mtime_ns, marker)
            self.assertEqual(path.read_bytes(), old_bytes)

    def test_compact_manifest_recovers_payload_after_entry_name_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source = root / "mirror/question.pdf"
            source.parent.mkdir()
            payload = b"%PDF-1.7 retained paper"
            source.write_bytes(payload)
            paper = make_paper(
                canonical_id="nurse", canonical_name="Nurse", year_roc=115,
                source_exam_id="exam-115", subject_code="0101", storage_key="question.pdf",
                subject_name_raw="Old subject",
            )
            paper.checksum = hashlib.sha256(payload).hexdigest()
            catalog = NormalizedCatalog(papers=[paper], review_queue=[])
            first = build_bundles(root / "bundles", root / "mirror", catalog, "")
            source.unlink()
            paper.subject_name_raw = "Reviewed subject"

            updated = build_bundles(root / "bundles", root / "mirror", catalog, "")

            self.assertEqual(updated.failures, [])
            self.assertNotEqual(first.bundles[0].checksum, updated.bundles[0].checksum)
            with zipfile.ZipFile(root / "bundles" / updated.bundles[0].asset_name) as archive:
                manifest = json.loads(archive.read("bundle.json"))
                ARCHIVE_SCHEMA.validate(manifest)
                row = manifest["papers"][0]
                self.assertIn("Reviewed subject", row["bundle_entry"])
                self.assertEqual(archive.read(row["bundle_entry"]), payload)

    def test_changed_paper_checksum_rebuilds_compact_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source = root / "mirror/question.pdf"
            source.parent.mkdir()
            source.write_bytes(b"%PDF-1.7 first")
            paper = make_paper(
                canonical_id="nurse", canonical_name="Nurse", year_roc=115,
                source_exam_id="exam-115", subject_code="0101", storage_key="question.pdf",
            )
            paper.checksum = hashlib.sha256(source.read_bytes()).hexdigest()
            catalog = NormalizedCatalog(papers=[paper], review_queue=[])
            first = build_bundles(root / "bundles", root / "mirror", catalog, "")
            source.write_bytes(b"%PDF-1.7 corrected")
            paper.checksum = hashlib.sha256(source.read_bytes()).hexdigest()

            updated = build_bundles(root / "bundles", root / "mirror", catalog, "")

            self.assertEqual(updated.failures, [])
            self.assertNotEqual(first.bundles[0].checksum, updated.bundles[0].checksum)
            with zipfile.ZipFile(root / "bundles" / updated.bundles[0].asset_name) as archive:
                manifest = json.loads(archive.read("bundle.json"))
                ARCHIVE_SCHEMA.validate(manifest)
                row = manifest["papers"][0]
                self.assertEqual(row["checksum"], paper.checksum)
                self.assertEqual(archive.read(row["bundle_entry"]), source.read_bytes())

    def test_compressed_payloads_are_stored_while_text_and_manifest_are_deflated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            mirror = root / "mirror"
            mirror.mkdir()
            papers = []
            payloads = {}
            for index, suffix in enumerate((".pdf", ".zip", ".rar", ".mp3", ".jpg", ".PNG", ".docx", ".txt")):
                source = mirror / f"{index}{suffix}"
                payload = ("可壓縮的文字內容" * 1000).encode() if suffix == ".txt" else os.urandom(6000)
                source.write_bytes(payload)
                paper = make_paper(
                    canonical_id="mixed", canonical_name="Mixed", year_roc=115,
                    source_exam_id="exam-115", subject_code=str(index), storage_key=source.name,
                )
                paper.checksum = hashlib.sha256(payload).hexdigest()
                papers.append(paper)
                payloads[str(index)] = payload
            result = build_bundles(root / "bundles", mirror, NormalizedCatalog(papers=papers, review_queue=[]), "")
            self.assertEqual(result.failures, [])
            with zipfile.ZipFile(root / "bundles" / result.bundles[0].asset_name) as archive:
                manifest = json.loads(archive.read("bundle.json"))
                ARCHIVE_SCHEMA.validate(manifest)
                self.assertEqual(archive.getinfo("bundle.json").compress_type, zipfile.ZIP_DEFLATED)
                for row in manifest["papers"]:
                    name = row["bundle_entry"]
                    info = archive.getinfo(name)
                    self.assertEqual(archive.read(name), payloads[row["subject_code"]])
                    if name.endswith(".txt"):
                        self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED)
                        self.assertLess(info.compress_size, info.file_size)
                    else:
                        self.assertEqual(info.compress_type, zipfile.ZIP_STORED)

    def test_missing_mirror_entry_is_checked_before_reusing_a_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            mirror_dir = root / "mirror"
            source = mirror_dir / "115/exam-115/101/0101/question.pdf"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"%PDF-1.7 question")
            paper = make_paper(
                canonical_id="nurse", canonical_name="Nurse", year_roc=115,
                source_exam_id="exam-115", subject_code="0101",
                storage_key="115/exam-115/101/0101/question.pdf",
            )
            catalog = NormalizedCatalog(papers=[paper], review_queue=[])
            bundle_dir = root / "bundles"
            first = build_bundles(bundle_dir, mirror_dir, catalog, "")
            archive_path = bundle_dir / first.bundles[0].asset_name
            source.unlink()
            marker = 946_684_800_000_000_000
            os.utime(archive_path, ns=(marker, marker))

            reused = build_bundles(bundle_dir, mirror_dir, catalog, "")
            self.assertEqual(reused.failures, [])
            self.assertEqual(archive_path.stat().st_mtime_ns, marker)

            with zipfile.ZipFile(archive_path) as archive:
                entry_name = next(name for name in archive.namelist() if name != "bundle.json")
                entry_bytes = archive.read(entry_name)
                manifest_bytes = archive.read("bundle.json")
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr(entry_name, entry_bytes)
                archive.writestr("bundle.json", manifest_bytes)
            with zipfile.ZipFile(archive_path) as archive:
                entry_offset = archive.getinfo(entry_name).header_offset
            with archive_path.open("r+b") as archive_file:
                archive_file.seek(entry_offset + 26)
                name_length = int.from_bytes(archive_file.read(2), "little")
                extra_length = int.from_bytes(archive_file.read(2), "little")
                archive_file.seek(entry_offset + 30 + name_length + extra_length)
                first_byte = archive_file.read(1)
                archive_file.seek(-1, os.SEEK_CUR)
                archive_file.write(bytes([first_byte[0] ^ 1]))

            rebuilt = build_bundles(bundle_dir, mirror_dir, catalog, "")
            self.assertEqual(len(rebuilt.failures), 1)
            self.assertEqual(rebuilt.failures[0].stage, "bundle")
            self.assertFalse(archive_path.exists())


if __name__ == "__main__":
    unittest.main()
