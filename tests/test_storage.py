import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.models import NormalizedCatalog, ParsedPaper, SourceExamPage
from app.storage import MirrorStore


class MirrorStoreTests(unittest.TestCase):
    def test_overwrite_removes_all_loaded_checksums_for_the_same_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / 'target.pdf').write_bytes(b'old')
            (root / 'unrelated.pdf').write_bytes(b'unrelated')
            old = hashlib.sha256(b'old').hexdigest()
            unrelated = hashlib.sha256(b'unrelated').hexdigest()
            store = MirrorStore(root)
            store.dedupe_index_path.write_text(json.dumps({'schema_version': 1, 'entries': {
                old: {'storage_key': 'target.pdf', 'size': 3},
                '0' * 64: {'storage_key': 'target.pdf', 'size': 3},
                unrelated: {'storage_key': 'unrelated.pdf', 'size': 9},
            }}))
            stored = store.write_bytes('target.pdf', b'new', overwrite=True)
            store.flush_dedupe_index()
            entries = json.loads(store.dedupe_index_path.read_text())['entries']
            self.assertEqual(set(entries), {stored.checksum, unrelated})
            self.assertEqual(entries[stored.checksum]['storage_key'], 'target.pdf')
            self.assertEqual((root / 'unrelated.pdf').read_bytes(), b'unrelated')

    def test_prune_rehomes_shared_payload_and_later_overwrite_clears_old_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            store = MirrorStore(root)
            orphan = store.write_bytes('providers/demo/orphan/question.pdf', b'shared')
            active = store.write_bytes('providers/demo/active/question.pdf', b'shared')
            with mock.patch.object(store, 'referenced_storage_keys', return_value={active.storage_key}):
                store.prune_unreferenced_provider('demo', [], NormalizedCatalog([], []), apply=True)
            entries = json.loads(store.dedupe_index_path.read_text())['entries']
            self.assertEqual(entries[orphan.checksum]['storage_key'], active.storage_key)
            self.assertFalse(orphan.path.exists())
            self.assertEqual(active.path.read_bytes(), b'shared')
            updated = store.write_bytes(active.storage_key, b'updated', overwrite=True)
            store.flush_dedupe_index()
            entries = json.loads(store.dedupe_index_path.read_text())['entries']
            self.assertEqual(set(entries), {updated.checksum})
            self.assertEqual(active.path.read_bytes(), b'updated')

    def test_dedupe_rebuild_supports_later_canonical_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / 'a.pdf').write_bytes(b'shared')
            (root / 'b.pdf').write_bytes(b'shared')
            store = MirrorStore(root)
            store.deduplicate_existing(apply=True)
            updated = store.write_bytes('a.pdf', b'updated', overwrite=True)
            store.flush_dedupe_index()
            entries = json.loads(store.dedupe_index_path.read_text())['entries']
            self.assertEqual(set(entries), {updated.checksum})
            self.assertEqual((root / 'b.pdf').read_bytes(), b'shared')
            self.assertFalse((root / 'a.pdf').samefile(root / 'b.pdf'))

    def test_write_bytes_is_idempotent_for_existing_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = MirrorStore(Path(tmp_dir))
            first = store.write_bytes("115/115030/101/0101/question.pdf", b"%PDF-1.7 demo")
            second = store.write_bytes("115/115030/101/0101/question.pdf", b"%PDF-1.7 demo")

            self.assertEqual(first.checksum, second.checksum)
            self.assertFalse(second.created)
            self.assertTrue((Path(tmp_dir) / "115" / "115030" / "101" / "0101" / "question.pdf").exists())

    def test_write_bytes_can_overwrite_existing_content_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "115" / "115030" / "101" / "0101" / "question.pdf"
            store = MirrorStore(Path(tmp_dir))
            store.write_bytes("115/115030/101/0101/question.pdf", b"%PDF-1.7 original")

            updated = store.write_bytes("115/115030/101/0101/question.pdf", b"%PDF-1.7 optimized", overwrite=True)

            self.assertFalse(updated.created)
            self.assertEqual(file_path.read_bytes(), b"%PDF-1.7 optimized")

    def test_find_existing_prefers_pdf_over_legacy_ashx_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            question_dir = root / "115" / "115030" / "101" / "0101"
            question_dir.mkdir(parents=True, exist_ok=True)
            (question_dir / "question.ashx").write_bytes(b"\xef\xbb\xbf<!DOCTYPE html>")
            (question_dir / "question.pdf").write_bytes(b"%PDF-1.7 valid")
            store = MirrorStore(root)

            stored = store.find_existing("115/115030/101/0101/question")

            self.assertIsNotNone(stored)
            self.assertEqual(stored.storage_key, "115/115030/101/0101/question.pdf")

    def test_delete_matching_except_prunes_legacy_siblings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            question_dir = root / "115" / "115030" / "101" / "0101"
            question_dir.mkdir(parents=True, exist_ok=True)
            (question_dir / "question.ashx").write_bytes(b"\xef\xbb\xbf<!DOCTYPE html>")
            (question_dir / "question.pdf").write_bytes(b"%PDF-1.7 valid")
            store = MirrorStore(root)

            store.delete_matching_except("115/115030/101/0101/question", "115/115030/101/0101/question.pdf")

            self.assertFalse((question_dir / "question.ashx").exists())
            self.assertTrue((question_dir / "question.pdf").exists())

    def test_write_bytes_hard_links_identical_payloads_and_persists_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            store = MirrorStore(root)
            first = store.write_bytes("providers/demo/115/one/question.pdf", b"%PDF-1.7 same payload")
            store.flush_dedupe_index()
            second = store.write_bytes("providers/demo/115/two/question.pdf", b"%PDF-1.7 same payload")
            store.flush_dedupe_index()

            self.assertTrue(second.created)
            self.assertEqual(os.stat(first.path).st_ino, os.stat(second.path).st_ino)
            self.assertTrue(store.dedupe_index_path.exists())

            reloaded_store = MirrorStore(root)
            third = reloaded_store.write_bytes("providers/demo/115/three/question.pdf", b"%PDF-1.7 same payload")
            self.assertEqual(os.stat(first.path).st_ino, os.stat(third.path).st_ino)

    def test_deduplicate_existing_relinks_byte_identical_files_without_removing_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            first_path = root / "providers" / "demo" / "115" / "one" / "question.pdf"
            second_path = root / "providers" / "demo" / "115" / "two" / "question.pdf"
            unique_path = root / "providers" / "demo" / "115" / "three" / "question.pdf"
            for path, data in (
                (first_path, b"%PDF-1.7 same payload"),
                (second_path, b"%PDF-1.7 same payload"),
                (unique_path, b"%PDF-1.7 different payload"),
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)

            store = MirrorStore(root)
            preview = store.deduplicate_existing()
            self.assertEqual(preview.duplicate_groups, 1)
            self.assertEqual(preview.relinked_files, 1)
            self.assertNotEqual(os.stat(first_path).st_ino, os.stat(second_path).st_ino)

            applied = store.deduplicate_existing(apply=True)
            self.assertTrue(applied.applied)
            self.assertEqual(applied.relinked_files, 1)
            self.assertEqual(os.stat(first_path).st_ino, os.stat(second_path).st_ino)
            self.assertEqual(first_path.read_bytes(), b"%PDF-1.7 same payload")
            self.assertEqual(unique_path.read_bytes(), b"%PDF-1.7 different payload")

    def test_existing_unindexed_mirror_is_compacted_before_sync_reuses_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            first_path = root / "providers" / "demo" / "115" / "one" / "question.pdf"
            second_path = root / "providers" / "demo" / "115" / "two" / "question.pdf"
            for path in (first_path, second_path):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"%PDF-1.7 same payload")

            store = MirrorStore(root)
            existing = store.find_existing("providers/demo/115/one/question")

            self.assertIsNotNone(existing)
            self.assertTrue(store.dedupe_index_path.exists())
            self.assertEqual(os.stat(first_path).st_ino, os.stat(second_path).st_ino)

    def test_prune_unreferenced_provider_removes_only_state_orphans(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            active_key = "providers/hakka_cert/115/event/101/0101/question.pdf"
            active_path = root / active_key
            stale_path = root / "providers" / "hakka_cert" / "115" / "stale" / "101" / "0101" / "question.pdf"
            active_path.parent.mkdir(parents=True, exist_ok=True)
            stale_path.parent.mkdir(parents=True, exist_ok=True)
            active_path.write_bytes(b"%PDF-1.7 active")
            stale_path.write_bytes(b"%PDF-1.7 stale")
            raw_pages = [
                SourceExamPage(
                    provider_id="hakka_cert",
                    source_exam_id="event",
                    year_ad=2026,
                    year_roc=115,
                    exam_name_raw="Hakka",
                    attachments=[],
                    papers=[
                        ParsedPaper(
                            category_raw="basic",
                            category_code="101",
                            subject_code="0101",
                            subject_name_raw="all",
                            files={},
                            mirror_files={"question": {"storage_key": active_key}},
                        )
                    ],
                )
            ]
            catalog = NormalizedCatalog(papers=[], review_queue=[])
            store = MirrorStore(root)

            preview = store.prune_unreferenced_provider("hakka_cert", raw_pages, catalog)
            self.assertEqual(preview.removed_files, 1)
            self.assertTrue(stale_path.exists())

            applied = store.prune_unreferenced_provider("hakka_cert", raw_pages, catalog, apply=True)
            self.assertTrue(applied.applied)
            self.assertFalse(stale_path.exists())
            self.assertTrue(active_path.exists())

    def test_prune_unreferenced_provider_refuses_missing_state_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            stale_path = root / "providers" / "hakka_cert" / "115" / "stale" / "question.pdf"
            stale_path.parent.mkdir(parents=True, exist_ok=True)
            stale_path.write_bytes(b"%PDF-1.7 stale")
            raw_pages = [
                SourceExamPage(
                    provider_id="hakka_cert",
                    source_exam_id="event",
                    year_ad=2026,
                    year_roc=115,
                    exam_name_raw="Hakka",
                    attachments=[],
                    papers=[
                        ParsedPaper(
                            category_raw="basic",
                            category_code="101",
                            subject_code="0101",
                            subject_name_raw="all",
                            files={},
                            mirror_files={"question": {"storage_key": "providers/hakka_cert/115/missing/question.pdf"}},
                        )
                    ],
                )
            ]

            with self.assertRaisesRegex(ValueError, "referenced file"):
                MirrorStore(root).prune_unreferenced_provider("hakka_cert", raw_pages, NormalizedCatalog(papers=[], review_queue=[]), apply=True)
            self.assertTrue(stale_path.exists())


if __name__ == "__main__":
    unittest.main()
