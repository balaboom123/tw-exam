"""CMU annual stage evidence must apply to each retained file independently."""

import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

from app.classification import classify_normalized_paper
from app.models import NormalizedCatalog
from app.normalizer import normalize_papers, renormalize_catalog
from app.paths import provider_paths
from app.providers.hce_cmu.identity import (
    annual_papers_path,
    read_annual_exams,
    validate_annual_papers,
)
from app.state import load_provider_state

ROOT = Path(__file__).resolve().parents[1]


class CmuIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pages, cls.catalog, failures = load_provider_state(provider_paths(ROOT, "hce_cmu"))
        assert not failures

    def test_reviewed_annual_stages_apply_to_all_native_roles(self):
        for p in self.catalog.papers:
            with self.subTest(year=p.year_roc, subject=p.subject_code, role=p.file_type):
                identity = classify_normalized_paper(p)
                self.assertEqual(identity.stage_id, "stage-1" if p.year_roc >= 112 else "unknown")
                self.assertEqual(identity.confidence, "high" if p.year_roc >= 112 else "review")
                self.assertEqual(identity.level_id, "not-applicable")
                self.assertEqual(identity.track_id, "hce-cmu")

    def test_unresolved_years_have_distinct_public_titles(self):
        identities = [classify_normalized_paper(p) for p in self.catalog.papers if p.year_roc < 112]
        self.assertEqual(len({i.bundle_id for i in identities}), 3)
        self.assertEqual(len({i.bundle_name for i in identities}), 3)
        for p in self.catalog.papers:
            if p.year_roc < 112:
                self.assertIn(
                    f"{p.year_roc}學年度｜階段待確認", classify_normalized_paper(p).bundle_name
                )

    def test_unreviewed_native_context_cannot_inherit_known_stage(self):
        original = next(
            p for p in self.catalog.papers if p.year_roc == 115 and p.file_type == "question"
        )
        for changes in (
            {"checksum": "0" * 64},
            {"download_url_source": "https://adm21.cmu.edu.tw/other.pdf"},
            {"subject_name_raw": "other"},
            {"category_code": "other"},
            {"exam_name_raw": "115學年度初試"},
            {"source_exam_id": "hce-cmu-114"},
            {"year_roc": 114},
        ):
            with self.subTest(changes=changes):
                identity = classify_normalized_paper(replace(original, **changes))
                self.assertEqual(identity.stage_id, "unknown")
                self.assertEqual(identity.confidence, "review")

    def test_question_checksum_cannot_approve_changed_paired_answer(self):
        page = next(p for p in self.pages if p.year_roc == 115)
        paper = deepcopy(next(p for p in page.papers if "answer" in p.files))
        paper.mirror_files["answer"]["checksum"] = "0" * 64
        before = deepcopy(paper)
        fresh = normalize_papers(
            page.source_exam_id,
            page.year_ad,
            page.exam_name_raw,
            [paper],
            [],
            "",
            {},
            provider_id="hce_cmu",
        )
        by_role = {p.file_type: p for p in fresh.papers}
        self.assertEqual(by_role["question"].stage_id, "stage-1")
        self.assertEqual(by_role["answer"].stage_id, "unknown")
        self.assertEqual(by_role["answer"].classification_confidence, "review")
        self.assertEqual(paper, before)
        self.assertEqual(renormalize_catalog(fresh, [], collect_reviews=True), fresh)

    def test_complete_raw_replay_equals_retained_normalization(self):
        retained = renormalize_catalog(self.catalog, [], collect_reviews=True)
        fresh = []
        for page in self.pages:
            result = normalize_papers(
                page.source_exam_id,
                page.year_ad,
                page.exam_name_raw,
                page.papers,
                [],
                "",
                {},
                provider_id="hce_cmu",
            )
            fresh.extend(result.papers)

        def key(p):
            return p.year_roc, p.subject_code, p.file_type

        self.assertEqual(sorted(fresh, key=key), sorted(retained.papers, key=key))

    def test_native_evidence_passes_schema_and_retained_reference_anchors(self):
        schema = json.loads((ROOT / "schemas/cmu-annual-papers-v1.schema.json").read_text())
        Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(
            json.loads(annual_papers_path(ROOT).read_text())
        )
        self.assertEqual(len(read_annual_exams(annual_papers_path(ROOT))), 7)
        self.assertEqual(validate_annual_papers(ROOT), 44)

    def test_invalid_evidence_cannot_approve_a_stage_or_change_native_context(self):
        original = json.loads(annual_papers_path(ROOT).read_text())
        mutations = [
            lambda d: d["events"][0].update(stage_id="stage-1"),
            lambda d: d["events"][1].update(stage_id="stage-1"),
            lambda d: d["events"][3].update(evidence_ids=["interview-113"]),
            lambda d: d["events"][3].update(evidence_ids=["interview-112", "interview-112"]),
            lambda d: d["events"][3].update(source_exam_id="hce-cmu-113"),
            lambda d: d["events"][3].update(year_roc=True),
            lambda d: d["events"].append(deepcopy(d["events"][3])),
            lambda d: d["events"][3]["files"].pop(0),
            lambda d: d["events"][3]["files"].append(deepcopy(d["events"][3]["files"][0])),
            lambda d: d["events"][3]["files"][0].update(source_url="https://example.org/paper.pdf"),
            lambda d: d["events"][3]["files"][0].update(checksum="bad"),
            lambda d: d["events"][3]["files"][0].update(subject_code="all"),
            lambda d: d["references"][0].update(pdf_pages=[0]),
            lambda d: d["references"][0].update(kind="historical_context"),
            lambda d: d["references"].append(deepcopy(d["references"][0])),
        ]
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "facts.json"
            for change in mutations:
                with self.subTest(change=change):
                    document = deepcopy(original)
                    change(document)
                    target.write_text(json.dumps(document))
                    with self.assertRaises(ValueError):
                        read_annual_exams(target)

    def test_evidence_requires_a_retained_native_anchor(self):
        with (
            patch(
                "app.state.load_provider_state", return_value=([], NormalizedCatalog([], []), [])
            ),
            patch("app.source_revisions.load_source_revisions", return_value=[]),
        ):
            with self.assertRaisesRegex(ValueError, "retained source anchor"):
                validate_annual_papers(ROOT)

    def test_external_mirror_metadata_also_classifies_each_paired_version(self):
        page = next(p for p in self.pages if p.year_roc == 115)
        paper = deepcopy(next(p for p in page.papers if "answer" in p.files))
        metadata = {
            (paper.category_code, paper.subject_code, r): m for r, m in paper.mirror_files.items()
        }
        metadata[paper.category_code, paper.subject_code, "answer"]["checksum"] = "0" * 64
        paper.mirror_files = {}
        fresh = normalize_papers(
            page.source_exam_id,
            page.year_ad,
            page.exam_name_raw,
            [paper],
            [],
            "",
            metadata,
            provider_id="hce_cmu",
        )
        self.assertEqual(
            {p.file_type: p.stage_id for p in fresh.papers},
            {"question": "stage-1", "answer": "unknown"},
        )

    def test_publication_keeps_supported_stage_and_holds_unresolved_annual_candidates(self):
        from app.bundler import build_bundles, public_bundle_ids, public_bundle_ids_from_indexes
        from app.provider_index import load_provider_index
        from app.site_registry import get_site_config

        policy = get_site_config("default")
        self.assertNotIn("hce-cmu", policy.public_min_years_by_canonical_prefix)
        catalog = renormalize_catalog(self.catalog, [], collect_reviews=True)
        options = dict(
            min_years=policy.public_min_years,
            min_years_by_canonical_prefix=policy.public_min_years_by_canonical_prefix,
        )
        expected = {p.bundle_id for p in catalog.papers if p.stage_id == "stage-1"}
        self.assertEqual(len(expected), 1)
        self.assertEqual(public_bundle_ids(catalog, **options), expected)
        self.assertEqual(
            public_bundle_ids_from_indexes(
                [load_provider_index(provider_paths(ROOT, "hce_cmu"))], **options
            ),
            expected,
        )
        with tempfile.TemporaryDirectory() as temp:
            # This consumer test supplies its own checksum-coherent bytes.
            # Native context and stage approval are checked above; CI has no
            # gitignored official mirror and must not silently depend on it.
            root = Path(temp)
            papers = []
            for paper in catalog.papers:
                payload = b"%PDF-1.4\nfixture " + paper.checksum.encode() + b"\n%%EOF"
                target = root / "mirror" / paper.storage_key
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
                papers.append(replace(paper, checksum=hashlib.sha256(payload).hexdigest()))
            fixture_catalog = NormalizedCatalog(papers, catalog.review_queue)
            result = build_bundles(
                root / "bundles", root / "mirror", fixture_catalog, "", **options
            )
            self.assertEqual(result.failures, [])
            self.assertEqual({b.bundle_id for b in result.bundles}, expected)
            self.assertEqual(sum(b.file_count for b in result.bundles), 27)
        self.assertEqual(len(catalog.papers), 44)
        self.assertEqual(sum(p.stage_id == "unknown" for p in catalog.papers), 17)
