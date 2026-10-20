"""Reviewed native members of the teacher qualification combined archives.

Individual pages omit formats and sometimes whole subjects. The original
containers remain source records; this evidence gives their paper members
explicit categories and roles without guessing from filenames at runtime.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit, urlunsplit

from app.archive_paths import portable_archive_name


@dataclass(frozen=True)
class MemberContext:
    selector_value: str
    category_label: str
    subject: str
    file_role: str


@dataclass(frozen=True)
class NativeMember:
    path_chain: tuple[str, ...]
    checksum: str
    contexts: tuple[MemberContext, ...]
    version_label: str
    review_basis: str

    @property
    def subject_code(self) -> str:
        value = json.dumps(self.path_chain, ensure_ascii=False)
        return "archive-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]

    def subject_label(self, context: MemberContext) -> str:
        return context.subject + (f"（{self.version_label}）" if self.version_label else "")


@dataclass(frozen=True)
class NativeArchive:
    source_exam_id: str
    checksum: str
    members: tuple[NativeMember, ...]


def native_archive_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/teacher_qual/archive-papers-v1.json"


def read_native_archives(path: Path) -> tuple[NativeArchive, ...]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        type(document.get("schema_version")) is not int
        or document["schema_version"] != 1
        or document.get("provider_id") != "teacher_qual"
        or document.get("evidence_url") != "https://tqa.rcpet.edu.tw/TEA_Exam/TEA03.aspx"
        or not isinstance(document.get("archives"), list)
        or not document["archives"]
    ):
        raise ValueError("Unsupported teacher qualification native archive evidence")
    date.fromisoformat(document["reviewed_at"])
    archives = []
    events = set()
    for row in document["archives"]:
        event = row["source_exam_id"]
        if (
            not re.fullmatch(r"teacher-qual-\d{2,3}(?:-[12])?", event)
            or event in events
            or not re.fullmatch(r"[0-9a-f]{64}", row["checksum"])
            or not row["members"]
        ):
            raise ValueError("Invalid or duplicate teacher qualification archive context")
        events.add(event)
        members = []
        paths = set()
        for item in row["members"]:
            chain = tuple(item["path_chain"])
            folded = tuple(unicodedata.normalize("NFC", name).casefold() for name in chain)
            contexts = tuple(MemberContext(**value) for value in item["contexts"])
            if (
                not chain
                or len(chain) > 4
                or any(not portable_archive_name(name) for name in chain)
                or any(not name.lower().endswith(".zip") for name in chain[:-1])
                or folded in paths
                or not re.fullmatch(r"[0-9a-f]{64}", item["checksum"])
                or len(set(contexts)) != len(contexts)
                or any(
                    context.selector_value not in {"10", "21", "22", "30", "40"}
                    or not context.category_label.strip()
                    or not context.subject.strip()
                    or context.file_role not in {"question", "answer", "question_answer"}
                    for context in contexts
                )
                or item["review_basis"]
                not in {
                    "exact_current_SHA256",
                    "same_complete_extracted_text_distinct_bytes",
                    "reviewed_native_header_missing_individual_subject",
                    "reviewed_native_shared_special_education_answer_before_change",
                    "paired_reviewed_native_PDF_and_Word_headers",
                    "excluded_operating_system_metadata",
                }
                or item["version_label"] not in {"", "來源整包版本", "來源整包，檔名：更改前"}
                or (
                    bool(contexts) != (item["review_basis"] != "excluded_operating_system_metadata")
                )
                or (contexts and Path(chain[-1]).suffix.lower() not in {".pdf", ".doc", ".docx"})
                or (not contexts and (Path(chain[-1]).name != ".DS_Store" or item["version_label"]))
            ):
                raise ValueError("Invalid teacher qualification native member evidence")
            paths.add(folded)
            members.append(
                NativeMember(
                    chain, item["checksum"], contexts, item["version_label"], item["review_basis"]
                )
            )
        archives.append(NativeArchive(event, row["checksum"], tuple(members)))
    return tuple(archives)


@lru_cache(maxsize=1)
def native_archives() -> tuple[NativeArchive, ...]:
    return read_native_archives(native_archive_path(Path(__file__).resolve().parents[3]))


def native_archive_for(source_exam_id: str) -> NativeArchive | None:
    return next(
        (archive for archive in native_archives() if archive.source_exam_id == source_exam_id), None
    )


def member_locator(source_url: str, archive: NativeArchive, member: NativeMember) -> str:
    chain = json.dumps(member.path_chain, ensure_ascii=False, separators=(",", ":"))
    return f"{source_url}#archive-sha256={archive.checksum}&member-chain={quote(chain, safe='')}"


def parse_member_locator(url: str) -> tuple[str, NativeArchive, NativeMember]:
    parts = urlsplit(url)
    values = parse_qs(parts.fragment, keep_blank_values=True, strict_parsing=True)
    if (
        parts.scheme != "https"
        or parts.netloc != "tqa.rcpet.edu.tw"
        or parts.path != "/TEA_Exam/ShowPicOut2.aspx"
        or not parse_qs(parts.query).get("ASParam")
        or set(values) != {"archive-sha256", "member-chain"}
        or any(len(items) != 1 for items in values.values())
    ):
        raise ValueError("Invalid teacher qualification archive member locator")
    chain = json.loads(values["member-chain"][0])
    matches = [
        (archive, member)
        for archive in native_archives()
        if archive.checksum == values["archive-sha256"][0]
        for member in archive.members
        if list(member.path_chain) == chain and member.contexts
    ]
    if len(matches) != 1:
        raise ValueError("Unreviewed teacher qualification archive member")
    archive, member = matches[0]
    return urlunsplit(parts._replace(fragment="")), archive, member


def native_member_subject(
    *,
    source_exam_id: str,
    category_code: str,
    subject_code: str,
    source_url: str,
    checksum: str,
    file_role: str,
    title: str,
) -> str | None:
    """Separate a reviewed file's version caption from its actual subject."""
    if "member-chain" not in parse_qs(urlsplit(source_url).fragment):
        return None
    try:
        _, archive, member = parse_member_locator(source_url)
    except ValueError:
        return None
    if (
        source_exam_id != archive.source_exam_id
        or subject_code != member.subject_code
        or checksum != member.checksum
    ):
        return None
    year = source_exam_id.split("-")[2]
    for context in member.contexts:
        if (
            category_code == f"{year}-{context.selector_value}"
            and file_role == context.file_role
            and title == member.subject_label(context)
        ):
            return context.subject
    return None


