from __future__ import annotations

import contextlib
import json
from functools import lru_cache
from pathlib import Path
import shutil
import tempfile
import unittest

import pytest

from app.source_inventory import (
    check_sync_floor,
    load_source_inventory,
    provider_ids_from_paths,
    validate_source_inventory,
)


ROOT = Path(__file__).resolve().parents[1]
FLOOR_FIXTURE_PROVIDER = "teacher_recruit_tainan"


@lru_cache(maxsize=1)
def _checked_in_inventory_report() -> dict:
    return validate_source_inventory(ROOT)


@contextlib.contextmanager
def _provider_sandbox(provider_id: str):
    """Yield a repo root holding only the inventory and one provider's state.

    The provider's reviewed floor comes back with the root. Pinning the
    fixture's current counts into the assertions instead would break these
    tests whenever that source legitimately publishes something new, which
    says nothing about whether the gate works.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        shutil.copytree(ROOT / "catalog", root / "catalog")
        shutil.copytree(
            ROOT / "data" / "providers" / provider_id,
            root / "data" / "providers" / provider_id,
        )
        entry = next(
            item
            for item in load_source_inventory(root)["providers"]
            if item["provider_id"] == provider_id
        )
        yield root, entry


class SyncFloorTests(unittest.TestCase):
    def test_staged_paths_resolve_to_the_providers_they_belong_to(self) -> None:
        self.assertEqual(
            provider_ids_from_paths(
                [
                    "data/providers/moex",
                    "data/providers/moex/source-manifest.json",
                    "data/sites/default",
                    "data/providers/hakka_cert/papers/2026.json",
                    "data/providers",
                    "frontend/src",
                ]
            ),
            ["moex", "hakka_cert"],
        )

    def test_unchanged_provider_state_sits_at_the_reviewed_floor(self) -> None:
        with _provider_sandbox(FLOOR_FIXTURE_PROVIDER) as (root, _entry):
            report = check_sync_floor(root, [FLOOR_FIXTURE_PROVIDER])

        self.assertEqual(
            [item["provider_id"] for item in report["providers"]],
            [FLOOR_FIXTURE_PROVIDER],
        )
        self.assertEqual(report["providers"][0]["losses"], [])

    def test_sync_that_drops_papers_is_refused_even_with_no_recorded_failure(self) -> None:
        # This is the shape of every silent loss on main: the sync writes a
        # shorter listing and reports no failure at all.
        with _provider_sandbox(FLOOR_FIXTURE_PROVIDER) as (root, entry):
            provider_dir = root / "data" / "providers" / FLOOR_FIXTURE_PROVIDER
            papers_path = sorted((provider_dir / "papers").glob("*.json"))[-1]
            papers = json.loads(papers_path.read_text(encoding="utf-8"))
            papers_path.write_text(json.dumps(papers[:-1], ensure_ascii=False), encoding="utf-8")
            failures_path = provider_dir / "sync-failures.json"
            self.assertEqual(json.loads(failures_path.read_text(encoding="utf-8")), [])
            recorded = entry["local_state"]["normalized_paper_records"]

            with self.assertRaisesRegex(ValueError, "drops below the reviewed source inventory floor") as context:
                check_sync_floor(root, [FLOOR_FIXTURE_PROVIDER])

        message = str(context.exception)
        self.assertIn(FLOOR_FIXTURE_PROVIDER, message)
        self.assertIn(f"normalized_paper_records {recorded} -> {recorded - 1}", message)
        self.assertIn("sync failures recorded: 0", message)

    def test_sync_that_drops_every_event_reports_the_lost_years(self) -> None:
        with _provider_sandbox(FLOOR_FIXTURE_PROVIDER) as (root, entry):
            provider_dir = root / "data" / "providers" / FLOOR_FIXTURE_PROVIDER
            for year_file in provider_dir.glob("*/*.json"):
                if year_file.parent.name in {"exams", "papers"}:
                    year_file.write_text("[]", encoding="utf-8")
            recorded_events = entry["local_state"]["raw_event_pages"]
            recorded_years = entry["local_years"]

            with self.assertRaises(ValueError) as context:
                check_sync_floor(root, [FLOOR_FIXTURE_PROVIDER])

        message = str(context.exception)
        self.assertIn(f"raw_event_pages {recorded_events} -> 0", message)
        self.assertIn(
            "years dropped: " + ", ".join(str(year) for year in recorded_years),
            message,
        )

    def test_sync_that_gains_records_is_ordinary_and_passes(self) -> None:
        # The floor must not force an inventory edit for every source that
        # publishes something new, or operators will stop trusting it.
        with _provider_sandbox(FLOOR_FIXTURE_PROVIDER) as (root, entry):
            provider_dir = root / "data" / "providers" / FLOOR_FIXTURE_PROVIDER
            papers_path = sorted((provider_dir / "papers").glob("*.json"))[-1]
            papers = json.loads(papers_path.read_text(encoding="utf-8"))
            extra = dict(papers[-1])
            extra["canonical_id"] = f"{extra['canonical_id']}-added"
            extra["storage_key"] = f"{extra['storage_key']}-added"
            papers_path.write_text(
                json.dumps(papers + [extra], ensure_ascii=False),
                encoding="utf-8",
            )
            recorded = entry["local_state"]["normalized_paper_records"]

            report = check_sync_floor(root, [FLOOR_FIXTURE_PROVIDER])

        self.assertEqual(report["providers"][0]["normalized_paper_records"], recorded + 1)
        self.assertEqual(report["providers"][0]["losses"], [])

    def test_floor_check_refuses_a_provider_the_inventory_never_reviewed(self) -> None:
        with _provider_sandbox(FLOOR_FIXTURE_PROVIDER) as (root, _entry):
            with self.assertRaisesRegex(ValueError, "no entry for provider: not_a_provider"):
                check_sync_floor(root, ["not_a_provider"])


class SourceInventoryTests(unittest.TestCase):
    @pytest.mark.repo_data
    def test_current_inventory_covers_default_registry_and_matches_local_state(self) -> None:
        report = _checked_in_inventory_report()

        self.assertEqual(report["provider_count"], 35)
        self.assertEqual(report["candidate_count"], 10)
        self.assertEqual(report["discovery_manifests_present"], 34)
        self.assertEqual(report["discovery_manifests_missing"], [])
        self.assertEqual(report["discovery_manifests_blocked"], ["teacher_recruit_kaohsiung"])
        self.assertEqual(
            report["discovery_manifests_incomplete"],
            [
                "cpc_recruit",
                "moea_recruit",
                "taipower_recruit",
                "taisugar_recruit",
                "sfi_cert",
                "tabf_cert",
                "tii_cert",
                "gept_cert",
                "tocfl_cert",
                "hakka_cert",
                "taigi_cert",
                "tqc_cert",
                "ipas_cert",
            ],
        )
        # Generated provider state changes independently in scheduled syncs.
        # Pinning exact gap records here makes ordinary additions or upstream
        # de-listings fail Pages until a test fixture is manually rewritten.
        # Maintained discovery coverage is the policy boundary: a partial
        # manifest may drift in either direction, while complete coverage may
        # not omit local events (enforced by validate_source_inventory above).
        incomplete = set(report["discovery_manifests_incomplete"])
        self.assertEqual(
            [item for item in report["manifest_event_gaps"] if item["enforced"]],
            [],
        )
        for field, records in (
            ("missing_events", report["manifest_event_gaps"]),
            ("events", report["manifest_unrepresented_events"]),
        ):
            with self.subTest(field=field):
                if field == "missing_events":
                    self.assertLessEqual(
                        {item["provider_id"] for item in records},
                        incomplete,
                    )
                for item in records:
                    self.assertTrue(item[field])
                    self.assertEqual(
                        item[field],
                        [list(event) for event in sorted(set(map(tuple, item[field])))],
                    )
        self.assertEqual(report["local_state_drift"], [])

    def test_cpc_manifest_records_verified_scope_and_contamination(self) -> None:
        manifest = json.loads(
            (ROOT / "data/providers/cpc_recruit/source-manifest.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(sorted(map(int, manifest["years"])), [2009, 2011, 2012, 2013, 2019])
        self.assertEqual(len(manifest["exams"]), 5)
        self.assertEqual(len(manifest["files"]), 5)
        self.assertEqual(
            sum(item["bytes"] for item in manifest["files"].values()),
            14_071_292,
        )
        policy = manifest["probe_policy"]
        self.assertEqual(policy["accepted_asset_count"], 5)
        self.assertEqual(policy["excluded_brochure_archive"]["asset_count"], 15)
        self.assertEqual(
            policy["retained_local_contamination"]["normalized_brochure_records"],
            12,
        )
        self.assertEqual(
            policy["contracted_source_blockers"][0]["status"],
            "login_required",
        )

    def test_moea_manifest_records_exact_listing_and_contamination(self) -> None:
        manifest = json.loads(
            (ROOT / "data/providers/moea_recruit/source-manifest.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            sorted(map(int, manifest["years"])),
            [
                2002, 2004, 2006, 2007, 2008, 2009, 2011,
                2012, 2013, 2014, 2015, 2016, 2017, 2018,
                2019, 2020, 2021, 2022, 2023, 2024, 2025,
            ],
        )
        self.assertEqual(len(manifest["exams"]), 21)
        self.assertEqual(manifest["files"], {})
        policy = manifest["probe_policy"]
        self.assertEqual(policy["subject_group_count"], 515)
        self.assertEqual(policy["listed_asset_count"], 1_486)
        self.assertEqual(
            sum(item["subject_group_count"] for item in manifest["years"].values()),
            515,
        )
        self.assertEqual(
            sum(item["asset_count"] for item in manifest["years"].values()),
            1_486,
        )
        contamination = policy["retained_local_contamination"]
        self.assertEqual(contamination["normalized_records"], 370)
        self.assertTrue(contamination["all_records_are_taipower_hiring_material"])
        self.assertTrue(contamination["exact_taipower_source_url_set_duplicate"])
        self.assertTrue(contamination["exact_taipower_checksum_set_duplicate"])
        self.assertEqual(
            contamination["source_only_events"],
            [
                ["moea-recruit-100", 2011],
                ["moea-recruit-107", 2018],
                ["moea-recruit-91", 2002],
                ["moea-recruit-93", 2004],
                ["moea-recruit-98", 2009],
            ],
        )
        self.assertEqual(
            contamination["local_only_events"],
            [
                ["moea-recruit-115", 2026],
                ["moea-recruit-90", 2001],
                ["moea-recruit-92", 2003],
                ["moea-recruit-94", 2005],
                ["moea-recruit-99", 2010],
            ],
        )

    def test_taipower_manifest_records_event_scope_and_truncation(self) -> None:
        manifest = json.loads(
            (ROOT / "data/providers/taipower_recruit/source-manifest.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(len(manifest["years"]), 22)
        self.assertEqual(len(manifest["exams"]), 23)
        self.assertEqual(manifest["files"], {})
        self.assertEqual(
            manifest["years"]["2018"]["exam_codes"],
            ["taipower-recruit-107-12", "taipower-recruit-107-5"],
        )
        policy = manifest["probe_policy"]
        self.assertEqual(policy["coverage_status"], "partial")
        self.assertEqual(
            policy["known_listing_evidence"]["older_unfiltered_archive"]
            ["indexed_subject_group_count"],
            301,
        )
        retained = policy["retained_local_state"]
        self.assertEqual(retained["normalized_records"], 370)
        self.assertEqual(
            retained["source_only_events"],
            [
                ["taipower-recruit-107-12", 2018],
                ["taipower-recruit-107-5", 2018],
            ],
        )
        self.assertEqual(policy["stale_mirror_files"]["count"], 8)
        self.assertEqual(policy["stale_mirror_files"]["bytes"], 3_570_035)

    def test_taisugar_manifest_records_public_assets_and_login_blocker(self) -> None:
        manifest = json.loads(
            (ROOT / "data/providers/taisugar_recruit/source-manifest.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(len(manifest["years"]), 8)
        self.assertEqual(len(manifest["exams"]), 8)
        self.assertEqual(len(manifest["files"]), 49)
        self.assertEqual(
            sum(item["bytes"] for item in manifest["files"].values()),
            69_909_204,
        )
        policy = manifest["probe_policy"]
        self.assertEqual(policy["coverage_status"], "partial")
        self.assertEqual(policy["listing_evidence"]["declared_row_count"], 35)
        self.assertEqual(policy["current_cycle_blocker"]["status"], "login_required")
        self.assertEqual(len(policy["excluded_paper_rows"]), 2)
        self.assertTrue(
            policy["retained_local_state"]["retained_asset_matches_live_sha256"]
        )

    def test_hakka_manifest_exposes_both_official_surfaces_and_identity_gaps(self) -> None:
        manifest = json.loads(
            (ROOT / "data/providers/hakka_cert/source-manifest.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(len(manifest["years"]), 9)
        self.assertEqual(len(manifest["exams"]), 11)
        self.assertEqual(len(manifest["files"]), 40)
        policy = manifest["probe_policy"]
        self.assertEqual(policy["coverage_status"], "partial")
        self.assertEqual(policy["primary_listing"]["page_count"], 9)
        self.assertEqual(policy["primary_listing"]["unique_download_count"], 607)
        self.assertEqual(policy["primary_listing"]["adapter_accepted_count"], 140)
        self.assertEqual(
            policy["primary_listing"]["additional_in_scope_sample_bundle_count"],
            5,
        )
        self.assertEqual(
            policy["secondary_download_center"]["unique_package_count"],
            50,
        )
        self.assertEqual(
            policy["secondary_download_center"]["in_scope_question_audio_package_count"],
            15,
        )
        self.assertEqual(
            policy["source_gaps"],
            {
                "primary_current_urls_not_retained": 20,
                "primary_sample_bundles_missing_locally": 5,
                "secondary_question_audio_declared_size_differs_from_retained": 10,
                "secondary_question_audio_declared_size_matches_retained": 5,
                "secondary_question_audio_packages_unintegrated": 15,
            },
        )
        self.assertTrue(
            policy["identity_risks"]["undated_advanced_material_forced_to_2026"]
        )
        self.assertTrue(
            policy["identity_risks"]["zip_suffix_is_currently_misclassified_as_listening_audio"]
        )

    def test_sfi_manifest_exposes_wrong_identity_publication(self) -> None:
        manifest = json.loads(
            (ROOT / "data/providers/sfi_cert/source-manifest.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(sorted(map(int, manifest["years"])), [2024, 2025, 2026])
        self.assertEqual(len(manifest["exams"]), 25)
        self.assertEqual(len(manifest["files"]), 50)
        self.assertEqual(
            sum(item["bytes"] for item in manifest["files"].values()),
            11_406_623,
        )
        policy = manifest["probe_policy"]
        self.assertEqual(policy["coverage_status"], "partial")
        self.assertEqual(policy["official_row_count"], 13)
        self.assertEqual(policy["official_event_count"], 25)
        self.assertEqual(policy["official_file_count"], 50)
        self.assertEqual(
            policy["adapter_gap"]["unrecognized_official_codes"],
            ["04", "34", "36", "53", "99"],
        )
        retained = policy["retained_local_state"]
        self.assertEqual(retained["mirrors_present_and_checksum_valid"], 30)
        self.assertEqual(retained["retained_files_under_wrong_identity"], 30)
        self.assertEqual(retained["retained_files_under_correct_identity"], 0)
        self.assertEqual(len(retained["source_only_events"]), 13)
        self.assertEqual(
            retained["local_only_events"],
            [
                ["sfi-cert-aml-2025-3", 2025],
                ["sfi-cert-aml-2026-1", 2026],
                ["sfi-cert-sustainability-2026-1", 2026],
            ],
        )
        self.assertEqual(
            sum(
                item["status"] == "retained_under_wrong_identity"
                for item in manifest["files"].values()
            ),
            30,
        )
        self.assertEqual(
            sum(
                item["status"] == "source_only"
                for item in manifest["files"].values()
            ),
            20,
        )
        self.assertEqual(
            policy["publication_risk"]["published_files_under_wrong_identity"],
            30,
        )

    def test_twc_manifest_records_exact_archive_and_source_defects(self) -> None:
        manifest = json.loads(
            (ROOT / "data/providers/twc_recruit/source-manifest.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            sorted(map(int, manifest["years"])),
            [2014, 2015, 2016, 2017, 2018, 2019, 2021, 2022, 2023, 2025],
        )
        self.assertEqual(len(manifest["exams"]), 10)
        self.assertEqual(len(manifest["files"]), 10)
        self.assertEqual(
            sum(item["bytes"] for item in manifest["files"].values()),
            75_196_680,
        )
        policy = manifest["probe_policy"]
        self.assertEqual(
            policy["coverage_status"], "complete_declared_scope_with_blockers"
        )
        self.assertEqual(
            policy["current_cycle_blocker"]["status"], "login_required"
        )
        self.assertEqual(
            policy["source_integrity"]["official_sha256_mismatch_count"], 3
        )
        self.assertEqual(
            policy["source_integrity"]["asset_zip_integrity_failure_count"], 1
        )
        self.assertTrue(
            policy["retained_local_state"][
                "all_retained_assets_match_live_sha256"
            ]
        )

    def test_tabf_manifest_exposes_year_and_taxonomy_contamination(self) -> None:
        manifest = json.loads(
            (ROOT / "data/providers/tabf_cert/source-manifest.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(sorted(map(int, manifest["years"])), [2020, 2023, 2024, 2025, 2026])
        self.assertEqual(len(manifest["exams"]), 50)
        self.assertEqual(len(manifest["files"]), 127)
        self.assertEqual(
            sum(item["bytes"] for item in manifest["files"].values()),
            58_493_699,
        )
        policy = manifest["probe_policy"]
        self.assertEqual(policy["coverage_status"], "partial")
        self.assertEqual(policy["official_row_count"], 19)
        self.assertEqual(policy["official_event_count"], 50)
        self.assertEqual(policy["official_file_count"], 127)
        adapter_gap = policy["adapter_gap"]
        self.assertEqual(adapter_gap["rows_exactly_preserved_by_current_subject_classifier"], 6)
        self.assertEqual(adapter_gap["current_phids_duplicated_across_2025_and_2026"], 47)
        self.assertEqual(adapter_gap["source_only_phids"], ["431"])
        self.assertEqual(adapter_gap["local_only_phids"], ["449", "456"])
        retained = policy["retained_local_state"]
        self.assertEqual(retained["mirrors_present_and_checksum_valid"], 252)
        self.assertEqual(retained["current_records_under_correct_identity"], 41)
        self.assertEqual(retained["current_records_under_wrong_identity"], 205)
        self.assertEqual(retained["stale_local_only_records"], 6)
        self.assertEqual(len(retained["source_only_events"]), 34)
        self.assertEqual(len(retained["local_only_events"]), 82)
        statuses = {
            status: sum(item["status"] == status for item in manifest["files"].values())
            for status in {
                "source_only",
                "retained_under_correct_identity",
                "retained_under_correct_identity_with_duplicate_wrong_identity",
                "retained_under_wrong_identity",
            }
        }
        self.assertEqual(
            statuses,
            {
                "source_only": 2,
                "retained_under_correct_identity": 2,
                "retained_under_correct_identity_with_duplicate_wrong_identity": 39,
                "retained_under_wrong_identity": 84,
            },
        )
        self.assertEqual(
            policy["publication_risk"]["published_current_records_under_wrong_identity"],
            205,
        )
        self.assertEqual(
            policy["publication_risk"]["published_stale_local_only_records"],
            6,
        )
        self.assertEqual(
            policy["legal_and_technical"]["automated_mirroring_status"],
            "blocked_pending_robots_policy_decision_or_written_permission",
        )

    def test_tii_manifest_exposes_listing_transport_and_content_gaps(self) -> None:
        manifest = json.loads(
            (ROOT / "data/providers/tii_cert/source-manifest.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(sorted(map(int, manifest["years"])), [2024, 2025, 2026])
        self.assertEqual(len(manifest["exams"]), 10)
        self.assertEqual(len(manifest["files"]), 24)
        policy = manifest["probe_policy"]
        self.assertEqual(policy["coverage_status"], "partial")
        self.assertEqual(policy["official_paper_family_count"], 3)
        self.assertEqual(policy["official_event_count"], 10)
        self.assertEqual(policy["official_listed_file_count"], 24)
        self.assertEqual(policy["direct_urls_not_enumerated"], 16)
        self.assertEqual(
            policy["historical_archive_blocker"]["status"],
            "blocked_by_tls_chain",
        )
        self.assertEqual(
            policy["legal_and_technical"]["tls_status"],
            "blocked_no_verification_bypass",
        )
        retained = policy["retained_local_state"]
        self.assertEqual(retained["mirrors_present_and_checksum_valid"], 5)
        self.assertEqual(retained["current_listed_files_under_correct_identity"], 4)
        self.assertEqual(retained["current_local_files_not_in_paper_listing"], 1)
        self.assertEqual(retained["official_listed_files_source_only"], 20)
        self.assertEqual(len(retained["source_only_events"]), 7)
        self.assertEqual(retained["local_only_events"], [])
        self.assertEqual(
            {
                status: sum(
                    item["status"] == status for item in manifest["files"].values()
                )
                for status in {"source_only", "retained_under_correct_identity"}
            },
            {"source_only": 20, "retained_under_correct_identity": 4},
        )
        self.assertEqual(
            policy["publication_risk"]["published_non_paper_files_as_question"],
            1,
        )

    def test_gept_manifest_exposes_identity_payload_and_history_gaps(self) -> None:
        manifest = json.loads(
            (ROOT / "data/providers/gept_cert/source-manifest.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(sorted(map(int, manifest["years"])), [2022])
        self.assertEqual(len(manifest["exams"]), 5)
        self.assertEqual(len(manifest["files"]), 34)
        policy = manifest["probe_policy"]
        self.assertEqual(policy["coverage_status"], "partial")
        self.assertEqual(policy["official_event_count"], 5)
        self.assertEqual(policy["official_listed_file_count"], 34)
        self.assertEqual(policy["official_unique_url_count"], 32)
        self.assertEqual(policy["official_unique_url_bytes"], 183_174_255)
        self.assertEqual(
            policy["removed_historical_archive"]["listed_entry_count"],
            108,
        )
        self.assertEqual(
            policy["removed_historical_archive"]["status"],
            "blocked_removed_listing",
        )
        retained = policy["retained_local_state"]
        self.assertEqual(retained["mirror_files"], 68)
        self.assertEqual(retained["unreferenced_mirror_files"], 37)
        self.assertEqual(
            policy["publication_risk"]["wrong_event_or_year_records"],
            34,
        )
        self.assertEqual(policy["publication_risk"]["wrong_payload_records"], 3)
        self.assertEqual(
            {
                status: sum(
                    item["local_status"] == status
                    for item in manifest["files"].values()
                )
                for status in {
                    "retained_under_wrong_identity",
                    "retained_under_wrong_identity_with_wrong_bytes",
                }
            },
            {
                "retained_under_wrong_identity": 31,
                "retained_under_wrong_identity_with_wrong_bytes": 3,
            },
        )

    def test_jlpt_manifest_bounds_exact_workbook_scope_and_rights_blocker(self) -> None:
        manifest = json.loads(
            (ROOT / "data/providers/jlpt_cert/source-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(sorted(map(int, manifest["years"])), [2012, 2018])
        self.assertEqual(len(manifest["exams"]), 2)
        policy = manifest["probe_policy"]
        self.assertEqual(policy["official_listed_file_count"], 116)
        self.assertEqual(policy["official_unique_url_count"], 116)
        self.assertEqual(policy["retained_local_state"]["unreferenced_mirror_files"], 116)
        self.assertEqual(
            policy["legal_and_technical"]["redistribution_status"],
            "operator_or_legal_review_required",
        )

    def test_tocfl_manifest_exposes_runtime_year_identity(self) -> None:
        manifest = json.loads(
            (ROOT / "data/providers/tocfl_cert/source-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(sorted(map(int, manifest["years"])), [2022, 2024, 2025])
        self.assertEqual(len(manifest["exams"]), 3)
        policy = manifest["probe_policy"]
        self.assertEqual(policy["official_unique_url_count"], 95)
        self.assertEqual(policy["adapter_gap"]["wrong_event_or_year_records"], 92)
        self.assertEqual(
            policy["retained_local_state"]["source_only_events"],
            [["tocfl-cert-mock-current", 2025]],
        )

    def test_taigi_manifest_exposes_undated_identity_and_robots_blocker(self) -> None:
        manifest = json.loads(
            (ROOT / "data/providers/taigi_cert/source-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(len(manifest["exams"]), 3)
        policy = manifest["probe_policy"]
        self.assertEqual(policy["official_listed_resource_count"], 37)
        self.assertEqual(policy["accepted_exam_resource_count"], 35)
        self.assertEqual(len(policy["excluded_non_exam_resources"]), 2)
        self.assertEqual(policy["adapter_gap"]["source_year_status"], "undated")
        self.assertEqual(
            policy["legal_and_technical"]["automated_mirroring_status"],
            "blocked_pending_written_permission_or_policy_change",
        )

    def test_tqc_manifest_exposes_nine_collision_payloads(self) -> None:
        manifest = json.loads(
            (ROOT / "data/providers/tqc_cert/source-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(len(manifest["years"]), 11)
        self.assertEqual(len(manifest["exams"]), 11)
        policy = manifest["probe_policy"]
        self.assertEqual(policy["official_listed_file_count"], 44)
        reconciliation = policy["live_payload_reconciliation"]
        self.assertEqual(reconciliation["matching_retained_payloads"], 35)
        self.assertEqual(reconciliation["wrong_retained_payloads"], 9)
        self.assertEqual(len(reconciliation["mismatches"]), 9)

    def test_ipas_manifest_exposes_family_and_document_role_gaps(self) -> None:
        manifest = json.loads(
            (ROOT / "data/providers/ipas_cert/source-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(len(manifest["exams"]), 16)
        policy = manifest["probe_policy"]
        self.assertEqual(policy["official_pdf_count"], 182)
        self.assertEqual(policy["adapter_pdf_count"], 62)
        self.assertEqual(policy["omitted_pdf_count"], 120)
        self.assertEqual(
            policy["paper_classification"]["paper_like_pdf_count_in_omitted_families"],
            34,
        )
        self.assertEqual(
            policy["paper_classification"]["non_paper_pdfs_published_as_question"],
            46,
        )

    @pytest.mark.repo_data
    def test_growth_above_the_reviewed_floor_does_not_gate_the_site(self) -> None:
        # check_sync_floor already treats a sync that adds records as ordinary.
        # This validator demanded exact equality, so the first sync to succeed
        # since 2026-06-29 added one event and 24 papers and failed the
        # deploy-pages gate on 2026-08-06, leaving the published site behind
        # data that had already been committed to main.
        report = _checked_in_inventory_report()

        self.assertEqual(report["local_state_drift"], [])
        for item in report["local_state_growth"]:
            with self.subTest(provider=item["provider_id"]):
                self.assertTrue(item["gains"])
                for field in ("raw_event_pages", "normalized_paper_records"):
                    self.assertGreaterEqual(item["actual"][field], item["inventory"][field])

    @pytest.mark.repo_data
    def test_local_state_below_the_reviewed_floor_still_fails(self) -> None:
        # Relaxing growth must not relax loss. Raising one provider's recorded
        # floor above what it actually holds is the same shape as a source that
        # briefly served a short listing.
        payload = json.loads((ROOT / "catalog/source-inventory.json").read_text(encoding="utf-8"))
        entry = next(item for item in payload["providers"] if item["provider_id"] == FLOOR_FIXTURE_PROVIDER)
        inflated = entry["local_state"]["normalized_paper_records"] + 500

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            shutil.copytree(ROOT / "catalog", root / "catalog")
            # The validator only reads provider state and evidence paths, so
            # linking them keeps this off the ~400 MB copy path.
            (root / "data").symlink_to(ROOT / "data")
            (root / "docs").symlink_to(ROOT / "docs")
            inventory_path = root / "catalog" / "source-inventory.json"
            mutated = json.loads(inventory_path.read_text(encoding="utf-8"))
            target = next(
                item for item in mutated["providers"] if item["provider_id"] == FLOOR_FIXTURE_PROVIDER
            )
            target["local_state"]["normalized_paper_records"] = inflated
            inventory_path.write_text(json.dumps(mutated, ensure_ascii=False, indent=2), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "local state regressed") as context:
                validate_source_inventory(root)

        message = str(context.exception)
        self.assertIn(FLOOR_FIXTURE_PROVIDER, message)
        self.assertIn(f"normalized_paper_records {inflated} -> ", message)

    @pytest.mark.repo_data
    def test_strict_manifest_requirement_remains_red_until_snapshots_are_complete(self) -> None:
        with self.assertRaisesRegex(ValueError, "complete source discovery remains unresolved") as context:
            validate_source_inventory(ROOT, require_discovery_manifests=True)

        self.assertNotIn("teacher_recruit_kaohsiung", str(context.exception))

    def test_blocked_discovery_requires_exact_provider_coverage_ledger(self) -> None:
        payload = json.loads((ROOT / "catalog/source-inventory.json").read_text(encoding="utf-8"))
        entry = next(
            item for item in payload["providers"]
            if item["provider_id"] == "teacher_recruit_kaohsiung"
        )
        entry["evidence"].remove("catalog/source-coverage/teacher_recruit_kaohsiung.json")

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            catalog = root / "catalog"
            catalog.mkdir()
            (catalog / "source-inventory.json").write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "blocked discovery requires exact provider coverage ledger"):
                load_source_inventory(root)

    def test_inventory_loader_rejects_unsupported_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            catalog = root / "catalog"
            catalog.mkdir()
            (catalog / "source-inventory.json").write_text(
                """{
                  "schema_version": 1,
                  "inventory_version": "test",
                  "captured_at": "2026-07-29",
                  "site_id": "default",
                  "providers": [{
                    "provider_id": "moex",
                    "official_source_urls": ["https://example.test"],
                    "exam_category": "test",
                    "status": "unknown",
                    "status_reason": "test",
                    "available_years": {"mode": "unknown", "note": "test", "start_ad": null, "end_ad": null},
                    "local_years": [],
                    "local_state": {"raw_event_pages": 0, "normalized_paper_records": 0, "sync_failures": 0},
                    "discovery_snapshot": {"manifest_path": "data/providers/moex/source-manifest.json", "status": "missing", "coverage": "unknown"},
                    "restrictions": [],
                    "evidence": ["test"]
                  }],
                  "candidates": []
                }""",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "unsupported status"):
                load_source_inventory(root)


if __name__ == "__main__":
    unittest.main()
