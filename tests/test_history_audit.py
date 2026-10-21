import tempfile
import unittest
import hashlib
import json
from pathlib import Path

import pytest

from app.history_audit import build_history_coverage_audit, history_audit_exit_code
from app.models import BundleAsset, ExamAttachment, ExamOption, NormalizedCatalog, NormalizedPaper, ParsedPaper, SourceExamPage
from app.paths import provider_paths, site_paths
from app.publisher import write_provider_state, write_site_state
from app.source_material import SourceDate, SourceMaterial


@pytest.mark.parametrize('condition',['verified','missing','corrupt','unreviewed','paper_also_listed','foreign_owner'])
def test_reference_collections_require_explicit_material_and_owned_verified_bytes(tmp_path,condition):
    provider=provider_paths(tmp_path,'moex')
    key='providers/moex/115/reference-collection/form.pdf'
    data=b'%PDF-1.7 native application form'
    path=tmp_path/'mirror'/key
    if condition!='missing':
        path.parent.mkdir(parents=True)
        path.write_bytes(b'%PDF-1.7 corrupt revision' if condition=='corrupt' else data)
    source='https://example.test/form.pdf'
    material=SourceMaterial('reference',SourceDate('unknown',None),source,'申請表','Native form has no established date')
    attachment=ExamAttachment('申請表','source_asset',source,storage_key=key,checksum=hashlib.sha256(data).hexdigest(),source_material=None if condition=='unreviewed' else material)
    papers=[ParsedPaper('category','101','0101','question',{'question':'https://example.test/paper.pdf'})] if condition=='paper_also_listed' else []
    page=SourceExamPage('reference-collection',2026,115,'來源文件',[attachment],papers,'moex')
    write_provider_state(provider,[page],NormalizedCatalog([],[]),[],[],None)
    if condition=='foreign_owner':
        foreign_key=key.replace('providers/moex/','providers/other/')
        foreign_path=tmp_path/'mirror'/foreign_key
        foreign_path.parent.mkdir(parents=True)
        foreign_path.write_bytes(data)
        raw_file=provider.exams_dir/'2026.json'
        raw=json.loads(raw_file.read_text())
        raw[0]['attachments'][0]['storage_key']=foreign_key
        raw_file.write_text(json.dumps(raw))
    write_site_state(site_paths(tmp_path,'default'),[],[])
    report=build_history_coverage_audit(tmp_path,provider_ids=['moex'])
    event=report['providers'][0]['events'][0]
    expected='reference_document_collection' if condition=='verified' else 'normalization_gap' if condition in {'unreviewed','paper_also_listed'} else 'download_gap'
    assert event['status']==expected
    assert history_audit_exit_code(report,strict=True)==(condition!='verified')
    if condition in {'missing','corrupt','foreign_owner'}:assert event['reference_attachment_errors']
    if condition=='missing':
        metadata_only=build_history_coverage_audit(tmp_path,provider_ids=['moex'],check_mirror=False)
        assert metadata_only['mirror_checked'] is False
        assert metadata_only['providers'][0]['events'][0]['status']=='reference_document_collection'


class _ProbeClient:
    provider_id = "moex"

    def discover_available_years(self) -> list[int]:
        return [2026]

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        return [
            ExamOption(code="115030", year_ad=year_ad, year_roc=115, label="stored event"),
            ExamOption(code="115040", year_ad=year_ad, year_roc=115, label="source-only event"),
        ]


class _FailingYearProbeClient:
    provider_id = "moex"

    def discover_available_years(self) -> list[int]:
        return [2026]

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        raise RuntimeError("year source unavailable")


class _FailingYearsProbeClient:
    provider_id = "moex"

    def discover_available_years(self) -> list[int]:
        raise RuntimeError("year index unavailable")


