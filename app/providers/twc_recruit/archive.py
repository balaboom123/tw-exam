"""Reviewed native paper references; original containers remain recoverable.

File review and occupational applicability are independent. A paper can have
several reviewed recruitment categories without duplicating its native payload.
Unreviewed archives and category relationships retain their review disposition.
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
from app.source_material import SourceDate, SourceMaterial


@dataclass(frozen=True)
class NativeCategory:
    category_id: str
    label: str
    track_id: str
    native_categories: tuple[str, ...]
    variants: tuple[tuple[str, str], ...]
    evidence_url: str
    evidence_label: str
    review_reason: str

    def category_code(self, year_roc: int) -> str:
        return f"{year_roc}-{self.category_id}"


@dataclass(frozen=True)
class NativeMember:
    name: str
    checksum: str
    file_role: str
    subject_label: str
    category_header: str
    revision_notice: str
    category_ids: tuple[str, ...] = ()
    paper_slot: str = ""

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
    categories: tuple[NativeCategory, ...] = ()


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
        categories = []
        category_ids = set()
        identities = set()
        for item in row.get("categories", []):
            category_id = item["category_id"]
            variants = tuple((variant["id"], variant["label"]) for variant in item["variants"])
            identity = (item["track_id"], tuple(variant_id for variant_id, _ in variants))
            if (
                re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", category_id) is None
                or re.fullmatch(r"twc-[a-z0-9]+(?:-[a-z0-9]+)*", item["track_id"]) is None
                or category_id in category_ids
                or identity in identities
                or len({variant_id for variant_id, _ in variants}) != len(variants)
                or any(
                    re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", variant_id) is None
                    or not label.strip()
                    for variant_id, label in variants
                )
                or not item["label"].strip()
                or not item["native_categories"]
                or len(set(item["native_categories"])) != len(item["native_categories"])
                or any(not label.strip() for label in item["native_categories"])
                or not item["evidence_label"].strip()
                or urlsplit(item["evidence_url"]).scheme != "https"
                or not urlsplit(item["evidence_url"]).netloc
            ):
                raise ValueError("Invalid or duplicate Taiwan Water native category evidence")
            categories.append(
                NativeCategory(
                    category_id,
                    item["label"],
                    item["track_id"],
                    tuple(item["native_categories"]),
                    variants,
                    item["evidence_url"],
                    item["evidence_label"],
                    item["review_reason"],
                )
            )
            category_ids.add(category_id)
            identities.add(identity)
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
            applies_to = tuple(item.get("category_ids", []))
            slot = item.get("paper_slot", "")
            if role not in {"question", "answer", "question_answer", "source_artifact"}:
                raise ValueError("Unsupported Taiwan Water native file role")
            if role != "source_artifact" and (
                not name.lower().endswith(".pdf")
                or not item["subject_label"].strip()
                or not item["native_category_header"].strip()
            ):
                raise ValueError("Taiwan Water native paper lacks reviewed PDF headers")
            if (
                len(set(applies_to)) != len(applies_to)
                or not set(applies_to) <= category_ids
                or (role == "source_artifact" and (applies_to or slot))
                or (role != "source_artifact" and categories and not applies_to)
                or slot not in {"", "common", "professional-1", "professional-2"}
                or bool(applies_to) != bool(slot)
            ):
                raise ValueError("Invalid Taiwan Water paper/category applicability")
            members.append(
                NativeMember(
                    name,
                    item["checksum"],
                    role,
                    item["subject_label"],
                    item["native_category_header"],
                    item["revision_notice"],
                    applies_to,
                    slot,
                )
            )
        if bool(members) == bool(row["review_reason"]):
            raise ValueError("A native archive needs reviewed members or a withholding reason")
        for category in categories:
            slots = {
                member.paper_slot
                for member in members
                if category.category_id in member.category_ids
            }
            if not slots or (
                not category.review_reason
                and slots != {"common", "professional-1", "professional-2"}
            ):
                raise ValueError("Taiwan Water category lacks its reviewed written-paper coverage")
        archives.append(
            NativeArchive(
                year,
                row["source_url"],
                row["checksum"],
                row["review_reason"],
                tuple(members),
                tuple(categories),
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


def native_category(
    archive: NativeArchive, member: NativeMember, category_code: str
) -> NativeCategory | None:
    return next(
        (
            category
            for category in archive.categories
            if category.category_id in member.category_ids
            and category.category_code(archive.year_roc) == category_code
        ),
        None,
    )


def native_material(
    archive: NativeArchive, member: NativeMember, category_code: str
) -> SourceMaterial | None:
    from app.providers.twc_recruit.identity import resolve_archive_identity

    parent = resolve_archive_identity(
        archive.year_roc, archive.checksum, source_url=archive.source_url
    )
    category = native_category(archive, member, category_code)
    if category is not None and category.review_reason:
        return SourceMaterial(
            "administered",
            SourceDate("unknown", None),
            category.evidence_url,
            category.evidence_label,
            category.review_reason,
        )
    if parent is not None and (category is not None or category_code == str(archive.year_roc)):
        return parent.material
    return None


def resolve_native_member(
    year_roc: int,
    subject_code: str,
    checksum: str,
    *,
    source_url: str | None = None,
    source_material: SourceMaterial | None = None,
    category_code: str | None = None,
) -> tuple[NativeArchive, NativeMember] | None:
    for archive in native_archives():
        if archive.year_roc != year_roc:
            continue
        for member in archive.members:
            code = category_code if category_code is not None else str(year_roc)
            if code != str(year_roc) and native_category(archive, member, code) is None:
                continue
            if (
                source_material is not None
                and native_material(archive, member, code) != source_material
            ):
                continue
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
            occupational_projection = any(
                page.year_roc == fact.year_roc
                and any(
                    paper.category_code != str(fact.year_roc)
                    and any(
                        url.startswith(f"{fact.source_url}#archive-sha256={fact.checksum}&")
                        for url in paper.files.values()
                    )
                    for paper in page.papers
                )
                for page in pages
            )
            if occupational_projection and not fact.categories:
                raise ValueError("Taiwan Water projection lacks reviewed category evidence")
            for member in fact.members:
                if member.file_role == "source_artifact":
                    continue
                codes = (
                    [
                        category.category_code(fact.year_roc)
                        for category in fact.categories
                        if category.category_id in member.category_ids
                    ]
                    if occupational_projection
                    else [str(fact.year_roc)]
                )
                # A migrated current projection must conserve every approved
                # relationship. Its retired generic reference cannot conceal
                # a missing occupation in current state.
                candidates = (
                    [record for _, record in retained] if occupational_projection else records
                )
                for code in codes:
                    anchors = [
                        record
                        for record in candidates
                        if record.get("year_roc") == fact.year_roc
                        and record.get("source_exam_id") == f"twc-recruit-{fact.year_roc}"
                        and record.get("category_code") == code
                        and record.get("subject_code") == member.subject_code
                        and record.get("download_url_source") == member_locator(fact, member)
                        and record.get("checksum") == member.checksum
                        and record.get("file_type") == member.file_role
                    ]
                    if not anchors:
                        raise ValueError(
                            "Taiwan Water native evidence lacks paper/category anchor: "
                            f"{member.name} ({code})"
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
