import argparse
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from app import cli
from app.manifest import SourceManifest, write_source_manifest
from app.cli import _download_affected_bundles, build_parser, command_discover, command_repair_failures, command_sync, main, run_probe_latest, run_sync_targeted
from app.providers.base import DownloadedFile, ResponseMetadata
from app.providers.moex.client import make_result_url
from app.models import AliasRule, BundleAsset, ExamOption, NormalizedCatalog, NormalizedPaper, ParsedPaper, SourceExamPage, SyncFailure
from app.paths import provider_paths, site_paths
from app.publisher import write_data_files, write_provider_state
from app.state import load_provider_state


def _paper(
    provider_id: str,
    canonical_id: str,
    *,
    year_roc: int = 115,
    source_exam_id: str | None = None,
) -> NormalizedPaper:
    canonical_name = "Nurse" if canonical_id == "nurse" else "CEEC GSAT"
    if source_exam_id is None:
        source_exam_id = f"{year_roc}030" if canonical_id == "nurse" else f"gsat-{year_roc}-guozong"
    category_raw = "Nurse" if canonical_id == "nurse" else "GSAT"
    return NormalizedPaper(
        provider_id=provider_id,
        canonical_id=canonical_id,
        canonical_name=canonical_name,
        year_roc=year_roc,
        exam_name_raw=f"{year_roc} {canonical_name} Exam",
        category_raw=category_raw,
        subject_name_raw="Subject",
        paper_code="101-0101-question",
        file_type="question",
        download_url_source=f"https://example.test/{provider_id}/{canonical_id}.pdf",
        category_code="101",
        source_exam_id=source_exam_id,
        subject_code="0101",
        storage_key=f"providers/{provider_id}/{year_roc}/{source_exam_id}/101/0101/question.pdf",
        checksum=f"{provider_id}-{canonical_id}-{year_roc}",
    )


class CliParserCleanupTests(unittest.TestCase):
    def test_removed_build_site_command_is_rejected(self) -> None:
        parser = build_parser()

        with self.assertRaises(SystemExit):
            parser.parse_args(["build-site"])

    def test_removed_sync_lootlabs_command_is_rejected(self) -> None:
        parser = build_parser()

        with self.assertRaises(SystemExit):
            parser.parse_args(["sync-lootlabs"])

    def test_sync_incremental_parser_rejects_legacy_site_dir_flag(self) -> None:
        parser = build_parser()

        with self.assertRaises(SystemExit):
            parser.parse_args(["sync-incremental", "--site-dir", "site"])


