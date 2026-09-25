import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from app.audit import build_publication_backlog
from app.history_audit import build_history_coverage_audit, history_audit_exit_code
from app.models import NormalizedCatalog, NormalizedPaper, SourceExamPage
from app.paths import provider_paths, site_paths
from app.publication_quarantine import (
    load_quarantine,
    quarantine_path,
    quarantined_provider_ids,
)
from app.publisher import load_site_catalog, write_provider_state, write_site_state
from app.site_registry import get_site_config

ROOT = Path(__file__).resolve().parents[1]


def _entry(provider_id: str = "sfi_cert", **overrides) -> dict:
    entry = {
        "provider_id": provider_id,
        "site_id": "default",
        "status": "wrong_identity",
        "reason": "Every published file is assigned to the wrong official event.",
        "evidence_path": "evidence.json",
        "spec_path": "spec.md",
    }
    entry.update(overrides)
    return entry


def _write(root: Path, entries: list[dict]) -> Path:
    path = quarantine_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema_version": 1, "quarantine": entries}, ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "evidence.json").write_text("{}", encoding="utf-8")
    (root / "spec.md").write_text("# spec\n", encoding="utf-8")
    return path


class QuarantineLoadingTests(unittest.TestCase):
    def test_missing_file_quarantines_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            self.assertEqual(quarantined_provider_ids(Path(tmp_dir), site_id="default"), frozenset())

    def test_entries_are_scoped_to_their_site(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            _write(root, [_entry("sfi_cert"), _entry("tqc_cert", site_id="other")])
            self.assertEqual(quarantined_provider_ids(root, site_id="default"), frozenset({"sfi_cert"}))
            self.assertEqual(quarantined_provider_ids(root, site_id="other"), frozenset({"tqc_cert"}))

    def test_same_provider_may_be_quarantined_on_separate_sites(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            _write(root, [_entry("sfi_cert"), _entry("sfi_cert", site_id="other")])
            self.assertEqual(quarantined_provider_ids(root, site_id="default"), frozenset({"sfi_cert"}))
            self.assertEqual(quarantined_provider_ids(root, site_id="other"), frozenset({"sfi_cert"}))

    def test_duplicate_entry_for_one_site_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            _write(root, [_entry("sfi_cert"), _entry("sfi_cert")])
            with self.assertRaisesRegex(ValueError, "duplicate quarantine entry"):
                load_quarantine(root, site_id="default")

    def test_unsupported_status_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            _write(root, [_entry(status="looks_wrong")])
            with self.assertRaisesRegex(ValueError, "unsupported status"):
                load_quarantine(root, site_id="default")

    def test_blank_reason_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            _write(root, [_entry(reason="  ")])
            with self.assertRaisesRegex(ValueError, "reason must be a non-empty string"):
                load_quarantine(root, site_id="default")

    def test_dangling_evidence_pointer_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            _write(root, [_entry(evidence_path="data/providers/sfi_cert/source-manifest.json")])
            with self.assertRaisesRegex(ValueError, "evidence_path .* does not exist"):
                load_quarantine(root, site_id="default")

    def test_dangling_spec_pointer_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            _write(root, [_entry(spec_path="docs/developer/providers/sfi_cert-spec.md")])
            with self.assertRaisesRegex(ValueError, "spec_path .* does not exist"):
                load_quarantine(root, site_id="default")


def _paper(provider_id: str, canonical_id: str) -> NormalizedPaper:
    return NormalizedPaper(
        canonical_id=canonical_id,
        canonical_name=canonical_id,
        year_roc=115,
        exam_name_raw="115年測試考試",
        category_raw="測試類科",
        subject_name_raw="測試科目",
        paper_code="301-0608-question",
        file_type="question",
        download_url_source="https://example.test/paper.pdf",
        category_code="301",
        source_exam_id="115010",
        subject_code="0608",
        provider_id=provider_id,
    )


def _seed_provider(root: Path, provider_id: str, canonical_id: str) -> None:
    write_provider_state(
        provider_paths(root, provider_id),
        raw_pages=[],
        normalized=NormalizedCatalog(papers=[_paper(provider_id, canonical_id)], review_queue=[]),
        aliases=[],
        failures=[],
        manifest=None,
    )


class SiteProjectionTests(unittest.TestCase):
    def _seed_required(self, root: Path) -> set[str]:
        required = set(get_site_config("default").required_provider_ids)
        for provider_id in required:
            _seed_provider(root, provider_id, f"kept-{provider_id}")
        return required

    def test_quarantined_provider_is_dropped_from_the_site_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            required = self._seed_required(root)
            _seed_provider(root, "sfi_cert", "dropped-sfi")
            _write(root, [_entry("sfi_cert")])

            normalized, _failures = load_site_catalog(root, site_id="default")

            self.assertEqual({paper.provider_id for paper in normalized.papers}, required)

    def test_quarantine_excludes_an_otherwise_publishable_backlog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self._seed_required(root)
            paper_115 = _paper("sfi_cert", "dropped-sfi")
            paper_114 = replace(paper_115, year_roc=114, source_exam_id="114010", paper_code="301-0608-114-question")
            write_provider_state(
                provider_paths(root, "sfi_cert"), raw_pages=[],
                normalized=NormalizedCatalog(papers=[paper_115, paper_114], review_queue=[]),
                aliases=[], failures=[], manifest=None,
            )
            _write(root, [_entry("sfi_cert")])

            withheld = build_publication_backlog(root)
            _write(root, [])
            visible = build_publication_backlog(root)

            self.assertEqual(withheld["unpublished_bundle_count"], 0)
            self.assertEqual(visible["provider_ids"], ["sfi_cert"])

    def test_projection_keeps_the_provider_without_a_quarantine_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            required = self._seed_required(root)
            _seed_provider(root, "sfi_cert", "kept-sfi")
            _write(root, [])

            normalized, _failures = load_site_catalog(root, site_id="default")

            self.assertEqual({paper.provider_id for paper in normalized.papers}, required | {"sfi_cert"})

    def test_quarantining_a_required_provider_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            required = sorted(self._seed_required(root))
            _write(root, [_entry(required[0])])

            with self.assertRaisesRegex(ValueError, "Required providers cannot be quarantined"):
                load_site_catalog(root, site_id="default")


class HistoryAuditQuarantineTests(unittest.TestCase):
    def _seed_multi_year_provider(self, root: Path, provider_id: str) -> None:
        """Seed enough years that the bundle is publication-eligible on its own."""
        raw_pages = []
        papers = []
        for year_roc in (114, 115):
            source_exam_id = f"{year_roc}030"
            raw_pages.append(
                SourceExamPage(
                    provider_id=provider_id,
                    source_exam_id=source_exam_id,
                    year_ad=year_roc + 1911,
                    year_roc=year_roc,
                    exam_name_raw=f"{provider_id} {source_exam_id}",
                    attachments=[],
                    papers=[],
                )
            )
            paper = NormalizedPaper(
                provider_id=provider_id,
                canonical_id="quarantined-subject",
                canonical_name="Quarantined Subject",
                year_roc=year_roc,
                exam_name_raw=f"{provider_id} {source_exam_id}",
                category_raw="Category",
                subject_name_raw="Subject",
                paper_code="101-0101-question",
                file_type="question",
                download_url_source="https://example.test/question.pdf",
                category_code="101",
                source_exam_id=source_exam_id,
                subject_code="0101",
                storage_key=f"{year_roc}/{source_exam_id}/101/0101/question.pdf",
            )
            papers.append(paper)
            mirror_path = root / "mirror" / "providers" / provider_id / paper.storage_key
            mirror_path.parent.mkdir(parents=True, exist_ok=True)
            mirror_path.write_bytes(b"paper")
        write_provider_state(
            provider_paths(root, provider_id),
            raw_pages=raw_pages,
            normalized=NormalizedCatalog(papers=papers, review_queue=[]),
            aliases=[],
            failures=[],
            manifest=None,
        )
        write_site_state(site_paths(root, "default"), [], [])

    def test_quarantined_events_are_reported_separately_from_publication_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self._seed_multi_year_provider(root, "sfi_cert")
            _write(root, [_entry("sfi_cert")])

            report = build_history_coverage_audit(root, provider_ids=["sfi_cert"])

        statuses = {event["status"] for event in report["providers"][0]["events"]}
        self.assertEqual(statuses, {"withheld_by_quarantine"})
        self.assertEqual(report["summary"]["withheld_by_quarantine"], 2)
        self.assertEqual(report["summary"].get("normalized_not_published", 0), 0)

    def test_the_same_events_are_a_publication_gap_without_quarantine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self._seed_multi_year_provider(root, "sfi_cert")
            _write(root, [])

            report = build_history_coverage_audit(root, provider_ids=["sfi_cert"])

        # Without an entry the identical state is an unexplained gap, so
        # quarantine must never be the reason a red gate turns green by accident.
        self.assertEqual(report["summary"]["normalized_not_published"], 2)
        self.assertEqual(report["summary"].get("withheld_by_quarantine", 0), 0)
        self.assertEqual(history_audit_exit_code(report, strict=True), 1)


class MirrorCheckScopeTests(unittest.TestCase):
    """An absent mirror tree is unverifiable, not a catastrophic download gap."""

    def _seed(self, root: Path) -> None:
        raw_page = SourceExamPage(
            provider_id="moex",
            source_exam_id="115030",
            year_ad=2026,
            year_roc=115,
            exam_name_raw="MOEX 115030",
            attachments=[],
            papers=[],
        )
        paper = NormalizedPaper(
            provider_id="moex",
            canonical_id="nurse",
            canonical_name="Nurse",
            year_roc=115,
            exam_name_raw="MOEX 115030",
            category_raw="Nurse",
            subject_name_raw="Subject",
            paper_code="101-0101-question",
            file_type="question",
            download_url_source="https://example.test/question.pdf",
            category_code="101",
            source_exam_id="115030",
            subject_code="0101",
            storage_key="115/115030/101/0101/question.pdf",
        )
        write_provider_state(
            provider_paths(root, "moex"),
            raw_pages=[raw_page],
            normalized=NormalizedCatalog(papers=[paper], review_queue=[]),
            aliases=[],
            failures=[],
            manifest=None,
        )
        write_site_state(site_paths(root, "default"), [], [])

    def test_absent_mirror_reports_download_gaps_when_checked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self._seed(root)

            report = build_history_coverage_audit(root, provider_ids=["moex"], check_mirror=True)

        self.assertEqual(report["summary"]["download_gap"], 1)
        self.assertTrue(report["mirror_checked"])
        self.assertEqual(history_audit_exit_code(report, strict=True), 1)

    def test_skipping_the_mirror_check_drops_only_that_dimension(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self._seed(root)

            report = build_history_coverage_audit(root, provider_ids=["moex"], check_mirror=False)

        self.assertEqual(report["summary"].get("download_gap", 0), 0)
        self.assertFalse(report["mirror_checked"])
        # The event is still accounted for, just under its non-mirror status.
        self.assertEqual(report["summary"]["excluded_by_publication_policy"], 1)

    def test_a_real_publication_gap_still_fails_without_the_mirror_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            # Two years makes the bundle publication-eligible, so leaving it
            # unpublished is a genuine gap rather than a min-years exclusion.
            raw_pages, papers = [], []
            for year_roc in (114, 115):
                source_exam_id = f"{year_roc}030"
                raw_pages.append(
                    SourceExamPage(
                        provider_id="moex",
                        source_exam_id=source_exam_id,
                        year_ad=year_roc + 1911,
                        year_roc=year_roc,
                        exam_name_raw=f"MOEX {source_exam_id}",
                        attachments=[],
                        papers=[],
                    )
                )
                papers.append(
                    NormalizedPaper(
                        provider_id="moex",
                        canonical_id="nurse",
                        canonical_name="Nurse",
                        year_roc=year_roc,
                        exam_name_raw=f"MOEX {source_exam_id}",
                        category_raw="Nurse",
                        subject_name_raw="Subject",
                        paper_code="101-0101-question",
                        file_type="question",
                        download_url_source="https://example.test/question.pdf",
                        category_code="101",
                        source_exam_id=source_exam_id,
                        subject_code="0101",
                        storage_key=f"{year_roc}/{source_exam_id}/101/0101/question.pdf",
                    )
                )
            write_provider_state(
                provider_paths(root, "moex"),
                raw_pages=raw_pages,
                normalized=NormalizedCatalog(papers=papers, review_queue=[]),
                aliases=[],
                failures=[],
                manifest=None,
            )
            write_site_state(site_paths(root, "default"), [], [])

            report = build_history_coverage_audit(root, provider_ids=["moex"], check_mirror=False)

        # Skipping the mirror dimension must not make unrelated gaps disappear.
        self.assertEqual(report["summary"]["normalized_not_published"], 2)
        self.assertEqual(report["summary"].get("download_gap", 0), 0)
        self.assertFalse(report["mirror_checked"])
        self.assertEqual(history_audit_exit_code(report, strict=True), 1)


class RepositoryQuarantineTests(unittest.TestCase):
    """The checked-in quarantine must stay consistent with the site registry."""

    def test_every_quarantined_provider_is_still_registered(self) -> None:
        site_config = get_site_config("default")
        entries = load_quarantine(ROOT, site_id="default")
        self.assertTrue(entries, "expected a non-empty checked-in quarantine")
        unregistered = sorted(set(entries) - set(site_config.provider_ids))
        # Quarantine withholds publication only. Dropping a provider from the
        # registry instead would remove it from the source-inventory, catalog,
        # and history denominators and hide the defect rather than expose it.
        self.assertEqual(unregistered, [], f"quarantined providers must stay registered: {unregistered}")

    def test_required_providers_are_never_quarantined(self) -> None:
        site_config = get_site_config("default")
        entries = load_quarantine(ROOT, site_id="default")
        quarantined_required = sorted(set(entries) & set(site_config.required_provider_ids))
        self.assertEqual(quarantined_required, [], f"required providers cannot be quarantined: {quarantined_required}")

    def test_checked_in_evidence_pointers_resolve(self) -> None:
        for provider_id, entry in sorted(load_quarantine(ROOT, site_id="default").items()):
            with self.subTest(provider_id=provider_id):
                self.assertTrue((ROOT / entry.evidence_path).is_file())
                self.assertTrue((ROOT / entry.spec_path).is_file())


if __name__ == "__main__":
    unittest.main()
