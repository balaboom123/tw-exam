from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from app.cli import build_parser, command_repair_failures, command_sync, run_sync_targeted
from app.manifest import SourceManifest
from app.models import ExamOption, ParsedPaper, SourceExamPage, SyncFailure
from app.paths import provider_paths
from app.providers.base import DownloadedFile
from app.state import load_provider_state

URL = "https://example.test/current-paper.pdf"


class ManifestProvider:
    provider_id = "moex"

    def discover_available_years(self) -> list[int]:
        return [2026]

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        return [ExamOption("115040", year_ad, year_ad - 1911, "Exam 115040")]

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        return SourceExamPage(
            exam_code,
            year_ad,
            year_ad - 1911,
            "Exam 115040",
            [],
            [ParsedPaper("nurse raw", "101", "0101", "Subject", {"question": URL})],
            provider_id=self.provider_id,
        )

    def download_file(self, url: str) -> DownloadedFile:
        return DownloadedFile(b"%PDF-1.7 current paper", "application/pdf", "paper.pdf")

    def refresh_source_manifest(
        self, manifest: SourceManifest, pages: list[SourceExamPage], mirror_root: Path, now: str
    ) -> SourceManifest:
        paper = pages[0].papers[0]
        assert (mirror_root / paper.mirror_files["question"]["storage_key"]).is_file()
        manifest.probe_policy["acquired_url"] = paper.files["question"]
        return manifest


@pytest.mark.parametrize("mode", ["full", "targeted", "targeted-custom", "repair"])
def test_every_successful_acquisition_mode_keeps_provider_refreshed_manifest(
    tmp_path: Path,
    mode: str,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    aliases = data / "aliases.json"
    aliases.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "match_type": "exact",
                        "raw_pattern": "nurse raw",
                        "canonical_id": "nurse",
                        "canonical_name": "Nurse",
                    }
                ]
            }
        )
    )
    state = provider_paths(tmp_path, "moex")
    state.data_dir.mkdir(parents=True, exist_ok=True)
    prior = SourceManifest(provider_id="moex", probe_policy={"acquired_url": "stale"})
    state.source_manifest_path.write_text(json.dumps(asdict(prior)))
    common = [
        "--provider",
        "moex",
        "--data-dir",
        str(data),
        "--mirror-dir",
        str(tmp_path / "mirror"),
        "--aliases",
        str(aliases),
    ]
    provider = ManifestProvider()
    custom = tmp_path / "custom-manifest.json"
    if mode.startswith("targeted"):
        probe = tmp_path / "probe.json"
        probe.write_text(
            json.dumps(
                {
                    "provider_id": "moex",
                    "should_sync": True,
                    "changed_exam_codes": ["115040"],
                    "exam_years": {"115040": 2026},
                    "updated_manifest": asdict(prior),
                }
            )
        )
        flags = ["--manifest", str(custom)] if mode == "targeted-custom" else []
        args = build_parser().parse_args(["sync-targeted", *common, "--probe", str(probe), *flags])
        code = run_sync_targeted(args, client=provider)
    elif mode == "repair":
        failure = SyncFailure(
            "download",
            "115040",
            115,
            "101-0101-question",
            "question",
            URL,
            "earlier source failure",
        )
        state.sync_failures_path.write_text(json.dumps([asdict(failure)]))
        args = build_parser().parse_args(["repair-failures", *common])
        code = command_repair_failures(args, client=provider)
    else:
        args = build_parser().parse_args(["sync-full", *common, "--years", "2026"])
        code = command_sync(args, client=provider)
    assert code == 0
    _, catalog, failures = load_provider_state(state)
    assert len(catalog.papers) == 1
    assert failures == []
    persisted = json.loads(state.source_manifest_path.read_text())
    assert persisted["probe_policy"]["acquired_url"] == URL
    if mode == "targeted-custom":
        assert json.loads(custom.read_text()) == persisted
