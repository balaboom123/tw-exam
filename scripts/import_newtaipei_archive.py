"""Verify a reviewed native RAR completely before importing its PDF mirrors.

System libarchive is an operator tool, never a production runtime dependency.
No archive-controlled path is extracted to the filesystem.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import hashlib
import json
import sys
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.archive_paths import portable_archive_name  # noqa: E402
from app.providers.teacher_recruit_newtaipei.archive_plan import (  # noqa: E402
    MAX_ARCHIVE_BYTES,
    MAX_EXPANDED_BYTES,
    MAX_MEMBER_BYTES,
    MAX_MEMBERS,
    ReviewedArchive,
    native_archives_path,
    read_native_archives,
    verify_member,
    verify_parent,
)
from app.storage import MirrorStore  # noqa: E402


@dataclass(frozen=True)
class NativeEntry:
    path: str
    kind: str
    size: int
    data: bytes
    linked: bool = False
    encrypted: bool = False


def read_rar_entries(parent: bytes) -> Iterable[NativeEntry]:
    library_name = ctypes.util.find_library("archive")
    if not library_name:
        raise RuntimeError("Import requires system libarchive; routine sync does not")
    lib = ctypes.CDLL(library_name)
    ptr = ctypes.c_void_p

    def bind(name, args, result):
        function = getattr(lib, name)
        function.argtypes = args
        function.restype = result
        return function

    new = bind("archive_read_new", [], ptr)
    support = bind("archive_read_support_format_rar5", [ptr], ctypes.c_int)
    open_archive = bind("archive_read_open_memory", [ptr, ptr, ctypes.c_size_t], ctypes.c_int)
    next_header = bind("archive_read_next_header", [ptr, ctypes.POINTER(ptr)], ctypes.c_int)
    pathname = bind("archive_entry_pathname_utf8", [ptr], ctypes.c_char_p)
    size = bind("archive_entry_size", [ptr], ctypes.c_int64)
    kind = bind("archive_entry_filetype", [ptr], ctypes.c_uint)
    symlink = bind("archive_entry_symlink", [ptr], ctypes.c_char_p)
    hardlink = bind("archive_entry_hardlink", [ptr], ctypes.c_char_p)
    encrypted = bind("archive_entry_is_encrypted", [ptr], ctypes.c_int)
    read = bind("archive_read_data", [ptr, ptr, ctypes.c_size_t], ctypes.c_ssize_t)
    skip = bind("archive_read_data_skip", [ptr], ctypes.c_int)
    free = bind("archive_read_free", [ptr], ctypes.c_int)
    error = bind("archive_error_string", [ptr], ctypes.c_char_p)
    if len(parent) > MAX_ARCHIVE_BYTES:
        raise ValueError("New Taipei RAR exceeds the operator byte limit")
    archive = new()
    if not archive:
        raise RuntimeError("Cannot allocate native archive reader")
    buffer = ctypes.create_string_buffer(1024 * 1024)
    parent_buffer = ctypes.create_string_buffer(parent)
    try:
        if support(archive) != 0 or open_archive(archive, parent_buffer, len(parent)) != 0:
            raise ValueError(f"Cannot open native RAR: {error(archive)!r}")
        total = 0
        count = 0
        while True:
            entry = ptr()
            status = next_header(archive, ctypes.byref(entry))
            if status == 1:
                break
            if status != 0:
                raise ValueError(f"Cannot read native RAR header: {error(archive)!r}")
            count += 1
            raw_name = pathname(entry)
            length = size(entry)
            file_kind = {0o100000: "file", 0o040000: "directory"}.get(kind(entry), "special")
            if (
                count > MAX_MEMBERS
                or raw_name is None
                or not 0 <= length <= MAX_MEMBER_BYTES
                or file_kind == "special"
                or symlink(entry) is not None
                or hardlink(entry) is not None
                or encrypted(entry) != 0
            ):
                raise ValueError("Unsafe, linked, encrypted or oversized native RAR entry")
            name = raw_name.decode("utf-8", errors="strict")
            if not portable_archive_name(name):
                raise ValueError("Nonportable native RAR member path")
            data = bytearray()
            if file_kind == "directory":
                if length or skip(archive) != 0:
                    raise ValueError("Invalid native RAR directory")
            else:
                while True:
                    got = read(archive, buffer, len(buffer))
                    if got == 0:
                        break
                    if got < 0 or len(data) + got > length:
                        raise ValueError("Invalid or overflowing native RAR member data")
                    total += got
                    if total > MAX_EXPANDED_BYTES:
                        raise ValueError("Native RAR expanded byte limit exceeded")
                    data.extend(buffer.raw[:got])
            if len(data) != length:
                raise ValueError("Truncated native RAR member")
            yield NativeEntry(name, file_kind, length, bytes(data))
    finally:
        free(archive)


def verify_entries(plan: ReviewedArchive, entries: Iterable[NativeEntry]) -> dict[str, bytes]:
    expected = {m.path: m for m in plan.members}
    seen: set[str] = set()
    collisions: set[str] = set()
    payloads: dict[str, bytes] = {}
    expanded = 0
    for entry in entries:
        collision = unicodedata.normalize("NFC", entry.path).casefold()
        if (
            len(seen) >= MAX_MEMBERS
            or entry.path in seen
            or collision in collisions
            or not portable_archive_name(entry.path)
            or entry.linked
            or entry.encrypted
            or entry.path not in expected
        ):
            raise ValueError("Unexpected, duplicate, linked or unsafe native RAR member")
        member = expected[entry.path]
        if (entry.kind, entry.size, len(entry.data)) != (member.kind, member.size, member.size):
            raise ValueError("Native RAR member type or size differs from reviewed plan")
        seen.add(entry.path)
        collisions.add(collision)
        expanded += len(entry.data)
        if expanded > MAX_EXPANDED_BYTES:
            raise ValueError("Native RAR expanded byte limit exceeded")
        if member.kind == "file":
            verify_member(member, entry.data)
            payloads[member.path] = entry.data
    if seen != expected.keys():
        raise ValueError("Native RAR does not conserve the complete reviewed member set")
    return payloads


def import_archive(repo_root: Path, source: Path, *, check_only: bool = False) -> dict:
    # The checksum and the decompressor see the same bounded, immutable bytes.
    with source.open("rb") as handle:
        parent = handle.read(MAX_ARCHIVE_BYTES + 1)
    if len(parent) > MAX_ARCHIVE_BYTES:
        raise ValueError("New Taipei RAR exceeds the operator byte limit")
    plans = read_native_archives(native_archives_path(repo_root))
    candidates = [p for p in plans if len(parent) == p.size]
    plan = next((p for p in candidates if hashlib.sha256(parent).hexdigest() == p.checksum), None)
    if plan is None:
        raise ValueError("Unreviewed New Taipei native RAR checksum")
    verify_parent(plan, parent)
    payloads = verify_entries(plan, read_rar_entries(parent))
    mirror_root = repo_root / "mirror"
    targets = [(m, mirror_root / plan.storage_key(m)) for m in plan.files]
    for member, target in targets:
        if (
            not target.resolve().is_relative_to(mirror_root.resolve())
            or target.is_symlink()
            or any(p.is_symlink() for p in target.parents if p.is_relative_to(mirror_root))
        ):
            raise ValueError("New Taipei mirror target escapes the repository mirror")
        if target.exists():
            verify_member(member, target.read_bytes())
    if not check_only:
        # MirrorStore may build its dedupe index on the first write. Scope that
        # work to this provider; an import must not relink unrelated mirrors.
        provider_root = mirror_root / "providers/teacher_recruit_newtaipei"
        store = MirrorStore(provider_root)
        for member, target in targets:
            stored = store.write_bytes(
                target.relative_to(provider_root).as_posix(), payloads[member.path]
            )
            if stored.checksum != member.checksum:
                raise ValueError("New Taipei mirror import did not conserve a native PDF")
    return {
        "schema_version": 1,
        "provider_id": "teacher_recruit_newtaipei",
        "source_exam_id": plan.source_exam_id,
        "parent_sha256": plan.checksum,
        "parent_bytes": plan.size,
        "complete_members_verified": len(plan.members),
        "PDFs_verified": len(plan.files),
        "PDF_bytes": sum(m.size for m in plan.files),
        "check_only": check_only,
        "classification_approved": False,
        "files": [
            {
                "member": m.path,
                "locator": plan.locator(m),
                "storage_key": plan.storage_key(m),
                "sha256": m.checksum,
                "bytes": m.size,
            }
            for m in plan.files
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true", help="Verify without writing mirrors")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.suffix != ".json" or args.report.resolve() == args.archive.resolve():
        parser.error("--report must be a separate JSON file, preserving the native archive")
    report = import_archive(args.repo_root.resolve(), args.archive, check_only=args.check)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "files"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
