"""Native TOCFL resource identity and acquisition boundaries."""

import hashlib
import tempfile
import unittest
from collections import Counter
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app.classification import classify_paper
from app.models import ExamAttachment, SourceExamPage
from app.providers.base import DownloadedFile
from app.providers.tocfl_cert.client import (
    DOWNLOAD_URL,
    MOCK_TEST_URL,
    TocflCertClient,
    parse_downloads,
)
from app.providers.tocfl_cert.evidence import (
    current_resources,
    native_catalog,
    native_materials_path,
    read_native_catalog,
    validate_native_payload,
)
from app.providers.tocfl_cert.provider import TocflCertProvider
from app.storage import MirrorStore
from app.sync import sync_exam_pages

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/tocfl"
REFERENCE_HTML = (FIXTURES / "references.html").read_text()
BANK_HTML = (FIXTURES / "question-bank.html").read_text()


def client_from_native_listing() -> TocflCertClient:
    client = TocflCertClient()
    client._fetch_text = lambda url: REFERENCE_HTML if url == DOWNLOAD_URL else BANK_HTML  # type: ignore[method-assign]
    return client


class TocflNativeTests(unittest.TestCase):
    def test_sync_refreshes_cached_payload_and_preserves_it_on_unreviewed_change(self) -> None:
        original = next(r for r in current_resources() if r.payload_format == "pdf")
        body = b"%PDF-1.7 retained transport fixture"
        resource = replace(original, checksum=hashlib.sha256(body).hexdigest(), bytes=len(body))

        def page() -> SourceExamPage:
            return SourceExamPage(
                source_exam_id=resource.source_exam_id,
                year_ad=2026,
                year_roc=115,
                exam_name_raw="TOCFL references",
                provider_id="tocfl_cert",
                papers=[],
                attachments=[
                    ExamAttachment(
                        title=resource.display_label,
                        file_type="reference",
                        download_url_source=resource.source_url,
                        source_material=resource.material,
                    )
                ],
            )

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("app.providers.tocfl_cert.client.current_resources", return_value=(resource,)),
        ):
            store = MirrorStore(Path(directory))
            first = TocflCertClient()
            with (
                patch.object(first, "fetch_exam_page", return_value=page()),
                patch.object(
                    first.http,
                    "download",
                    return_value=DownloadedFile(body, "application/pdf", "reference.pdf"),
                ),
            ):
                pages, _catalog, failures = sync_exam_pages(
                    TocflCertProvider(first), [(resource.source_exam_id, 2026)], store, [], ""
                )
            self.assertEqual(failures, [])
            key = pages[0].attachments[0].storage_key
            second = TocflCertClient()
            with (
                patch.object(second, "fetch_exam_page", return_value=page()),
                patch.object(
                    second.http,
                    "download",
                    return_value=DownloadedFile(
                        b"%PDF-1.7 unreviewed replacement", "application/pdf", "reference.pdf"
                    ),
                ) as download,
            ):
                _pages, _catalog, failures = sync_exam_pages(
                    TocflCertProvider(second),
                    [(resource.source_exam_id, 2026)],
                    store,
                    [],
                    "",
                    previous_pages=pages,
                )
            download.assert_called_once()
            self.assertEqual(len(failures), 1)
            self.assertIn("bytes changed", failures[0].message)
            self.assertEqual((Path(directory) / key).read_bytes(), body)

    def test_native_listing_roles_preserve_all_units_and_source_formats(self) -> None:
        references = parse_downloads(REFERENCE_HTML)
        bank = parse_downloads(BANK_HTML, base_url=MOCK_TEST_URL)
        self.assertEqual(len(references), 3)
        self.assertEqual(len(bank), 92)
        self.assertEqual(
            Counter(r.file_type for r in bank),
            {
                "question": 44,
                "answer": 22,
                "listening_audio": 11,
                "listening_transcript": 11,
                "instructions": 4,
            },
        )
        self.assertEqual({r.resource.programme for r in references}, {"tocfl", "cccc"})
        self.assertEqual({r.resource.payload_format for r in bank}, {"rar", "xlsx"})
        self.assertEqual(
            Counter(r.resource.skill for r in bank), {"聽力": 55, "閱讀": 33, "combined": 4}
        )
        self.assertEqual(
            {r.resource.skill for r in bank if r.resource.unit_label == "完成段落"}, {"閱讀"}
        )
        self.assertTrue(all(r.resource.material.date.basis == "undated" for r in bank))

    def test_listing_context_and_unreviewed_links_fail_closed(self) -> None:
        for changed in [
            BANK_HTML.replace("第一部分", "未審核部分", 1),
            BANK_HTML.replace("N_L_Part%20One_T.rar", "Unknown.rar", 1),
            BANK_HTML.replace("[正體試題]", "[答案]", 1),
            BANK_HTML.replace(
                "https://tocfl.edu.tw/tocfl/assets/files/",
                "https://example.com/tocfl/assets/files/",
                1,
            ),
            BANK_HTML.replace("section-title-NoB", "section-title-BandA", 1),
            BANK_HTML + '<a href="/new-source/question.pdf">new material</a>',
            BANK_HTML + '<a href="https://example.com/paper.rar">new material</a>',
        ]:
            with self.subTest(changed=changed[:100]):
                with self.assertRaises(ValueError):
                    parse_downloads(changed, base_url=MOCK_TEST_URL)
        with self.assertRaises(ValueError):
            parse_downloads(BANK_HTML, base_url=DOWNLOAD_URL)
        with self.assertRaises(ValueError):
            parse_downloads(REFERENCE_HTML, base_url="https://example.com/")

    def test_duplicate_and_missing_native_links_cannot_replace_retained_state(self) -> None:
        anchor = '<a href="https://tocfl.edu.tw/tocfl/assets/files/vocabulary/8000_description_202204.pdf">華語八千詞表調整說明</a>'
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            parse_downloads(REFERENCE_HTML + anchor)
        client = client_from_native_listing()
        client._fetch_text = lambda url: REFERENCE_HTML if url == DOWNLOAD_URL else ""  # type: ignore[method-assign]
        with self.assertRaisesRegex(ValueError, "membership"):
            client.discover_available_years()

    def test_stable_groups_use_capture_partition_without_filename_years(self) -> None:
        client = client_from_native_listing()
        self.assertEqual(client.discover_available_years(), [2026])
        self.assertEqual(
            {e.code for e in client.discover_exams(2026)},
            {
                "tocfl-cert-novice-question-bank",
                "tocfl-cert-a-question-bank",
                "tocfl-cert-b-question-bank",
                "tocfl-cert-c-question-bank",
                "tocfl-cert-reference",
                "cccc-cert-reference",
            },
        )
        self.assertEqual(client.discover_exams(2022), [])
        for selector, year in [
            ("tocfl-cert-2026", 2026),
            ("tocfl-cert-materials", 2026),
            ("tocfl-cert-a-question-bank", 2027),
        ]:
            with self.assertRaises(ValueError):
                client.fetch_exam_page(selector, year)

    def test_paper_variants_share_companions_but_keep_independent_question_scripts(self) -> None:
        client = client_from_native_listing()
        pages = [client.fetch_exam_page(e.code, 2026) for e in client.discover_exams(2026)]
        papers = [r for p in pages for r in p.papers]
        self.assertEqual(len(papers), 132)
        self.assertEqual(
            Counter(role for p in papers for role in p.files),
            {
                "question": 44,
                "answer": 44,
                "listening_audio": 22,
                "listening_transcript": 22,
            },
        )
        self.assertEqual(sum(len(p.attachments) for p in pages), 7)
        self.assertEqual(
            {a.file_type for p in pages for a in p.attachments}, {"reference", "instructions"}
        )
        for r in current_resources():
            rows = [p for p in papers if r.source_url in p.files.values()]
            self.assertEqual(len(rows), len(r.script_variants))
            self.assertEqual(
                {p.category_code for p in rows}, {r.category_code(s) for s in r.script_variants}
            )
        reference_pages = [p for p in pages if p.source_exam_id.endswith("-reference")]
        self.assertTrue(all(not p.papers for p in reference_pages))
        child = next(p for p in reference_pages if p.source_exam_id == "cccc-cert-reference")
        self.assertEqual([a.title for a in child.attachments], ["兒童華語文能力測驗情境詞彙表"])

    def test_classification_requires_exact_native_context_for_each_script(self) -> None:
        bundles = set()
        for resource in current_resources():
            if resource.collection != "question_bank":
                continue
            for script in resource.script_variants:
                kwargs = dict(
                    provider_id="tocfl_cert",
                    source_exam_id=resource.source_exam_id,
                    year_ad=2026,
                    exam_name_raw="TOCFL bank",
                    category_raw="TOCFL bank",
                    category_code=resource.category_code(script),
                    subject_code=resource.subject_code,
                    subject_name_raw=resource.display_label,
                    canonical_id="tocfl-cert",
                    canonical_name="TOCFL華語文能力測驗",
                    source_material=resource.material,
                    source_checksum=resource.checksum,
                )
                identity = classify_paper(**kwargs)
                self.assertEqual(identity.confidence, "high")
                self.assertIn("script-" + script, identity.variant_ids)
                self.assertEqual(identity.stage_id, "not-applicable")
                self.assertIn(
                    {"traditional": "正體", "simplified": "簡體"}[script], identity.bundle_name
                )
                self.assertNotIn("2026", identity.bundle_name)
                bundles.add(identity.bundle_id)
                for field, value in [
                    ("source_checksum", "0" * 64),
                    ("source_exam_id", "tocfl-cert-2026"),
                    ("category_code", "wrong-band"),
                    ("subject_name_raw", "unreviewed unit"),
                ]:
                    with self.subTest(resource=resource.id, field=field):
                        self.assertEqual(
                            classify_paper(**{**kwargs, field: value}).confidence, "review"
                        )
        self.assertEqual(len(bundles), 8)
        self.assertEqual(native_catalog().bands[0].level_id, "tocfl-novice-1-2")

    def test_payload_changes_and_unknown_endpoints_stop_before_cache_or_request(self) -> None:
        resource = current_resources()[0]
        client = TocflCertClient()
        with patch.object(
            client.http,
            "download",
            return_value=DownloadedFile(b"changed", "application/octet-stream", "changed.rar"),
        ) as download:
            with self.assertRaisesRegex(ValueError, "bytes changed"):
                client.download_file(resource.source_url)
            self.assertNotIn(resource.source_url, client._payloads)
            self.assertEqual(download.call_count, 1)
        for url in [
            "https://example.com/test.rar",
            resource.source_url + "?other=1",
            resource.source_url.replace(".rar", "-unknown.rar"),
        ]:
            with patch.object(client.http, "head") as head:
                with self.assertRaises(ValueError):
                    client.head(url)
                head.assert_not_called()
        invalid = b"<html>not a RAR</html>"
        matching_hash = replace(
            resource, bytes=len(invalid), checksum=hashlib.sha256(invalid).hexdigest()
        )
        with self.assertRaisesRegex(ValueError, "format"):
            validate_native_payload(matching_hash, invalid)

    def test_catalog_rejects_role_confusion_and_duplicate_current_anchors(self) -> None:
        import json

        original = json.loads(native_materials_path(ROOT).read_text())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            for mutate in ["duplicate", "role", "date"]:
                data = json.loads(json.dumps(original))
                if mutate == "duplicate":
                    data["resources"].append(data["resources"][0])
                elif mutate == "role":
                    data["resources"][1]["file_type"] = "answer"
                else:
                    data["resources"][1]["publication_year"] = 2026
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    read_native_catalog(path)


if __name__ == "__main__":
    unittest.main()
