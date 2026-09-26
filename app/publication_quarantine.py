"""Providers withheld from a site projection because of a published defect.

Quarantine lives under ``catalog/mappings`` because it is publication policy,
not source evidence.  A quarantined provider stays registered in
``app.site_registry`` and therefore keeps its source inventory, catalog audit,
and history audit obligations; only ``load_site_catalog`` drops it.  That
separation is deliberate: an unpublished provider must still be measured,
otherwise quarantine would silently shrink the completeness denominator.

Entries describe what the repository currently *publishes* incorrectly.  An
incomplete or blocked upstream source is not a quarantine reason; that belongs
in ``catalog/source-coverage`` via ``app.coverage_exceptions``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.evidence import local_evidence_exists

_ALLOWED_STATUSES = {
    "wrong_identity",
    "wrong_payload",
    "corrupt_payload",
    "non_paper_role",
    "duplicate_source_identity",
}


@dataclass(frozen=True)
class QuarantineEntry:
    provider_id: str
    site_id: str
    status: str
    reason: str
    evidence_path: str
    spec_path: str


def quarantine_path(repo_root: Path) -> Path:
    return repo_root / "catalog" / "mappings" / "publication-quarantine.json"


def _require_text(value: Any, field: str, *, path: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}: {field} must be a non-empty string")
    return value


def _parse_entry(value: Any, *, path: Path, repo_root: Path) -> QuarantineEntry:
    if not isinstance(value, dict):
        raise ValueError(f"{path}: every quarantine entry must be an object")
    provider_id = _require_text(value.get("provider_id"), "provider_id", path=path)
    site_id = _require_text(value.get("site_id"), "site_id", path=path)
    status = _require_text(value.get("status"), "status", path=path)
    if status not in _ALLOWED_STATUSES:
        raise ValueError(f"{path}: unsupported status {status!r} for {provider_id}")
    reason = _require_text(value.get("reason"), "reason", path=path)
    evidence_path = _require_text(value.get("evidence_path"), "evidence_path", path=path)
    spec_path = _require_text(value.get("spec_path"), "spec_path", path=path)
    # A quarantine entry withholds public data, so it must stay attached to
    # reviewable evidence; a stale pointer would leave the withholding
    # unexplained after the referenced file is moved or removed.
    for field, reference in (("evidence_path", evidence_path), ("spec_path", spec_path)):
        if not local_evidence_exists(repo_root, reference):
            raise ValueError(f"{path}: {field} {reference!r} for {provider_id} does not exist")
    return QuarantineEntry(
        provider_id=provider_id,
        site_id=site_id,
        status=status,
        reason=reason,
        evidence_path=evidence_path,
        spec_path=spec_path,
    )


def load_quarantine(repo_root: Path, *, site_id: str) -> dict[str, QuarantineEntry]:
    """Return quarantined providers for ``site_id``, keyed by provider id."""
    path = quarantine_path(repo_root)
    if not path.exists():
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{path}: quarantine document must be an object")
    raw_entries = document.get("quarantine", [])
    if not isinstance(raw_entries, list):
        raise ValueError(f"{path}: quarantine must be a list")
    entries: dict[str, QuarantineEntry] = {}
    for raw_entry in raw_entries:
        entry = _parse_entry(raw_entry, path=path, repo_root=repo_root)
        if entry.site_id != site_id:
            continue
        if entry.provider_id in entries:
            raise ValueError(
                f"{path}: duplicate quarantine entry for {entry.provider_id} on site {site_id}"
            )
        entries[entry.provider_id] = entry
    return entries


def quarantined_provider_ids(repo_root: Path, *, site_id: str) -> frozenset[str]:
    return frozenset(load_quarantine(repo_root, site_id=site_id))
