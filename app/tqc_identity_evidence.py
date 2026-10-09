"""Reviewed TQC sample identities anchored to native PDF bytes.

The catalog owns subjects, editions and native grade coverage. A filename or
paper-code numeral cannot establish a grade. Changed bytes need fresh review.
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
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True)
class TqcSampleIdentity:
    fact_id: str
    title: str
    checksum: str
    source_url: str
    track_id: str
    track_label: str
    level_id: str
    level_label: str
    variants: tuple[tuple[str, str], ...]
    reason: str


@dataclass(frozen=True)
class TqcIdentityCatalog:
    programme: tuple[str, str, str, str]
    facts: tuple[TqcSampleIdentity, ...]


def tqc_evidence_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/tqc/sample-identity-v1.json"


def _title_key(title: str) -> str:
    return "".join(unicodedata.normalize("NFKC", title).casefold().split())


def _text(row: dict[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"TQC evidence requires nonempty {field}")
    return value


def _official_url(value: str, *, sample: bool = False) -> None:
    url = urlsplit(value)
    if url.scheme not in {"http", "https"} or url.hostname not in {"www.tqc.org.tw", "tqc.org.tw"}:
        raise ValueError("TQC evidence requires an official source URL")
    if sample and not (
        url.path.lower().startswith("/user/example/") and url.path.lower().endswith(".pdf")
    ):
        raise ValueError("TQC native evidence must identify a sample PDF")


def read_tqc_identities(path: Path) -> TqcIdentityCatalog:
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(document, dict)
        or type(document.get("schema_version")) is not int
        or document["schema_version"] != 1
        or document.get("provider_id") != "tqc_cert"
        or document.get("catalog_version") != "exam-identity-v2"
    ):
        raise ValueError("Unsupported TQC identity evidence contract")
    programme = document["programme"]
    _official_url(_text(programme, "evidence_url"))
    levels, subjects = document["levels"], document["subjects"]
    for subject in subjects.values():
        _official_url(_text(subject, "evidence_url"))
    facts = []
    contexts: set[tuple[str, str]] = set()
    fact_ids: set[str] = set()
    variant_labels: dict[str, str] = {}
    for row in document["facts"]:
        date.fromisoformat(_text(row, "reviewed_at"))
        checksum = _text(row, "checksum")
        if re.fullmatch(r"[0-9a-f]{64}", checksum) is None:
            raise ValueError("TQC evidence requires a SHA-256 checksum")
        source_url = _text(row, "source_url")
        _official_url(source_url, sample=True)
        track_id, level_id = _text(row, "track_id"), _text(row, "level_id")
        if track_id not in subjects or level_id not in levels:
            raise ValueError("Unknown TQC subject or native grade coverage")
        variants = []
        for variant in row["variants"]:
            variant_id, label = _text(variant, "id"), _text(variant, "label")
            if variant_id in variant_labels and variant_labels[variant_id] != label:
                raise ValueError("Conflicting TQC variant labels")
            variant_labels[variant_id] = label
            variants.append((variant_id, label))
        if len({key for key, _ in variants}) != len(variants):
            raise ValueError("Duplicate TQC content variant")
        fact = TqcSampleIdentity(
            fact_id=_text(row, "id"),
            title=_text(row, "title"),
            checksum=checksum,
            source_url=source_url,
            track_id=track_id,
            track_label=_text(subjects[track_id], "label"),
            level_id=level_id,
            level_label=_text(levels[level_id], "label"),
            variants=tuple(variants),
            reason=_text(row, "reason"),
        )
        context = (_title_key(fact.title), fact.checksum)
        if context in contexts or fact.fact_id in fact_ids:
            raise ValueError("Duplicate TQC sample evidence context or fact id")
        contexts.add(context)
        fact_ids.add(fact.fact_id)
        facts.append(fact)
    return TqcIdentityCatalog(
        (
            _text(programme, "domain_id"),
            _text(programme, "exam_family_id"),
            _text(programme, "id"),
            _text(programme, "label"),
        ),
        tuple(facts),
    )


@lru_cache(maxsize=1)
def tqc_identity_catalog() -> TqcIdentityCatalog:
    return read_tqc_identities(tqc_evidence_path(Path(__file__).resolve().parents[1]))


def resolve_tqc_sample_identity(title: str, checksum: str) -> TqcSampleIdentity | None:
    key = (_title_key(title), checksum)
    return next(
        (
            fact
            for fact in tqc_identity_catalog().facts
            if (_title_key(fact.title), fact.checksum) == key
        ),
        None,
    )


def validate_tqc_identity_evidence(repo_root: Path, *, verify_mirror: bool = False) -> int:
    """Reconcile each approved native anchor with current or retained history."""
    from app.bundler import resolve_mirror_storage_path
    from app.models import to_plain_data
    from app.paths import provider_paths
    from app.source_revisions import load_source_revisions
    from app.state import load_provider_state

    catalog = read_tqc_identities(tqc_evidence_path(repo_root))
    provider = provider_paths(repo_root, "tqc_cert")
    _, current, _ = load_provider_state(provider)
    records = [(to_plain_data(paper), paper.storage_key) for paper in current.papers]
    records.extend(
        (entry["source_record"], entry["blob_storage_key"])
        for entry in load_source_revisions(provider)
        if entry["record_type"] == "paper"
    )
    for fact in catalog.facts:
        matches = [
            key
            for paper, key in records
            if paper["file_type"] == "question"
            and _title_key(paper["subject_name_raw"]) == _title_key(fact.title)
            and paper["download_url_source"] == fact.source_url
            and paper["checksum"] == fact.checksum
        ]
        if not matches:
            raise ValueError(f"{fact.fact_id}: retained TQC native anchor is missing or changed")
        if verify_mirror:
            path = resolve_mirror_storage_path(repo_root / "mirror", matches[0], "tqc_cert")
            if path is None:
                raise ValueError(f"{fact.fact_id}: TQC native mirror is missing")
            with path.open("rb") as stream:
                checksum = hashlib.file_digest(stream, "sha256").hexdigest()
            if checksum != fact.checksum:
                raise ValueError(f"{fact.fact_id}: TQC native mirror checksum differs")
    return len(catalog.facts)
