import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app.cli import main
from app.models import AliasRule, NormalizedCatalog, ParsedPaper, SourceExamPage, SyncFailure
from app.normalizer import normalize_papers, renormalize_catalog
from app.paths import provider_paths
from app.providers.twc_recruit.client import (
    TwcRecruitClient,
    TwcRecruitEntry,
    archive_file_role,
    retained_archive_file_role,
)
from app.publisher import write_provider_state
from app.source_revisions import load_source_revisions
from app.state import load_provider_state


YEARS = (103, 104, 105, 106, 107, 108, 110, 111, 112, 114, 115)
ALIASES = [AliasRule("exact", "台水評價職位人員甄試", "twc-recruit", "台水評價職位人員甄試")]


def source_url(year):
    return f"https://www.water.gov.tw/ch/ServerFile/Get/00000000-0000-0000-0000-{year:012d}?nodeId=715"


def archive_payload(year):
    if year == 107:
        return b"PK\x03\x04retained corrupt official archive"
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("question-answer.pdf", b"%PDF-1.7 native source " + str(year).encode())
    return stream.getvalue()


def legacy_page(year, checksum):
    key = f"providers/twc_recruit/{year}/twc-recruit-{year}/{year}/twc-recruit-{year}-bundle/accessible_bundle.zip"
    paper = ParsedPaper(
        category_raw="台水評價職位人員甄試", category_code=str(year),
        subject_code=f"twc-recruit-{year}-bundle", subject_name_raw=f"{year}年試題(解答).zip",
        files={"accessible_bundle": source_url(year)},
        mirror_files={"accessible_bundle": {"storage_key": key, "checksum": checksum}},
    )
    return SourceExamPage(
        source_exam_id=f"twc-recruit-{year}", year_ad=year + 1911, year_roc=year,
        exam_name_raw=f"{year}年試題(解答)(檔案下載)", attachments=[], papers=[paper],
        provider_id="twc_recruit",
    )


