import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.models import NormalizedCatalog
from app.paths import MIRROR_ORIGIN_NAME
from app.storage import MirrorStore


class MirrorReclaimTests(unittest.TestCase):
    def test_pruning_keeps_provider_snapshot_recovery_metadata(self) -> None:
        for duplicates_only in (False, True):
            with self.subTest(duplicates_only=duplicates_only), tempfile.TemporaryDirectory() as tmp_dir:
                root = Path(tmp_dir)
                active = root / "providers/demo/active.pdf"
                active.parent.mkdir(parents=True)
                active.write_bytes(b"retained")
                marker = active.with_name(MIRROR_ORIGIN_NAME)
                temporary = active.with_name(MIRROR_ORIGIN_NAME + ".temporary")
                for path in (marker, temporary):
                    path.write_bytes(b"retained")
                store = MirrorStore(root)
                with mock.patch.object(store, "referenced_storage_keys", return_value={
                    active.relative_to(root).as_posix()
                }):
                    result = store.prune_unreferenced_provider(
                        "demo", [], NormalizedCatalog([], []), apply=True,
                        duplicates_only=duplicates_only,
                    )
                self.assertEqual(result.scanned_files, 1)
                self.assertEqual(result.removed_files, 0)
                self.assertEqual(result.reclaimable_bytes, 0)
                for path in (marker, temporary):
                    self.assertEqual(path.read_bytes(), b"retained")

    def test_deduplication_skips_recovery_markers_but_keeps_nested_source_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            active = root / "providers/demo/active.pdf"
            active.parent.mkdir(parents=True)
            active.write_bytes(b"retained")
            marker = active.with_name(MIRROR_ORIGIN_NAME)
            marker.write_bytes(active.read_bytes())
            source = active.parent / "native" / MIRROR_ORIGIN_NAME
            source.parent.mkdir()
            source.write_bytes(active.read_bytes())
            result = MirrorStore(root).deduplicate_existing(apply=True)
            self.assertEqual(result.scanned_files, 2)
            self.assertEqual(result.relinked_files, 1)
            self.assertFalse(active.samefile(marker))
            self.assertTrue(active.samefile(source))
            self.assertEqual(marker.read_bytes(), b"retained")

    def test_pruning_a_retained_hard_link_reclaims_no_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            active = root / "providers/demo/active.pdf"
            stale = active.with_name("stale.pdf")
            active.parent.mkdir(parents=True)
            active.write_bytes(b"retained")
            os.link(active, stale)
            self.assertTrue(active.samefile(stale))
            self._assert_prune(root, active, [stale], 0)

    def test_pruning_an_independent_duplicate_reclaims_its_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            active = root / "providers/demo/active.pdf"
            stale = active.with_name("stale.pdf")
            active.parent.mkdir(parents=True)
            active.write_bytes(b"retained")
            stale.write_bytes(active.read_bytes())
            self.assertFalse(active.samefile(stale))
            self._assert_prune(root, active, [stale], len(b"retained"))

    def test_pruning_all_links_to_an_independent_duplicate_counts_it_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            active = root / "providers/demo/active.pdf"
            stale = active.with_name("stale.pdf")
            alias = active.with_name("stale-alias.pdf")
            active.parent.mkdir(parents=True)
            active.write_bytes(b"retained")
            stale.write_bytes(active.read_bytes())
            os.link(stale, alias)
            self.assertFalse(active.samefile(stale))
            self.assertTrue(stale.samefile(alias))
            self._assert_prune(root, active, [stale, alias], len(b"retained"))

    def test_pruning_preserves_payload_linked_outside_the_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            active = root / "providers/demo/active.pdf"
            stale = active.with_name("stale.pdf")
            external = root / "outside-provider.pdf"
            active.parent.mkdir(parents=True)
            active.write_bytes(b"retained")
            stale.write_bytes(active.read_bytes())
            os.link(stale, external)
            self._assert_prune(root, active, [stale], 0)
            self.assertEqual(external.read_bytes(), b"retained")

    def test_pruning_a_symbolic_link_does_not_count_or_delete_its_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            active = root / "providers/demo/active.pdf"
            stale = active.with_name("stale.pdf")
            external = root / "outside-provider.pdf"
            active.parent.mkdir(parents=True)
            active.write_bytes(b"retained")
            external.write_bytes(active.read_bytes())
            stale.symlink_to(external)
            self._assert_prune(root, active, [stale], 0)
            self.assertEqual(external.read_bytes(), b"retained")

    def test_deduplication_counts_replaced_hard_links_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            canonical = root / "a.pdf"
            duplicate = root / "b.pdf"
            alias = root / "c.pdf"
            canonical.write_bytes(b"retained")
            duplicate.write_bytes(canonical.read_bytes())
            os.link(duplicate, alias)
            store = MirrorStore(root)
            preview = store.deduplicate_existing()
            self.assertEqual(preview.reclaimable_bytes, len(b"retained"))
            self.assertEqual(preview.relinked_files, 2)
            self.assertFalse(canonical.samefile(duplicate))
            result = store.deduplicate_existing(apply=True)
            self.assertEqual(result.reclaimable_bytes, preview.reclaimable_bytes)
            self.assertTrue(canonical.samefile(duplicate))
            self.assertTrue(canonical.samefile(alias))
            self.assertEqual(canonical.read_bytes(), b"retained")

    def test_deduplication_does_not_count_an_external_retained_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, tempfile.TemporaryDirectory() as external_dir:
            root = Path(tmp_dir)
            canonical = root / "a.pdf"
            duplicate = root / "b.pdf"
            external = Path(external_dir) / "retained.pdf"
            canonical.write_bytes(b"retained")
            duplicate.write_bytes(canonical.read_bytes())
            os.link(duplicate, external)
            store = MirrorStore(root)
            preview = store.deduplicate_existing()
            self.assertEqual(preview.reclaimable_bytes, 0)
            result = store.deduplicate_existing(apply=True)
            self.assertEqual(result.reclaimable_bytes, 0)
            self.assertTrue(canonical.samefile(duplicate))
            self.assertFalse(canonical.samefile(external))
            self.assertEqual(external.read_bytes(), b"retained")

    def _assert_prune(self, root: Path, active: Path, stale: list[Path], expected: int) -> None:
        store = MirrorStore(root)
        with mock.patch.object(
            store, "referenced_storage_keys", return_value={active.relative_to(root).as_posix()}
        ):
            preview = store.prune_unreferenced_provider(
                "demo", [], NormalizedCatalog([], []), duplicates_only=True
            )
            self.assertEqual(preview.reclaimable_bytes, expected)
            self.assertEqual(preview.removed_files, len(stale))
            self.assertTrue(all(path.exists() for path in stale))
            result = store.prune_unreferenced_provider(
                "demo", [], NormalizedCatalog([], []), duplicates_only=True, apply=True
            )
        self.assertEqual(result.reclaimable_bytes, expected)
        self.assertTrue(all(not path.exists() for path in stale))
        self.assertEqual(active.read_bytes(), b"retained")
