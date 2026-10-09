"""Reviewed native answer corrections, independent of archive listing labels."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True)
class AnswerCorrection:
    source_exam_id: str
    category_code: str
    subject_code: str
    title: str
    source_url: str
    checksum: str
    native_page: int
    reviewed_at: str
    reason: str

    @property
    def source_key(self) -> tuple[str, ...]:
        return (
            self.source_exam_id,
            self.category_code,
            self.subject_code,
            self.title,
            self.source_url,
            self.checksum,
        )


def taipower_corrections_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/taipower/answer-corrections-v1.json"


def read_taipower_corrections(path: Path) -> tuple[AnswerCorrection, ...]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(document, dict)
        or type(document.get("schema_version")) is not int
        or document["schema_version"] != 1
        or document.get("provider_id") != "taipower_recruit"
        or not isinstance(document.get("facts"), list)
        or not document["facts"]
    ):
        raise ValueError("Unsupported Taipower answer correction evidence")
    facts = []
    keys: set[tuple[str, ...]] = set()
    for row in document["facts"]:
        fact = AnswerCorrection(**row)
        for field in fact.source_key + (fact.reviewed_at, fact.reason):
            if not isinstance(field, str) or not field.strip():
                raise ValueError("Taipower correction evidence requires nonempty text")
        date.fromisoformat(fact.reviewed_at)
        if (
            re.fullmatch(r"taipower-recruit-\d{2,3}(?:-\d{1,2})?", fact.source_exam_id) is None
            or fact.category_code != fact.source_exam_id.split("-")[2]
            or re.fullmatch(r"hiring-\d{2,}", fact.subject_code) is None
            or re.fullmatch(r"[0-9a-f]{64}", fact.checksum) is None
            or type(fact.native_page) is not int
            or fact.native_page < 1
        ):
            raise ValueError("Invalid Taipower correction source key, checksum or native page")
        url = urlsplit(fact.source_url)
        if (
            url.scheme != "https"
            or url.netloc != "www.taipower.com.tw"
            or not url.path.startswith("/media/")
            or not url.path.lower().endswith(".pdf")
        ):
            raise ValueError("Taipower corrections require an official native PDF")
        if fact.source_key in keys:
            raise ValueError("Duplicate Taipower answer correction evidence")
        keys.add(fact.source_key)
        facts.append(fact)
    return tuple(facts)


@lru_cache(maxsize=1)
def taipower_corrections() -> tuple[AnswerCorrection, ...]:
    return read_taipower_corrections(taipower_corrections_path(Path(__file__).resolve().parents[1]))


def taipower_file_role(
    file_type: str,
    *,
    source_exam_id: str,
    year_roc: int,
    category_code: str,
    subject_code: str,
    title: str,
    source_url: str,
    checksum: str,
) -> str:
    if file_type not in {"answer", "corrected_answer"}:
        return file_type
    key = (source_exam_id, category_code, subject_code, title, source_url, checksum)
    for fact in taipower_corrections():
        if fact.source_key[:-1] == key[:-1] and year_roc == int(fact.category_code):
            if checksum != fact.checksum:
                raise ValueError(
                    "Taipower correction source has changed or unreviewed native bytes"
                )
            return "corrected_answer"
    if file_type == "corrected_answer":
        raise ValueError("Taipower corrected answer has changed or unreviewed native evidence")
    return file_type


def validate_taipower_corrections(repo_root: Path, *, verify_mirror: bool = False) -> int:
    """Reconcile every reviewed original with current or immutable source history."""
    from app.bundler import resolve_mirror_storage_path
    from app.paths import provider_paths
    from app.source_revisions import load_source_revisions
    from app.state import load_provider_state

    facts = read_taipower_corrections(taipower_corrections_path(repo_root))
    provider = provider_paths(repo_root, "taipower_recruit")
    _, catalog, _ = load_provider_state(provider)
    records: list[tuple[dict[str, Any], str]] = [
        (vars(paper), paper.storage_key) for paper in catalog.papers
    ]
    records.extend(
        (entry["source_record"], entry["blob_storage_key"])
        for entry in load_source_revisions(provider)
        if entry["record_type"] == "paper"
    )
    for fact in facts:
        matches = [
            key
            for paper, key in records
            if paper["file_type"] in {"answer", "corrected_answer"}
            and paper["year_roc"] == int(fact.category_code)
            and (
                paper["source_exam_id"],
                paper["category_code"],
                paper["subject_code"],
                paper["subject_name_raw"],
                paper["download_url_source"],
                paper["checksum"],
            )
            == fact.source_key
        ]
        if not matches:
            raise ValueError(f"{fact.source_exam_id}/{fact.subject_code}: native anchor is missing")
        if verify_mirror:
            path = resolve_mirror_storage_path(
                repo_root / "mirror", matches[0], provider.provider_id
            )
            if path is None:
                raise ValueError("Taipower correction native mirror is missing")
            with path.open("rb") as stream:
                checksum = hashlib.file_digest(stream, "sha256").hexdigest()
            if checksum != fact.checksum:
                raise ValueError("Taipower correction native mirror checksum differs")
    return len(facts)