class TwcArchiveRoleTests(unittest.TestCase):
    def test_native_labels_distinguish_ordinary_and_explicit_accessibility_archives(self):
        for label in ("115年試題(解答).zip(4.3M)", "115年試題及答案.zip"):
            self.assertEqual(archive_file_role(label), "question_answer")
        self.assertEqual(archive_file_role("115年無障礙試題(解答).zip"), "accessible_bundle")
        with self.assertRaisesRegex(ValueError, "Unresolved Taiwan Water archive material role"):
            archive_file_role("115年其他資料.zip")

    def test_fetch_refuses_an_unresolved_material_role(self):
        client = TwcRecruitClient()
        client._cached_entries = [TwcRecruitEntry(115, 2026, "115年其他資料.zip", source_url(115))]
        with self.assertRaisesRegex(ValueError, "Unresolved Taiwan Water archive material role"):
            client.fetch_exam_page("twc-recruit-115", 2026)

    def test_retained_correction_requires_the_original_archive_context(self):
        context = dict(year_roc=115, source_exam_id="twc-recruit-115", category_code="115",
                       subject_code="twc-recruit-115-bundle", label="115年試題(解答).zip",
                       source_url=source_url(115))
        self.assertEqual(retained_archive_file_role("accessible_bundle", **context), "question_answer")
        for changed in (
            {"source_exam_id": "other-event"}, {"category_code": "other-category"},
            {"subject_code": "native-child-paper"}, {"source_url": "https://example.test/archive.zip"},
            {"label": "115年其他資料.zip"}, {"label": "115年無障礙試題(解答).zip"},
        ):
            with self.subTest(changed=changed):
                self.assertEqual(retained_archive_file_role("accessible_bundle", **(context | changed)), "accessible_bundle")
        for role in ("question", "answer", "corrected_answer", "question_answer"):
            self.assertEqual(retained_archive_file_role(role, **context), role)

    def test_ingestion_and_historical_migration_share_the_role_and_preserve_locators(self):
        for year in YEARS:
            with self.subTest(year=year):
                checksum = hashlib.sha256(archive_payload(year)).hexdigest()
                page = legacy_page(year, checksum)
                catalog = normalize_papers(
                    source_exam_id=page.source_exam_id, year_ad=page.year_ad,
                    exam_name_raw=page.exam_name_raw, papers=page.papers, alias_rules=ALIASES,
                    mirror_base_url="", mirror_metadata={}, provider_id="twc_recruit",
                )
                current = catalog.papers[0]
                self.assertEqual(current.file_type, "question_answer")
                self.assertEqual(current.storage_key, page.papers[0].mirror_files["accessible_bundle"]["storage_key"])
                self.assertEqual(current.checksum, checksum)
                legacy = replace(current, file_type="accessible_bundle",
                                 paper_code=f"{year}-twc-recruit-{year}-bundle-accessible_bundle")
                migrated = renormalize_catalog(NormalizedCatalog([legacy], []), ALIASES)
                self.assertEqual(migrated.papers, [current])
                self.assertEqual(migrated.papers[0].download_url_source, source_url(year))

    def test_other_providers_accessibility_roles_are_unchanged(self):
        page = legacy_page(115, "a" * 64)
        result = normalize_papers(
            source_exam_id=page.source_exam_id, year_ad=page.year_ad, exam_name_raw=page.exam_name_raw,
            papers=page.papers, alias_rules=ALIASES, mirror_base_url="", mirror_metadata={},
            provider_id="moex",
        )
        self.assertEqual(result.papers[0].file_type, "accessible_bundle")

    def test_cli_migrates_all_history_retaining_original_roles_bytes_and_failed_receipts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = provider_paths(root, "twc_recruit")
            pages, old_papers, payloads = [], [], {}
            for year in YEARS:
                data = archive_payload(year)
                checksum = hashlib.sha256(data).hexdigest()
                page = legacy_page(year, checksum)
                key = page.papers[0].mirror_files["accessible_bundle"]["storage_key"]
                path = root / "mirror" / key
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                payloads[checksum] = data
                normalized = normalize_papers(
                    source_exam_id=page.source_exam_id, year_ad=page.year_ad,
                    exam_name_raw=page.exam_name_raw, papers=page.papers, alias_rules=ALIASES,
                    mirror_base_url="", mirror_metadata={}, provider_id="twc_recruit",
                ).papers[0]
                old_papers.append(replace(normalized, file_type="accessible_bundle",
                                         paper_code=f"{year}-twc-recruit-{year}-bundle-accessible_bundle"))
                pages.append(page)
            failure = SyncFailure("download", "twc-recruit-107", 107,
                                  "107-twc-recruit-107-bundle-accessible_bundle", "accessible_bundle",
                                  source_url(107), "Invalid ZIP container")
            write_provider_state(provider, pages, NormalizedCatalog(old_papers, []), ALIASES, [failure], None)
            status = provider.data_dir / "sync-status.json"
            receipts = b'{"schema_version":1,"provider_id":"twc_recruit","events":{}}\n'
            status.write_bytes(receipts)
            with patch("sys.stdout", new=io.StringIO()):
                self.assertEqual(main(["migrate-catalog", "--repo-root", str(root),
                                       "--provider", "twc_recruit"]), 0)
            raw, migrated, failures = load_provider_state(provider)
            self.assertEqual(raw, pages)
            self.assertEqual(failures, [failure])
            self.assertEqual(status.read_bytes(), receipts)
            self.assertEqual(len(migrated.papers), 11)
            self.assertEqual({p.file_type for p in migrated.papers}, {"question_answer"})
            revisions = load_source_revisions(provider)
            self.assertEqual(len(revisions), 11)
            for revision in revisions:
                self.assertEqual(revision["source_record"]["file_type"], "accessible_bundle")
                checksum = revision["source_record"]["checksum"]
                self.assertEqual((root / "mirror" / revision["blob_storage_key"]).read_bytes(), payloads[checksum])
            old = {p.year_roc: p for p in old_papers}
            for paper in migrated.papers:
                self.assertEqual(replace(paper, file_type="accessible_bundle",
                                         paper_code=old[paper.year_roc].paper_code), old[paper.year_roc])
            self.assertEqual(len(json.loads(provider.index_path.read_text())["papers"]), 11)
