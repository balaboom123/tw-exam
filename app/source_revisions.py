"""Provider-owned recovery evidence for superseded source references and bytes.

Current normalized records continue to describe the current source projection.
The journal retains earlier evidence separately; recovery records must never be
substituted into current publication merely because their URLs are the same.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from app.models import NormalizedPaper, SourceExamPage, to_plain_data
from app.paths import ProviderPaths, is_provider_mirror_metadata
from app.source_material import source_material
from app.source_revision_review import reviewed_reference_revisions

REVISION_DIRECTORY = "recovery/source-revisions"


def is_revision_blob_key(storage_key: str) -> bool:
    parts = PurePosixPath(storage_key).parts
    return (
        len(parts) >= 5
        and parts[0] == "providers"
        and parts[2:4] == ("recovery", "source-revisions")
    )


def revision_blob_key(provider_id: str, checksum: str, suffix: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", provider_id) is None:
        raise ValueError("Invalid revision provider owner")
    if re.fullmatch(r"[0-9a-f]{64}", checksum) is None:
        raise ValueError("A revision requires a SHA-256 checksum")
    if re.fullmatch(r"\.[A-Za-z0-9]+", suffix) is None:
        raise ValueError("A revision requires a safe payload extension")
    return f"providers/{provider_id}/{REVISION_DIRECTORY}/{checksum}{suffix.lower()}"


def payload_checksum(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _copy_verified_payload(source: Path, target: Path, checksum: str) -> None:
    if target.exists():
        if payload_checksum(target) != checksum:
            raise ValueError(f"Revision blob checksum mismatch: {target}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(f".{target.name}.retain-{uuid.uuid4().hex}")
    try:
        shutil.copyfile(source, staged)
        if payload_checksum(staged) != checksum:
            raise ValueError("Source changed while preserving its previous revision")
        os.replace(staged, target)
    finally:
        staged.unlink(missing_ok=True)


def preserve_mirror_payload(mirror_root: Path, storage_key: str) -> tuple[str, str, int] | None:
    """Copy scoped old bytes before replacement or extension cleanup."""
    parts = PurePosixPath(storage_key).parts
    if not parts or parts[0] != "providers":
        return None  # Legacy unscoped callers keep their existing interface.
    if len(parts) < 3 or ".." in parts or PurePosixPath(storage_key).is_absolute():
        raise ValueError("Invalid provider revision locator")
    source = mirror_root / storage_key
    checksum = payload_checksum(source)
    key = revision_blob_key(parts[1], checksum, source.suffix)
    target = mirror_root / key
    if source.is_relative_to(mirror_root / "providers" / parts[1] / REVISION_DIRECTORY):
        raise ValueError("Revision blobs are immutable")
    _copy_verified_payload(source, target, checksum)
    return key, checksum, source.stat().st_size


def revision_journal_path(provider: ProviderPaths) -> Path:
    return provider.data_dir / "source-revisions.json"


def _reference_key(record_type: str, record: dict[str, Any]) -> tuple[Any, ...]:
    return (record_type,) + tuple(
        record.get(field, "")
        for field in (
            "provider_id",
            "year_roc",
            "source_exam_id",
            "category_code",
            "subject_code",
            "file_type",
            "download_url_source",
        )
    )


def _source_records(
    provider_id: str, pages: list[SourceExamPage], papers: list[NormalizedPaper]
) -> list[tuple[str, dict[str, Any]]]:
    records = []
    for paper in papers:
        record = to_plain_data(paper)
        record.pop("download_url_bundle", None)
        record["provider_id"] = paper.provider_id or provider_id
        records.append(("paper", record))
    for page in pages:
        for attachment in page.attachments:
            record = to_plain_data(attachment)
            material = attachment.source_material or page.source_material
            if material is not None:
                record["source_material"] = to_plain_data(material)
            records.append(
                (
                    "attachment",
                    {
                        **record,
                        "provider_id": page.provider_id or provider_id,
                        "year_roc": page.year_roc,
                        "source_exam_id": page.source_exam_id,
                        "exam_name_raw": page.exam_name_raw,
                    },
                )
            )
    return records


def _validate_source_record(provider_id: str, record: dict[str, Any]) -> None:
    string_fields = (
        "provider_id",
        "source_exam_id",
        "file_type",
        "download_url_source",
        "checksum",
        "storage_key",
    )
    if (
        any(not isinstance(record.get(field), str) for field in string_fields)
        or type(record.get("year_roc")) is not int
        or record["provider_id"] != provider_id
        or re.fullmatch(r"(?:[0-9a-f]{64})?", record["checksum"]) is None
    ):
        raise ValueError("Invalid source revision reference")
    parts = PurePosixPath(record["storage_key"]).parts
    if (
        PurePosixPath(record["storage_key"]).is_absolute()
        or ".." in parts
        or (parts and parts[0] == "providers" and parts[:2] != ("providers", provider_id))
    ):
        raise ValueError("Invalid source revision ownership or locator")


def load_source_revisions(provider: ProviderPaths) -> list[dict[str, Any]]:
    path = revision_journal_path(provider)
    if not path.exists():
        return []
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(document, dict)
        or type(document.get("schema_version")) is not int
        or document.get("schema_version") != 1
        or document.get("provider_id") != provider.provider_id
        or not isinstance(document.get("revisions"), list)
    ):
        raise ValueError(f"Unsupported source revision journal: {path}")
    entries = document["revisions"]
    ids: set[str] = set()
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("source_record"), dict)
            or "blob_storage_key" not in entry
        ):
            raise ValueError(f"Invalid source revision record: {path}")
        _validate_source_record(provider.provider_id, entry["source_record"])
        revision_id = entry.get("id")
        if not isinstance(revision_id, str) or re.fullmatch(r"[0-9a-f]{64}", revision_id) is None:
            raise ValueError(f"Invalid source revision id: {path}")
        if revision_id in ids or entry["source_record"].get("provider_id") != provider.provider_id:
            raise ValueError(f"Ambiguous source revision ownership: {path}")
        ids.add(revision_id)
        if entry.get("record_type") not in {"paper", "attachment"}:
            raise ValueError(f"Unsupported source revision record type: {path}")
        material = source_material(entry["source_record"].get("source_material"))
        if entry["record_type"] == "paper":
            version = entry["source_record"].get("schema_version", 1)
            if type(version) is not int or version not in {1, 2, 3}:
                raise ValueError(f"Unsupported source revision paper version: {path}")
            if (version == 3) != (material is not None):
                raise ValueError(f"Source revision material facts require paper v3: {path}")
        if entry.get("reason") not in {"payload_replaced", "source_reference_retired"}:
            raise ValueError(f"Unsupported source revision disposition: {path}")
        try:
            retained_at = datetime.fromisoformat(entry["retained_at"])
            if retained_at.tzinfo is None:
                raise ValueError("missing timezone")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid revision retention timestamp: {path}") from exc
        checksum = entry["source_record"].get("checksum", "")
        expected_id = hashlib.sha256(
            json.dumps(
                [_reference_key(entry["record_type"], entry["source_record"]), checksum],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if revision_id != expected_id:
            raise ValueError(f"Source revision id does not match its source reference: {path}")
        if checksum:
            expected = revision_blob_key(
                provider.provider_id, checksum, Path(entry["source_record"]["storage_key"]).suffix
            )
            if entry.get("blob_storage_key") != expected:
                raise ValueError(f"Source revision blob locator mismatch: {path}")
        elif entry.get("blob_storage_key") is not None:
            raise ValueError(f"Unverified source revision cannot claim retained bytes: {path}")
    return list(entries)


def validate_revision_conservation(previous: ProviderPaths, incoming: ProviderPaths) -> None:
    """Refuse a state transfer that deletes or rewrites retained evidence."""
    if previous.provider_id != incoming.provider_id:
        raise ValueError("Source revision transfer requires the same provider owner")
    before = load_source_revisions(previous)
    after = {entry["id"]: entry for entry in load_source_revisions(incoming)}
    for entry in before:
        if after.get(entry["id"]) != entry:
            raise ValueError(f"Provider snapshot would lose source revision {entry['id']}")


def audit_source_revisions(provider: ProviderPaths, *, verify_mirror: bool) -> dict[str, Any]:
    entries = load_source_revisions(provider)
    verified = 0
    unverified = 0
    errors = []
    for entry in entries:
        key = entry["blob_storage_key"]
        if key is None:
            unverified += 1
            continue
        if verify_mirror:
            path = provider.mirror_dir.parents[1] / key
            if not path.is_file() or payload_checksum(path) != entry["source_record"]["checksum"]:
                errors.append({"revision_id": entry["id"], "blob_storage_key": key})
            else:
                verified += 1
    report = {
        "revision_count": len(entries),
        "verified_payload_count": verified,
        "unverified_source_reference_count": unverified,
        "mirror_checked": verify_mirror,
        "errors": errors,
        "reviewed_non_paper_references": reviewed_reference_revisions(
            entries, repo_root=provider.data_dir.parents[2]
        ),
    }
    if provider.provider_id == "sfi_cert":
        from app.providers.sfi_cert.evidence import reviewed_native_revisions

        reviewed = reviewed_native_revisions(entries, provider.data_dir.parents[2])
        report["reviewed_native_paper_references"] = reviewed
        report["native_paper_review_unresolved"] = sum(
            row["record_type"] == "paper" for row in entries
        ) - len(reviewed)
    if provider.provider_id == "ipas_cert":
        from app.providers.ipas_cert.evidence import (
            reviewed_native_revisions as ipas_revision_views,
        )

        reviewed_documents = ipas_revision_views(entries, repo_root=provider.data_dir.parents[2])
        report["reviewed_native_document_references"] = reviewed_documents
        report["native_document_review_unresolved"] = len(entries) - len(reviewed_documents)
        report["native_document_date_review_unresolved"] = sum(
            row["material_date"]["basis"] == "unknown" for row in reviewed_documents
        )
    if provider.provider_id == "tabf_cert":
        from app.providers.tabf_cert.evidence import (
            reviewed_native_revisions as tabf_revision_views,
        )

        reviewed_papers = tabf_revision_views(entries, repo_root=provider.data_dir.parents[2])
        report["reviewed_native_paper_references"] = reviewed_papers
        report["native_paper_review_unresolved"] = len(entries) - len(reviewed_papers)
    if provider.provider_id == "taigi_cert":
        from app.providers.taigi_cert.evidence import (
            reviewed_native_revisions as taigi_revision_views,
        )

        reviewed_resources = taigi_revision_views(entries, repo_root=provider.data_dir.parents[2])
        report["reviewed_native_resource_references"] = reviewed_resources
        report["native_resource_review_unresolved"] = len(entries) - len(reviewed_resources)
    return report


def _retain_source_payload(provider: ProviderPaths, record: dict[str, Any]) -> str | None:
    checksum = record.get("checksum", "")
    if not checksum:
        return None
    storage_key = record.get("storage_key", "")
    blob_key = revision_blob_key(provider.provider_id, checksum, Path(storage_key).suffix)
    mirror_root = provider.mirror_dir.parents[1]
    blob = mirror_root / blob_key
    if not blob.is_file():
        raise ValueError(
            f"Cannot retain previous source bytes for {record['source_exam_id']}; "
            "restore the checksum-verified original before refreshing provider state"
        )
    if blob.is_symlink() or not blob.resolve().is_relative_to(provider.mirror_dir.resolve()):
        raise ValueError("Retained source revision must be a regular provider-owned file")
    if payload_checksum(blob) != checksum:
        raise ValueError(f"Retained source revision checksum mismatch: {blob_key}")
    return blob_key


def _restore_missing_verified_payloads(
    provider: ProviderPaths,
    records: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> None:
    """Recover expected bytes from another recorded locator in this provider.

    Older generations can predate a corrected locator or a journal blob. Only
    exact checksum matches from recorded source paths are reusable. Existing
    paths are left alone, and recovery copies remain independent files.
    """
    mirror_root = provider.mirror_dir.parents[1]
    provider_root = provider.mirror_dir.resolve()

    def source_path(record: dict[str, Any]) -> Path | None:
        key = record.get("storage_key", "")
        if not isinstance(key, str):
            raise ValueError("Invalid source recovery locator")
        if not key:
            return None
        _validate_source_record(provider.provider_id, record)
        path = (
            mirror_root / key
            if PurePosixPath(key).parts[0] == "providers"
            else provider.mirror_dir / key
        )
        if not path.resolve().is_relative_to(provider_root):
            raise ValueError("Source recovery locator escapes its provider directory")
        return path

    missing: dict[str, list[Path]] = {}
    for record in records:
        checksum = record.get("checksum", "")
        if not checksum:
            continue
        target = source_path(record)
        if target is not None and not target.exists():
            if target.is_symlink():
                raise ValueError("Cannot recover source bytes through a symbolic link")
            missing.setdefault(checksum, []).append(target)
    if not missing or not provider.mirror_dir.exists():
        return
    if provider.mirror_dir.is_symlink():
        raise ValueError("Cannot recover source bytes through a provider directory symlink")
    for record in candidates:
        checksum = record.get("checksum", "")
        if checksum not in missing:
            continue
        candidate = source_path(record)
        if candidate is None or candidate.is_symlink() or not candidate.is_file():
            continue
        if is_provider_mirror_metadata(candidate.relative_to(mirror_root).as_posix()):
            continue
        if payload_checksum(candidate) != checksum:
            continue
        for target in missing.pop(checksum):
            _copy_verified_payload(candidate, target, checksum)
        if not missing:
            break


def retain_superseded_sources(
    provider: ProviderPaths,
    previous_pages: list[SourceExamPage],
    previous_papers: list[NormalizedPaper],
    current_pages: list[SourceExamPage],
    current_papers: list[NormalizedPaper],
) -> int:
    """Persist previous source evidence before the provider writer replaces it."""
    entries = load_source_revisions(provider)
    current_records = _source_records(provider.provider_id, current_pages, current_papers)
    current_keys = {_reference_key(kind, record) for kind, record in current_records}
    current = {
        (_reference_key(kind, record), record.get("checksum", ""))
        for kind, record in current_records
    }
    previous_records = _source_records(provider.provider_id, previous_pages, previous_papers)
    recovery_records = [record for _kind, record in current_records]
    for entry in entries:
        if entry["blob_storage_key"] is not None:
            recovery_records.append(
                {**entry["source_record"], "storage_key": entry["blob_storage_key"]}
            )
    for kind, record in previous_records:
        if (
            record.get("checksum")
            and (_reference_key(kind, record), record["checksum"]) not in current
        ):
            recovery_records.append(
                {
                    **record,
                    "storage_key": revision_blob_key(
                        provider.provider_id, record["checksum"], Path(record["storage_key"]).suffix
                    ),
                }
            )
    candidates = [record for _kind, record in current_records + previous_records]
    candidates.extend(entry["source_record"] for entry in entries)
    candidates.extend(recovery_records)
    _restore_missing_verified_payloads(provider, recovery_records, candidates)
    for entry in entries:
        _retain_source_payload(provider, entry["source_record"])
    known = {entry["id"] for entry in entries}
    retained = 0
    for kind, record in previous_records:
        _validate_source_record(provider.provider_id, record)
        key = _reference_key(kind, record)
        checksum = record.get("checksum", "")
        if (key, checksum) in current:
            continue
        revision_id = hashlib.sha256(
            json.dumps([key, checksum], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if revision_id in known:
            continue
        blob_key = _retain_source_payload(provider, record)
        entries.append(
            {
                "id": revision_id,
                "record_type": kind,
                "source_record": record,
                "blob_storage_key": blob_key,
                "retained_at": datetime.now(UTC).isoformat(),
                "reason": "payload_replaced" if key in current_keys else "source_reference_retired",
            }
        )
        known.add(revision_id)
        retained += 1
    if retained:
        path = revision_journal_path(provider)
        path.parent.mkdir(parents=True, exist_ok=True)
        staged = path.with_name(f".{path.name}.write-{uuid.uuid4().hex}")
        try:
            staged.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "provider_id": provider.provider_id,
                        "revisions": entries,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(staged, path)
        finally:
            staged.unlink(missing_ok=True)
    return retained
