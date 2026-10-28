"""Exact PDF source contexts shared by native-role and annual-rule evidence."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from app.providers.taisugar_recruit.archives import (
    member_subject_code,
    parse_member_locator,
    read_pdf_members,
)


def native_pdf_subject_code(url: str) -> str:
    from app.providers.taisugar_recruit.client import source_subject_code

    source = urlsplit(url)
    if source.fragment:
        parent, _checksum, name = parse_member_locator(url)
        if Path(name).suffix.lower() != ".pdf":
            raise ValueError("Taiwan Sugar native evidence requires a PDF archive member")
        return member_subject_code(parent, name)
    if (
        source.scheme != "https"
        or source.netloc != "www.taisugar.com.tw"
        or re.fullmatch(r"/upload/UserFiles/News/\d+/[^/]+\.pdf", source.path, re.IGNORECASE)
        is None
        or source.query
    ):
        raise ValueError("Taiwan Sugar native evidence requires an exact original PDF URL")
    return source_subject_code(url)


def verify_native_anchor(repo_root: Path, storage_key: str, checksum: str) -> Path:
    key = PurePosixPath(storage_key)
    if (
        key.is_absolute()
        or ".." in key.parts
        or "\\" in storage_key
        or key.parts[:2] != ("providers", "taisugar_recruit")
    ):
        raise ValueError("Taiwan Sugar native evidence has an invalid mirror locator")
    path = repo_root / "mirror" / storage_key
    provider_root = repo_root / "mirror/providers/taisugar_recruit"
    if (
        not path.resolve().is_relative_to(provider_root.resolve())
        or path.is_symlink()
        or not path.is_file()
    ):
        raise ValueError("Taiwan Sugar native evidence has missing or changed mirror bytes")
    from app.source_revisions import payload_checksum

    if payload_checksum(path) != checksum:
        raise ValueError("Taiwan Sugar native evidence has missing or changed mirror bytes")

    return path


def validate_member_parents(
    repo_root: Path,
    anchors: Iterable[tuple[str, int, str, str]],
    *,
    verify_mirror: bool,
) -> None:
    """Require each reviewed member's exact retained container and native bytes."""
    from app.paths import provider_paths
    from app.source_revisions import load_source_revisions
    from app.state import load_provider_state

    expected: dict[tuple[str, int, str, str], dict[str, str]] = {}
    for event, year, url, checksum in anchors:
        if not urlsplit(url).fragment:
            continue
        parent, parent_checksum, name = parse_member_locator(url)
        members = expected.setdefault((event, year, parent, parent_checksum), {})
        if name in members and members[name] != checksum:
            raise ValueError("Conflicting Taiwan Sugar native member checksums")
        members[name] = checksum
    if not expected:
        return
    owner = provider_paths(repo_root, "taisugar_recruit")
    pages, _, _ = load_provider_state(owner)
    parents = [
        (p.source_exam_id, p.year_roc, a.download_url_source, a.checksum, a.storage_key)
        for p in pages
        for a in p.attachments
        if a.file_type == "source_archive"
    ]
    for revision in load_source_revisions(owner):
        row = revision["source_record"]
        if revision["record_type"] != "attachment" or row["file_type"] != "source_archive":
            continue
        parents.append(
            (
                row["source_exam_id"],
                row["year_roc"],
                row["download_url_source"],
                row["checksum"],
                revision["blob_storage_key"],
            )
        )
    for context, members in expected.items():
        matches = [row[-1] for row in parents if row[:-1] == context]
        if not matches:
            raise ValueError("Taiwan Sugar native member lacks its retained parent anchor")
        if not verify_mirror:
            continue
        for key in dict.fromkeys(matches):
            path = verify_native_anchor(repo_root, key, context[-1])
            contents = read_pdf_members(path.read_bytes())
            for name, checksum in members.items():
                if name not in contents or hashlib.sha256(contents[name]).hexdigest() != checksum:
                    raise ValueError("Taiwan Sugar native member differs from its retained parent")
