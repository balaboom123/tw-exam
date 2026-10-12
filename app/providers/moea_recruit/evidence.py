"""Exact native evidence for conflicting recruitment archive labels.

These acquisition exceptions preserve source labels and do not approve an
occupation, examination identity or publication. Changed bytes fail closed.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from html import unescape
from pathlib import Path
from urllib.parse import urlsplit


def normalized_label(value: str) -> str:
    return " ".join(unescape(value).split())


@dataclass(frozen=True)
class NativeFile:
    source_url: str
    source_label: str
    checksum: str
    native_role: str
    native_subject: str
    inspected_pages: tuple[int, ...]

    @property
    def subject_code(self) -> str:
        return "joint-" + hashlib.sha256(self.source_url.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class LabelConflict:
    year_roc: int
    listing_group: str
    conflict_label: str
    native_files: tuple[NativeFile, ...]
    reviewed_at: str
    reason: str

    @property
    def conflicting_urls(self) -> frozenset[str]:
        return frozenset(
            file.source_url
            for file in self.native_files
            if normalized_label(file.source_label) == normalized_label(self.conflict_label)
        )


def source_conflicts_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/moea/source-label-conflicts-v1.json"


def read_source_conflicts(path: Path) -> tuple[LabelConflict, ...]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        type(document.get("schema_version")) is not int
        or document["schema_version"] != 1
        or document.get("provider_id") != "moea_recruit"
        or not isinstance(document.get("facts"), list)
        or not document["facts"]
    ):
        raise ValueError("Unsupported MOEA source-label conflict evidence")
    facts = []
    contexts = set()
    for row in document["facts"]:
        native_files = tuple(
            NativeFile(**{**file, "inspected_pages": tuple(file["inspected_pages"])})
            for file in row["native_files"]
        )
        fact = LabelConflict(**{**row, "native_files": native_files})
        if type(fact.year_roc) is not int or not 1 <= fact.year_roc <= 999:
            raise ValueError("Invalid MOEA source-label conflict year")
        for text in (fact.listing_group, fact.conflict_label, fact.reviewed_at, fact.reason):
            if not isinstance(text, str) or not text.strip():
                raise ValueError("MOEA source-label evidence requires nonempty text")
        date.fromisoformat(fact.reviewed_at)
        urls = set()
        for file in native_files:
            url = urlsplit(file.source_url)
            if (
                url.scheme != "https"
                or url.netloc != "www.taipower.com.tw"
                or not url.path.startswith("/media/")
                or not url.path.lower().endswith(".pdf")
                or url.fragment
                or file.source_url in urls
                or re.fullmatch(r"[0-9a-f]{64}", file.checksum) is None
                or file.native_role
                not in {"question", "answer", "question_answer", "corrected_answer"}
                or not file.source_label.strip()
                or not file.native_subject.strip()
                or not file.inspected_pages
                or file.inspected_pages != tuple(range(1, len(file.inspected_pages) + 1))
                or any(type(page) is not int for page in file.inspected_pages)
            ):
                raise ValueError("Invalid MOEA native file evidence")
            urls.add(file.source_url)
        if len(fact.conflicting_urls) < 2:
            raise ValueError("MOEA conflict evidence requires distinct URLs for one label")
        context = (
            fact.year_roc,
            normalized_label(fact.listing_group),
            normalized_label(fact.conflict_label),
        )
        if context in contexts:
            raise ValueError("Duplicate MOEA source-label conflict evidence")
        contexts.add(context)
        facts.append(fact)
    return tuple(facts)


@lru_cache(maxsize=1)
def source_conflicts() -> tuple[LabelConflict, ...]:
    return read_source_conflicts(source_conflicts_path(Path(__file__).resolve().parents[3]))


def reviewed_conflict(
    year_roc: int, listing_group: str, label: str, urls: set[str]
) -> LabelConflict | None:
    return next(
        (
            fact
            for fact in source_conflicts()
            if fact.year_roc == year_roc
            and normalized_label(fact.listing_group) == normalized_label(listing_group)
            and normalized_label(fact.conflict_label) == normalized_label(label)
            and fact.conflicting_urls == urls
        ),
        None,
    )


def native_file_role(
    file_type: str,
    *,
    source_exam_id: str,
    year_roc: int,
    category_code: str,
    category_raw: str,
    subject_code: str,
    title: str,
    source_url: str,
    checksum: str,
) -> str:
    for fact in source_conflicts():
        if (
            source_exam_id != f"moea-recruit-{fact.year_roc}"
            or year_roc != fact.year_roc
            or category_code != str(fact.year_roc)
            or normalized_label(category_raw) != normalized_label(fact.listing_group)
        ):
            continue
        for file in fact.native_files:
            if (
                subject_code == file.subject_code
                and normalized_label(title) == normalized_label(file.source_label)
                and source_url == file.source_url
            ):
                if checksum != file.checksum:
                    raise ValueError(
                        "MOEA source-label conflict has changed or unreviewed native bytes"
                    )
                if file_type not in {"question", file.native_role}:
                    raise ValueError("MOEA source-label conflict has an unreviewed file role")
                return file.native_role
    return file_type
