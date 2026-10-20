"""Exact native alternatives for confirmed individual-source label conflicts."""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit, urlunsplit

from app.archive_paths import portable_archive_name


@dataclass(frozen=True)
class ArchiveFallback:
    year_roc: int
    selector_value: str
    subject: str
    file_role: str
    conflicting_checksum: str
    conflicting_year_roc: int
    archive_checksum: str
    member_path: str
    member_checksum: str
    evidence_url: str
    evidence_label: str
    reviewed_at: str
    inspected_pages: tuple[int, ...]
    reason: str


def fallback_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/teacher_qual/archive-fallbacks-v1.json"


def read_fallbacks(path: Path) -> tuple[ArchiveFallback, ...]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        type(document.get("schema_version")) is not int
        or document.get("schema_version") != 1
        or document.get("provider_id") != "teacher_qual"
        or not isinstance(document.get("fallbacks"), list)
        or not document["fallbacks"]
    ):
        raise ValueError("Unsupported teacher qualification fallback evidence")
    facts = []
    contexts = set()
    for row in document["fallbacks"]:
        fact = ArchiveFallback(**{**row, "inspected_pages": tuple(row["inspected_pages"])})
        context = (fact.year_roc, fact.selector_value, fact.subject, fact.file_role)
        if (
            type(fact.year_roc) is not int
            or not 1 <= fact.year_roc <= 999
            or type(fact.conflicting_year_roc) is not int
            or not 1 <= fact.conflicting_year_roc <= 999
            or not re.fullmatch(r"\d+", fact.selector_value)
            or fact.file_role not in {"question", "answer", "question_answer"}
            or not fact.subject.strip()
            or context in contexts
            or not all(
                re.fullmatch(r"[0-9a-f]{64}", value)
                for value in (
                    fact.conflicting_checksum,
                    fact.archive_checksum,
                    fact.member_checksum,
                )
            )
            or fact.conflicting_checksum == fact.member_checksum
            or not portable_archive_name(fact.member_path)
            or not fact.member_path.lower().endswith(".pdf")
            or not fact.inspected_pages
            or any(type(page) is not int or page < 1 for page in fact.inspected_pages)
            or len(set(fact.inspected_pages)) != len(fact.inspected_pages)
            or fact.evidence_url != "https://tqa.rcpet.edu.tw/TEA_Exam/TEA03.aspx"
            or not fact.evidence_label.strip()
            or not fact.reason.strip()
        ):
            raise ValueError("Invalid teacher qualification native fallback evidence")
        date.fromisoformat(fact.reviewed_at)
        contexts.add(context)
        facts.append(fact)
    return tuple(facts)


@lru_cache(maxsize=1)
def native_fallbacks() -> tuple[ArchiveFallback, ...]:
    return read_fallbacks(fallback_path(Path(__file__).resolve().parents[3]))


def fallback_for(year: int, selector: str, subject: str, role: str) -> ArchiveFallback | None:
    return next(
        (
            fact
            for fact in native_fallbacks()
            if (fact.year_roc, fact.selector_value, fact.subject, fact.file_role)
            == (year, selector, subject, role)
        ),
        None,
    )


def member_locator(source_url: str, fact: ArchiveFallback) -> str:
    return (
        f"{source_url}#archive-sha256={fact.archive_checksum}"
        f"&zip-entry={quote(fact.member_path, safe='')}"
    )


def parse_member_locator(url: str) -> tuple[str, ArchiveFallback]:
    parts = urlsplit(url)
    values = parse_qs(parts.fragment, keep_blank_values=True, strict_parsing=True)
    if (
        parts.scheme != "https"
        or parts.netloc != "tqa.rcpet.edu.tw"
        or parts.path != "/TEA_Exam/ShowPicOut2.aspx"
        or not parse_qs(parts.query).get("ASParam")
        or set(values) != {"archive-sha256", "zip-entry"}
        or any(len(items) != 1 for items in values.values())
    ):
        raise ValueError("Invalid teacher qualification native member locator")
    matches = [
        fact
        for fact in native_fallbacks()
        if (fact.archive_checksum, fact.member_path)
        == (values["archive-sha256"][0], values["zip-entry"][0])
    ]
    if len(matches) != 1:
        raise ValueError("Unreviewed teacher qualification native member")
    return urlunsplit(parts._replace(fragment="")), matches[0]


def extract_member(data: bytes, fact: ArchiveFallback) -> bytes:
    if hashlib.sha256(data).hexdigest() != fact.archive_checksum:
        raise ValueError("Teacher qualification original archive changed; review is required")
    # This exact reviewed archive uses CP950 names. Python still respects the
    # UTF-8 flag when present; no guessed renaming or filesystem extraction occurs.
    with zipfile.ZipFile(io.BytesIO(data), metadata_encoding="cp950") as archive:
        matches = [info for info in archive.infolist() if info.filename == fact.member_path]
        if len(matches) != 1:
            raise ValueError("Teacher qualification native member is missing or duplicated")
        payload = archive.read(matches[0])
    if (
        not payload.startswith(b"%PDF")
        or hashlib.sha256(payload).hexdigest() != fact.member_checksum
    ):
        raise ValueError("Teacher qualification native paper checksum mismatch")
    return payload


def validate_archive_fallbacks(repo_root: Path, *, verify_mirror: bool = False) -> int:
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
    facts = read_fallbacks(fallback_path(repo_root))
    for fact in facts:
        anchors = [
            record
            for record in [*current, *historical]
            if record.get("year_roc") == fact.year_roc
            and record.get("source_exam_id") == f"teacher-qual-{fact.year_roc}"
            and record.get("checksum") == fact.archive_checksum
            and (
                record.get("file_type") == "source_archive"
                or record.get("subject_code") == "all-categories"
            )
        ]
        if not anchors:
            raise ValueError("Teacher qualification fallback lacks its original source anchor")
        if verify_mirror:
            for record in anchors:
                key = record.get("storage_key")
                if not isinstance(key, str):
                    raise ValueError("Teacher qualification fallback original is not recoverable")
                extract_member((repo_root / "mirror" / key).read_bytes(), fact)
    return len(facts)
