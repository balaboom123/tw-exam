import json
import tempfile
import unittest
from pathlib import Path

from app.audit import audit_exit_code, build_catalog_audit, build_publication_backlog
from app.models import NormalizedCatalog, NormalizedPaper, ReviewItem
from app.normalizer import _derive_canonical
from app.paths import provider_paths
from app.publisher import load_site_catalog, write_provider_state
from app.state import filter_catalog_by_canonical_ids

def paper(category: str, event: str, year: int, source: str) -> NormalizedPaper:
    return NormalizedPaper(
        provider_id="moex",
        canonical_id="一般行政",
        canonical_name="一般行政",
        year_roc=year,
        exam_name_raw=event,
        category_raw=category,
        subject_name_raw="一般行政",
        paper_code=f"101-0101-{source}",
        file_type="question",
        download_url_source=f"https://source.example/{source}.pdf",
        category_code="101",
        source_exam_id=source,
        subject_code="0101",
        storage_key=f"115/{source}/101/0101/question.pdf",
    )


class ReviewQueueStalenessTests(unittest.TestCase):
    # renormalize_catalog only derives a canonical, and so only raises
    # needs_review, for a paper that has none yet. A rebuild over persisted
    # papers therefore reproduces the confidence=="review" rows but never the
    # legacy-canonicalization rows a sync writes while the records are fresh.
    # Treating the two as one population marked every such row stale the moment
    # it was written, which failed audit-catalog --strict and blocked the
    # deploy on 2026-08-06 after the first content-changing sync since 06-29.
    # The real row was moex/115110/三等考試_司法官及律師, written by the sync that
    # succeeded on 2026-08-06. deploy-pages already runs audit-catalog --strict
    # over the whole catalog, so this reproduces the shape on a fixture instead
    # of rebuilding 245k records per assertion.
    REVIEW_CATEGORY = "三等考試_司法官及律師"

    def _audit_with_queued_row(self, root: Path) -> dict:
        queued = paper(self.REVIEW_CATEGORY, "115年公務人員特種考試司法官考試（第一試）", 115, "115110")
        write_provider_state(
            provider_paths(root, "moex"),
            raw_pages=[],
            normalized=NormalizedCatalog(
                papers=[queued],
                review_queue=[
                    ReviewItem(
                        raw_category=queued.category_raw,
                        normalized_candidate=queued.canonical_name,
                        source_exam_id=queued.source_exam_id,
                        year_roc=queued.year_roc,
                        provider_id="moex",
                        reason="legacy canonicalization requires review; explicit category marker",
                    )
                ],
            ),
            aliases=[],
            failures=[],
            manifest=None,
        )
        return build_catalog_audit(root)

    def test_the_category_actually_raises_needs_review(self) -> None:
        # Guards the fixture: if this category ever stops being ambiguous the
        # test below would pass for the wrong reason.
        *_unused, needs_review = _derive_canonical("115110", self.REVIEW_CATEGORY, "", 2026, [])

        self.assertTrue(needs_review)

    def test_a_legacy_canonicalization_row_is_not_stale_while_its_papers_remain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            report = self._audit_with_queued_row(Path(tmp_dir))

        self.assertEqual(report["review_queue_stale_entries"], 0)
        self.assertEqual(audit_exit_code(report, strict=True), 0)

    def test_excusing_a_row_never_demands_new_rows(self) -> None:
        # The derivation only runs for keys the queue already holds, so it can
        # excuse an existing row but can never add one. Always deriving would
        # raise 117 further keys across MOEX alone, which is a reviewed change
        # and not something an audit should manufacture.
        with tempfile.TemporaryDirectory() as tmp_dir:
            report = self._audit_with_queued_row(Path(tmp_dir))

        self.assertEqual(report["review_queue_missing_entries"], 0)

    def test_incomplete_canonical_fields_still_rebuild_review_evidence(self) -> None:
        incomplete = paper(self.REVIEW_CATEGORY, "115年公務人員特種考試司法官考試（第一試）", 115, "115110")
        incomplete.canonical_id = ""
        incomplete.canonical_name = ""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_provider_state(
                provider_paths(root, "moex"),
                raw_pages=[],
                normalized=NormalizedCatalog(papers=[incomplete], review_queue=[]),
                aliases=[], failures=[], manifest=None,
            )
            report = build_catalog_audit(root)

        self.assertEqual(report["review_queue_missing_entries"], 1)


