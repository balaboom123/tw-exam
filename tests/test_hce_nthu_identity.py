"""Reviewed annual boundaries, physical source reuse and uncertain evidence."""

from copy import deepcopy
from dataclasses import replace
import json, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".tmp"
STAGE = ROOT
from jsonschema import Draft202012Validator
from app.classification import classify_normalized_paper
from app.models import ParsedPaper, SourceExamPage, NormalizedCatalog, to_plain_data
from app.normalizer import load_alias_rules, normalize_papers
from app.paths import provider_paths
from app.providers.hce_nthu.identity import (
    annual_papers_path,
    project_admission_groups,
    read_annual_exams,
)
from app.providers.hce_archive import HCE_CONFIGS
from app.source_material import SourceDate
from app.state import load_provider_state
from app.sync import _mirror_prefix_for_paper


class NthuProposalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_pages = []
        config = HCE_CONFIGS["hce_nthu"]
        for annual in read_annual_exams(annual_papers_path(ROOT)):
            if annual.year_roc not in {111, 112, 113, 114, 115}:
                continue
            papers = []
            for native in annual.files:
                key = f"providers/hce_nthu/{annual.year_roc}/{annual.source_exam_id}/post-bacc-medicine/{native.subject_code}/{native.file_type}.pdf"
                papers.append(
                    ParsedPaper(
                        config.category_name,
                        config.category_code,
                        native.subject_code,
                        native.subject_name_raw,
                        {native.file_type: native.source_url},
                        {native.file_type: {"storage_key": key, "checksum": native.checksum}},
                    )
                )
            cls.old_pages.append(
                SourceExamPage(
                    annual.source_exam_id,
                    annual.year_roc + 1911,
                    annual.year_roc,
                    f"{annual.year_roc}學年度{config.exam_name}",
                    [],
                    papers,
                    "hce_nthu",
                )
            )
        cls.aliases = []
        cls.pages = [project_admission_groups(page) for page in cls.old_pages]
        cls.catalog = NormalizedCatalog([], [])
        for page in cls.pages:
            normalized = normalize_papers(
                source_exam_id=page.source_exam_id,
                year_ad=page.year_ad,
                exam_name_raw=page.exam_name_raw,
                papers=page.papers,
                alias_rules=[],
                mirror_base_url="",
                mirror_metadata={},
                provider_id="hce_nthu",
            )
            assert not normalized.review_queue
            cls.catalog.papers.extend(normalized.papers)
        cls.evidence = json.loads(annual_papers_path(STAGE).read_text())
        cls.schema = Draft202012Validator(
            json.loads((STAGE / "schemas/nthu-annual-papers-v1.schema.json").read_text()),
            format_checker=Draft202012Validator.FORMAT_CHECKER,
        )

    def test_reviewed_evidence_passes_both_contracts(self):
        self.schema.validate(self.evidence)
        self.assertEqual(len(read_annual_exams(annual_papers_path(STAGE))), 5)

    def test_all_original_source_files_keep_native_paths(self):
        original = {
            (page.year_roc, url): paper.mirror_files[role]["storage_key"]
            for page in self.old_pages
            for paper in page.papers
            for role, url in paper.files.items()
        }
        count = 0
        for page in self.old_pages:
            projected = project_admission_groups(page)
            for paper in projected.papers:
                for role, url in paper.files.items():
                    before = original[page.year_roc, url]
                    self.assertEqual(
                        _mirror_prefix_for_paper(projected, paper, role) + ".pdf", before
                    )
                    self.assertEqual(paper.source_material.evidence_url, url)
                    count += 1
        self.assertEqual(count, 54)

    def test_clarification_uses_its_own_checksum_beside_a_question(self):
        page = next(p for p in self.old_pages if p.year_roc == 115)
        projected = project_admission_groups(page)
        paper = next(
            p
            for p in projected.papers
            if p.subject_code == "english" and "answer_clarification" in p.files
        )
        question = next(
            p for p in page.papers if p.subject_code == "english" and "question" in p.files
        )
        global_metadata = {
            (paper.category_code, paper.subject_code, "question"): question.mirror_files["question"]
        }
        result = normalize_papers(
            source_exam_id=page.source_exam_id,
            year_ad=page.year_ad,
            exam_name_raw=page.exam_name_raw,
            papers=[paper],
            alias_rules=self.aliases,
            mirror_base_url="",
            mirror_metadata=global_metadata,
            provider_id="hce_nthu",
        )
        self.assertFalse(result.review_queue)
        self.assertEqual(result.papers[0].stage_id, "stage-1")
        self.assertEqual(
            result.papers[0].checksum, paper.mirror_files["answer_clarification"]["checksum"]
        )

    def test_changed_payload_cannot_inherit_reviewed_identity(self):
        paper = self.catalog.papers[0]
        identity = classify_normalized_paper(replace(paper, checksum="f" * 64))
        self.assertEqual((identity.stage_id, identity.confidence), ("unknown", "review"))

    def test_wrong_event_group_subject_or_material_does_not_match(self):
        paper = next(
            p
            for p in self.catalog.papers
            if p.track_id == "hce-nthu-natural-science" and p.subject_code == "chemistry-physics"
        )
        changes = [
            {"source_exam_id": "hce-nthu-999"},
            {"category_code": "post-bacc-medicine-information-technology"},
            {"subject_code": "computer-science"},
            {"source_material": replace(paper.source_material, date=SourceDate("exam_year", 2026))},
            {
                "source_material": replace(
                    paper.source_material, evidence_url="https://adms.site.nthu.edu.tw/other.pdf"
                )
            },
        ]
        for change in changes:
            with self.subTest(change=list(change)):
                identity = classify_normalized_paper(replace(paper, **change))
                self.assertEqual((identity.stage_id, identity.confidence), ("unknown", "review"))

    def test_future_year_remains_unresolved(self):
        old = next(p for p in self.old_pages if p.year_roc == 115)
        future = replace(old, source_exam_id="hce-nthu-116", year_ad=2027, year_roc=116)
        projected = project_admission_groups(future)
        self.assertTrue(all(p.source_material.date.basis == "unknown" for p in projected.papers))
        result = normalize_papers(
            source_exam_id=projected.source_exam_id,
            year_ad=projected.year_ad,
            exam_name_raw=projected.exam_name_raw,
            papers=projected.papers,
            alias_rules=self.aliases,
            mirror_base_url="",
            mirror_metadata={},
            provider_id="hce_nthu",
        )
        self.assertTrue(result.review_queue)
        self.assertEqual({p.stage_id for p in result.papers}, {"unknown"})

    def test_unreviewed_source_and_wrong_role_stay_unresolved(self):
        old = next(p for p in self.old_pages if p.year_roc == 115)
        original = old.papers[0]
        variants = [
            replace(original, files={"question": "https://adms.site.nthu.edu.tw/new.pdf"}),
            replace(original, files={"answer_clarification": original.files["question"]}),
        ]
        for p in variants:
            with self.subTest(role=list(p.files)):
                projected = project_admission_groups(replace(old, papers=[p]))
                self.assertTrue(all(p.source_material.needs_review for p in projected.papers))

    def test_unknown_fields_fail_the_schema(self):
        changed = deepcopy(self.evidence)
        changed["events"][0]["unexpected"] = "value"
        self.assertTrue(list(self.schema.iter_errors(changed)))

    def test_conflicting_or_incomplete_annual_evidence_fails(self):
        changed = []
        x = deepcopy(self.evidence)
        x["events"].append(x["events"][0])
        changed.append(x)
        x = deepcopy(self.evidence)
        x["events"][0]["written_test_date"] = "2026-01-01"
        changed.append(x)
        x = deepcopy(self.evidence)
        x["events"][0]["groups"][1]["category_code"] = x["events"][0]["groups"][0]["category_code"]
        changed.append(x)
        x = deepcopy(self.evidence)
        x["events"][0]["files"][0]["checksum"] = "BAD"
        changed.append(x)
        x = deepcopy(self.evidence)
        x["events"][0]["rule_url"] = "https://example.com/rules.pdf"
        changed.append(x)
        x = deepcopy(self.evidence)
        x["events"][0]["files"] = [
            f for f in x["events"][0]["files"] if f["subject_code"] != "computer-science"
        ]
        changed.append(x)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            for number, document in enumerate(changed):
                with self.subTest(case=number):
                    path.write_text(json.dumps(document))
                    with self.assertRaises(ValueError):
                        read_annual_exams(path)

    def test_native_payload_versions_can_be_appended_without_dropping_old_evidence(self):
        changed = deepcopy(self.evidence)
        extra = deepcopy(changed["events"][0]["files"][0])
        extra["checksum"] = "f" * 64
        changed["events"][0]["files"].append(extra)
        self.schema.validate(changed)
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "evidence.json"
            p.write_text(json.dumps(changed))
            self.assertEqual(len(read_annual_exams(p)[0].files), 9)

    def test_native_category_is_optional_but_must_be_one_portable_segment(self):
        kwargs = dict(
            category_raw="test",
            category_code="group",
            subject_code="english",
            subject_name_raw="English",
            files={},
        )
        self.assertNotIn("source_category_code", to_plain_data(ParsedPaper(**kwargs)))
        for bad in ["../old", "/absolute", "x/y", 123]:
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    ParsedPaper(**kwargs, source_category_code=bad)


if __name__ == "__main__":
    unittest.main()
