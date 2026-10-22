"""Native group identity, byte-plan integrity and ordinary source acquisition."""

import base64
import hashlib
import json
import shutil
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

from app.bundler import public_bundle_ids
from app.classification import classify_normalized_paper
from app.models import NormalizedCatalog, ParsedPaper, SourceExamPage
from app.normalizer import load_alias_rules, normalize_papers
from app.paths import provider_paths
from app.providers.base import DownloadedFile, ResponseMetadata
from app.providers.hce_archive import HCE_CONFIGS
from app.providers.hce_nsysu.evidence import (
    AdmissionGroup,
    NativeDocument,
    SelectedPaper,
    annual_exams,
    native_selections_path,
    project_native_page,
    read_annual_exams,
    resolve_selection_locator,
    reviewed_native_revisions,
    selection_locator,
    validate_native_selections,
)
from app.providers.hce_nsysu.provider import HceNsysuProvider
from app.providers.hce_nsysu.selection_plan import (
    MAX_SELECTION_BYTES,
    read_compiled_selection,
    selection_plan_path,
)
from app.site_registry import get_site_config
from app.source_revisions import load_source_revisions
from app.storage import MirrorStore
from app.sync import _mirror_prefix_for_paper, sync_exam_pages

ROOT = Path(__file__).resolve().parents[1]


def checksum(data):
    return hashlib.sha256(data).hexdigest()


def raw_page(annual, source_url):
    config = HCE_CONFIGS["hce_nsysu"]
    return SourceExamPage(
        annual.source_exam_id,
        annual.year_roc + 1911,
        annual.year_roc,
        f"{annual.year_roc}學年度{config.exam_name}",
        [],
        [
            ParsedPaper(
                config.category_name,
                config.category_code,
                "all",
                "各科試題與參考答案",
                {"question_answer": source_url},
            )
        ],
        "hce_nsysu",
    )


class AnnualSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.facts = json.loads(native_selections_path(ROOT).read_text())
        cls.exams = annual_exams()
        cls.pages = []
        cls.papers = []
        aliases = load_alias_rules(provider_paths(ROOT, "hce_nsysu").aliases_path)
        for annual in cls.exams:
            document = next(
                doc for doc in annual.documents if doc.checksum == annual.active_parent_checksum
            )
            page = project_native_page(raw_page(annual, document.source_url))
            for paper in page.papers:
                role, locator = next(iter(paper.files.items()))
                selected = resolve_selection_locator(locator)[2]
                paper.mirror_files[role] = {
                    "checksum": selected.checksum,
                    "storage_key": _mirror_prefix_for_paper(page, paper, role) + ".pdf",
                }
            result = normalize_papers(
                source_exam_id=page.source_exam_id,
                year_ad=page.year_ad,
                exam_name_raw=page.exam_name_raw,
                papers=page.papers,
                alias_rules=aliases,
                mirror_base_url="",
                mirror_metadata={},
                provider_id="hce_nsysu",
            )
            assert not result.review_queue
            cls.pages.append(page)
            cls.papers.extend(result.papers)

    def test_catalog_and_all_generated_plans_match_their_contracts(self):
        schema = json.loads((ROOT / "schemas/nsysu-native-selections-v1.schema.json").read_text())
        Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(
            self.facts
        )
        plan_schema = Draft202012Validator(
            json.loads((ROOT / "schemas/compiled-pdf-selection-v1.schema.json").read_text())
        )
        checked = 0
        for annual in self.exams:
            for document in annual.documents:
                for selection in document.selections:
                    path = selection_plan_path(ROOT, selection.checksum)
                    plan_schema.validate(json.loads(path.read_text()))
                    read_compiled_selection(
                        path,
                        parent_checksum=document.checksum,
                        parent_bytes=document.size,
                        output_checksum=selection.checksum,
                        output_bytes=selection.size,
                    )
                    checked += 1
        self.assertEqual((len(self.exams), checked), (5, 20))

    def test_complete_history_has_three_pure_admission_routes(self):
        self.assertEqual(len(self.papers), 29)
        self.assertEqual({paper.stage_id for paper in self.papers}, {"stage-1"})
        self.assertEqual({paper.level_id for paper in self.papers}, {"not-applicable"})
        self.assertEqual({paper.classification_confidence for paper in self.papers}, {"high"})
        self.assertEqual(len({paper.bundle_id for paper in self.papers}), 3)
        site = get_site_config("default")
        self.assertEqual(
            public_bundle_ids(
                NormalizedCatalog(self.papers, []),
                min_years=site.public_min_years,
                min_years_by_canonical_prefix=site.public_min_years_by_canonical_prefix,
            ),
            {paper.bundle_id for paper in self.papers},
        )
        self.assertEqual(sum(p.track_id == "hce-nsysu-general-medicine" for p in self.papers), 13)
        self.assertEqual(sum(p.track_id == "hce-nsysu-smart-medical" for p in self.papers), 13)
        self.assertEqual(sum(p.track_id == "hce-nsysu" for p in self.papers), 3)
        self.assertTrue(all("初試（筆試）" in paper.bundle_name for paper in self.papers))
        computing = [paper for paper in self.papers if paper.subject_code == "computer-programming"]
        self.assertEqual({paper.track_id for paper in computing}, {"hce-nsysu-smart-medical"})
        physics = [paper for paper in self.papers if paper.subject_code == "physics-chemistry"]
        self.assertNotIn("hce-nsysu-smart-medical", {paper.track_id for paper in physics})

    def test_native_originals_and_shared_subject_paths_are_retained(self):
        self.assertEqual(sum(len(page.attachments) for page in self.pages), 5)
        self.assertTrue(
            all(page.attachments[0].file_type == "source_document" for page in self.pages)
        )
        keys = [paper.storage_key for paper in self.papers]
        self.assertEqual(len(set(keys)), 20)
        self.assertEqual(len(keys) - len(set(keys)), 9)
        answers = [paper for paper in self.papers if paper.file_type == "all_answers"]
        self.assertEqual(len(answers), 2)
        self.assertEqual(len({paper.checksum for paper in answers}), 1)
        self.assertEqual(len({paper.storage_key for paper in answers}), 1)
        self.assertEqual({paper.year_roc for paper in answers}, {111})

    def test_changed_bytes_and_wrong_applicability_return_to_review(self):
        computing = next(
            paper for paper in self.papers if paper.subject_code == "computer-programming"
        )
        cases = [
            replace(computing, checksum="0" * 64),
            replace(computing, category_code="post-bacc-medicine-general-medicine"),
            replace(computing, subject_code="physics-chemistry"),
            replace(computing, source_exam_id="hce-nsysu-999"),
            replace(
                computing,
                source_material=replace(
                    computing.source_material, evidence_label="unreviewed wording"
                ),
            ),
            replace(computing, checksum=self.exams[0].documents[0].checksum),
        ]
        for paper in cases:
            with self.subTest(paper=paper.paper_code, subject=paper.subject_code):
                identity = classify_normalized_paper(paper)
                self.assertEqual((identity.stage_id, identity.confidence), ("unknown", "review"))

    def test_future_native_document_is_preserved_with_unknown_evidence(self):
        annual = replace(self.exams[-1], year_roc=116, source_exam_id="hce-nsysu-116")
        page = project_native_page(
            raw_page(annual, "https://www3.nsysu.edu.tw/fixture-only-future.pdf")
        )
        self.assertEqual(len(page.papers), 1)
        self.assertFalse(page.attachments)
        self.assertTrue(page.papers[0].source_material.needs_review)
        self.assertEqual(page.papers[0].source_material.date.basis, "unknown")

    def test_changed_native_parent_cannot_inherit_reviewed_page_selections(self):
        annual = self.exams[-1]
        document = annual.documents[0]
        page = project_native_page(raw_page(annual, document.source_url), parent_checksum="0" * 64)
        self.assertEqual(len(page.papers), 1)
        self.assertFalse(page.attachments)
        self.assertEqual(page.papers[0].files, {"question_answer": document.source_url})
        self.assertTrue(page.papers[0].source_material.needs_review)

    def test_semantic_reader_rejects_incomplete_or_conflicting_source_facts(self):
        changes = {
            "page lost": lambda data: data["events"][0]["documents"][0]["selections"][0][
                "pages"
            ].pop(),
            "page repeated": lambda data: data["events"][0]["documents"][0]["selections"][1][
                "pages"
            ].insert(0, 17),
            "answer tables lost": lambda data: data["events"][0]["documents"][0][
                "selections"
            ].pop(),
            "group subject missing": lambda data: data["events"][0]["groups"][0]["subjects"].append(
                "missing-subject"
            ),
            "active parent missing": lambda data: data["events"][0].update(
                active_parent_checksum="0" * 64
            ),
            "year conflicts with date": lambda data: data["events"][0].update(
                written_test_date="2025-01-01"
            ),
            "rule from another owner": lambda data: data["events"][0]["rule_evidence"][0].update(
                url="https://example.com/guide.pdf"
            ),
            "boolean native page": lambda data: data["events"][0]["documents"][0]["selections"][0][
                "pages"
            ].__setitem__(0, True),
            "ambiguous group label": lambda data: data["events"][0]["groups"][0].update(label=""),
            "duplicate native version": lambda data: data["events"][0]["documents"].append(
                deepcopy(data["events"][0]["documents"][0])
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "facts.json"
            for name, change in changes.items():
                with self.subTest(name=name):
                    changed = deepcopy(self.facts)
                    change(changed)
                    path.write_text(json.dumps(changed))
                    with self.assertRaises(ValueError):
                        read_annual_exams(path)

    def test_evidence_gate_rejects_partial_history_wrong_roles_and_orphan_plans(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = provider_paths(root, "hce_nsysu")
            shutil.copytree(provider_paths(ROOT, "hce_nsysu").data_dir, paths.data_dir)
            facts_path = native_selections_path(root)
            facts_path.parent.mkdir(parents=True)
            shutil.copyfile(native_selections_path(ROOT), facts_path)
            self.assertEqual(validate_native_selections(root), 20)
            paper_path = paths.papers_dir / "2022.json"
            original = paper_path.read_bytes()
            papers = json.loads(original)
            for changed in [
                papers[:-1],
                [{**papers[0], "file_type": "answer"}, *papers[1:]],
                [*papers, papers[0]],
            ]:
                with self.subTest(count=len(changed), role=changed[0]["file_type"]):
                    paper_path.write_text(json.dumps(changed))
                    with self.assertRaises(ValueError):
                        validate_native_selections(root)
            paper_path.write_bytes(original)
            orphan = paths.data_dir / "native-selections/orphan.json"
            orphan.write_text("{}")
            with self.assertRaisesRegex(ValueError, "missing or orphaned"):
                validate_native_selections(root)
            orphan.unlink()
            plan = next((paths.data_dir / "native-selections").glob("*.json"))
            plan.unlink()
            with self.assertRaises(FileNotFoundError):
                validate_native_selections(root)

    def test_revision_review_describes_originals_without_rewriting_the_journal(self):
        entries = load_source_revisions(provider_paths(ROOT, "hce_nsysu"))
        self.assertEqual(len(entries), 5)
        before = deepcopy(entries)
        views = reviewed_native_revisions(entries, repo_root=ROOT)
        self.assertEqual(entries, before)
        self.assertEqual(len(views), 5)
        self.assertEqual({view["disposition"] for view in views}, {"native_source_document"})
        self.assertEqual({view["stage_id"] for view in views}, {"stage-1"})
        self.assertEqual(sorted(len(view["admission_groups"]) for view in views), [1, 2, 2, 2, 2])


class CompiledSelectionTests(unittest.TestCase):
    def setUp(self):
        self.parent = b"%PDF-1.4\nnative fixture bytes\n"
        self.result = self.parent + b"%%EOF\n"
        self.plan = {
            "schema_version": 1,
            "parent_SHA256": checksum(self.parent),
            "output_SHA256": checksum(self.result),
            "output_bytes": len(self.result),
            "operations": [
                {"copy_from": 0, "length": len(self.parent)},
                {"literal_base64": base64.b64encode(b"%%EOF\n").decode()},
            ],
        }

    def read(self, path):
        return read_compiled_selection(
            path,
            parent_checksum=checksum(self.parent),
            parent_bytes=len(self.parent),
            output_checksum=checksum(self.result),
            output_bytes=len(self.result),
        )

    def test_copy_and_literal_plan_is_bound_to_both_digests(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            path.write_text(json.dumps(self.plan))
            compiled = self.read(path)
            self.assertEqual(compiled.apply(self.parent), self.result)
            with self.assertRaisesRegex(ValueError, "changed"):
                compiled.apply(self.parent + b"new source revision")
            changed = deepcopy(self.plan)
            changed["output_SHA256"] = "0" * 64
            path.write_text(json.dumps(changed))
            with self.assertRaises(ValueError):
                self.read(path)
            changed = deepcopy(self.plan)
            changed["operations"][-1]["literal_base64"] = base64.b64encode(b"BROKEN").decode()
            path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, "differs"):
                self.read(path).apply(self.parent)

    def test_unbounded_and_malformed_plans_are_rejected(self):
        changed = []
        for offset, length in [(-1, 1), (True, 1), (0, 0), (len(self.parent), 1)]:
            changed.append({**self.plan, "operations": [{"copy_from": offset, "length": length}]})
        changed.extend(
            [
                {**self.plan, "operations": [{"literal_base64": "!!!"}]},
                {**self.plan, "operations": []},
                {**self.plan, "operations": self.plan["operations"][:-1]},
                {**self.plan, "output_bytes": MAX_SELECTION_BYTES + 1},
                {**self.plan, "schema_version": True},
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            for number, plan in enumerate(changed):
                with self.subTest(case=number):
                    path.write_text(json.dumps(plan))
                    with self.assertRaises(ValueError):
                        self.read(path)

    def test_provider_reuses_one_native_fetch_and_observes_the_parent_head(self):
        url = "https://www3.nsysu.edu.tw/fixture-only-source.pdf"
        selection = SelectedPaper(
            "english", "英文", "question_answer", (1,), checksum(self.result), len(self.result)
        )
        document = NativeDocument(url, checksum(self.parent), len(self.parent), 1, (selection,))
        annual = replace(
            annual_exams()[-1],
            documents=(document,),
            active_parent_checksum=document.checksum,
            groups=(AdmissionGroup("post-bacc-medicine", "hce-nsysu", "", ("english",)),),
        )
        locator = selection_locator(document, selection)

        class Client:
            def __init__(self, data):
                self.data, self.gets, self.heads = data, [], []

            def download_file(self, source_url):
                self.gets.append(source_url)
                return DownloadedFile(self.data, "application/pdf", "native.pdf")

            def head(self, source_url):
                self.heads.append(source_url)
                return ResponseMetadata(source_url, 200, len(self.data), "application/pdf")

            def fetch_exam_page(self, exam_code, year_ad):
                return raw_page(annual, url)

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("app.providers.hce_nsysu.evidence.annual_exams", return_value=(annual,)),
        ):
            root = Path(directory)
            plan = selection_plan_path(root, selection.checksum)
            plan.parent.mkdir(parents=True)
            plan.write_text(json.dumps(self.plan))
            client = Client(self.parent)
            provider = HceNsysuProvider(client, repo_root=root)
            self.assertEqual(provider.download_file(url).data, self.parent)
            self.assertEqual(provider.download_file(locator).data, self.result)
            self.assertEqual(provider.download_file(locator).data, self.result)
            self.assertEqual(client.gets, [url])
            response = provider.head(locator)
            self.assertEqual((response.url, response.content_length), (url, len(self.parent)))
            provider.head(url)
            self.assertEqual(client.heads, [url])
            with self.assertRaises(ValueError):
                provider.download_file(url + "#unrecognized=selection")
            changed = HceNsysuProvider(Client(self.parent + b"changed"), repo_root=root)
            with self.assertRaises(ValueError):
                changed.download_file(locator)
            self.assertEqual(changed.download_file(url).data, self.parent + b"changed")
            plan.unlink()
            missing = HceNsysuProvider(Client(self.parent), repo_root=root)
            with self.assertRaises(FileNotFoundError):
                missing.download_file(locator)
            self.assertEqual(missing.download_file(url).data, self.parent)

    def test_warm_mirror_checks_native_revision_before_reusing_children(self):
        url = "https://www3.nsysu.edu.tw/fixture-only-source.pdf"
        selection = SelectedPaper(
            "english", "英文", "question_answer", (1,), checksum(self.result), len(self.result)
        )
        document = NativeDocument(url, checksum(self.parent), len(self.parent), 1, (selection,))
        annual = replace(
            annual_exams()[-1],
            documents=(document,),
            active_parent_checksum=document.checksum,
            groups=(AdmissionGroup("post-bacc-medicine", "hce-nsysu", "", ("english",)),),
        )

        class Client:
            def __init__(self, data):
                self.data, self.gets = data, []

            def fetch_exam_page(self, exam_code, year_ad):
                return raw_page(annual, url)

            def download_file(self, source_url):
                self.gets.append(source_url)
                return DownloadedFile(self.data, "application/pdf", "native.pdf")

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("app.providers.hce_nsysu.evidence.annual_exams", return_value=(annual,)),
        ):
            root = Path(directory)
            plan = selection_plan_path(root, selection.checksum)
            plan.parent.mkdir(parents=True)
            plan.write_text(json.dumps(self.plan))
            mirror = MirrorStore(root / "mirror")
            codes = [(annual.source_exam_id, annual.year_roc + 1911)]

            def sync(data, previous):
                client = Client(data)
                result = sync_exam_pages(
                    HceNsysuProvider(client, repo_root=root),
                    codes,
                    mirror,
                    [],
                    "",
                    previous_pages=previous,
                )
                self.assertEqual(client.gets, [url])
                self.assertFalse(result[2])
                return result[:2]

            pages, catalog = sync(self.parent, [])
            self.assertEqual(catalog.papers[0].checksum, checksum(self.result))
            old_child = mirror.root / catalog.papers[0].storage_key
            repeated, same = sync(self.parent, pages)
            self.assertEqual(same, catalog)
            changed = self.parent + b"unreviewed native revision\n"
            changed_pages, changed_catalog = sync(changed, repeated)
            self.assertEqual(len(changed_pages[0].papers), 1)
            self.assertFalse(changed_pages[0].attachments)
            self.assertEqual(changed_catalog.papers[0].checksum, checksum(changed))
            self.assertEqual(changed_catalog.papers[0].stage_id, "unknown")
            self.assertEqual(changed_catalog.papers[0].classification_confidence, "review")
            self.assertEqual(old_child.read_bytes(), self.result)


if __name__ == "__main__":
    unittest.main()
