"""Reviewed original mathematics booklets containing several distinct forms."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class BookletMember:
    form: str
    first_page: int
    last_page: int

    @property
    def subject_code(self) -> str:
        return f"math-{self.form.lower()}"

    @property
    def title(self) -> str:
        return f"共同科目 數學({self.form})"


@dataclass(frozen=True)
class NativeBooklet:
    year_roc: int
    source_exam_id: str
    exam_name_raw: str
    category_code: str
    category_raw: str
    source_url: str
    checksum: str
    page_count: int
    inspected_pages: tuple[int, ...]
    common_pages: tuple[int, ...]
    members: tuple[BookletMember, ...]
    reviewed_at: str
    reason: str

    @property
    def publication_code(self) -> str:
        return "math-" + "".join(m.form.lower() for m in self.members)

    @property
    def publication_title(self) -> str:
        return "共同科目 數學(" + "、".join(m.form for m in self.members) + ")合訂本"

    def source_key(self, member: BookletMember) -> tuple[str | int, ...]:
        return (
            self.year_roc,
            self.source_exam_id,
            self.exam_name_raw,
            self.category_code,
            self.category_raw,
            member.subject_code,
            member.title,
            self.source_url,
            self.checksum,
        )


def shared_booklets_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/tcte/shared-booklets-v1.json"


def read_shared_booklets(path: Path) -> tuple[NativeBooklet, ...]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        set(document) != {"schema_version", "provider_id", "facts"}
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 1
        or document["provider_id"] != "tcte_tve"
        or not document["facts"]
    ):
        raise ValueError("Unsupported TCTE shared booklet evidence")
    facts = []
    keys = set()
    for row in document["facts"]:
        fields = dict(row)
        fields["members"] = tuple(BookletMember(**m) for m in fields["members"])
        for name in ("inspected_pages", "common_pages"):
            fields[name] = tuple(fields[name])
        fact = NativeBooklet(**fields)
        year = fact.year_roc
        forms = [m.form for m in fact.members]
        key = year, fact.source_url, fact.checksum
        if (
            type(year) is not int
            or not 1 <= year <= 999
            or fact.source_exam_id != f"tcte-tve-{year}"
            or fact.category_code != "common"
            or fact.category_raw != "四技二專統一入學測驗"
            or fact.exam_name_raw != f"{year}學年度{fact.category_raw}"
            or fact.source_url != f"https://web1.tcte.edu.tw/EXAM/{year:03}_4y/{year}-4y-00-m.pdf"
            or re.fullmatch(r"[0-9a-f]{64}", fact.checksum) is None
            or key in keys
            or type(fact.page_count) is not int
            or fact.page_count < 3
            or any(type(p) is not int for p in fact.inspected_pages)
            or fact.inspected_pages != tuple(range(1, fact.page_count + 1))
            or not fact.common_pages
            or any(type(p) is not int or not 2 <= p <= fact.page_count for p in fact.common_pages)
            or list(fact.common_pages) != sorted(set(fact.common_pages))
            or len(forms) < 2
            or forms != sorted(set(forms))
            or any(re.fullmatch(r"[A-Z]", form) is None for form in forms)
            or not isinstance(fact.reason, str)
            or not fact.reason.strip()
        ):
            raise ValueError("Invalid TCTE native booklet context")
        covered = set(fact.common_pages)
        for member in fact.members:
            if (
                type(member.first_page) is not int
                or type(member.last_page) is not int
                or not 2 <= member.first_page <= member.last_page <= fact.page_count
            ):
                raise ValueError("Invalid TCTE native form page range")
            pages = set(range(member.first_page, member.last_page + 1))
            if covered & pages:
                raise ValueError("TCTE native form page ranges overlap")
            covered.update(pages)
        if covered != set(range(2, fact.page_count + 1)):
            raise ValueError("TCTE booklet structure has unreviewed pages")
        date.fromisoformat(fact.reviewed_at)
        keys.add(key)
        facts.append(fact)
    return tuple(facts)


@lru_cache(maxsize=1)
def shared_booklets() -> tuple[NativeBooklet, ...]:
    return read_shared_booklets(shared_booklets_path(Path(__file__).resolve().parents[3]))


def resolve_booklet(
    *,
    year_roc: int,
    source_exam_id: str,
    exam_name_raw: str,
    category_code: str,
    category_raw: str,
    subject_code: str,
    title: str,
    source_url: str,
    checksum: str,
) -> NativeBooklet | None:
    if category_code != "common" or not checksum:
        return None
    key = (
        year_roc,
        source_exam_id,
        exam_name_raw,
        category_code,
        category_raw,
        subject_code,
        title,
        source_url,
        checksum,
    )
    return next(
        (
            fact
            for fact in shared_booklets()
            if any(fact.source_key(m) == key for m in fact.members)
        ),
        None,
    )


def validate_shared_booklets(repo_root: Path, *, verify_mirror: bool = False) -> int:
    """Keep every approved original anchored to complete current or historical references."""
    from app.bundler import resolve_mirror_storage_path
    from app.paths import provider_paths
    from app.source_revisions import load_source_revisions, payload_checksum
    from app.state import load_provider_state

    provider = provider_paths(repo_root, "tcte_tve")
    _, catalog, _ = load_provider_state(provider)
    records = [(vars(p), p.storage_key) for p in catalog.papers]
    records.extend(
        (r["source_record"], r["blob_storage_key"])
        for r in load_source_revisions(provider)
        if r["record_type"] == "paper"
    )
    facts = read_shared_booklets(shared_booklets_path(repo_root))
    for fact in facts:
        for member in fact.members:
            matches = [
                storage_key
                for p, storage_key in records
                if p["file_type"] == "question"
                and p.get("source_material") is None
                and (
                    p["year_roc"],
                    p["source_exam_id"],
                    p["exam_name_raw"],
                    p["category_code"],
                    p["category_raw"],
                    p["subject_code"],
                    p["subject_name_raw"],
                    p["download_url_source"],
                    p["checksum"],
                )
                == fact.source_key(member)
            ]
            if not matches:
                raise ValueError("TCTE shared booklet lacks a retained native form anchor")
            if verify_mirror:
                for key in set(matches):
                    path = resolve_mirror_storage_path(repo_root / "mirror", key, "tcte_tve")
                    if path is None or payload_checksum(path) != fact.checksum:
                        raise ValueError("TCTE native booklet mirror checksum differs")
    return len(facts)
