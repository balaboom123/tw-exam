"""Reviewed native paper references; original containers remain recoverable.

This catalogue establishes file roles and subject headers, not occupations.
All projected papers still require occupational applicability review.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit, urlunsplit

from app.archive_paths import portable_archive_name
from app.source_material import SourceMaterial


@dataclass(frozen=True)
class NativeMember:
    name: str
    checksum: str
    file_role: str
    subject_label: str
    category_header: str
    revision_notice: str

    @property
    def subject_code(self) -> str:
        return "native-" + hashlib.sha256(self.name.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class NativeArchive:
    year_roc: int
    source_url: str
    checksum: str
    review_reason: str
    members: tuple[NativeMember, ...]


def native_archive_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/twc/archive-papers-v1.json"


def read_native_archives(path: Path) -> tuple[NativeArchive, ...]:
    from app.providers.twc_recruit.client import TwcRecruitClient

    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        type(document.get("schema_version")) is not int
        or document["schema_version"] != 1
        or document.get("provider_id") != "twc_recruit"
    ):
        raise ValueError("Unsupported Taiwan Water native archive evidence")
    archives = []
    contexts = set()
    for row in document["archives"]:
        year = row["programme_year_roc"]
        if type(year) is not int or not 1 <= year <= 999:
            raise ValueError("Invalid Taiwan Water native programme year")
        TwcRecruitClient._validate_download_url(row["source_url"])
        date.fromisoformat(row["reviewed_at"])
        context = (year, row["source_url"], row["checksum"])
        if context in contexts or not re.fullmatch(r"[0-9a-f]{64}", row["checksum"]):
            raise ValueError("Duplicate or invalid Taiwan Water native archive context")
        contexts.add(context)
        members = []
        names = set()
        for item in row["members"]:
            name = item["native_name"]
            folded = unicodedata.normalize("NFC", name).casefold()
            if folded in names or not portable_archive_name(name):
                raise ValueError("Unsafe or duplicate Taiwan Water native member")
            names.add(folded)
            if not re.fullmatch(r"[0-9a-f]{64}", item["checksum"]):
                raise ValueError("Taiwan Water native member requires SHA-256")
            role = item["file_role"]
            if role not in {"question", "answer", "question_answer", "source_artifact"}:
                raise ValueError("Unsupported Taiwan Water native file role")
            if role != "source_artifact" and (
                not name.lower().endswith(".pdf")
                or not item["subject_label"].strip()
                or not item["native_category_header"].strip()
            ):
                raise ValueError("Taiwan Water native paper lacks reviewed PDF headers")
            members.append(
                NativeMember(
                    name,
                    item["checksum"],
                    role,
                    item["subject_label"],
                    item["native_category_header"],
                    item["revision_notice"],
                )
            )
        if bool(members) == bool(row["review_reason"]):
            raise ValueError("A native archive needs reviewed members or a withholding reason")
        archives.append(
            NativeArchive(
                year, row["source_url"], row["checksum"], row["review_reason"], tuple(members)
            )
        )
    return tuple(archives)


@lru_cache(maxsize=1)
def native_archives() -> tuple[NativeArchive, ...]:
    return read_native_archives(native_archive_path(Path(__file__).resolve().parents[3]))


def member_locator(archive: NativeArchive, member: NativeMember) -> str:
    return (
        f"{archive.source_url}#archive-sha256={archive.checksum}"
        f"&zip-entry={quote(member.name, safe='')}"
    )


def parse_member_locator(url: str) -> tuple[str, str, str]:
    from app.providers.twc_recruit.client import TwcRecruitClient

    parts = urlsplit(url)
    values = parse_qs(parts.fragment, keep_blank_values=True, strict_parsing=True)
    if set(values) != {"archive-sha256", "zip-entry"} or any(
        len(items) != 1 for items in values.values()
    ):
        raise ValueError("Invalid Taiwan Water native member locator")
    archive_url = urlunsplit(parts._replace(fragment=""))
    TwcRecruitClient._validate_download_url(archive_url)
    checksum, name = values["archive-sha256"][0], values["zip-entry"][0]
    if not re.fullmatch(r"[0-9a-f]{64}", checksum) or not portable_archive_name(name):
        raise ValueError("Invalid Taiwan Water native member checksum or path")
    return archive_url, checksum, name


def resolve_native_member(
    year_roc: int,
    subject_code: str,
    checksum: str,
    *,
    source_url: str | None = None,
    source_material: SourceMaterial | None = None,
) -> tuple[NativeArchive, NativeMember] | None:
    for archive in native_archives():
        if archive.year_roc != year_roc:
            continue
        if source_material is not None:
            from app.providers.twc_recruit.identity import resolve_archive_identity

            parent = resolve_archive_identity(
                year_roc, archive.checksum, source_url=archive.source_url
            )
            if parent is None or parent.material != source_material:
                continue
        for member in archive.members:
            if (
                member.file_role != "source_artifact"
                and member.subject_code == subject_code
                and member.checksum == checksum
                and (source_url is None or member_locator(archive, member) == source_url)
            ):
                return archive, member
    return None


def decoded_member_name(info: zipfile.ZipInfo) -> str:
    if "\x00" in info.orig_filename or "\x00" in info.filename:
        raise ValueError("Taiwan Water native member contains a null path byte")
    if info.flag_bits & 0x800:
        return info.orig_filename
    raw = info.orig_filename.encode("cp437")
    extra = info.extra
    unicode_names = []
    while extra:
        if len(extra) < 4:
            raise ValueError("Malformed Taiwan Water ZIP extra field")
        identifier = int.from_bytes(extra[:2], "little")
        size = int.from_bytes(extra[2:4], "little")
        field, extra = extra[4 : 4 + size], extra[4 + size :]
        if len(field) != size:
            raise ValueError("Truncated Taiwan Water ZIP extra field")
        if identifier == 0x7075:
            if (
                len(field) < 5
                or field[0] != 1
                or int.from_bytes(field[1:5], "little") != zlib.crc32(raw)
            ):
                raise ValueError("Invalid Taiwan Water Unicode path field")
            unicode_names.append(field[5:].decode("utf-8"))
    if len(set(unicode_names)) > 1:
        raise ValueError("Conflicting Taiwan Water Unicode paths")
    return unicode_names[0] if unicode_names else raw.decode("cp950")


def extract_native_members(archive: NativeArchive, data: bytes) -> dict[str, bytes]:
    if hashlib.sha256(data).hexdigest() != archive.checksum:
        raise ValueError("Taiwan Water source archive changed; native papers need review")
    if not archive.members:
        raise ValueError(f"Taiwan Water source archive is withheld: {archive.review_reason}")
    expected = {member.name: member for member in archive.members}
    payloads = {}
    folded_paths = set()
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        for info in source.infolist():
            name = decoded_member_name(info)
            path = name.removesuffix("/") if info.is_dir() else name
            folded = unicodedata.normalize("NFC", path).casefold()
            if (
                not portable_archive_name(path)
                or folded in folded_paths
                or stat.S_ISLNK(info.external_attr >> 16)
                or info.flag_bits & 1
            ):
                raise ValueError("Unsafe, duplicate, linked or encrypted Taiwan Water native path")
            folded_paths.add(folded)
            if info.is_dir():
                continue
            if name not in expected:
                raise ValueError(f"Unreviewed Taiwan Water native member: {name}")
            # Verify every member's CRC, including source artifacts.
            payload = source.read(info)
            member = expected[name]
            if hashlib.sha256(payload).hexdigest() != member.checksum:
                raise ValueError(f"Taiwan Water native member checksum mismatch: {name}")
            if member.file_role != "source_artifact" and not payload.startswith(b"%PDF-"):
                raise ValueError(f"Taiwan Water native paper is not a PDF: {name}")
            payloads[name] = payload
    if set(payloads) != set(expected):
        raise ValueError("Taiwan Water native archive coverage differs from reviewed evidence")
    return payloads


def validate_native_archives(repo_root: Path, *, verify_mirror: bool = False) -> int:
    from app.paths import provider_paths
    from app.source_revisions import _source_records, load_source_revisions
    from app.state import load_provider_state

    provider = provider_paths(repo_root, "twc_recruit")
    pages, current, _ = load_provider_state(provider)
    retained = _source_records("twc_recruit", pages, current.papers)
    records = [record for _, record in retained]
    records.extend(
        {**entry["source_record"], "storage_key": entry["blob_storage_key"]}
        for entry in load_source_revisions(provider)
    )
    facts = read_native_archives(native_archive_path(repo_root))
    for fact in facts:
        parents = [
            record
            for record in records
            if record.get("year_roc") == fact.year_roc
            and record.get("source_exam_id") == f"twc-recruit-{fact.year_roc}"
            and record.get("download_url_source") == fact.source_url
            and record.get("checksum") == fact.checksum
            and (
                record.get("file_type") == "source_archive"
                or record.get("subject_code") == f"twc-recruit-{fact.year_roc}-bundle"
            )
        ]
        if not parents:
            raise ValueError(f"Taiwan Water native evidence lacks original anchor: {fact.year_roc}")
        projected = any(
            page.year_roc == fact.year_roc
            and any(
                item.file_type == "source_archive"
                and item.download_url_source == fact.source_url
                and item.checksum == fact.checksum
                for item in page.attachments
            )
            for page in pages
        )
        if projected:
            for member in fact.members:
                if member.file_role == "source_artifact":
                    continue
                anchors = [
                    record
                    for record in records
                    if record.get("year_roc") == fact.year_roc
                    and record.get("source_exam_id") == f"twc-recruit-{fact.year_roc}"
                    and record.get("category_code") == str(fact.year_roc)
                    and record.get("subject_code") == member.subject_code
                    and record.get("download_url_source") == member_locator(fact, member)
                    and record.get("checksum") == member.checksum
                    and record.get("file_type") == member.file_role
                ]
                if not anchors:
                    raise ValueError(
                        f"Taiwan Water native evidence lacks paper anchor: {member.name}"
                    )
                if verify_mirror:
                    for record in anchors:
                        path = repo_root / "mirror" / record["storage_key"]
                        if hashlib.sha256(path.read_bytes()).hexdigest() != member.checksum:
                            raise ValueError(
                                f"Taiwan Water native mirror checksum mismatch: {path}"
                            )
        if verify_mirror:
            for parent in parents:
                path = repo_root / "mirror" / parent["storage_key"]
                if hashlib.sha256(path.read_bytes()).hexdigest() != fact.checksum:
                    raise ValueError(f"Taiwan Water original anchor changed: {path}")
            if fact.members:
                extract_native_members(
                    fact, (repo_root / "mirror" / parents[0]["storage_key"]).read_bytes()
                )
    return len(facts)
