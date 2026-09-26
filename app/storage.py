from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.models import NormalizedCatalog, SourceExamPage, StoredFile

DEDUPE_INDEX_FILE = ".mirror-dedupe-index.json"


@dataclass(frozen=True)
class MirrorDedupeResult:
    scanned_files: int
    indexed_payloads: int
    duplicate_groups: int
    duplicate_files: int
    relinked_files: int
    reclaimable_bytes: int
    applied: bool


@dataclass(frozen=True)
class MirrorPruneResult:
    scanned_files: int
    retained_files: int
    removed_files: int
    reclaimable_bytes: int
    missing_references: int
    applied: bool


class MirrorStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._dedupe_index: dict[str, dict[str, int | str]] | None = None
        self._checksums_by_path: dict[str, set[str]] = {}
        self._index_dirty = False

    @property
    def dedupe_index_path(self) -> Path:
        return self.root / DEDUPE_INDEX_FILE

    def _payload_paths(self) -> list[Path]:
        if not self.root.exists():
            return []
        return [
            path
            for path in sorted(self.root.rglob("*"))
            if path.is_file()
            and path != self.dedupe_index_path
            and not path.name.startswith(f".{DEDUPE_INDEX_FILE}.")
        ]

    @staticmethod
    def _checksum_path(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _load_dedupe_index(self) -> dict[str, dict[str, int | str]]:
        if self._dedupe_index is not None:
            return self._dedupe_index
        entries: dict[str, dict[str, int | str]] = {}
        try:
            payload = json.loads(self.dedupe_index_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            payload = {}
        raw_entries = payload.get("entries", {}) if isinstance(payload, dict) else {}
        if isinstance(raw_entries, dict):
            for checksum, entry in raw_entries.items():
                if not isinstance(checksum, str) or not isinstance(entry, dict):
                    continue
                storage_key = entry.get("storage_key")
                size = entry.get("size")
                if isinstance(storage_key, str) and isinstance(size, int) and size >= 0:
                    entries[checksum] = {"storage_key": storage_key, "size": size}
        self._set_dedupe_index(entries)
        return entries

    def _set_dedupe_index(self, entries: dict[str, dict[str, int | str]]) -> None:
        self._dedupe_index = entries
        self._checksums_by_path = {}
        for checksum, entry in entries.items():
            storage_key = entry.get("storage_key")
            if isinstance(storage_key, str):
                self._checksums_by_path.setdefault(storage_key, set()).add(checksum)

    def _discard_checksum(self, checksum: str) -> None:
        entry = self._load_dedupe_index().pop(checksum, None)
        if entry is None:
            return
        storage_key = entry.get("storage_key")
        if isinstance(storage_key, str):
            checksums = self._checksums_by_path.get(storage_key)
            if checksums is not None:
                checksums.discard(checksum)
                if not checksums:
                    del self._checksums_by_path[storage_key]
        self._index_dirty = True

    def _ensure_dedupe_index(self) -> None:
        if self._dedupe_index is not None:
            return
        if self.dedupe_index_path.exists():
            self._load_dedupe_index()
            return
        if self._payload_paths():
            self.deduplicate_existing(apply=True)
            return
        self._dedupe_index = {}

    def flush_dedupe_index(self) -> None:
        if not self._index_dirty:
            return
        entries = self._load_dedupe_index()
        self.root.mkdir(parents=True, exist_ok=True)
        temporary_path = self.dedupe_index_path.with_name(
            f".{DEDUPE_INDEX_FILE}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temporary_path.write_text(
                json.dumps(
                    {"schema_version": 1, "entries": entries},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            os.replace(temporary_path, self.dedupe_index_path)
            self._index_dirty = False
        finally:
            temporary_path.unlink(missing_ok=True)

    def _storage_key_for_path(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def _stored_file_for_path(
        self,
        path: Path,
        *,
        created: bool,
        checksum: str | None = None,
        size: int | None = None,
    ) -> StoredFile:
        resolved_size = path.stat().st_size if size is None else size
        resolved_checksum = self._checksum_path(path) if checksum is None else checksum
        stored = StoredFile(
            storage_key=self._storage_key_for_path(path),
            path=path,
            checksum=resolved_checksum,
            created=created,
            size=resolved_size,
        )
        self._register_checksum(stored.checksum, stored.size, stored.storage_key)
        return stored

    def _candidate_paths(self, storage_key_prefix: str) -> list[Path]:
        path_prefix = self.root / Path(storage_key_prefix)
        candidates: list[Path] = []
        if path_prefix.is_file():
            candidates.append(path_prefix)
        if path_prefix.parent.exists():
            candidates.extend(
                candidate
                for candidate in sorted(path_prefix.parent.glob(f"{path_prefix.name}.*"))
                if candidate.is_file()
            )
        return candidates

    def _register_checksum(self, checksum: str, size: int, storage_key: str) -> None:
        index = self._load_dedupe_index()
        entry = index.get(checksum)
        if entry is None:
            index[checksum] = {"storage_key": storage_key, "size": size}
            self._checksums_by_path.setdefault(storage_key, set()).add(checksum)
            self._index_dirty = True

    def _discard_index_paths(self, storage_keys: set[str]) -> None:
        if not storage_keys:
            return
        self._load_dedupe_index()
        for storage_key in storage_keys:
            for checksum in tuple(self._checksums_by_path.get(storage_key, ())):
                self._discard_checksum(checksum)

    def _canonical_path(self, checksum: str, size: int) -> Path | None:
        index = self._load_dedupe_index()
        entry = index.get(checksum)
        if entry is None or entry.get("size") != size:
            return None
        storage_key = entry.get("storage_key")
        if not isinstance(storage_key, str):
            self._discard_checksum(checksum)
            return None
        path = self.root / Path(storage_key)
        if path.is_file() and path.stat().st_size == size and self._checksum_path(path) == checksum:
            return path
        self._discard_checksum(checksum)
        return None

    @staticmethod
    def _replace_with_hard_link(canonical_path: Path, target_path: Path) -> bool:
        try:
            if target_path.exists() and canonical_path.samefile(target_path):
                return False
        except FileNotFoundError:
            pass
        temporary_path = target_path.with_name(f".{target_path.name}.dedupe-{uuid.uuid4().hex}")
        try:
            os.link(canonical_path, temporary_path)
            os.replace(temporary_path, target_path)
        finally:
            temporary_path.unlink(missing_ok=True)
        return True

    def find_existing(self, storage_key_prefix: str) -> StoredFile | None:
        self._ensure_dedupe_index()
        unique_matches = list(dict.fromkeys(self._candidate_paths(storage_key_prefix)))
        if len(unique_matches) > 1:
            preferred_matches = [
                candidate
                for candidate in unique_matches
                if candidate.suffix.lower() in {".pdf", ".zip"}
            ]
            if len(preferred_matches) == 1:
                return self._stored_file_for_path(preferred_matches[0], created=False)
        if len(unique_matches) != 1:
            return None
        return self._stored_file_for_path(unique_matches[0], created=False)

    def delete_matching_except(self, storage_key_prefix: str, keep_storage_key: str) -> None:
        keep_path = self.root / Path(keep_storage_key)
        removed_storage_keys: set[str] = set()
        for candidate in dict.fromkeys(self._candidate_paths(storage_key_prefix)):
            if candidate != keep_path:
                removed_storage_keys.add(self._storage_key_for_path(candidate))
                candidate.unlink(missing_ok=True)
        self._discard_index_paths(removed_storage_keys)

    def write_bytes(self, storage_key: str, data: bytes, *, overwrite: bool = False) -> StoredFile:
        self._ensure_dedupe_index()
        path = self.root / Path(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        created = not path.exists()
        if not created and not overwrite:
            return self._stored_file_for_path(path, created=False)

        checksum = hashlib.sha256(data).hexdigest()
        size = len(data)
        self._discard_index_paths({storage_key})
        canonical_path = self._canonical_path(checksum, size)
        if canonical_path is None:
            temporary_path = path.with_name(f".{path.name}.write-{uuid.uuid4().hex}")
            try:
                temporary_path.write_bytes(data)
                os.replace(temporary_path, path)
            finally:
                temporary_path.unlink(missing_ok=True)
            self._register_checksum(checksum, size, storage_key)
        elif canonical_path != path:
            self._replace_with_hard_link(canonical_path, path)

        return StoredFile(
            storage_key=storage_key, path=path, checksum=checksum, created=created, size=size
        )

    def deduplicate_existing(self, *, apply: bool = False) -> MirrorDedupeResult:
        paths = self._payload_paths()
        payloads: dict[tuple[int, str], list[Path]] = {}
        for path in paths:
            size = path.stat().st_size
            checksum = self._checksum_path(path)
            payloads.setdefault((size, checksum), []).append(path)

        duplicate_groups = 0
        duplicate_files = 0
        relinked_files = 0
        reclaimable_bytes = 0
        rebuilt_index: dict[str, dict[str, int | str]] = {}
        for (size, checksum), group_paths in payloads.items():
            ordered_paths = sorted(group_paths)
            canonical_path = ordered_paths[0]
            rebuilt_index[checksum] = {
                "storage_key": self._storage_key_for_path(canonical_path),
                "size": size,
            }
            if len(ordered_paths) < 2:
                continue
            duplicate_groups += 1
            duplicate_files += len(ordered_paths)
            for duplicate_path in ordered_paths[1:]:
                if canonical_path.samefile(duplicate_path):
                    continue
                relinked_files += 1
                reclaimable_bytes += size
                if apply:
                    self._replace_with_hard_link(canonical_path, duplicate_path)

        if apply:
            self._set_dedupe_index(rebuilt_index)
            self._index_dirty = True
            self.flush_dedupe_index()
        return MirrorDedupeResult(
            scanned_files=len(paths),
            indexed_payloads=len(payloads),
            duplicate_groups=duplicate_groups,
            duplicate_files=duplicate_files,
            relinked_files=relinked_files,
            reclaimable_bytes=reclaimable_bytes,
            applied=apply,
        )

    @staticmethod
    def _normalize_provider_storage_key(provider_id: str, storage_key: str) -> str | None:
        normalized = storage_key.replace("\\", "/").lstrip("/")
        if not normalized:
            return None
        parts = PurePosixPath(normalized).parts
        provider_prefix = ("providers", provider_id)
        if parts[:2] == provider_prefix:
            return normalized
        if parts and parts[0] == "providers":
            return None
        return f"providers/{provider_id}/{normalized}"

    def referenced_storage_keys(
        self,
        provider_id: str,
        raw_pages: list[SourceExamPage],
        catalog: NormalizedCatalog,
    ) -> set[str]:
        storage_keys: set[str] = set()

        def add(storage_key: str) -> None:
            normalized = self._normalize_provider_storage_key(provider_id, storage_key)
            if normalized is not None:
                storage_keys.add(normalized)

        for page in raw_pages:
            for attachment in page.attachments:
                if attachment.storage_key:
                    add(attachment.storage_key)
            for paper in page.papers:
                for metadata in paper.mirror_files.values():
                    storage_key = metadata.get("storage_key")
                    if isinstance(storage_key, str) and storage_key:
                        add(storage_key)
        for normalized_paper in catalog.papers:
            if normalized_paper.storage_key:
                add(normalized_paper.storage_key)
        return storage_keys

    def prune_unreferenced_provider(
        self,
        provider_id: str,
        raw_pages: list[SourceExamPage],
        catalog: NormalizedCatalog,
        *,
        apply: bool = False,
    ) -> MirrorPruneResult:
        references = self.referenced_storage_keys(provider_id, raw_pages, catalog)
        if not references:
            raise ValueError(
                f"Refusing to prune mirror provider {provider_id}: "
                f"no referenced storage keys were found."
            )
        provider_root = self.root / "providers" / provider_id
        paths = [path for path in self._payload_paths() if path.is_relative_to(provider_root)]
        files_by_key = {self._storage_key_for_path(path): path for path in paths}
        missing_references = references - files_by_key.keys()
        if missing_references:
            raise ValueError(
                f"Refusing to prune mirror provider {provider_id}: "
                f"{len(missing_references)} referenced file(s) are missing."
            )
        stale_paths = [path for key, path in files_by_key.items() if key not in references]
        reclaimable_bytes = sum(path.stat().st_size for path in stale_paths)
        if apply:
            stale_keys = {self._storage_key_for_path(path) for path in stale_paths}
            active_inode_keys = {
                (path.stat().st_dev, path.stat().st_ino): key
                for key, path in files_by_key.items()
                if key in references
            }
            index = self._load_dedupe_index()
            stale_checksums = {
                checksum
                for storage_key in stale_keys
                for checksum in self._checksums_by_path.get(storage_key, ())
            }
            for checksum in stale_checksums:
                entry = index[checksum]
                storage_key = entry.get("storage_key")
                if not isinstance(storage_key, str) or storage_key not in stale_keys:
                    continue
                stale_path = files_by_key[storage_key]
                replacement = active_inode_keys.get(
                    (stale_path.stat().st_dev, stale_path.stat().st_ino)
                )
                if replacement is None:
                    continue
                self._discard_checksum(checksum)
                self._register_checksum(checksum, stale_path.stat().st_size, replacement)
            for path in stale_paths:
                path.unlink()
            self._discard_index_paths(stale_keys)
            for directory in sorted(
                (path for path in provider_root.rglob("*") if path.is_dir()),
                key=lambda path: len(path.parts),
                reverse=True,
            ):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            self.flush_dedupe_index()
        return MirrorPruneResult(
            scanned_files=len(paths),
            retained_files=len(paths) - len(stale_paths),
            removed_files=len(stale_paths),
            reclaimable_bytes=reclaimable_bytes,
            missing_references=0,
            applied=apply,
        )
