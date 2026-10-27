"""Reviewed corrections to native file roles, independent of archive filenames."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AnswerCorrection:
    source_exam_id: str
    year_roc: int
    category_code: str
    subject_code: str
    title: str
    source_url: str
    checksum: str
    native_page: int
    reviewed_at: str
    reason: str

    @property
    def source_key(self) -> tuple[str | int, ...]:
        return (
            self.source_exam_id,
            self.year_roc,
            self.category_code,
            self.subject_code,
            self.title,
            self.source_url,
            self.checksum,
        )


def read_corrections(
    path: Path, *, provider_id: str, validate_source: Callable[[AnswerCorrection], None]
) -> tuple[AnswerCorrection, ...]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(document, dict)
        or type(document.get("schema_version")) is not int
        or document["schema_version"] != 1
        or document.get("provider_id") != provider_id
        or not isinstance(document.get("facts"), list)
        or not document["facts"]
    ):
        raise ValueError("Unsupported native answer correction evidence")
    facts = []
    keys: set[tuple[str | int, ...]] = set()
    for row in document["facts"]:
        fact = AnswerCorrection(**row)
        for value in (
            fact.source_exam_id,
            fact.category_code,
            fact.subject_code,
            fact.title,
            fact.source_url,
            fact.checksum,
            fact.reviewed_at,
            fact.reason,
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Correction evidence requires nonempty text")
        date.fromisoformat(fact.reviewed_at)
        if (
            type(fact.year_roc) is not int
            or not 1 <= fact.year_roc <= 999
            or re.fullmatch(r"[0-9a-f]{64}", fact.checksum) is None
            or type(fact.native_page) is not int
            or fact.native_page < 1
        ):
            raise ValueError("Invalid correction source key, checksum or native page")
        validate_source(fact)
        # One review per source slot; conflicting digests need a new review.
        slot = fact.source_key[:-1]
        if slot in keys:
            raise ValueError("Duplicate native correction source slot")
        keys.add(slot)
        facts.append(fact)
    return tuple(facts)


def file_role(
    file_type: str,
    *,
    facts: Iterable[AnswerCorrection],
    source_exam_id: str,
    year_roc: int,
    category_code: str,
    subject_code: str,
    title: str,
    source_url: str,
    checksum: str,
    source_role: str = "answer",
    reviewed_role: str = "corrected_answer",
    require_reviewed_role: bool = True,
) -> str:
    if file_type not in {source_role, reviewed_role}:
        return file_type
    key = (source_exam_id, year_roc, category_code, subject_code, title, source_url, checksum)
    for fact in facts:
        if fact.source_key[:-1] == key[:-1]:
            if checksum != fact.checksum:
                raise ValueError("Native file-role review has changed or unreviewed bytes")
            return reviewed_role
    if require_reviewed_role and file_type == reviewed_role:
        raise ValueError("Reviewed file role has unreviewed source evidence")
    return file_type


def validate_corrections(
    repo_root: Path,
    *,
    provider_id: str,
    facts: tuple[AnswerCorrection, ...],
    verify_mirror: bool = False,
    allowed_roles: frozenset[str] = frozenset({"answer", "corrected_answer"}),
) -> int:
    """Require each review to retain its original current or historical payload."""
    from app.bundler import resolve_mirror_storage_path
    from app.paths import provider_paths
    from app.source_revisions import load_source_revisions
    from app.state import load_provider_state

    provider = provider_paths(repo_root, provider_id)
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
            if paper["file_type"] in allowed_roles
            and (
                paper["source_exam_id"],
                paper["year_roc"],
                paper["category_code"],
                paper["subject_code"],
                paper["subject_name_raw"],
                paper["download_url_source"],
                paper["checksum"],
            )
            == fact.source_key
        ]
        if not matches:
            raise ValueError(f"{fact.source_exam_id}/{fact.subject_code}: native anchor missing")
        if verify_mirror:
            path = resolve_mirror_storage_path(
                repo_root / "mirror", matches[0], provider.provider_id
            )
            if path is None:
                raise ValueError("Native answer correction mirror is missing")
            with path.open("rb") as stream:
                checksum = hashlib.file_digest(stream, "sha256").hexdigest()
            if checksum != fact.checksum:
                raise ValueError("Native answer correction mirror checksum differs")
    return len(facts)
