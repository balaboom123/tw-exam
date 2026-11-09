"""Reviewed native RAR members; recruitment applicability is a separate decision.

Routine acquisition uses verified PDF mirrors restored from a snapshot or imported
by the operator tool. Production does not depend on a RAR decompressor.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from app.archive_paths import portable_archive_name
from app.models import ParsedPaper, SourceExamPage
from app.providers.base import UnreviewedSourceFile

PROVIDER_ID = "teacher_recruit_newtaipei"
_DOWNLOAD = (
    r"https://career\.ntpc\.edu\.tw/web-elec-bulletin/open/oauth_data/op_api/download/"
    r"[0-9a-f]{31,32}"
)
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_MEMBER_BYTES = 25 * 1024 * 1024
MAX_EXPANDED_BYTES = 200 * 1024 * 1024
MAX_MEMBERS = 500


@dataclass(frozen=True)
class ArchiveMember:
    path: str
    kind: str
    size: int
    checksum: str
    subject_name: str
    file_type: str
    native_header: str
    page_count: int
    inspected_pages: tuple[int, ...]

    @property
    def subject_code(self) -> str:
        return "subject-" + hashlib.sha1(self.subject_name.encode("utf-8")).hexdigest()[:8]


@dataclass(frozen=True)
class ReviewedArchive:
    source_exam_id: str
    year_roc: int
    programme_name: str
    source_url: str
    checksum: str
    size: int
    reviewed_at: str
    review_notes: str
    members: tuple[ArchiveMember, ...]

    @property
    def files(self) -> tuple[ArchiveMember, ...]:
        return tuple(m for m in self.members if m.kind == "file")

    def locator(self, member: ArchiveMember) -> str:
        return (
            f"{self.source_url}#archive-sha256={self.checksum}&member={quote(member.path, safe='')}"
        )

    def storage_key(self, member: ArchiveMember) -> str:
        # Use the sync owner's path policy, including the preserved native category.
        from app.sync import _mirror_prefix_for_paper

        page = SourceExamPage(
            self.source_exam_id,
            self.year_roc + 1911,
            self.year_roc,
            self.programme_name,
            [],
            [],
            PROVIDER_ID,
        )
        paper = ParsedPaper(
            "新北市教師甄試",
            str(self.year_roc),
            member.subject_code,
            member.subject_name,
            {member.file_type: self.locator(member)},
        )
        return _mirror_prefix_for_paper(page, paper, member.file_type) + ".pdf"


def native_archives_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/teacher-recruit-newtaipei/native-archives-v1.json"


def _checksum(value: str) -> bool:
    return re.fullmatch(r"[0-9a-f]{64}", value) is not None


def read_native_archives(path: Path) -> tuple[ReviewedArchive, ...]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        set(document) != {"schema_version", "provider_id", "archives"}
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 1
        or document["provider_id"] != PROVIDER_ID
        or not isinstance(document["archives"], list)
        or not document["archives"]
    ):
        raise ValueError("Unsupported New Taipei native archive evidence")
    archives = []
    seen_sources: set[str] = set()
    for row in document["archives"]:
        try:
            fields = dict(row)
            fields["members"] = tuple(
                ArchiveMember(**{**m, "inspected_pages": tuple(m["inspected_pages"])})
                for m in fields["members"]
            )
            archive = ReviewedArchive(**fields)
        except (KeyError, TypeError) as exc:
            raise ValueError("Invalid New Taipei native archive fields") from exc
        if (
            type(archive.year_roc) is not int
            or not 1 <= archive.year_roc <= 999
            or archive.source_exam_id != f"teacher-recruit-newtaipei-{archive.year_roc}-junior"
            or not archive.programme_name.strip()
            or re.fullmatch(_DOWNLOAD, archive.source_url) is None
            or archive.source_url in seen_sources
            or not _checksum(archive.checksum)
            or type(archive.size) is not int
            or not 0 < archive.size <= MAX_ARCHIVE_BYTES
            or date.fromisoformat(archive.reviewed_at).year < archive.year_roc + 1911
            or not archive.review_notes.strip()
            or not 0 < len(archive.members) <= MAX_MEMBERS
        ):
            raise ValueError("Invalid New Taipei native archive source or review")
        seen_sources.add(archive.source_url)
        seen_paths: set[str] = set()
        kinds: dict[str, str] = {}
        subjects: dict[str, set[str]] = {}
        subject_codes: dict[str, str] = {}
        expanded_bytes = 0
        for member in archive.members:
            collision_key = unicodedata.normalize("NFC", member.path).casefold()
            if (
                not portable_archive_name(member.path)
                or collision_key in seen_paths
                or member.kind not in {"file", "directory"}
                or type(member.size) is not int
                or not 0 <= member.size <= MAX_MEMBER_BYTES
            ):
                raise ValueError("Unsafe, duplicate or oversized New Taipei native member")
            seen_paths.add(collision_key)
            kinds[collision_key] = member.kind
            if member.kind == "directory":
                if member.size or any(
                    (
                        member.checksum,
                        member.subject_name,
                        member.file_type,
                        member.native_header,
                        member.page_count,
                        member.inspected_pages,
                    )
                ):
                    raise ValueError("New Taipei native directory has file metadata")
                continue
            if (
                not member.path.endswith(".pdf")
                or not member.size
                or not _checksum(member.checksum)
                or not member.subject_name.strip()
                or member.file_type not in {"question", "answer", "source_document"}
                or not member.native_header.strip()
                or type(member.page_count) is not int
                or member.page_count < 1
                or not member.inspected_pages
                or tuple(sorted(set(member.inspected_pages))) != member.inspected_pages
                or any(
                    type(p) is not int or not 1 <= p <= member.page_count
                    for p in member.inspected_pages
                )
                or not {1, member.page_count} <= set(member.inspected_pages)
                or member.file_type in subjects.setdefault(member.subject_name, set())
            ):
                raise ValueError("Invalid New Taipei native PDF role or focused page review")
            subjects[member.subject_name].add(member.file_type)
            if (
                subject_codes.setdefault(member.subject_code, member.subject_name)
                != member.subject_name
            ):
                raise ValueError("New Taipei native subject storage codes collide")
            expanded_bytes += member.size
        if (
            not subjects
            or expanded_bytes > MAX_EXPANDED_BYTES
            or any(
                roles not in ({"question", "answer"}, {"question", "source_document"})
                for roles in subjects.values()
            )
        ):
            raise ValueError("Incomplete New Taipei native booklet roles or expanded byte limit")
        for member in archive.members:
            parts = unicodedata.normalize("NFC", member.path).casefold().split("/")
            if any(kinds.get("/".join(parts[:i])) != "directory" for i in range(1, len(parts))):
                raise ValueError("New Taipei native member lacks explicit directory ancestry")
        archives.append(archive)
    return tuple(archives)


@lru_cache(maxsize=1)
def native_archives() -> tuple[ReviewedArchive, ...]:
    return read_native_archives(native_archives_path(Path(__file__).resolve().parents[3]))


def resolve_member_locator(url: str) -> tuple[ReviewedArchive, ArchiveMember] | None:
    # Comparing the canonical locator also rejects duplicate fragment keys,
    # alternate encodings, unknown revisions and unreviewed member paths.
    for archive in native_archives():
        for member in archive.files:
            if archive.locator(member) == url:
                return archive, member
    return None


def verify_parent(archive: ReviewedArchive, data: bytes) -> None:
    if (
        len(data) != archive.size
        or not data.startswith(b"Rar!\x1a\x07\x01\x00")
        or hashlib.sha256(data).hexdigest() != archive.checksum
    ):
        raise UnreviewedSourceFile(
            "New Taipei native RAR changed; retain previous state pending review",
            url=archive.source_url,
        )


def verify_member(member: ArchiveMember, data: bytes) -> None:
    if (
        len(data) != member.size
        or not data.startswith(b"%PDF-")
        or hashlib.sha256(data).hexdigest() != member.checksum
    ):
        raise ValueError("New Taipei native PDF differs from its reviewed archive member")


def member_storage_keys(
    repo_root: Path, archive: ReviewedArchive, member: ArchiveMember
) -> tuple[str, ...]:
    """Find a reference PDF after a redundant import-cache alias is pruned.

    Administered papers use the sync paper path. Reference attachments use the
    attachment path chosen by sync; its retained exact source anchor owns that
    path, including any collision suffix. Do not duplicate that path policy.
    """
    keys = [archive.storage_key(member)]
    if member.file_type != "source_document":
        return tuple(keys)
    from app.paths import provider_paths
    from app.state import load_provider_state

    pages, _catalog, _failures = load_provider_state(provider_paths(repo_root, PROVIDER_ID))
    for page in pages:
        if (page.source_exam_id, page.year_roc) != (archive.source_exam_id, archive.year_roc):
            continue
        for attachment in page.attachments:
            if (attachment.download_url_source, attachment.checksum, attachment.file_type) != (
                archive.locator(member),
                member.checksum,
                member.file_type,
            ):
                continue
            key = attachment.storage_key
            if not portable_archive_name(key) or not key.startswith(f"providers/{PROVIDER_ID}/"):
                raise ValueError("New Taipei reference member has an unsafe retained mirror anchor")
            if key not in keys:
                keys.append(key)
    return tuple(keys)


def validate_native_archives(repo_root: Path, *, verify_mirror: bool = False) -> int:
    from app.paths import provider_paths
    from app.source_revisions import _source_records, load_source_revisions
    from app.state import load_provider_state

    provider = provider_paths(repo_root, PROVIDER_ID)
    pages, catalog, _ = load_provider_state(provider)
    records = [record for _, record in _source_records(PROVIDER_ID, pages, catalog.papers)]
    records.extend(
        {**entry["source_record"], "storage_key": entry["blob_storage_key"]}
        for entry in load_source_revisions(provider)
    )
    plans = read_native_archives(native_archives_path(repo_root))
    for plan in plans:
        parents = [
            record
            for record in records
            if (
                record.get("source_exam_id"),
                record.get("year_roc"),
                record.get("download_url_source"),
                record.get("checksum"),
            )
            == (plan.source_exam_id, plan.year_roc, plan.source_url, plan.checksum)
            and (
                record.get("file_type") == "source_archive"
                or (
                    record.get("file_type"),
                    record.get("subject_code"),
                    record.get("category_code"),
                )
                == ("question_answer", "junior", str(plan.year_roc))
            )
        ]
        if not parents or any(not p.get("storage_key") for p in parents):
            raise ValueError("New Taipei native archive lacks its retained original source anchor")
        if verify_mirror:
            for parent in parents:
                verify_parent(plan, (repo_root / "mirror" / parent["storage_key"]).read_bytes())
            for member in plan.files:
                candidates = [
                    repo_root / "mirror" / key
                    for key in member_storage_keys(repo_root, plan, member)
                ]
                existing = [p for p in candidates if p.is_file()]
                if not existing:
                    raise ValueError(
                        "New Taipei native member lacks a verified mirror or retained attachment"
                    )
                for path in existing:
                    verify_member(member, path.read_bytes())
    return len(plans)