def extract_native_members(archive: NativeArchive, data: bytes) -> dict[tuple[str, ...], bytes]:
    if hashlib.sha256(data).hexdigest() != archive.checksum:
        raise ValueError(
            "Teacher qualification combined archive changed; native members need review"
        )
    expected = {member.path_chain: member for member in archive.members}
    payloads = {}

    def visit(body: bytes, prefix: tuple[str, ...] = ()) -> None:
        with zipfile.ZipFile(io.BytesIO(body)) as source:
            names = set()
            for info in source.infolist():
                name = info.orig_filename
                if not info.flag_bits & 0x800:
                    raw = name.encode("cp437")
                    # The reviewed older containers use CP950. ROC 112 and
                    # 113 contain UTF-8 names without the ZIP UTF-8 flag.
                    try:
                        name = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        name = raw.decode("cp950")
                if info.is_dir():
                    name = name.rstrip("/")
                folded = unicodedata.normalize("NFC", name).casefold()
                if (
                    not portable_archive_name(name)
                    or "\x00" in info.orig_filename
                    or folded in names
                    or stat.S_ISLNK(info.external_attr >> 16)
                    or info.flag_bits & 1
                ):
                    raise ValueError("Unsafe, duplicate, linked or encrypted teacher native path")
                names.add(folded)
                if info.is_dir():
                    continue
                chain = (*prefix, name)
                # Read every entry to verify CRC before ignoring metadata or
                # traversing a nested container. Never extract to the filesystem.
                payload = source.read(info)
                if name.lower().endswith(".zip"):
                    if len(chain) >= 4 or not any(key[: len(chain)] == chain for key in expected):
                        raise ValueError("Unreviewed teacher qualification nested archive")
                    visit(payload, chain)
                    continue
                member = expected.get(chain)
                if member is None or hashlib.sha256(payload).hexdigest() != member.checksum:
                    raise ValueError("Unreviewed or changed teacher qualification native member")
                suffix = Path(name).suffix.lower()
                if member.contexts and (
                    (suffix == ".pdf" and not payload.startswith(b"%PDF-"))
                    or (
                        suffix == ".doc"
                        and not payload.startswith(bytes.fromhex("d0cf11e0a1b11ae1"))
                    )
                    or (suffix == ".docx" and not zipfile.is_zipfile(io.BytesIO(payload)))
                ):
                    raise ValueError("Teacher qualification native payload format differs")
                payloads[chain] = payload

    visit(data)
    if set(payloads) != set(expected):
        raise ValueError("Teacher qualification combined archive lacks reviewed native members")
    return payloads


def validate_native_archives(repo_root: Path, *, verify_mirror: bool = False) -> int:
    from app.paths import provider_paths
    from app.source_revisions import _source_records, load_source_revisions
    from app.state import load_provider_state

    provider = provider_paths(repo_root, "teacher_qual")
    pages, catalog, _ = load_provider_state(provider)
    current = [record for _, record in _source_records("teacher_qual", pages, catalog.papers)]
    historical = [
        {**entry["source_record"], "storage_key": entry["blob_storage_key"]}
        for entry in load_source_revisions(provider)
    ]
    facts = read_native_archives(native_archive_path(repo_root))
    for archive in facts:
        parents = [
            record
            for record in [*current, *historical]
            if record.get("source_exam_id") == archive.source_exam_id
            and record.get("checksum") == archive.checksum
            and (
                record.get("file_type") == "source_archive"
                or record.get("subject_code") == "all-categories"
            )
        ]
        if not parents:
            raise ValueError("Teacher qualification native archive lacks its original anchor")
        projected = any(
            p.source_exam_id == archive.source_exam_id and p.category_code != str(p.year_roc)
            for p in catalog.papers
        )
        if projected:
            for member in archive.members:
                for context in member.contexts:
                    matches = [
                        paper
                        for paper in catalog.papers
                        if paper.source_exam_id == archive.source_exam_id
                        and paper.category_code == f"{paper.year_roc}-{context.selector_value}"
                        and paper.file_type == context.file_role
                        and paper.checksum == member.checksum
                        and paper.subject_name_raw
                        in {context.subject, member.subject_label(context)}
                    ]
                    if not matches:
                        raise ValueError(
                            "Teacher qualification projection omits a reviewed "
                            "native member/category"
                        )
                    if verify_mirror:
                        for paper in matches:
                            if (
                                hashlib.sha256(
                                    (repo_root / "mirror" / paper.storage_key).read_bytes()
                                ).hexdigest()
                                != member.checksum
                            ):
                                raise ValueError(
                                    "Teacher qualification native mirror checksum mismatch"
                                )
        if verify_mirror:
            for parent in parents:
                extract_native_members(
                    archive, (repo_root / "mirror" / parent["storage_key"]).read_bytes()
                )
    return len(facts)
