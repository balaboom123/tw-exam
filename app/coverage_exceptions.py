"""Reviewed evidence for official sources that are currently blocked or excluded.

Coverage exceptions live under ``catalog/source-coverage`` because they are
manual, source-grounded decisions.  They are deliberately matched against the
current raw event/failure state; an exception cannot silently hide a changed
source or a repaired failure.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.models import SyncFailure

_ALLOWED_SCOPES = {"event", "file"}
_ALLOWED_STATUSES = {"blocked", "intentionally_out_of_scope"}
_SHA256_LENGTH = 64


@dataclass(frozen=True)
class CoverageException:
    scope: str
    provider_id: str
    source_exam_id: str
    year_ad: int
    status: str
    reason_code: str
    reason: str
    source_url: str
    evidence: dict[str, Any]
    paper_code: str = ""
    file_type: str = ""

    @property
    def key(self) -> tuple[str, str, int, str, str]:
        return (self.scope, self.source_exam_id, self.year_ad, self.paper_code, self.file_type)

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "scope": self.scope,
            "provider_id": self.provider_id,
            "source_exam_id": self.source_exam_id,
            "year_ad": self.year_ad,
            "status": self.status,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "source_url": self.source_url,
            "evidence": self.evidence,
        }
        if self.scope == "file":
            value["paper_code"] = self.paper_code
            value["file_type"] = self.file_type
        return value


def coverage_exception_path(repo_root: Path, provider_id: str) -> Path:
    return repo_root / "catalog" / "source-coverage" / f"{provider_id}.json"


def _require_text(value: Any, field: str, *, path: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}: {field} must be a non-empty string")
    return value


def _validate_evidence(value: Any, *, path: Path) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path}: evidence must be an object")
    _require_text(value.get("captured_at"), "evidence.captured_at", path=path)
    http_status = value.get("http_status")
    if (
        not isinstance(http_status, int)
        or isinstance(http_status, bool)
        or not 0 <= http_status <= 599
    ):
        raise ValueError(f"{path}: evidence.http_status must be an integer from 0 through 599")
    response_bytes = value.get("response_bytes")
    if (
        not isinstance(response_bytes, int)
        or isinstance(response_bytes, bool)
        or response_bytes < 0
    ):
        raise ValueError(f"{path}: evidence.response_bytes must be a non-negative integer")
    response_sha256 = _require_text(
        value.get("response_sha256"), "evidence.response_sha256", path=path
    ).lower()
    if len(response_sha256) != _SHA256_LENGTH or any(
        character not in "0123456789abcdef" for character in response_sha256
    ):
        raise ValueError(f"{path}: evidence.response_sha256 must be a SHA-256 hex digest")
    _require_text(value.get("observation"), "evidence.observation", path=path)
    return dict(value)


def _parse_exception(value: Any, provider_id: str, *, path: Path) -> CoverageException:
    if not isinstance(value, dict):
        raise ValueError(f"{path}: every exception must be an object")
    scope = _require_text(value.get("scope"), "scope", path=path)
    if scope not in _ALLOWED_SCOPES:
        raise ValueError(f"{path}: unsupported scope {scope!r}")
    entry_provider_id = _require_text(value.get("provider_id"), "provider_id", path=path)
    if entry_provider_id != provider_id:
        raise ValueError(f"{path}: provider_id must be {provider_id!r}")
    source_exam_id = _require_text(value.get("source_exam_id"), "source_exam_id", path=path)
    year_ad = value.get("year_ad")
    if not isinstance(year_ad, int) or isinstance(year_ad, bool) or year_ad < 1:
        raise ValueError(f"{path}: year_ad must be a positive integer")
    status = _require_text(value.get("status"), "status", path=path)
    if status not in _ALLOWED_STATUSES:
        raise ValueError(f"{path}: unsupported status {status!r}")
    reason_code = _require_text(value.get("reason_code"), "reason_code", path=path)
    reason = _require_text(value.get("reason"), "reason", path=path)
    source_url = _require_text(value.get("source_url"), "source_url", path=path)
    paper_code = value.get("paper_code", "")
    file_type = value.get("file_type", "")
    if scope == "file":
        paper_code = _require_text(paper_code, "paper_code", path=path)
        file_type = _require_text(file_type, "file_type", path=path)
    elif paper_code or file_type:
        raise ValueError(f"{path}: event exceptions cannot specify paper_code or file_type")
    evidence = _validate_evidence(value.get("evidence"), path=path)
    return CoverageException(
        scope=scope,
        provider_id=entry_provider_id,
        source_exam_id=source_exam_id,
        year_ad=year_ad,
        status=status,
        reason_code=reason_code,
        reason=reason,
        source_url=source_url,
        evidence=evidence,
        paper_code=paper_code,
        file_type=file_type,
    )


def load_coverage_exceptions(repo_root: Path, provider_id: str) -> list[CoverageException]:
    path = coverage_exception_path(repo_root, provider_id)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid coverage exceptions {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: top-level value must be an object")
    if payload.get("schema_version") != 1:
        raise ValueError(f"{path}: unsupported schema_version {payload.get('schema_version')!r}")
    if payload.get("provider_id") != provider_id:
        raise ValueError(f"{path}: provider_id must be {provider_id!r}")
    raw_exceptions = payload.get("exceptions")
    if not isinstance(raw_exceptions, list):
        raise ValueError(f"{path}: exceptions must be an array")
    exceptions = [_parse_exception(value, provider_id, path=path) for value in raw_exceptions]
    seen: set[tuple[str, str, int, str, str]] = set()
    for exception in exceptions:
        if exception.key in seen:
            raise ValueError(f"{path}: duplicate exception key {exception.key!r}")
        seen.add(exception.key)
    return exceptions


def event_exception_for(
    exceptions: list[CoverageException], source_exam_id: str, year_ad: int
) -> CoverageException | None:
    matches = [
        exception
        for exception in exceptions
        if exception.scope == "event"
        and exception.source_exam_id == source_exam_id
        and exception.year_ad == year_ad
    ]
    if len(matches) > 1:
        raise ValueError(f"multiple event coverage exceptions for {(source_exam_id, year_ad)!r}")
    return matches[0] if matches else None


def failure_exception_for(
    provider_id: str, failure: SyncFailure, exceptions: list[CoverageException]
) -> CoverageException | None:
    """Match only the exact current download failure represented by an entry."""
    matches = [
        exception
        for exception in exceptions
        if exception.scope == "file"
        and exception.provider_id == provider_id
        and exception.source_exam_id == failure.source_exam_id
        and exception.year_ad == failure.year_roc + 1911
        and exception.paper_code == failure.paper_code
        and exception.file_type == failure.file_type
        and exception.source_url == failure.url
        and failure.stage == "download"
    ]
    if len(matches) > 1:
        failure_key = (
            failure.source_exam_id,
            failure.year_roc + 1911,
            failure.paper_code,
            failure.file_type,
        )
        raise ValueError(f"multiple file coverage exceptions for {failure_key!r}")
    return matches[0] if matches else None