class HistoryAuditTests(unittest.TestCase):
    def test_probe_errors_count_as_parser_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            for client, expected_status in (
                (_FailingYearProbeClient(), "partial"),
                (_FailingYearsProbeClient(), "error"),
            ):
                with self.subTest(expected_status=expected_status):
                    report = build_history_coverage_audit(
                        root, provider_ids=["moex"], probe_sources=True,
                        check_mirror=False, clients={"moex": client},
                    )
                    self.assertEqual(report["providers"][0]["source_probe"]["status"], expected_status)
                    self.assertEqual(report["summary"]["parser_gap"], 1)
                    self.assertEqual(history_audit_exit_code(report, strict=True), 1)

    def test_audit_explicitly_disposes_single_year_publication_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            provider = provider_paths(root, "moex")
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
                provider,
                raw_pages=[raw_page],
                normalized=NormalizedCatalog(papers=[paper], review_queue=[]),
                aliases=[],
                failures=[],
                manifest=None,
            )
            mirror_path = root / "mirror" / "providers" / "moex" / paper.storage_key
            mirror_path.parent.mkdir(parents=True, exist_ok=True)
            mirror_path.write_bytes(b"paper")
            write_site_state(site_paths(root, "default"), [], [])

            report = build_history_coverage_audit(root, provider_ids=["moex"])

        event = report["providers"][0]["events"][0]
        self.assertEqual(event["status"], "excluded_by_publication_policy")
        self.assertEqual(event["policy_eligible_bundle_ids"], [])
        self.assertEqual(event["policy_excluded_bundle_ids"], ["nurse"])
        self.assertEqual(report["summary"]["excluded_by_publication_policy"], 1)
        self.assertEqual(history_audit_exit_code(report, strict=True), 0)
        self.assertEqual(history_audit_exit_code({"summary": {"normalized_not_published": 1}}, strict=True), 1)
        self.assertEqual(history_audit_exit_code({"summary": {"sync_failure_recorded": 1}}, strict=True), 1)

    def test_audit_reports_missing_mirror_and_authoritative_source_only_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            provider = provider_paths(root, "moex")
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
                storage_key="providers/moex/115/115030/101/0101/question.pdf",
            )
            write_provider_state(
                provider,
                raw_pages=[raw_page],
                normalized=NormalizedCatalog(papers=[paper], review_queue=[]),
                aliases=[],
                failures=[],
                manifest=None,
            )
            bundle = BundleAsset(
                canonical_id="nurse",
                canonical_name="Nurse",
                years=[115, 114],
                file_count=1,
                storage_key="bundles/sites/default/nurse.zip",
                asset_name="nurse.zip",
            )
            write_site_state(site_paths(root, "default"), [bundle], [])

            report = build_history_coverage_audit(
                root,
                provider_ids=["moex"],
                probe_sources=True,
                clients={"moex": _ProbeClient()},
            )

        provider_report = report["providers"][0]
        event = provider_report["events"][0]
        self.assertEqual(event["status"], "download_gap")
        self.assertEqual(event["published_bundle_ids"], ["nurse"])
        self.assertEqual(event["missing_mirror_files"], ["providers/moex/115/115030/101/0101/question.pdf"])
        self.assertEqual(provider_report["source_probe"]["status"], "ok")
        self.assertEqual(
            provider_report["source_probe"]["source_only_events"],
            [
                {
                    "source_exam_id": "115040",
                    "year_ad": 2026,
                    "year_roc": 115,
                    "label": "source-only event",
                }
            ],
        )
        self.assertEqual(report["summary"]["download_gap"], 1)
        self.assertEqual(report["summary"]["parser_gap"], 1)
        self.assertEqual(history_audit_exit_code(report, strict=True), 1)

    def test_audit_does_not_probe_sources_unless_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            report = build_history_coverage_audit(Path(tmp_dir), provider_ids=["moex"], probe_sources=False)

        self.assertEqual(report["providers"][0]["source_probe"]["status"], "not_requested")
        self.assertEqual(report["summary"]["parser_gap"], 0)


class AnnouncedEventTests(unittest.TestCase):
    """special_admission discovered 116學年度身心障礙學生升學大專校院甄試 on
    2026-08-08. The exam is announced but not yet held, so its page lists no
    papers, and treating that as a normalization gap failed the strict audit and
    blocked every deploy for a day.
    """

    def _audit(self, papers: list) -> dict:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            provider = provider_paths(root, "moex")
            write_provider_state(
                provider,
                raw_pages=[
                    SourceExamPage(
                        provider_id="moex",
                        source_exam_id="116010",
                        year_ad=2027,
                        year_roc=116,
                        exam_name_raw="announced but not yet held",
                        attachments=[],
                        papers=papers,
                    )
                ],
                normalized=NormalizedCatalog(papers=[], review_queue=[]),
                aliases=[],
                failures=[],
                manifest=None,
            )
            write_site_state(site_paths(root, "default"), [], [])
            return build_history_coverage_audit(root, provider_ids=["moex"])

    def test_an_event_whose_source_lists_no_papers_does_not_fail_the_gate(self) -> None:
        report = self._audit([])

        self.assertEqual(report["providers"][0]["events"][0]["status"], "awaiting_papers")
        self.assertEqual(report["summary"].get("normalization_gap", 0), 0)
        self.assertEqual(history_audit_exit_code(report, strict=True), 0)

    def test_a_page_that_lists_papers_but_normalizes_none_is_still_a_gap(self) -> None:
        # The guard this check exists for must keep working: papers on the page
        # and nothing in the catalog means normalization dropped them.
        report = self._audit(
            [
                ParsedPaper(
                    category_raw="category",
                    category_code="101",
                    subject_code="0101",
                    subject_name_raw="subject",
                    files={"question": "https://example.test/question.pdf"},
                )
            ]
        )

        self.assertEqual(report["providers"][0]["events"][0]["status"], "normalization_gap")
        self.assertEqual(history_audit_exit_code(report, strict=True), 1)


if __name__ == "__main__":
    unittest.main()