class CliCommandTests(unittest.TestCase):

    def test_sync_incremental_years_flag_is_a_window_size(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["sync-incremental", "--years", "3"])
        self.assertEqual(args.year_window, 3)

    def test_parser_accepts_mirror_maintenance_commands(self) -> None:
        parser = build_parser()

        dedupe_args = parser.parse_args(["dedupe-mirror", "--mirror-dir", "mirror", "--apply"])
        prune_args = parser.parse_args(["prune-orphaned-mirror", "--provider", "hakka_cert", "--apply"])
        sync_args = parser.parse_args(["sync-full", "--provider", "hakka_cert", "--prune-orphaned-mirror"])

        self.assertEqual(dedupe_args.mirror_dir, Path("mirror"))
        self.assertTrue(dedupe_args.apply)
        self.assertEqual(prune_args.provider, "hakka_cert")
        self.assertTrue(prune_args.apply)
        self.assertTrue(sync_args.prune_orphaned_mirror)

    @patch("app.cli.sync_exam_pages")
    def test_command_sync_incremental_limits_to_requested_source_exam_ids(self, sync_exam_pages_mock) -> None:
        class SelectiveClient:
            provider_id = "moex"

            def discover_available_years(self) -> list[int]:
                return [2026]

            def discover_exams(self, year_ad: int) -> list[ExamOption]:
                return [
                    ExamOption(code="115030", year_ad=year_ad, year_roc=115, label="Exam 115030"),
                    ExamOption(code="115040", year_ad=year_ad, year_roc=115, label="Exam 115040"),
                ]

        sync_exam_pages_mock.return_value = ([], NormalizedCatalog(papers=[], review_queue=[]), [])
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            data_dir = root / "data"
            data_dir.mkdir()
            (data_dir / "aliases.json").write_text('{"rules": []}', encoding="utf-8")
            args = build_parser().parse_args(
                [
                    "sync-incremental",
                    "--years",
                    "1",
                    "--provider",
                    "moex",
                    "--source-exam-id",
                    "115040",
                    "--data-dir",
                    str(data_dir),
                    "--mirror-dir",
                    str(root / "mirror"),
                    "--aliases",
                    str(data_dir / "aliases.json"),
                ]
            )

            self.assertEqual(command_sync(args, client=SelectiveClient()), 0)

        self.assertEqual(sync_exam_pages_mock.call_args.kwargs["exam_codes"], [("115040", 2026)])

    def test_parser_accepts_provider_and_site_for_sync_commands(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["sync-full", "--provider", "moex", "--site-id", "default"])

        self.assertEqual(args.provider, "moex")
        self.assertEqual(args.site_id, "default")

    def test_parser_accepts_publish_site_command(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["publish-site", "--site-id", "default", "--publish-plan", ".tmp/site-publish-plan.json"])

        self.assertEqual(args.site_id, "default")
        self.assertEqual(args.repository, "example/repo")
        self.assertEqual(args.publish_plan, Path(".tmp/site-publish-plan.json"))

    def test_discover_can_write_manifest_without_clobbering_probe_fields(self) -> None:
        class DiscoveryClient:
            provider_id = "moex"

            def discover_available_years(self) -> list[int]:
                return [2025, 2024]

            def discover_exams(self, year_ad: int) -> list[ExamOption]:
                year_roc = year_ad - 1911
                return [
                    ExamOption(
                        code=f"{year_roc}010",
                        year_ad=year_ad,
                        year_roc=year_roc,
                        label=f"Official {year_ad}",
                    )
                ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "source-manifest.json"
            write_source_manifest(
                path,
                SourceManifest(
                    provider_id="moex",
                    years={
                        "2025": {
                            "head_content_length": 123,
                            "exam_codes": ["114010", "old"],
                        }
                    },
                    exams={"114010": {"paper_count": 7}, "old": {"paper_count": 1}},
                ),
            )
            args = build_parser().parse_args(
                [
                    "discover",
                    "--provider",
                    "moex",
                    "--years",
                    "2025",
                    "2024",
                    "--manifest",
                    str(path),
                    "--write-manifest",
                ]
            )

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = command_discover(args, client=DiscoveryClient())

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual([row["year_ad"] for row in payload], [2025, 2024])
            manifest = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["years"]["2025"]["head_content_length"], 123)
            self.assertEqual(manifest["years"]["2025"]["exam_codes"], ["114010"])
            self.assertNotIn("old", manifest["exams"])
            self.assertEqual(manifest["exams"]["114010"]["paper_count"], 7)
            self.assertEqual(manifest["exams"]["113010"]["exam_label"], "Official 2024")
            self.assertEqual(manifest["probe_policy"]["discovery_mode"], "official-year-exam-listing")

    def test_discover_uses_discovery_only_url_model_for_non_probe_provider(self) -> None:
        class DiscoveryOnlyClient:
            provider_id = "teacher_recruit_newtaipei"

            def discover_available_years(self) -> list[int]:
                return [2026]

            def discover_exams(self, year_ad: int) -> list[ExamOption]:
                return [
                    ExamOption(
                        code="teacher-recruit-newtaipei-115-junior",
                        year_ad=year_ad,
                        year_roc=115,
                        label="115學年度新北市教師甄試_國中",
                    )
                ]

            def build_discovery_year_url(self, year_ad: int) -> str:
                return "https://official.example.test/notices"

            def build_discovery_exam_url(self, exam_code: str, year_ad: int) -> str:
                return f"https://official.example.test/notices/{exam_code}"

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "source-manifest.json"
            args = build_parser().parse_args(
                [
                    "discover",
                    "--provider",
                    "teacher_recruit_newtaipei",
                    "--years",
                    "2026",
                    "--manifest",
                    str(path),
                    "--write-manifest",
                ]
            )

            with redirect_stdout(io.StringIO()):
                exit_code = command_discover(args, client=DiscoveryOnlyClient())

            self.assertEqual(exit_code, 0)
            manifest = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["provider_id"], "teacher_recruit_newtaipei")
            self.assertEqual(manifest["years"]["2026"]["search_url"], "https://official.example.test/notices")
            self.assertEqual(
                manifest["exams"]["teacher-recruit-newtaipei-115-junior"]["result_url"],
                "https://official.example.test/notices/teacher-recruit-newtaipei-115-junior",
            )

    def test_publish_site_command_aggregates_provider_outputs_for_default_site(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            moex_provider = provider_paths(root, "moex")
            ceec_provider = provider_paths(root, "ceec_gsat")
            moex_papers = [
                _paper("moex", "nurse", year_roc=115),
                _paper("moex", "nurse", year_roc=114),
            ]
            ceec_papers = [
                _paper("ceec_gsat", "ceec-gsat", year_roc=115),
                _paper("ceec_gsat", "ceec-gsat", year_roc=114),
            ]

            for paper in [*moex_papers, *ceec_papers]:
                mirror_path = root / "mirror" / paper.storage_key
                mirror_path.parent.mkdir(parents=True, exist_ok=True)
                mirror_path.write_bytes(b"%PDF-1.7 demo")

            write_provider_state(
                moex_provider,
                raw_pages=[],
                normalized=NormalizedCatalog(papers=moex_papers, review_queue=[]),
                aliases=[],
                failures=[],
                manifest=None,
            )
            write_provider_state(
                ceec_provider,
                raw_pages=[],
                normalized=NormalizedCatalog(papers=ceec_papers, review_queue=[]),
                aliases=[],
                failures=[],
                manifest=None,
            )

            exit_code = main(
                [
                    "publish-site",
                    "--repo-root",
                    str(root),
                    "--site-id",
                    "default",
                    "--repository",
                    "example/repo",
                ]
            )

            self.assertEqual(exit_code, 0)
            bundles_payload = json.loads(site_paths(root, "default").bundles_path.read_text(encoding="utf-8"))
            self.assertEqual(
                {bundle["canonical_id"] for bundle in bundles_payload["bundles"]},
                {"nurse", "ceec-gsat"},
            )
            self.assertEqual(
                {bundle["storage_key"] for bundle in bundles_payload["bundles"]},
                {
                    "bundles/sites/default/nurse.zip",
                    "bundles/sites/default/ceec-gsat.zip",
                },
            )

    @patch("app.cli.sync_exam_pages")
    def test_non_moex_sync_plan_updates_frontend_feed(self, sync_exam_pages_mock) -> None:
        class CeecClient:
            provider_id = "ceec_gsat"

            def __init__(self) -> None:
                self.discovery_calls = 0

            def discover_available_years(self) -> list[int]:
                self.discovery_calls += 1
                if self.discovery_calls == 1:
                    raise TimeoutError("temporary listing timeout")
                return [2026]

            def discover_exams(self, year_ad: int) -> list[ExamOption]:
                return [ExamOption(code="gsat-115-guozong", year_ad=year_ad, year_roc=115, label="GSAT 115")]

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            data_dir = root / "data"
            data_dir.mkdir()
            (data_dir / "aliases.json").write_text('{"rules": []}', encoding="utf-8")
            prior_papers = [_paper("ceec_gsat", "ceec-gsat", year_roc=year) for year in (113, 114)]
            new_paper = _paper("ceec_gsat", "ceec-gsat", year_roc=115)
            for paper in [*prior_papers, new_paper]:
                mirror_path = root / "mirror" / paper.storage_key
                mirror_path.parent.mkdir(parents=True, exist_ok=True)
                mirror_path.write_bytes(b"%PDF-1.7 demo")

            write_provider_state(
                provider_paths(root, "moex"),
                raw_pages=[], normalized=NormalizedCatalog(papers=[], review_queue=[]),
                aliases=[], failures=[], manifest=None,
            )
            write_provider_state(
                provider_paths(root, "ceec_gsat"),
                raw_pages=[], normalized=NormalizedCatalog(papers=prior_papers, review_queue=[]),
                aliases=[], failures=[], manifest=None,
            )
            publish_args = [
                "publish-site", "--repo-root", str(root), "--site-id", "default",
                "--repository", "example/repo",
            ]
            self.assertEqual(main(publish_args), 0)
            feed_path = site_paths(root, "default").frontend_bundles_path
            before = json.loads(feed_path.read_text(encoding="utf-8"))["bundles"]
            self.assertEqual(before[0]["years"], [114, 113])

            page = SourceExamPage(
                provider_id="ceec_gsat", source_exam_id="gsat-115-guozong",
                year_ad=2026, year_roc=115, exam_name_raw="GSAT 115",
                attachments=[], papers=[],
            )
            sync_exam_pages_mock.return_value = (
                [page], NormalizedCatalog(papers=[new_paper], review_queue=[]), [],
            )
            plan_path = root / ".tmp" / "site-publish-plan.json"
            sync_args = build_parser().parse_args(
                [
                    "sync-full", "--provider", "ceec_gsat", "--site-id", "default",
                    "--data-dir", str(data_dir), "--mirror-dir", str(root / "mirror"),
                    "--bundle-dir", str(root / "bundles"),
                    "--aliases", str(data_dir / "aliases.json"),
                    "--publish-plan-output", str(plan_path),
                ]
            )
            client = CeecClient()
            with patch("app.sync.time.sleep"):
                self.assertEqual(command_sync(sync_args, client=client), 0)
            self.assertEqual(client.discovery_calls, 2)
            self.assertTrue(plan_path.exists())
            receipts = json.loads(provider_paths(root, "ceec_gsat").sync_status_path.read_text(encoding="utf-8"))
            self.assertEqual(set(receipts["events"]), {"gsat-115-guozong"})
            self.assertEqual(main([*publish_args, "--publish-plan", str(plan_path)]), 0)

            after = json.loads(feed_path.read_text(encoding="utf-8"))["bundles"]
            self.assertEqual(len(after), 1)
            self.assertEqual(after[0]["years"], [115, 114, 113])
            self.assertEqual(after[0]["fileCount"], 3)

    def test_publish_site_command_returns_non_zero_when_bundle_build_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            broken_papers = [
                _paper("moex", "nurse", year_roc=115),
                _paper("moex", "nurse", year_roc=114),
            ]
            write_provider_state(
                provider_paths(root, "moex"),
                raw_pages=[],
                normalized=NormalizedCatalog(papers=broken_papers, review_queue=[]),
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

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "publish-site",
                        "--repo-root",
                        str(root),
                        "--site-id",
                        "default",
                        "--repository",
                        "example/repo",
                    ]
                )

            self.assertEqual(exit_code, 1)
            self.assertIn("Missing mirrored file for bundle entry", output.getvalue())
            self.assertFalse(site_paths(root, "default").bundles_path.exists())

    def test_publish_site_command_returns_non_zero_when_a_required_provider_state_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            ceec_provider = provider_paths(root, "ceec_gsat")
            ceec_paper = _paper("ceec_gsat", "ceec-gsat")
            mirror_path = root / "mirror" / ceec_paper.storage_key
            mirror_path.parent.mkdir(parents=True, exist_ok=True)
            mirror_path.write_bytes(b"%PDF-1.7 demo")

            write_provider_state(
                ceec_provider,
                raw_pages=[],
                normalized=NormalizedCatalog(papers=[ceec_paper], review_queue=[]),
                aliases=[],
                failures=[],
                manifest=None,
            )

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "publish-site",
                        "--repo-root",
                        str(root),
                        "--site-id",
                        "default",
                        "--repository",
                        "example/repo",
                    ]
                )

            self.assertEqual(exit_code, 1)
            self.assertIn("moex", output.getvalue())
            self.assertIn("provider state", output.getvalue())

    def test_sync_attachment_defaults_match_command_risk(self) -> None:
        parser = build_parser()

        full_args = parser.parse_args(["sync-full"])
        incremental_args = parser.parse_args(["sync-incremental"])

        self.assertFalse(full_args.download_attachments)
        self.assertFalse(incremental_args.download_attachments)

    def test_removed_pdf_optimization_flags_are_rejected(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["sync-full", "--optimize-pdfs"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["optimize-mirror-pdfs"])

    def test_sync_incremental_can_download_attachments_explicitly(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["sync-incremental", "--download-attachments"])
        self.assertTrue(args.download_attachments)

    def test_sync_incremental_can_write_source_manifest_for_audits(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["sync-incremental", "--write-manifest", "--manifest", "data/source-manifest.json"])
        self.assertTrue(args.write_manifest)
        self.assertEqual(args.manifest, Path("data/source-manifest.json"))

    def test_sync_incremental_defaults_manifest_to_provider_scope_when_not_explicitly_set(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["sync-incremental", "--write-manifest"])

        self.assertTrue(args.write_manifest)
        self.assertIsNone(args.manifest)

    def test_command_sync_rejects_write_manifest_for_provider_without_probe_support(self) -> None:
        class UnsupportedManifestClient:
            provider_id = "ceec_gsat"

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            args = build_parser().parse_args(
                [
                    "sync-incremental",
                    "--provider",
                    "ceec_gsat",
                    "--write-manifest",
                    "--data-dir",
                    str(root / "data"),
                    "--mirror-dir",
                    str(root / "mirror"),
                    "--bundle-dir",
                    str(root / "bundles"),
                ]
            )
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = command_sync(args, client=UnsupportedManifestClient())

        self.assertEqual(exit_code, 1)
        self.assertIn("--write-manifest", output.getvalue())
        self.assertIn("ceec_gsat", output.getvalue())

    def test_command_sync_preserves_existing_provider_state_when_discovery_is_unavailable(self) -> None:
        class DiscoveryOutageClient:
            provider_id = "wdasec_skill"

            def __init__(self) -> None:
                self.calls = 0

            def discover_available_years(self) -> list[int]:
                self.calls += 1
                raise OSError("temporary name resolution failure")

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            data_dir = root / "data"
            data_dir.mkdir()
            (data_dir / "aliases.json").write_text('{"rules": []}', encoding="utf-8")
            provider = provider_paths(root, "wdasec_skill")
            existing_page = SourceExamPage(
                provider_id="wdasec_skill",
                source_exam_id="202603160001",
                year_ad=2026,
                year_roc=115,
                exam_name_raw="Existing skill exam",
                attachments=[],
                papers=[],
            )
            existing_paper = _paper(
                "wdasec_skill",
                "wdasec-skill",
                source_exam_id="202603160001",
            )
            write_provider_state(
                provider,
                raw_pages=[existing_page],
                normalized=NormalizedCatalog(papers=[existing_paper], review_queue=[]),
                aliases=[],
                failures=[],
                manifest=None,
            )
            before_exams = (provider.exams_dir / "2026.json").read_text(encoding="utf-8")
            before_papers = (provider.papers_dir / "2026.json").read_text(encoding="utf-8")
            plan_path = root / "site-publish-plan.json"
            plan_path.write_text('{"affected_canonical_ids": ["stale"]}', encoding="utf-8")
            args = build_parser().parse_args(
                [
                    "sync-full",
                    "--provider",
                    "wdasec_skill",
                    "--data-dir",
                    str(data_dir),
                    "--mirror-dir",
                    str(root / "mirror"),
                    "--aliases",
                    str(data_dir / "aliases.json"),
                    "--publish-plan-output",
                    str(plan_path),
                ]
            )
            output = io.StringIO()
            client = DiscoveryOutageClient()

            with redirect_stdout(output), patch("app.sync.time.sleep"):
                exit_code = command_sync(args, client=client)

            self.assertEqual(exit_code, 1)
            self.assertEqual(client.calls, 3)
            self.assertIn("preserving existing", output.getvalue())
            self.assertFalse(plan_path.exists())
            self.assertEqual((provider.exams_dir / "2026.json").read_text(encoding="utf-8"), before_exams)
            self.assertEqual((provider.papers_dir / "2026.json").read_text(encoding="utf-8"), before_papers)

    def test_command_sync_records_year_discovery_failure(self) -> None:
        class YearDiscoveryFailureClient:
            provider_id = "wdasec_skill"

            def discover_available_years(self) -> list[int]:
                return [2026]

            def discover_exams(self, year_ad: int):
                raise OSError("temporary year page failure")

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            data_dir = root / "data"
            data_dir.mkdir()
            (data_dir / "aliases.json").write_text('{"rules": []}', encoding="utf-8")
            args = build_parser().parse_args(
                [
                    "sync-full",
                    "--provider",
                    "wdasec_skill",
                    "--data-dir",
                    str(data_dir),
                    "--mirror-dir",
                    str(root / "mirror"),
                    "--aliases",
                    str(data_dir / "aliases.json"),
                ]
            )
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = command_sync(args, client=YearDiscoveryFailureClient())

            failures = json.loads((provider_paths(root, "wdasec_skill").sync_failures_path).read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 1)
        self.assertIn("failed to discover exams", output.getvalue())
        self.assertEqual(failures[0]["stage"], "discover")
        self.assertEqual(failures[0]["source_exam_id"], "wdasec_skill-2026")

    def test_command_sync_incremental_merges_and_writes_provider_scoped_state(self) -> None:
        class SuccessfulIncrementalClient:
            provider_id = "moex"

            def discover_available_years(self) -> list[int]:
                return [2026]

            def discover_exams(self, year_ad: int) -> list[ExamOption]:
                return [ExamOption(code="115040", year_ad=year_ad, year_roc=115, label="Exam 115040")]

            def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
                return SourceExamPage(
                    provider_id="moex",
                    source_exam_id=exam_code,
                    year_ad=year_ad,
                    year_roc=115,
                    exam_name_raw="Exam 115040",
                    attachments=[],
                    papers=[
                        ParsedPaper(
                            category_raw="nurse raw",
                            category_code="101",
                            subject_code="0101",
                            subject_name_raw="Subject",
                            files={"question": "https://example.test/question.pdf"},
                        )
                    ],
                )

            def download_file(self, url: str) -> DownloadedFile:
                return DownloadedFile(data=b"%PDF-1.7 demo", content_type="application/pdf", file_name=Path(url).name)

            def head(self, url: str) -> ResponseMetadata:
                lengths = {
                    "https://wwwq.moex.gov.tw/exam/wFrmExamQandASearch.aspx?y=2026": 800,
                    make_result_url("115040", 2026): 500,
                }
                return ResponseMetadata(url=url, status=200, content_length=lengths[url])

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            data_dir = root / "data"
            data_dir.mkdir()
            alias_rule = AliasRule(match_type="exact", raw_pattern="nurse raw", canonical_id="nurse", canonical_name="Nurse")
            (data_dir / "aliases.json").write_text(
                json.dumps({"rules": [alias_rule.__dict__]}, ensure_ascii=False),
                encoding="utf-8",
            )
            provider = provider_paths(root, "moex")
            old_paper = _paper("moex", "nurse")
            write_provider_state(
                provider,
                raw_pages=[
                    SourceExamPage(
                        provider_id="moex",
                        source_exam_id="115030",
                        year_ad=2026,
                        year_roc=115,
                        exam_name_raw="Exam 115030",
                        attachments=[],
                        papers=[],
                    )
                ],
                normalized=NormalizedCatalog(papers=[old_paper], review_queue=[]),
                aliases=[alias_rule],
                failures=[],
                manifest=SourceManifest(provider_id="moex"),
            )
            args = build_parser().parse_args(
                [
                    "sync-incremental",
                    "--years",
                    "1",
                    "--provider",
                    "moex",
                    "--write-manifest",
                    "--data-dir",
                    str(data_dir),
                    "--mirror-dir",
                    str(root / "mirror"),
                    "--bundle-dir",
                    str(root / "bundles"),
                    "--aliases",
                    str(data_dir / "aliases.json"),
                    "--manifest",
                    str(data_dir / "source-manifest.json"),
                ]
            )

            exit_code = command_sync(args, client=SuccessfulIncrementalClient())

            self.assertEqual(exit_code, 0)
            provider_raw_pages, provider_catalog, provider_failures = load_provider_state(provider)
            self.assertEqual({page.source_exam_id for page in provider_raw_pages}, {"115030", "115040"})
            self.assertEqual({paper.source_exam_id for paper in provider_catalog.papers}, {"115030", "115040"})
            self.assertEqual(provider_failures, [])
            provider_manifest = json.loads(provider.source_manifest_path.read_text(encoding="utf-8"))
            legacy_manifest = json.loads((data_dir / "source-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(provider_manifest, legacy_manifest)
            self.assertFalse((data_dir / "bundles.json").exists())
            self.assertFalse((data_dir / "release-assets.json").exists())

    def test_incremental_sync_is_not_failed_by_a_retained_failure_it_never_refetched(self) -> None:
        # MOEX has served an HTML placeholder for five ROC 85/90/93 files since
        # before this automation existed. Those events sit outside a two-year
        # window, so an audit never re-fetches them and learns nothing new about
        # them - but counting them made audit-recent permanently red: the run on
        # 2026-08-07 fetched 9,323 papers with zero failures and still exited 1,
        # publishing nothing. The record is kept; only the exit code changes.
        class CleanClient:
            provider_id = "moex"

            def discover_available_years(self) -> list[int]:
                return [2026]

            def discover_exams(self, year_ad: int) -> list[ExamOption]:
                return [ExamOption(code="115040", year_ad=year_ad, year_roc=115, label="Exam 115040")]

            def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
                return SourceExamPage(
                    provider_id="moex",
                    source_exam_id=exam_code,
                    year_ad=year_ad,
                    year_roc=115,
                    exam_name_raw="Exam 115040",
                    attachments=[],
                    papers=[
                        ParsedPaper(
                            category_raw="nurse raw",
                            category_code="101",
                            subject_code="0101",
                            subject_name_raw="Subject",
                            files={"question": "https://example.test/question.pdf"},
                        )
                    ],
                )

            def download_file(self, url: str) -> DownloadedFile:
                return DownloadedFile(data=b"%PDF-1.7 demo", content_type="application/pdf", file_name=Path(url).name)

            def head(self, url: str) -> ResponseMetadata:
                lengths = {
                    "https://wwwq.moex.gov.tw/exam/wFrmExamQandASearch.aspx?y=2026": 800,
                    make_result_url("115040", 2026): 500,
                }
                return ResponseMetadata(url=url, status=200, content_length=lengths[url])

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            data_dir = root / "data"
            data_dir.mkdir()
            alias_rule = AliasRule(match_type="exact", raw_pattern="nurse raw", canonical_id="nurse", canonical_name="Nurse")
            (data_dir / "aliases.json").write_text(
                json.dumps({"rules": [alias_rule.__dict__]}, ensure_ascii=False),
                encoding="utf-8",
            )
            provider = provider_paths(root, "moex")
            stale_failure = SyncFailure(
                stage="download",
                source_exam_id="085210",
                year_roc=85,
                paper_code="c041",
                file_type="question",
                url="https://wwwq.moex.gov.tw/exam/wHandExamQandA_File.ashx?t=Q&code=085210",
                message="Downloaded HTML placeholder instead of .pdf",
            )
            write_provider_state(
                provider,
                raw_pages=[],
                normalized=NormalizedCatalog(papers=[_paper("moex", "nurse")], review_queue=[]),
                aliases=[alias_rule],
                failures=[stale_failure],
                manifest=SourceManifest(provider_id="moex"),
            )
            args = build_parser().parse_args(
                [
                    "sync-incremental", "--years", "1", "--provider", "moex",
                    "--data-dir", str(data_dir),
                    "--mirror-dir", str(root / "mirror"),
                    "--bundle-dir", str(root / "bundles"),
                    "--aliases", str(data_dir / "aliases.json"),
                    "--manifest", str(data_dir / "source-manifest.json"),
                ]
            )

            exit_code = command_sync(args, client=CleanClient())

            self.assertEqual(exit_code, 0)
            _pages, _catalog, provider_failures = load_provider_state(provider)
            self.assertEqual([failure.source_exam_id for failure in provider_failures], ["085210"])

    @patch("app.cli.write_data_files")
    @patch("app.cli.build_bundles")
    @patch("app.cli.sync_exam_pages")
    def test_command_sync_full_for_moex_does_not_write_legacy_publication_outputs(
        self,
        sync_exam_pages_mock,
        build_bundles_mock,
        write_data_files_mock,
    ) -> None:
        class MoexClient:
            provider_id = "moex"

            def discover_available_years(self) -> list[int]:
                return [2026]

            def discover_exams(self, year_ad: int) -> list[ExamOption]:
                return [ExamOption(code="115030", year_ad=year_ad, year_roc=115, label="MOEX 115")]

        page = SourceExamPage(
            provider_id="moex",
            source_exam_id="115030",
            year_ad=2026,
            year_roc=115,
            exam_name_raw="MOEX 115",
            attachments=[],
            papers=[],
        )
        catalog = NormalizedCatalog(papers=[_paper("moex", "nurse")], review_queue=[])
        sync_exam_pages_mock.return_value = ([page], catalog, [])
        build_bundles_mock.return_value = type("BuildResult", (), {"bundles": [], "failures": []})()

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            data_dir = root / "data"
            data_dir.mkdir()
            (data_dir / "aliases.json").write_text('{"rules": []}', encoding="utf-8")
            args = build_parser().parse_args(
                [
                    "sync-full",
                    "--provider",
                    "moex",
                    "--data-dir",
                    str(data_dir),
                    "--mirror-dir",
                    str(root / "mirror"),
                    "--bundle-dir",
                    str(root / "bundles"),
                    "--aliases",
                    str(data_dir / "aliases.json"),
                ]
            )

            exit_code = command_sync(args, client=MoexClient())

            provider_raw_pages, provider_catalog, provider_failures = load_provider_state(provider_paths(root, "moex"))
            self.assertEqual([page.source_exam_id for page in provider_raw_pages], ["115030"])
            self.assertEqual([paper.canonical_id for paper in provider_catalog.papers], ["nurse"])
            self.assertEqual(provider_failures, [])
            self.assertFalse((data_dir / "bundles.json").exists())
            self.assertFalse((data_dir / "release-assets.json").exists())

        self.assertEqual(exit_code, 0)
        build_bundles_mock.assert_not_called()
        write_data_files_mock.assert_not_called()

    @patch("app.cli._restore_new_public_bundle_files", return_value=[])
    @patch("app.cli._download_affected_bundles")
    @patch("app.cli.sync_exam_pages")
    def test_full_sync_downloads_existing_affected_bundles_when_requested(
        self, sync_exam_pages_mock, download_mock, restore_mock,
    ) -> None:
        class MoexClient:
            provider_id = "moex"

            def discover_available_years(self) -> list[int]:
                return [2026]

            def discover_exams(self, year_ad: int) -> list[ExamOption]:
                return [ExamOption(code="115030", year_ad=year_ad, year_roc=115, label="MOEX 115")]

        page = SourceExamPage(
            provider_id="moex", source_exam_id="115030", year_ad=2026,
            year_roc=115, exam_name_raw="MOEX 115", attachments=[], papers=[],
        )
        sync_exam_pages_mock.return_value = (
            [page], NormalizedCatalog(papers=[_paper("moex", "nurse")], review_queue=[]), [],
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            data_dir = root / "data"
            data_dir.mkdir()
            (data_dir / "aliases.json").write_text('{"rules": []}', encoding="utf-8")
            site = site_paths(root, "default")
            site.data_dir.mkdir(parents=True, exist_ok=True)
            site.bundles_path.write_text(json.dumps({
                "schema_version": 1,
                "site_id": "default",
                "bundles": [{
                    "canonical_id": "nurse", "canonical_name": "Nurse", "years": [115, 114],
                    "file_count": 2, "storage_key": "bundles/sites/default/nurse.zip",
                    "asset_name": "nurse.zip", "release_tag": "default-bundles-001",
                }],
            }), encoding="utf-8")
            for recovery_flag in ("--download-affected-bundles", "--restore-new-public-files"):
                with self.subTest(recovery_flag=recovery_flag):
                    if recovery_flag == "--restore-new-public-files":
                        shutil.rmtree(data_dir / "providers")
                    download_mock.reset_mock()
                    restore_mock.reset_mock()
                    args = build_parser().parse_args([
                        "sync-full", "--provider", "moex", "--data-dir", str(data_dir),
                        "--mirror-dir", str(root / "mirror"),
                        "--aliases", str(data_dir / "aliases.json"), recovery_flag,
                    ])
                    self.assertEqual(command_sync(args, client=MoexClient()), 0)
                    if recovery_flag == "--download-affected-bundles":
                        download_mock.assert_called_once()
                        self.assertEqual(download_mock.call_args.args[0], site.bundle_dir)
                        self.assertEqual(download_mock.call_args.args[2], {"nurse"})
                    else:
                        download_mock.assert_not_called()
                    restore_mock.assert_called_once()

    @patch("app.cli.publish_site")
    @patch("app.cli._download_affected_bundles")
    @patch("app.cli.load_site_bundles")
    @patch("app.cli._load_publish_plan", return_value=({"nurse"}, {}))
    def test_publication_recovers_previous_zips_from_current_site_assignments(
        self, plan_mock, load_mock, download_mock, publish_mock,
    ) -> None:
        calls = []
        download_mock.side_effect = lambda *a, **kw: calls.append("download")
        publish_mock.side_effect = lambda *a, **kw: calls.append("publish")
        args = build_parser().parse_args([
            "publish-site", "--repo-root", "/tmp/publication-test",
            "--download-affected-bundles", "--publish-plan", "/tmp/plan.json",
        ])

        self.assertEqual(cli.command_publish_site(args), 0)

        self.assertEqual(calls, ["download", "publish"])
        current_site = site_paths(args.repo_root, args.site_id)
        load_mock.assert_called_once_with(current_site)
        download_mock.assert_called_once_with(current_site.bundle_dir, load_mock.return_value, {"nurse"}, "")
        self.assertEqual(publish_mock.call_args.kwargs["affected_canonical_ids"], {"nurse"})

    @patch("app.cli.sync_exam_pages", side_effect=RuntimeError("parser crashed"))
    def test_full_sync_records_unexpected_year_failure(self, sync_exam_pages_mock) -> None:
        class MoexClient:
            provider_id = "moex"

            def discover_available_years(self) -> list[int]:
                return [2026]

            def discover_exams(self, year_ad: int) -> list[ExamOption]:
                return [ExamOption(code="115030", year_ad=year_ad, year_roc=115, label="MOEX 115")]

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            data_dir = root / "data"
            data_dir.mkdir()
            (data_dir / "aliases.json").write_text('{"rules": []}', encoding="utf-8")
            args = build_parser().parse_args(
                [
                    "sync-full", "--provider", "moex",
                    "--data-dir", str(data_dir),
                    "--mirror-dir", str(root / "mirror"),
                    "--bundle-dir", str(root / "bundles"),
                    "--aliases", str(data_dir / "aliases.json"),
                ]
            )

            exit_code = command_sync(args, client=MoexClient())

            pages, catalog, failures = load_provider_state(provider_paths(root, "moex"))
            self.assertEqual(pages, [])
            self.assertEqual(catalog.papers, [])
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0].stage, "sync")
            self.assertEqual(failures[0].source_exam_id, "moex-2026")
            self.assertIn("parser crashed", failures[0].message)

        self.assertEqual(exit_code, 1)
        sync_exam_pages_mock.assert_called_once()

    @patch("app.cli.write_data_files")
    @patch("app.cli.build_bundles")
    @patch("app.cli.sync_exam_pages")
    def test_full_sync_retains_an_event_the_source_stopped_listing(
        self,
        sync_exam_pages_mock,
        build_bundles_mock,
        write_data_files_mock,
    ) -> None:
        # New Taipei and Hakka both lost retained events this way: the source
        # dropped a listing, the sync reported no failure because there was
        # nothing to fail on, and a full sync overwrote state with the shorter
        # answer. Nothing may disappear just because a source went quiet.
        class ShrinkingClient:
            provider_id = "moex"

            def discover_available_years(self) -> list[int]:
                return [2026]

            def discover_exams(self, year_ad: int) -> list[ExamOption]:
                return [ExamOption(code="115030", year_ad=year_ad, year_roc=115, label="MOEX 115")]

        still_listed = SourceExamPage(
            provider_id="moex",
            source_exam_id="115030",
            year_ad=2026,
            year_roc=115,
            exam_name_raw="MOEX 115",
            attachments=[],
            papers=[],
        )
        sync_exam_pages_mock.return_value = (
            [still_listed],
            NormalizedCatalog(papers=[_paper("moex", "nurse", source_exam_id="115030")], review_queue=[]),
            [],
        )
        build_bundles_mock.return_value = type("BuildResult", (), {"bundles": [], "failures": []})()

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            data_dir = root / "data"
            data_dir.mkdir()
            (data_dir / "aliases.json").write_text('{"rules": []}', encoding="utf-8")
            provider = provider_paths(root, "moex")
            retired = SourceExamPage(
                provider_id="moex",
                source_exam_id="115040",
                year_ad=2026,
                year_roc=115,
                exam_name_raw="MOEX 115 retired",
                attachments=[],
                papers=[],
            )
            retained_failure = SyncFailure(
                stage="download",
                source_exam_id="115040",
                year_roc=115,
                paper_code="retired-paper",
                file_type="question",
                url="https://source.example/retired.pdf",
                message="Source removed the file",
            )
            write_provider_state(
                provider,
                raw_pages=[still_listed, retired],
                normalized=NormalizedCatalog(
                    papers=[
                        _paper("moex", "nurse", source_exam_id="115030"),
                        _paper("moex", "nurse", source_exam_id="115040"),
                    ],
                    review_queue=[],
                ),
                aliases=[],
                failures=[retained_failure],
                manifest=None,
            )

            args = build_parser().parse_args(
                [
                    "sync-full",
                    "--provider",
                    "moex",
                    "--data-dir",
                    str(data_dir),
                    "--mirror-dir",
                    str(root / "mirror"),
                    "--bundle-dir",
                    str(root / "bundles"),
                    "--aliases",
                    str(data_dir / "aliases.json"),
                ]
            )

            exit_code = command_sync(args, client=ShrinkingClient())

            raw_pages, catalog, failures = load_provider_state(provider)

        self.assertEqual(exit_code, 0)
        self.assertEqual({page.source_exam_id for page in raw_pages}, {"115030", "115040"})
        self.assertEqual({paper.source_exam_id for paper in catalog.papers}, {"115030", "115040"})
        self.assertEqual([failure.source_exam_id for failure in failures], ["115040"])

    @patch("app.cli.write_data_files")
    @patch("app.cli.build_bundles")
    @patch("app.cli.sync_exam_pages")
    def test_command_sync_full_for_ceec_provider_does_not_write_legacy_publication_outputs(
        self,
        sync_exam_pages_mock,
        build_bundles_mock,
        write_data_files_mock,
    ) -> None:
        class CeecClient:
            provider_id = "ceec_gsat"

            def discover_available_years(self) -> list[int]:
                return [2026]

            def discover_exams(self, year_ad: int) -> list[ExamOption]:
                return [ExamOption(code="gsat-115-guozong", year_ad=year_ad, year_roc=115, label="GSAT 115")]

        build_bundles_mock.return_value = type("BuildResult", (), {"bundles": [], "failures": []})()
        page = SourceExamPage(
            provider_id="ceec_gsat",
            source_exam_id="gsat-115-guozong",
            year_ad=2026,
            year_roc=115,
            exam_name_raw="GSAT 115",
            attachments=[],
            papers=[],
        )
        catalog = NormalizedCatalog(papers=[_paper("ceec_gsat", "ceec-gsat")], review_queue=[])
        sync_exam_pages_mock.return_value = ([page], catalog, [])

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            data_dir = root / "data"
            data_dir.mkdir()
            (data_dir / "aliases.json").write_text('{"rules": []}', encoding="utf-8")
            args = build_parser().parse_args(
                [
                    "sync-full",
                    "--provider",
                    "ceec_gsat",
                    "--data-dir",
                    str(data_dir),
                    "--mirror-dir",
                    str(root / "mirror"),
                    "--bundle-dir",
                    str(root / "bundles"),
                    "--aliases",
                    str(data_dir / "aliases.json"),
                ]
            )

            exit_code = command_sync(args, client=CeecClient())

            self.assertEqual(exit_code, 0)
            provider_raw_pages, provider_catalog, provider_failures = load_provider_state(provider_paths(root, "ceec_gsat"))
            self.assertEqual([page.source_exam_id for page in provider_raw_pages], ["gsat-115-guozong"])
            self.assertEqual([paper.canonical_id for paper in provider_catalog.papers], ["ceec-gsat"])
            self.assertEqual(provider_failures, [])
            self.assertFalse((data_dir / "bundles.json").exists())
            self.assertFalse((data_dir / "release-assets.json").exists())

        build_bundles_mock.assert_not_called()
        write_data_files_mock.assert_not_called()

    def test_probe_latest_parser_accepts_manifest_and_output_paths(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["probe-latest", "--years", "2", "--manifest", "data/source-manifest.json", "--output", ".tmp/source-probe.json"])
        self.assertEqual(args.years, 2)
        self.assertEqual(args.manifest, Path("data/source-manifest.json"))
        self.assertEqual(args.output, Path(".tmp/source-probe.json"))
        self.assertFalse(args.write_manifest)

    def test_probe_latest_defaults_manifest_to_provider_scope_when_not_explicitly_set(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["probe-latest", "--years", "2", "--write-manifest"])

        self.assertEqual(args.years, 2)
        self.assertIsNone(args.manifest)
        self.assertTrue(args.write_manifest)

    def test_run_probe_latest_writes_probe_output_and_manifest_when_requested(self) -> None:
        class ProbeClient:
            def discover_available_years(self) -> list[int]:
                return [2026]

            def discover_exams(self, year_ad: int) -> list[ExamOption]:
                return [ExamOption(code="115040", year_ad=year_ad, year_roc=115, label="Exam 115040")]

            def head(self, url: str) -> ResponseMetadata:
                lengths = {
                    "https://wwwq.moex.gov.tw/exam/wFrmExamQandASearch.aspx?y=2026": 800,
                    make_result_url("115040", 2026): 500,
                }
                return ResponseMetadata(url=url, status=200, content_length=lengths[url])

            def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
                return SourceExamPage(
                    source_exam_id=exam_code,
                    year_ad=year_ad,
                    year_roc=115,
                    exam_name_raw="Exam 115040",
                    attachments=[],
                    papers=[
                        ParsedPaper(
                            category_raw="Category",
                            category_code="101",
                            subject_code="0101",
                            subject_name_raw="Subject",
                            files={"question": "https://example.test/question.pdf"},
                        )
                    ],
                )

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            args = build_parser().parse_args(
                [
                    "probe-latest",
                    "--years",
                    "1",
                    "--output",
                    str(root / ".tmp" / "source-probe.json"),
                    "--write-manifest",
                ]
            )

            exit_code = run_probe_latest(args, client=ProbeClient(), now="2026-05-20T00:00:00+08:00")

            probe = json.loads((root / ".tmp" / "source-probe.json").read_text(encoding="utf-8"))
            manifest = json.loads((root / "data" / "providers" / "moex" / "source-manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertTrue(probe["should_sync"])
        self.assertEqual(probe["changed_exam_codes"], ["115040"])
        self.assertEqual(probe["exam_years"], {"115040": 2026})
        self.assertEqual(probe["updated_manifest"]["years"]["2026"]["exam_codes"], ["115040"])
        self.assertEqual(manifest["years"]["2026"]["exam_codes"], ["115040"])
        self.assertFalse((root / "data" / "source-manifest.json").exists())

    def test_run_probe_latest_returns_non_zero_for_provider_without_probe_support(self) -> None:
        class UnsupportedProbeClient:
            provider_id = "ceec_gsat"

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            args = build_parser().parse_args(
                [
                    "probe-latest",
                    "--provider",
                    "ceec_gsat",
                    "--manifest",
                    str(root / "source-manifest.json"),
                    "--output",
                    str(root / ".tmp" / "source-probe.json"),
                    "--write-manifest",
                ]
            )
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = run_probe_latest(args, client=UnsupportedProbeClient(), now="2026-05-20T00:00:00+08:00")

        self.assertEqual(exit_code, 1)
        self.assertIn("ceec_gsat", output.getvalue())
        self.assertFalse((root / ".tmp" / "source-probe.json").exists())
        self.assertFalse((root / "source-manifest.json").exists())

    def test_sync_parsers_do_not_default_to_a_retired_release_tag(self) -> None:
        # The default used to be moex-bundles, retired at the v2 sharding on
        # 2026-06-19, and no workflow passes --release-tag. A bundle carrying
        # no tag of its own would therefore be fetched from a release that
        # cannot hold it, turning a benign rebuild into a hard sync failure.
        parser = build_parser()

        for argv in (
            ["sync-targeted", "--probe", ".tmp/source-probe.json"],
            ["sync-full"],
            ["sync-incremental"],
        ):
            with self.subTest(command=argv[0]):
                self.assertEqual(parser.parse_args(argv).release_tag, "")

    def test_untagged_bundle_is_rebuilt_rather_than_fetched_from_a_guessed_release(self) -> None:
        bundle = BundleAsset(
            canonical_id="canonical-1",
            canonical_name="護理師",
            years=[115],
            file_count=1,
            storage_key="bundles/nurse.zip",
            asset_name="nurse.zip",
            release_tag="",
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("app.cli.subprocess.run") as runner:
                _download_affected_bundles(Path(tmp_dir), [bundle], {"canonical-1"}, "")

        runner.assert_not_called()

    def test_sync_targeted_parser_accepts_probe_path(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "sync-targeted",
                "--probe",
                ".tmp/source-probe.json",
                "--download-affected-bundles",
                "--publish-plan-output",
                ".tmp/site-publish-plan.json",
                "--release-tag",
                "moex-bundles",
            ]
        )

        self.assertEqual(args.probe, Path(".tmp/source-probe.json"))
        self.assertFalse(args.download_attachments)
        self.assertTrue(args.download_affected_bundles)
        self.assertEqual(args.publish_plan_output, Path(".tmp/site-publish-plan.json"))
        self.assertIsNone(args.provider)
        self.assertEqual(args.release_tag, "moex-bundles")

    def test_sync_incremental_parser_accepts_publish_plan_output_and_bundle_downloads(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "sync-incremental",
                "--download-affected-bundles",
                "--publish-plan-output",
                ".tmp/site-publish-plan.json",
            ]
        )

        self.assertTrue(args.download_affected_bundles)
        self.assertEqual(args.publish_plan_output, Path(".tmp/site-publish-plan.json"))

    @patch("app.cli.subprocess.run")
    def disabled_test_download_affected_bundles_checks_primary_and_legacy_asset_names(self, run) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle_dir = Path(tmp_dir) / "bundles"
            _download_affected_bundles(
                bundle_dir,
                [
                    BundleAsset(
                        canonical_id="nurse",
                        canonical_name="護理師",
                        years=[115],
                        file_count=1,
                        storage_key="bundles/護理師__nurse.zip",
                        asset_name="護理師__nurse.zip",
                        legacy_asset_names=["nurse.zip"],
                    )
                ],
                {"nurse"},
                "moex-bundles",
            )

        patterns = [call.args[0][5] for call in run.call_args_list]
        self.assertEqual(patterns, ["nurse.zip", "護理師__nurse.zip"])
        self.assertTrue(all(call.args[0][3] == "moex-bundles" for call in run.call_args_list))

    @patch("app.cli.subprocess.run")
    def test_download_affected_bundles_prefers_primary_asset_name(self, run) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle_dir = Path(tmp_dir) / "bundles"
            _download_affected_bundles(
                bundle_dir,
                [
                    BundleAsset(
                        canonical_id="nurse",
                        canonical_name="Nurse",
                        years=[115],
                        file_count=1,
                        storage_key="bundles/sites/default/nurse.zip",
                        asset_name="nurse.zip",
                        legacy_asset_names=["nurse-display.zip"],
                        bundle_id="bundle-nurse",
                        schema_version=2,
                    )
                ],
                {"bundle-nurse"},
                "default-bundles-001",
            )

        self.assertEqual([call.args[0][5] for call in run.call_args_list], ["nurse.zip"])
        self.assertEqual(run.call_args.args[0][3], "default-bundles-001")

    @patch("app.cli.subprocess.run")
    def test_download_affected_bundles_falls_back_to_legacy_asset_names(self, run) -> None:
        run.side_effect = [
            subprocess.CalledProcessError(1, ["gh", "release", "download"]),
            None,
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle_dir = Path(tmp_dir) / "bundles"
            _download_affected_bundles(
                bundle_dir,
                [
                    BundleAsset(
                        canonical_id="nurse",
                        canonical_name="Nurse",
                        years=[115],
                        file_count=1,
                        storage_key="bundles/sites/default/nurse.zip",
                        asset_name="nurse.zip",
                        legacy_asset_names=["nurse-display.zip"],
                    )
                ],
                {"nurse"},
                "default-bundles-001",
            )

        self.assertEqual([call.args[0][5] for call in run.call_args_list], ["nurse.zip", "nurse-display.zip"])

    @patch("app.cli.subprocess.run")
    def test_download_affected_bundles_uses_bundle_specific_release_tags_when_present(self, run) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle_dir = Path(tmp_dir) / "bundles"
            _download_affected_bundles(
                bundle_dir,
                [
                    BundleAsset(
                        canonical_id="nurse",
                        canonical_name="Nurse",
                        years=[115],
                        file_count=1,
                        storage_key="bundles/sites/default/nurse.zip",
                        asset_name="nurse.zip",
                        release_tag="default-bundles-001",
                    ),
                    BundleAsset(
                        canonical_id="doctor",
                        canonical_name="Doctor",
                        years=[115],
                        file_count=1,
                        storage_key="bundles/sites/default/doctor.zip",
                        asset_name="doctor.zip",
                        release_tag="default-bundles-002",
                    ),
                ],
                {"nurse", "doctor"},
                "fallback-release",
            )

        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ["gh", "release", "download", "default-bundles-001", "--pattern", "nurse.zip", "--dir", str(bundle_dir)],
                ["gh", "release", "download", "default-bundles-002", "--pattern", "doctor.zip", "--dir", str(bundle_dir)],
            ],
        )

    @patch("app.cli._download_affected_bundles")
    @patch("app.cli.write_provider_state")
    @patch("app.cli.load_provider_state")
    @patch("app.cli.merge_targeted_state")
    @patch("app.cli.sync_exam_pages")
    def test_run_sync_targeted_writes_publish_plan_and_downloads_affected_bundles(
        self,
        sync_exam_pages_mock,
        merge_targeted_state_mock,
        load_provider_state_mock,
        write_provider_state_mock,
        download_affected_bundles_mock,
    ) -> None:
        class TargetedProvider:
            provider_id = "moex"

        refreshed_page = SourceExamPage(
            provider_id="moex",
            source_exam_id="115030",
            year_ad=2026,
            year_roc=115,
            exam_name_raw="Exam 115030",
            attachments=[],
            papers=[],
        )
        refreshed_catalog = NormalizedCatalog(papers=[_paper("moex", "nurse")], review_queue=[])
        sync_exam_pages_mock.return_value = ([refreshed_page], refreshed_catalog, [])
        merge_targeted_state_mock.return_value = (
            [refreshed_page],
            refreshed_catalog,
            [],
            {"nurse"},
            {"nurse": ["legacy-nurse"]},
        )
        load_provider_state_mock.return_value = ([], NormalizedCatalog(papers=[], review_queue=[]), [])

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            probe_path = root / ".tmp" / "source-probe.json"
            probe_path.parent.mkdir()
            probe_path.write_text(
                json.dumps(
                    {
                        "should_sync": True,
                        "provider_id": "moex",
                        "changed_exam_codes": ["115030"],
                        "removed_exam_codes": [],
                        "exam_years": {"115030": 2026},
                    }
                ),
                encoding="utf-8",
            )
            aliases_path = root / "data" / "aliases.json"
            aliases_path.parent.mkdir(parents=True, exist_ok=True)
            aliases_path.write_text(json.dumps({"rules": []}), encoding="utf-8")
            site = site_paths(root, "default")
            site.data_dir.mkdir(parents=True, exist_ok=True)
            site.bundles_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "site_id": "default",
                        "bundles": [
                            {
                                "canonical_id": "nurse",
                                "canonical_name": "Nurse",
                                "years": [115, 114],
                                "file_count": 2,
                                "storage_key": "bundles/sites/default/nurse.zip",
                                "asset_name": "nurse.zip",
                                "release_tag": "default-bundles-001",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            publish_plan_path = root / ".tmp" / "site-publish-plan.json"
            args = build_parser().parse_args(
                [
                    "sync-targeted",
                    "--provider",
                    "moex",
                    "--probe",
                    str(probe_path),
                    "--data-dir",
                    str(root / "data"),
                    "--mirror-dir",
                    str(root / "mirror"),
                    "--bundle-dir",
                    str(root / "bundles" / "sites" / "default"),
                    "--aliases",
                    str(aliases_path),
                    "--download-affected-bundles",
                    "--publish-plan-output",
                    str(publish_plan_path),
                ]
            )

            exit_code = run_sync_targeted(args, client=TargetedProvider())

            self.assertEqual(exit_code, 0)
            payload = json.loads(publish_plan_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["site_id"], "default")
            self.assertEqual(payload["affected_canonical_ids"], ["nurse"])
            self.assertEqual(payload["canonical_aliases"], {"nurse": ["legacy-nurse"]})
            download_affected_bundles_mock.assert_called_once()
            self.assertEqual(download_affected_bundles_mock.call_args.args[0], root / "bundles" / "sites" / "default")
            self.assertEqual(download_affected_bundles_mock.call_args.args[2], {"nurse"})
            write_provider_state_mock.assert_called_once()

    @patch("app.cli._download_affected_bundles")
    @patch("app.cli.write_provider_state")
    @patch("app.cli.load_provider_state")
    @patch("app.cli.merge_targeted_state")
    @patch("app.cli.sync_exam_pages")
    def test_run_sync_targeted_resolves_default_bundle_dir_to_site_scope(
        self,
        sync_exam_pages_mock,
        merge_targeted_state_mock,
        load_provider_state_mock,
        write_provider_state_mock,
        download_affected_bundles_mock,
    ) -> None:
        class TargetedProvider:
            provider_id = "moex"

        refreshed_page = SourceExamPage(
            provider_id="moex",
            source_exam_id="115030",
            year_ad=2026,
            year_roc=115,
            exam_name_raw="Exam 115030",
            attachments=[],
            papers=[],
        )
        refreshed_catalog = NormalizedCatalog(papers=[_paper("moex", "nurse")], review_queue=[])
        sync_exam_pages_mock.return_value = ([refreshed_page], refreshed_catalog, [])
        merge_targeted_state_mock.return_value = (
            [refreshed_page],
            refreshed_catalog,
            [],
            {"nurse"},
            {"nurse": ["legacy-nurse"]},
        )
        load_provider_state_mock.return_value = ([], NormalizedCatalog(papers=[], review_queue=[]), [])

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            probe_path = root / ".tmp" / "source-probe.json"
            probe_path.parent.mkdir()
            probe_path.write_text(
                json.dumps(
                    {
                        "should_sync": True,
                        "provider_id": "moex",
                        "changed_exam_codes": ["115030"],
                        "removed_exam_codes": [],
                        "exam_years": {"115030": 2026},
                    }
                ),
                encoding="utf-8",
            )
            aliases_path = root / "data" / "aliases.json"
            aliases_path.parent.mkdir(parents=True, exist_ok=True)
            aliases_path.write_text(json.dumps({"rules": []}), encoding="utf-8")
            site = site_paths(root, "default")
            site.data_dir.mkdir(parents=True, exist_ok=True)
            site.bundles_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "site_id": "default",
                        "bundles": [
                            {
                                "canonical_id": "nurse",
                                "canonical_name": "Nurse",
                                "years": [115, 114],
                                "file_count": 2,
                                "storage_key": "bundles/sites/default/nurse.zip",
                                "asset_name": "nurse.zip",
                                "release_tag": "default-bundles-001",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            args = build_parser().parse_args(
                [
                    "sync-targeted",
                    "--provider",
                    "moex",
                    "--probe",
                    str(probe_path),
                    "--data-dir",
                    str(root / "data"),
                    "--mirror-dir",
                    str(root / "mirror"),
                    "--aliases",
                    str(aliases_path),
                    "--download-affected-bundles",
                ]
            )

            exit_code = run_sync_targeted(args, client=TargetedProvider())

            self.assertEqual(exit_code, 0)
            download_affected_bundles_mock.assert_called_once()
            self.assertEqual(download_affected_bundles_mock.call_args.args[0], site.bundle_dir)
            self.assertEqual(download_affected_bundles_mock.call_args.args[2], {"nurse"})
            write_provider_state_mock.assert_called_once()

    def test_run_sync_targeted_exits_without_writes_when_probe_has_no_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            probe_path = root / ".tmp" / "source-probe.json"
            probe_path.parent.mkdir()
            probe_path.write_text(json.dumps({"should_sync": False}), encoding="utf-8")
            args = build_parser().parse_args(
                [
                    "sync-targeted",
                    "--probe",
                    str(probe_path),
                    "--data-dir",
                    str(root / "data"),
                    "--mirror-dir",
                    str(root / "mirror"),
                    "--bundle-dir",
                    str(root / "bundles"),
                ]
            )

            exit_code = run_sync_targeted(args, client=None)

            self.assertEqual(exit_code, 0)
            self.assertFalse((root / "data").exists())

    def test_run_sync_targeted_fails_closed_when_refreshed_exam_has_download_failure(self) -> None:
        class FailingTargetedClient:
            def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
                return SourceExamPage(
                    source_exam_id=exam_code,
                    year_ad=year_ad,
                    year_roc=year_ad - 1911,
                    exam_name_raw="Exam 115030",
                    attachments=[],
                    papers=[
                        ParsedPaper(
                            category_raw="nurse raw",
                            category_code="101",
                            subject_code="0101",
                            subject_name_raw="Subject",
                            files={
                                "question": "https://example.test/question.pdf",
                                "answer": "https://example.test/answer.pdf",
                            },
                        )
                    ],
                )

            def download_file(self, url: str) -> DownloadedFile:
                if url.endswith("answer.pdf"):
                    raise RuntimeError("temporary download failure")
                return DownloadedFile(data=b"%PDF-1.7 demo", content_type="application/pdf", file_name=Path(url).name)

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            data_dir = root / "data"
            aliases = [AliasRule(match_type="exact", raw_pattern="nurse raw", canonical_id="nurse", canonical_name="Nurse")]
            existing_papers = [
                NormalizedPaper(
                    canonical_id="nurse",
                    canonical_name="Nurse",
                    year_roc=115,
                    exam_name_raw="Exam 115030",
                    category_raw="nurse raw",
                    category_code="101",
                    source_exam_id="115030",
                    subject_code="0101",
                    subject_name_raw="Subject",
                    paper_code=f"101-0101-{file_type}",
                    file_type=file_type,
                    download_url_source=f"https://example.test/old-{file_type}.pdf",
                    storage_key=f"115/115030/101/0101/{file_type}.pdf",
                    checksum=f"old-{file_type}",
                )
                for file_type in ("question", "answer")
            ]
            write_data_files(
                data_dir=data_dir,
                raw_pages=[
                    SourceExamPage(
                        source_exam_id="115030",
                        year_ad=2026,
                        year_roc=115,
                        exam_name_raw="Exam 115030",
                        attachments=[],
                        papers=[],
                    )
                ],
                normalized=NormalizedCatalog(papers=existing_papers, review_queue=[]),
                aliases=aliases,
                bundles=[
                    BundleAsset(
                        canonical_id="nurse",
                        canonical_name="Nurse",
                        years=[115],
                        file_count=2,
                        storage_key="bundles/nurse.zip",
                        asset_name="nurse.zip",
                    )
                ],
                failures=[],
            )
            original_papers = (data_dir / "papers" / "2026.json").read_text(encoding="utf-8")
            probe_path = root / ".tmp" / "source-probe.json"
            probe_path.parent.mkdir()
            probe_path.write_text(
                json.dumps(
                    {
                        "should_sync": True,
                        "changed_exam_codes": ["115030"],
                        "removed_exam_codes": [],
                        "exam_years": {"115030": 2026},
                    }
                ),
                encoding="utf-8",
            )
            args = build_parser().parse_args(
                [
                    "sync-targeted",
                    "--probe",
                    str(probe_path),
                    "--data-dir",
                    str(data_dir),
                    "--mirror-dir",
                    str(root / "mirror"),
                    "--bundle-dir",
                    str(root / "bundles"),
                ]
            )

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = run_sync_targeted(args, client=FailingTargetedClient())

            self.assertEqual(exit_code, 1)
            self.assertIn("115030", output.getvalue())
            self.assertIn("101-0101-answer", output.getvalue())
            self.assertIn("temporary download failure", output.getvalue())
            self.assertEqual((data_dir / "papers" / "2026.json").read_text(encoding="utf-8"), original_papers)

    def test_run_sync_targeted_allow_partial_writes_valid_subset_and_failure_record(self) -> None:
        class PartialTargetedClient:
            provider_id = "moex"

            def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
                return SourceExamPage(
                    provider_id="moex",
                    source_exam_id=exam_code,
                    year_ad=year_ad,
                    year_roc=year_ad - 1911,
                    exam_name_raw="Exam 115030",
                    attachments=[],
                    papers=[
                        ParsedPaper(
                            category_raw="nurse raw",
                            category_code="101",
                            subject_code="0101",
                            subject_name_raw="Subject",
                            files={
                                "question": "https://example.test/question.pdf",
                                "answer": "https://example.test/answer.pdf",
                            },
                        )
                    ],
                )

            def download_file(self, url: str) -> DownloadedFile:
                if url.endswith("answer.pdf"):
                    raise RuntimeError("official answer placeholder")
                return DownloadedFile(data=b"%PDF-1.7 demo", content_type="application/pdf", file_name=Path(url).name)

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            aliases_path = root / "data" / "aliases.json"
            aliases_path.parent.mkdir(parents=True, exist_ok=True)
            aliases_path.write_text(json.dumps({"rules": []}), encoding="utf-8")
            probe_path = root / ".tmp" / "source-probe.json"
            probe_path.parent.mkdir(parents=True, exist_ok=True)
            probe_path.write_text(
                json.dumps(
                    {
                        "should_sync": True,
                        "provider_id": "moex",
                        "changed_exam_codes": ["115030"],
                        "removed_exam_codes": [],
                        "exam_years": {"115030": 2026},
                    }
                ),
                encoding="utf-8",
            )
            args = build_parser().parse_args(
                [
                    "sync-targeted",
                    "--provider",
                    "moex",
                    "--probe",
                    str(probe_path),
                    "--data-dir",
                    str(root / "data"),
                    "--mirror-dir",
                    str(root / "mirror"),
                    "--aliases",
                    str(aliases_path),
                    "--bundle-dir",
                    str(root / "bundles"),
                    "--allow-partial",
                ]
            )
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = run_sync_targeted(args, client=PartialTargetedClient())

            provider = provider_paths(root, "moex")
            raw_pages, catalog, failures = load_provider_state(provider)

        self.assertEqual(exit_code, 1)
        self.assertIn("Committed partial targeted state", output.getvalue())
        self.assertEqual([page.source_exam_id for page in raw_pages], ["115030"])
        self.assertEqual([paper.file_type for paper in catalog.papers], ["question"])
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].source_exam_id, "115030")
        self.assertEqual(failures[0].file_type, "answer")

    def test_run_sync_targeted_writes_probe_manifest_after_successful_sync(self) -> None:
        class SuccessfulTargetedClient:
            def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
                return SourceExamPage(
                    source_exam_id=exam_code,
                    year_ad=year_ad,
                    year_roc=year_ad - 1911,
                    exam_name_raw="Exam 115040",
                    attachments=[],
                    papers=[
                        ParsedPaper(
                            category_raw="nurse raw",
                            category_code="101",
                            subject_code="0101",
                            subject_name_raw="Subject",
                            files={"question": "https://example.test/question.pdf"},
                        )
                    ],
                )

            def download_file(self, url: str) -> DownloadedFile:
                return DownloadedFile(data=b"%PDF-1.7 demo", content_type="application/pdf", file_name=Path(url).name)

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            data_dir = root / "data"
            data_dir.mkdir()
            alias_rule = AliasRule(match_type="exact", raw_pattern="nurse raw", canonical_id="nurse", canonical_name="Nurse")
            (data_dir / "aliases.json").write_text(
                json.dumps({"rules": [alias_rule.__dict__]}),
                encoding="utf-8",
            )
            provider = provider_paths(root, "moex")
            write_provider_state(
                provider,
                raw_pages=[
                    SourceExamPage(
                        provider_id="moex",
                        source_exam_id="115030",
                        year_ad=2026,
                        year_roc=115,
                        exam_name_raw="Exam 115030",
                        attachments=[],
                        papers=[],
                    )
                ],
                normalized=NormalizedCatalog(papers=[_paper("moex", "nurse")], review_queue=[]),
                aliases=[alias_rule],
                failures=[],
                manifest=SourceManifest(provider_id="moex"),
            )
            manifest_payload = {
                "schema_version": 1,
                "provider_id": "moex",
                "probe_policy": {},
                "years": {"2026": {"year_ad": 2026, "exam_codes": ["115040"]}},
                "exams": {"115040": {"source_exam_id": "115040", "head_content_length": 500}},
                "files": {},
            }
            probe_path = root / ".tmp" / "source-probe.json"
            probe_path.parent.mkdir()
            probe_path.write_text(
                json.dumps(
                    {
                        "should_sync": True,
                        "changed_exam_codes": ["115040"],
                        "removed_exam_codes": [],
                        "exam_years": {"115040": 2026},
                        "updated_manifest": manifest_payload,
                    }
                ),
                encoding="utf-8",
            )
            args = build_parser().parse_args(
                [
                    "sync-targeted",
                    "--probe",
                    str(probe_path),
                    "--data-dir",
                    str(data_dir),
                    "--mirror-dir",
                    str(root / "mirror"),
                    "--bundle-dir",
                    str(root / "bundles"),
                ]
            )

            exit_code = run_sync_targeted(args, client=SuccessfulTargetedClient())

            self.assertEqual(exit_code, 0)
            provider_raw_pages, provider_catalog, provider_failures = load_provider_state(provider)
            self.assertEqual({page.source_exam_id for page in provider_raw_pages}, {"115030", "115040"})
            self.assertEqual({paper.source_exam_id for paper in provider_catalog.papers}, {"115030", "115040"})
            self.assertEqual(provider_failures, [])
            provider_manifest = json.loads(provider.source_manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(provider_manifest, manifest_payload)
            self.assertFalse((data_dir / "source-manifest.json").exists())
            self.assertFalse((data_dir / "bundles.json").exists())

    def test_run_sync_targeted_requires_explicit_exam_years_for_non_moex_probe(self) -> None:
        class CeecTargetedClient:
            provider_id = "ceec_gsat"

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            probe_path = root / ".tmp" / "source-probe.json"
            probe_path.parent.mkdir()
            probe_path.write_text(
                json.dumps(
                    {
                        "should_sync": True,
                        "provider_id": "ceec_gsat",
                        "changed_exam_codes": ["gsat-115-guozong"],
                        "removed_exam_codes": [],
                        "exam_years": {},
                    }
                ),
                encoding="utf-8",
            )
            args = build_parser().parse_args(
                [
                    "sync-targeted",
                    "--provider",
                    "ceec_gsat",
                    "--probe",
                    str(probe_path),
                    "--data-dir",
                    str(root / "data"),
                    "--mirror-dir",
                    str(root / "mirror"),
                    "--bundle-dir",
                    str(root / "bundles"),
                ]
            )
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = run_sync_targeted(args, client=CeecTargetedClient())

        self.assertEqual(exit_code, 1)
        self.assertIn("exam_years", output.getvalue())
        self.assertIn("ceec_gsat", output.getvalue())

    @patch("app.cli.write_data_files")
    @patch("app.cli.build_bundles")
    @patch("app.cli.merge_targeted_state")
    @patch("app.cli.load_existing_state")
    @patch("app.cli.sync_exam_pages")
    @patch("app.cli.get_provider")
    def test_run_sync_targeted_uses_probe_provider_when_args_default_to_moex(
        self,
        get_provider_mock,
        sync_exam_pages_mock,
        load_existing_state_mock,
        merge_targeted_state_mock,
        build_bundles_mock,
        _write_data_files_mock,
    ) -> None:
        class CeecProvider:
            provider_id = "ceec_gsat"

        class WrongProvider:
            provider_id = "moex"

        def provider_factory(provider_id: str):
            if provider_id == "ceec_gsat":
                return CeecProvider()
            if provider_id == "moex":
                return WrongProvider()
            raise AssertionError(provider_id)

        def sync_side_effect(*, client, exam_codes, **_kwargs):
            self.assertEqual(client.provider_id, "ceec_gsat")
            self.assertEqual(exam_codes, [("gsat-115-guozong", 2026)])
            return [], NormalizedCatalog(papers=[], review_queue=[]), []

        get_provider_mock.side_effect = provider_factory
        sync_exam_pages_mock.side_effect = sync_side_effect
        load_existing_state_mock.return_value = ([], NormalizedCatalog(papers=[], review_queue=[]), [], [])
        merge_targeted_state_mock.return_value = ([], NormalizedCatalog(papers=[], review_queue=[]), [], set(), {})
        build_bundles_mock.return_value = type("BuildResult", (), {"bundles": [], "failures": []})()

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            probe_path = root / ".tmp" / "source-probe.json"
            probe_path.parent.mkdir()
            probe_path.write_text(
                json.dumps(
                    {
                        "should_sync": True,
                        "provider_id": "ceec_gsat",
                        "changed_exam_codes": ["gsat-115-guozong"],
                        "removed_exam_codes": [],
                        "exam_years": {"gsat-115-guozong": 2026},
                    }
                ),
                encoding="utf-8",
            )
            args = build_parser().parse_args(
                [
                    "sync-targeted",
                    "--probe",
                    str(probe_path),
                    "--data-dir",
                    str(root / "data"),
                    "--mirror-dir",
                    str(root / "mirror"),
                    "--bundle-dir",
                    str(root / "bundles"),
                ]
            )

            exit_code = run_sync_targeted(args, client=None)

        self.assertEqual(exit_code, 0)
        get_provider_mock.assert_called_with("ceec_gsat")

    @patch("app.cli.sync_exam_pages")
    @patch("app.cli.get_provider")
    def test_run_sync_targeted_fails_when_explicit_provider_mismatches_probe(
        self,
        get_provider_mock,
        sync_exam_pages_mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            probe_path = root / ".tmp" / "source-probe.json"
            probe_path.parent.mkdir()
            probe_path.write_text(
                json.dumps(
                    {
                        "should_sync": True,
                        "provider_id": "ceec_gsat",
                        "changed_exam_codes": ["gsat-115-guozong"],
                        "removed_exam_codes": [],
                        "exam_years": {"gsat-115-guozong": 2026},
                    }
                ),
                encoding="utf-8",
            )
            args = build_parser().parse_args(
                [
                    "sync-targeted",
                    "--provider",
                    "moex",
                    "--probe",
                    str(probe_path),
                    "--data-dir",
                    str(root / "data"),
                    "--mirror-dir",
                    str(root / "mirror"),
                    "--bundle-dir",
                    str(root / "bundles"),
                ]
            )
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = run_sync_targeted(args, client=None)

        self.assertEqual(exit_code, 1)
        self.assertIn("Probe provider mismatch", output.getvalue())
        self.assertIn("ceec_gsat", output.getvalue())
        self.assertIn("moex", output.getvalue())
        get_provider_mock.assert_not_called()
        sync_exam_pages_mock.assert_not_called()


    def test_command_sync_incremental_preserves_existing_data_on_download_failure(self) -> None:
        class PartialFailureClient:
            def discover_available_years(self) -> list[int]:
                return [2026]

            def discover_exams(self, year_ad: int) -> list[ExamOption]:
                return [ExamOption(code="115030", year_ad=2026, year_roc=115, label="Exam")]

            def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
                return SourceExamPage(
                    source_exam_id="115030",
                    year_ad=2026,
                    year_roc=115,
                    exam_name_raw="115年護理師考試",
                    attachments=[],
                    papers=[
                        ParsedPaper(
                            category_raw="護理師",
                            category_code="101",
                            subject_code="0101",
                            subject_name_raw="基礎醫學",
                            files={
                                "question": "https://example.test/question.pdf",
                                "answer": "https://example.test/answer.pdf",
                            },
                        )
                    ],
                )

            def download_file(self, url: str) -> DownloadedFile:
                if url.endswith("answer.pdf"):
                    raise RuntimeError("transient network failure")
                return DownloadedFile(data=b"%PDF-1.7 demo", content_type="application/pdf", file_name=Path(url).name)

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            data_dir = root / "data"
            aliases = [AliasRule(match_type="exact", raw_pattern="護理師", canonical_id="nurse", canonical_name="護理師")]
            existing_papers = [
                NormalizedPaper(
                    canonical_id="nurse",
                    canonical_name="護理師",
                    year_roc=115,
                    exam_name_raw="115年護理師考試",
                    category_raw="護理師",
                    category_code="101",
                    source_exam_id="115030",
                    subject_code="0101",
                    subject_name_raw="基礎醫學",
                    paper_code=f"101-0101-{file_type}",
                    file_type=file_type,
                    download_url_source=f"https://example.test/old-{file_type}.pdf",
                    storage_key=f"115/115030/101/0101/{file_type}.pdf",
                    checksum=f"old-{file_type}",
                )
                for file_type in ("question", "answer")
            ]
            write_data_files(
                data_dir=data_dir,
                raw_pages=[
                    SourceExamPage(
                        source_exam_id="115030",
                        year_ad=2026,
                        year_roc=115,
                        exam_name_raw="115年護理師考試",
                        attachments=[],
                        papers=[],
                    )
                ],
                normalized=NormalizedCatalog(papers=existing_papers, review_queue=[]),
                aliases=aliases,
                bundles=[
                    BundleAsset(
                        canonical_id="nurse",
                        canonical_name="護理師",
                        years=[115],
                        file_count=2,
                        storage_key="bundles/nurse.zip",
                        asset_name="nurse.zip",
                    )
                ],
                failures=[],
            )
            original_papers = json.loads((data_dir / "papers" / "2026.json").read_text(encoding="utf-8"))
            args = build_parser().parse_args(
                [
                    "sync-incremental",
                    "--years",
                    "1",
                    "--data-dir",
                    str(data_dir),
                    "--mirror-dir",
                    str(root / "mirror"),
                    "--bundle-dir",
                    str(root / "bundles"),
                    "--aliases",
                    str(data_dir / "aliases.json"),
                ]
            )

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = command_sync(args, client=PartialFailureClient())

            self.assertEqual(exit_code, 1)
            self.assertIn("failure", output.getvalue().lower())
            papers = json.loads((data_dir / "papers" / "2026.json").read_text(encoding="utf-8"))
            nurse_papers = [p for p in papers if p["source_exam_id"] == "115030"]
            self.assertEqual(len(nurse_papers), 2)
            self.assertEqual({p["file_type"] for p in nurse_papers}, {"question", "answer"})
            self.assertEqual({p["checksum"] for p in nurse_papers}, {"old-question", "old-answer"})

    def test_repair_failures_refetches_only_recorded_bundle_failure_and_clears_it(self) -> None:
        class RepairClient:
            provider_id = "moex"

            def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
                self.fetched = (exam_code, year_ad)
                return SourceExamPage(
                    provider_id="moex",
                    source_exam_id=exam_code,
                    year_ad=year_ad,
                    year_roc=year_ad - 1911,
                    exam_name_raw="113 Nurse Exam",
                    attachments=[],
                    papers=[
                        ParsedPaper(
                            category_raw="Nurse",
                            category_code="101",
                            subject_code="0101",
                            subject_name_raw="Subject",
                            files={"question": "https://example.test/question.pdf"},
                        )
                    ],
                )

            def download_file(self, url: str) -> DownloadedFile:
                return DownloadedFile(data=b"%PDF-1.7 repaired", content_type="application/pdf", file_name="question.pdf")

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            data_dir = root / "data"
            provider = provider_paths(root, "moex")
            aliases = [AliasRule(match_type="exact", raw_pattern="Nurse", canonical_id="nurse", canonical_name="Nurse")]
            write_provider_state(
                provider,
                raw_pages=[
                    SourceExamPage(
                        provider_id="moex",
                        source_exam_id="113010",
                        year_ad=2024,
                        year_roc=113,
                        exam_name_raw="113 Nurse Exam",
                        attachments=[],
                        papers=[],
                    )
                ],
                normalized=NormalizedCatalog(papers=[_paper("moex", "nurse", year_roc=113, source_exam_id="113010")], review_queue=[]),
                aliases=aliases,
                failures=[
                    SyncFailure(
                        stage="bundle",
                        source_exam_id="113010",
                        year_roc=113,
                        paper_code="101-0101-question",
                        file_type="question",
                        url="",
                        message="Missing mirrored file for bundle entry",
                    )
                ],
                manifest=None,
            )
            args = build_parser().parse_args(
                [
                    "repair-failures",
                    "--provider",
                    "moex",
                    "--years",
                    "2024",
                    "--data-dir",
                    str(data_dir),
                    "--mirror-dir",
                    str(root / "mirror"),
                    "--aliases",
                    str(provider.aliases_path),
                ]
            )
            client = RepairClient()

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = command_repair_failures(args, client=client)

            raw_pages, catalog, failures = load_provider_state(provider)
            self.assertEqual(exit_code, 0)
            self.assertEqual(client.fetched, ("113010", 2024))
            self.assertEqual({page.source_exam_id for page in raw_pages}, {"113010"})
            self.assertEqual({paper.source_exam_id for paper in catalog.papers}, {"113010"})
            self.assertEqual(failures, [])
            self.assertIn("Repaired 1/1", output.getvalue())
            self.assertTrue((root / "mirror" / "providers/moex/113/113010/101/0101/question.pdf").is_file())


if __name__ == "__main__":
    unittest.main()


class SyncManifestRefreshTests(unittest.TestCase):
    """special_admission discovered ROC 116 on 2026-08-08 and committed the
    event, but source-manifest.json is a hand-maintained snapshot that nothing
    in the automation refreshes, so the deploy gate rejected the repository for
    a day. A sync now records what it discovered in the same commit.
    """

    class _Provider:
        provider_id = "fake"

        def build_discovery_year_url(self, year_ad):
            return f"https://example.test/year/{year_ad}"

        def build_discovery_exam_url(self, code, year_ad):
            return f"https://example.test/exam/{code}"

    class _NoBuilderProvider:
        provider_id = "fake"

    def _args(self, manifest_path):
        return argparse.Namespace(manifest=manifest_path, data_dir=None, mirror_dir=None)

    def _discovery(self, codes):
        return [(2027, [ExamOption(code=code, year_ad=2027, year_roc=116, label=code) for code in codes])]

    def test_a_newly_discovered_year_is_written_into_the_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "source-manifest.json"
            manifest = cli._refresh_manifest_from_sync(
                self._args(path), self._Provider(), "fake", self._discovery(["special-admission-116"])
            )

        self.assertIsNotNone(manifest)
        self.assertEqual(manifest.years["2027"]["exam_codes"], ["special-admission-116"])

    def test_a_sync_that_discovers_nothing_new_leaves_no_diff(self) -> None:
        # Otherwise every scheduled sync of all 34 providers rewrites a
        # timestamp and commits a manifest nobody changed.
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "source-manifest.json"
            args = self._args(path)
            first = cli._refresh_manifest_from_sync(args, self._Provider(), "fake", self._discovery(["exam-1"]))
            write_source_manifest(path, first)
            again = cli._refresh_manifest_from_sync(args, self._Provider(), "fake", self._discovery(["exam-1"]))

        self.assertIsNone(again)

    def test_a_sync_manifest_retains_an_event_the_source_delisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "source-manifest.json"
            write_source_manifest(
                path,
                SourceManifest(
                    provider_id="fake",
                    years={"2027": {"exam_codes": ["old-event"]}},
                    exams={"old-event": {"source_exam_id": "old-event", "year_ad": 2027}},
                ),
            )

            manifest = cli._refresh_manifest_from_sync(
                self._args(path), self._Provider(), "fake", self._discovery(["new-event"])
            )

        self.assertIsNotNone(manifest)
        self.assertEqual(manifest.years["2027"]["exam_codes"], ["new-event"])
        self.assertEqual(set(manifest.exams), {"new-event", "old-event"})
        self.assertEqual(manifest.probe_policy["retained_exam_codes"], ["old-event"])

    def test_a_provider_that_cannot_enumerate_its_source_is_left_alone(self) -> None:
        # sfi_cert and friends already carry reviewed, non-enforced manifest
        # gaps. Failing or rewriting their sync over this would be a regression.
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "source-manifest.json"
            manifest = cli._refresh_manifest_from_sync(
                self._args(path), self._NoBuilderProvider(), "fake", self._discovery(["exam-1"])
            )

        self.assertIsNone(manifest)