class CatalogAuditTests(unittest.TestCase):
    def test_audit_detects_review_queue_rows_not_present_in_current_papers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            current_paper = paper("一般行政", "公務人員高等考試三級", 115, "high-115")
            write_provider_state(
                provider_paths(root, "moex"),
                raw_pages=[],
                normalized=NormalizedCatalog(
                    papers=[current_paper],
                    review_queue=[
                        ReviewItem(
                            raw_category=current_paper.category_raw,
                            normalized_candidate=current_paper.canonical_name,
                            source_exam_id=current_paper.source_exam_id,
                            year_roc=current_paper.year_roc,
                            provider_id="moex",
                            reason="stale",
                        )
                    ],
                ),
                aliases=[],
                failures=[],
                manifest=None,
            )

            report = build_catalog_audit(root)

            self.assertEqual(report["review_queue_stale_entries"], 1)
            self.assertEqual(report["review_queue_missing_entries"], 0)
            self.assertEqual(audit_exit_code(report, strict=True), 1)

    def test_audit_scans_all_registered_provider_slots_and_reports_projected_split(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_provider_state(
                provider_paths(root, "moex"),
                raw_pages=[],
                normalized=NormalizedCatalog(papers=[
                    paper("一般行政", "公務人員高等考試三級", 115, "high-115"),
                    paper("一般行政", "公務人員高等考試三級", 114, "high-114"),
                    paper("一般行政", "公務人員普通考試", 114, "ordinary-114"),
                ], review_queue=[]),
                aliases=[],
                failures=[],
                manifest=None,
            )

            report = build_catalog_audit(root)

            self.assertEqual(report["provider_count"], 35)
            self.assertEqual(report["paper_records_scanned"], 3)
            self.assertEqual(report["records_with_identity"], 3)
            self.assertTrue(report["all_records_covered"])
            self.assertEqual(report["records_needing_review"], 0)
            self.assertGreaterEqual(report["planned_bundle_count"], 1)
            self.assertGreaterEqual(report["planned_release_shards"], 1)
            self.assertIn("planned_release_asset_counts", report)
            self.assertIn("current_release_capacity_ok", report)

    def test_audit_report_is_json_serializable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            report = build_catalog_audit(Path(tmp_dir))
            json.dumps(report, ensure_ascii=False)

    def test_release_plan_uses_v2_namespace_and_counts_aliases(self) -> None:
        from app.audit import build_release_plan
        from app.models import BundleAsset
        from app.paths import site_paths
        from app.publisher import write_site_state

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            bundle = BundleAsset(
                canonical_id="一般行政",
                canonical_name="一般行政",
                years=[115, 114],
                file_count=2,
                storage_key="bundles/sites/default/general.zip",
                asset_name="general.zip",
                release_tag="default-bundles-001",
                legacy_asset_names=["general-legacy.zip"],
            )
            write_site_state(
                site_paths(root, "default"),
                bundles=[bundle],
                frontend_bundles=[],
            )

            plan = build_release_plan(root)

            self.assertEqual(plan["schema_version"], 2)
            self.assertEqual(plan["shards"][0]["release_tag"], "default-bundles-v2-001")
            self.assertEqual(plan["shards"][0]["asset_count"], 2)
            self.assertEqual(plan["bundles"][0]["bundle_id"], "一般行政")


class PublicationBacklogTests(unittest.TestCase):
    def test_audit_reuses_classification_for_publication_backlog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_provider_state(
                provider_paths(root, "moex"),
                raw_pages=[],
                normalized=NormalizedCatalog(papers=[
                    paper("一般行政", "公務人員高等考試三級", 115, "high-115"),
                    paper("一般行政", "公務人員高等考試三級", 114, "high-114"),
                ], review_queue=[]),
                aliases=[], failures=[], manifest=None,
            )
            write_provider_state(
                provider_paths(root, "ceec_gsat"),
                raw_pages=[], normalized=NormalizedCatalog(papers=[], review_queue=[]),
                aliases=[], failures=[], manifest=None,
            )

            audit = build_catalog_audit(root, include_publication_backlog=True)
            standalone = build_publication_backlog(root)
            normalized, _failures = load_site_catalog(root, site_id="default")
            selected = filter_catalog_by_canonical_ids(normalized, set(standalone["affected_canonical_ids"]))

            self.assertEqual(audit["publication_backlog"], standalone)
            self.assertEqual(standalone["unpublished_bundle_count"], 1)
            self.assertEqual(standalone["unpublished_record_count"], 2)
            self.assertEqual(len(selected.papers), standalone["unpublished_record_count"])

    def test_incomplete_records_use_publication_backlog_load_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            incomplete = paper("一般行政", "公務人員高等考試三級", 115, "high-115")
            incomplete.canonical_id = ""
            incomplete.canonical_name = ""
            write_provider_state(
                provider_paths(root, "moex"),
                raw_pages=[], normalized=NormalizedCatalog(papers=[incomplete], review_queue=[]),
                aliases=[], failures=[], manifest=None,
            )
            write_provider_state(
                provider_paths(root, "ceec_gsat"),
                raw_pages=[], normalized=NormalizedCatalog(papers=[], review_queue=[]),
                aliases=[], failures=[], manifest=None,
            )

            audit = build_catalog_audit(root, include_publication_backlog=True)
            standalone = build_publication_backlog(root)

            self.assertEqual(audit["publication_backlog"], standalone)

if __name__ == "__main__":
    unittest.main()
