import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from app.models import NormalizedPaper
from app.normalizer import normalize_papers
from app.providers.teacher_recruit_kaohsiung.evidence import (
    annual_exams,
    legacy_category_targets,
    native_programmes_path,
    read_annual_exams,
    resolve_identity,
    validate_public_papers,
)
from app.publication_metadata import derive_public_metadata, public_coverage_notes

ROOT = Path(__file__).resolve().parents[1]


def normalized_native_rows():
    rows = []
    for exam in annual_exams():
        page = exam.source_page()
        checksums = {file.source_url: file.checksum for file in exam.files}
        metadata = {
            (paper.category_code, paper.subject_code, role): {"checksum": checksums[url]}
            for paper in page.papers
            for role, url in paper.files.items()
        }
        catalog = normalize_papers(
            page.source_exam_id,
            page.year_ad,
            page.exam_name_raw,
            page.papers,
            [],
            "",
            metadata,
            provider_id=page.provider_id,
        )
        if catalog.review_queue:
            raise AssertionError(catalog.review_queue)
        rows.extend(catalog.papers)
    return rows


class KaohsiungPublicationBoundaryTests(unittest.TestCase):
    def test_search_retains_region_applicability_and_essay_answer_scope_is_explicit(self):
        rows = normalized_native_rows()
        general = [p for p in rows if p.track_id == "kaohsiung-elementary-general"]
        aliases, _subjects = derive_public_metadata(
            general, bundle_id=general[0].bundle_id, canonical_name=general[0].canonical_name
        )
        self.assertIn("偏遠地區學校組", aliases)
        self.assertIn("原住民地區及原住民重點學校組", aliases)
        essay = next(p for p in rows if p.subject_code == "counselor-knowledge")
        self.assertEqual(
            public_coverage_notes(essay), ["「專任輔導教師輔導知能」為申論題，官方未提供參考解答。"]
        )
        self.assertEqual(public_coverage_notes(replace(essay, checksum="0" * 64)), [])

    def test_complete_native_scope_normalizes_all_punctuation_forms_without_merging_posts(self):
        rows = normalized_native_rows()
        self.assertEqual(len(rows), 91)
        self.assertEqual(len({paper.bundle_id for paper in rows}), 18)
        self.assertEqual(len({paper.bundle_name for paper in rows}), 18)
        self.assertEqual({paper.stage_id for paper in rows}, {"stage-1"})
        self.assertEqual({paper.level_id for paper in rows}, {"not-applicable"})
        self.assertEqual({paper.classification_confidence for paper in rows}, {"high"})
        validate_public_papers(rows, complete_history=True)

    def test_publication_rejects_unresolved_or_wrong_classified_identity(self):
        rows = normalized_native_rows()
        for changes in [
            {"classification_confidence": "review"},
            {"exam_series_id": "teacher-recruitment"},
            {"track_id": "kaohsiung-unknown"},
            {"level_id": "level-3"},
            {"subject_name_raw": "other native booklet"},
        ]:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ValueError, "exact native programme"):
                    validate_public_papers(
                        [replace(rows[0], **changes), *rows[1:]], complete_history=True
                    )

    def test_omitting_a_shared_bilingual_post_is_not_complete_native_history(self):
        rows = [
            paper
            for paper in normalized_native_rows()
            if paper.track_id != "kaohsiung-elementary-visual-arts-bilingual"
        ]
        with self.assertRaisesRegex(ValueError, "omitted or duplicated"):
            validate_public_papers(rows, complete_history=True)

    def test_legacy_keys_only_redirect_to_the_exact_complete_special_education_successors(self):
        rows = normalized_native_rows()
        special = annual_exams()[1]
        for source in special.legacy_sources:
            previous = NormalizedPaper(
                canonical_id="teacher-recruit-kaohsiung",
                canonical_name="高雄市教師甄試",
                paper_code="retained",
                **source.source_record,
            )
            targets = legacy_category_targets(previous, rows)
            expected = 3 if source.native_file_id.startswith("disability-") else 2
            self.assertEqual(len(targets), expected)
            self.assertTrue(all(p.exam_series_id == special.series_id for p in targets))
            incomplete = [p for p in rows if p is not targets[0]]
            self.assertEqual(legacy_category_targets(previous, incomplete), [])
            self.assertEqual(
                legacy_category_targets(replace(previous, checksum="0" * 64), rows), []
            )


class KaohsiungNativeMeaningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.elementary, self.special = annual_exams()

    def test_official_posts_remain_distinct_when_the_written_booklet_is_shared(self) -> None:
        self.assertEqual(len(self.elementary.jobs), 13)
        self.assertEqual(len(self.special.jobs), 5)
        jobs = {job.track_id: job for job in self.elementary.jobs}
        for ordinary, other in [
            ("music", "music-arts-class"),
            ("visual-arts", "visual-arts-bilingual"),
            ("physical-education", "physical-education-bilingual"),
        ]:
            with self.subTest(posts=(ordinary, other)):
                first = jobs["kaohsiung-elementary-" + ordinary]
                second = jobs["kaohsiung-elementary-" + other]
                self.assertNotEqual(first.track_id, second.track_id)
                self.assertEqual(
                    self.elementary.papers_for(first), self.elementary.papers_for(second)
                )
        self.assertEqual(
            {job.school_level for job in self.special.jobs}, {"elementary", "junior", "preschool"}
        )
        self.assertEqual(sum(job.booklets == ("disability",) for job in self.special.jobs), 3)
        self.assertEqual(sum(job.booklets == ("gifted",) for job in self.special.jobs), 2)

    def test_distinct_common_papers_and_four_essay_counselor_paper_follow_the_annual_rule(
        self,
    ) -> None:
        jobs = {job.code: job for job in self.elementary.jobs}
        general = jobs["job-elementary-general"]
        counselor = jobs["job-elementary-dedicated-counselor"]
        specialist = jobs["job-elementary-science"]
        self.assertEqual(general.booklets, ("general-common", "education-professional"))
        self.assertEqual(general.weights, (60, 40))
        self.assertEqual(counselor.booklets, ("counselor-knowledge", "education-professional"))
        self.assertEqual(counselor.weights, (60, 40))
        self.assertEqual(specialist.booklets, ("specialist-common", "science"))
        self.assertEqual(specialist.weights, (40, 60))
        papers = self.elementary.papers_for(counselor)
        essays = [file for file in papers if file.subject_code == "counselor-knowledge"]
        self.assertEqual(
            [(file.file_type, file.multiple_choice_count, file.essay_count) for file in essays],
            [("question", 0, 4)],
        )
        self.assertEqual(len(papers), 4)
        self.assertEqual(len(self.elementary.source_page().papers), 76)
        self.assertEqual(len(self.special.source_page().papers), 15)

    def test_final_roles_survive_nine_identical_keys_and_two_real_key_changes(self) -> None:
        suggested = {
            file.subject_code: file for file in self.elementary.files if file.file_type == "answer"
        }
        final = {
            file.subject_code: file
            for file in self.elementary.files
            if file.file_type == "corrected_answer"
        }
        same = {
            subject
            for subject, file in suggested.items()
            if file.checksum == final[subject].checksum
        }
        changed = suggested.keys() - same
        self.assertEqual(len(same), 9)
        self.assertEqual(changed, {"visual-arts", "physical-education"})
        for subject in same:
            self.assertNotEqual(suggested[subject].source_url, final[subject].source_url)
            self.assertNotEqual(suggested[subject].notice_id, final[subject].notice_id)

    def test_legacy_final_keys_have_exact_retained_sources_and_distinct_new_native_urls(
        self,
    ) -> None:
        files = {file.file_id: file for file in self.special.files}
        self.assertEqual(len(self.special.legacy_sources), 2)
        for legacy in self.special.legacy_sources:
            current = files[legacy.native_file_id]
            self.assertEqual(legacy.source_record["checksum"], current.checksum)
            self.assertEqual(legacy.source_record["file_type"], current.file_type)
            self.assertIn("exam.kh.edu.tw", legacy.source_record["download_url_source"])
            self.assertIn("bulletin.kh.edu.tw", current.source_url)

    def test_identity_requires_the_exact_post_event_booklet_revision_and_material(self) -> None:
        exam = self.elementary
        job = next(job for job in exam.jobs if job.code == "job-elementary-science")
        file = next(
            file
            for file in exam.papers_for(job)
            if file.subject_code == "science" and file.file_type == "question"
        )
        context = dict(
            source_exam_id=exam.source_exam_id,
            year_ad=2026,
            exam_name_raw=exam.programme_name,
            category_code=job.code,
            category_raw=job.label,
            subject_code=file.subject_code,
            subject_name_raw=file.subject_name,
            source_url=file.source_url,
            checksum=file.checksum,
            source_material=exam.material(file),
        )
        self.assertEqual(resolve_identity(**context), (exam, job, file))
        for key, value in [
            ("year_ad", 2025),
            ("category_raw", "自然"),
            ("subject_code", "general-common"),
            ("checksum", "0" * 64),
            ("source_url", file.source_url.split("#")[0]),
            ("source_material", None),
        ]:
            with self.subTest(key=key):
                self.assertIsNone(resolve_identity(**{**context, key: value}))


class KaohsiungEvidenceValidationTests(unittest.TestCase):
    def assert_invalid(self, document: dict) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "native.json"
            path.write_text(json.dumps(document, ensure_ascii=False))
            with self.assertRaises(ValueError):
                read_annual_exams(path)

    def setUp(self) -> None:
        self.document = json.loads(native_programmes_path(ROOT).read_text())

    def test_removing_an_identical_final_key_is_still_a_coverage_loss(self) -> None:
        document = copy.deepcopy(self.document)
        files = document["events"][0]["files"]
        files[:] = [file for file in files if file["file_id"] != "english-corrected-answer"]
        self.assert_invalid(document)

    def test_member_and_notice_bindings_cannot_change_independently(self) -> None:
        for field, value in [("file_type", "corrected_answer"), ("notice_id", "B115002808")]:
            with self.subTest(field=field):
                document = copy.deepcopy(self.document)
                document["events"][0]["files"][0][field] = value
                self.assert_invalid(document)
        document = copy.deepcopy(self.document)
        document["events"][0]["files"][0]["source"]["member_path"] = "unknown.pdf"
        self.assert_invalid(document)

    def test_partial_or_unverified_legacy_context_is_rejected(self) -> None:
        for change in ["omit", "checksum"]:
            with self.subTest(change=change):
                document = copy.deepcopy(self.document)
                legacy = document["events"][1]["legacy_sources"][0]["source_record"]
                if change == "omit":
                    legacy.pop("subject_code")
                else:
                    legacy["checksum"] = "0" * 64
                self.assert_invalid(document)

    def test_invented_years_and_missing_native_booklet_applicability_are_rejected(self) -> None:
        document = copy.deepcopy(self.document)
        document["events"][0]["year_roc"] = 114
        self.assert_invalid(document)
        document = copy.deepcopy(self.document)
        jobs = document["events"][0]["jobs"]
        jobs[:] = [job for job in jobs if job["code"] != "job-elementary-hakka"]
        self.assert_invalid(document)
