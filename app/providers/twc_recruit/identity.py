"""Reviewed outer-archive dates and stages, separate from programme years.

These facts do not establish occupational tracks or approve child publication.
Unreviewed years and changed archive bytes keep an explicit review disposition.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.source_material import SourceDate, SourceMaterial


@dataclass(frozen=True)
class TwcArchiveIdentity:
    year_roc: int
    source_url: str
    checksum: str
    written_test_date: date | None
    evidence_url: str
    evidence_label: str
    review_reason: str

    @property
    def stage_id(self) -> str:
        return "stage-1" if self.written_test_date is not None else "unknown"

    @property
    def material(self) -> SourceMaterial:
        source_date = (
            SourceDate("exam_year", self.written_test_date.year)
            if self.written_test_date is not None
            else SourceDate("unknown", None)
        )
        return SourceMaterial(
            "administered",
            source_date,
            self.evidence_url,
            self.evidence_label,
            self.review_reason,
        )


def archive_identity_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/twc/archive-identity-v1.json"


def read_archive_identities(path: Path) -> tuple[TwcArchiveIdentity, ...]:
    from app.providers.twc_recruit.client import TwcRecruitClient

    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        document.get("schema_version") != 1
        or type(document.get("schema_version")) is not int
        or document.get("provider_id") != "twc_recruit"
    ):
        raise ValueError("Unsupported Taiwan Water archive identity evidence")
    facts = []
    contexts: set[tuple[int, str, str]] = set()
    dates_by_payload: dict[tuple[int, str], date | None] = {}
    for row in document["facts"]:
        year = row["programme_year_roc"]
        if type(year) is not int or not 1 <= year <= 999:
            raise ValueError("Invalid Taiwan Water programme year")
        TwcRecruitClient._validate_download_url(row["source_url"])
        if (
            not isinstance(row["checksum"], str)
            or re.fullmatch(r"[0-9a-f]{64}", row["checksum"]) is None
        ):
            raise ValueError("Taiwan Water archive evidence requires a SHA-256 checksum")
        date.fromisoformat(row["reviewed_at"])
        written = date.fromisoformat(row["written_test_date"]) if row["written_test_date"] else None
        context = (year, row["source_url"], row["checksum"])
        if context in contexts:
            raise ValueError("Duplicate Taiwan Water archive identity context")
        payload = (year, row["checksum"])
        if payload in dates_by_payload and dates_by_payload[payload] != written:
            raise ValueError("Conflicting Taiwan Water archive dates for the same payload")
        fact = TwcArchiveIdentity(
            year,
            row["source_url"],
            row["checksum"],
            written,
            row["evidence_url"],
            row["evidence_label"],
            row["review_reason"],
        )
        _ = fact.material  # Validate explicit unknown/date evidence at the shared owner.
        facts.append(fact)
        contexts.add(context)
        dates_by_payload[payload] = written
    return tuple(facts)


@lru_cache(maxsize=1)
def archive_identities() -> tuple[TwcArchiveIdentity, ...]:
    return read_archive_identities(archive_identity_path(Path(__file__).resolve().parents[3]))


def is_outer_archive_context(
    source_exam_id: str,
    year_roc: int,
    category_code: str,
    subject_code: str,
) -> bool:
    return (
        source_exam_id == f"twc-recruit-{year_roc}"
        and category_code == str(year_roc)
        and subject_code == f"twc-recruit-{year_roc}-bundle"
    )


def resolve_archive_identity(
    year_roc: int, checksum: str, *, source_url: str | None = None
) -> TwcArchiveIdentity | None:
    return next(
        (
            fact
            for fact in archive_identities()
            if fact.year_roc == year_roc
            and fact.checksum == checksum
            and (source_url is None or fact.source_url == source_url)
        ),
        None,
    )


def archive_material(
    *,
    source_exam_id: str,
    year_roc: int,
    category_code: str,
    subject_code: str,
    label: str,
    source_url: str,
    checksum: str,
) -> SourceMaterial | None:
    if (
        source_exam_id == f"twc-recruit-{year_roc}"
        and (category_code == str(year_roc) or category_code.startswith(f"{year_roc}-"))
        and re.fullmatch(r"native-[0-9a-f]{64}", subject_code) is not None
    ):
        from app.providers.twc_recruit.archive import native_material, resolve_native_member

        native = resolve_native_member(
            year_roc, subject_code, checksum, source_url=source_url, category_code=category_code
        )
        if native is not None:
            material = native_material(*native, category_code)
            if material is not None:
                return material
        return SourceMaterial(
            "administered",
            SourceDate("unknown", None),
            source_url,
            label,
            "Taiwan Water native paper lacks reviewed parent and member checksum evidence",
        )
    from app.providers.twc_recruit.client import TwcRecruitClient, archive_file_role

    if not is_outer_archive_context(source_exam_id, year_roc, category_code, subject_code):
        return None
    try:
        TwcRecruitClient._validate_download_url(source_url)
        if archive_file_role(label) != "question_answer":
            return None
    except ValueError:
        return None
    fact = resolve_archive_identity(year_roc, checksum, source_url=source_url)
    if fact is not None:
        return fact.material
    return SourceMaterial(
        "administered",
        SourceDate("unknown", None),
        source_url,
        label,
        "Taiwan Water outer archive lacks reviewed annual date and stage evidence for these bytes",
    )


def validate_archive_identities(repo_root: Path, *, verify_mirror: bool = False) -> int:
    from app.models import to_plain_data
    from app.paths import provider_paths
    from app.source_revisions import load_source_revisions
    from app.state import load_provider_state

    provider = provider_paths(repo_root, "twc_recruit")
    _, current, _ = load_provider_state(provider)
    records: list[tuple[dict[str, Any], str]] = [
        (to_plain_data(paper), paper.storage_key) for paper in current.papers
    ]
    records.extend(
        (entry["source_record"], entry["blob_storage_key"])
        for entry in load_source_revisions(provider)
        if entry["record_type"] == "paper"
    )
    facts = read_archive_identities(archive_identity_path(repo_root))
    for fact in facts:
        matches = [
            (record, storage_key)
            for record, storage_key in records
            if record.get("year_roc") == fact.year_roc
            and record.get("download_url_source") == fact.source_url
            and record.get("checksum") == fact.checksum
            and is_outer_archive_context(
                record["source_exam_id"],
                record["year_roc"],
                record["category_code"],
                record["subject_code"],
            )
        ]
        if not matches:
            raise ValueError(
                f"Taiwan Water archive evidence lacks retained anchor: {fact.year_roc}"
            )
        if verify_mirror:
            for _, storage_key in matches:
                with (repo_root / "mirror" / storage_key).open("rb") as stream:
                    checksum = hashlib.file_digest(stream, "sha256").hexdigest()
                if checksum != fact.checksum:
                    raise ValueError(
                        f"Taiwan Water archive anchor checksum mismatch: {storage_key}"
                    )
    return len(facts)
