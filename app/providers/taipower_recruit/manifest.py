"""Keep observed archive links distinct from retained provider originals."""

from __future__ import annotations

from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from app.manifest import SourceManifest
from app.models import SourceExamPage
from app.source_revisions import payload_checksum

PROVIDER_ID = "taipower_recruit"


def _verified_files(
    pages: list[SourceExamPage], mirror_root: Path, now: str
) -> dict[str, dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}
    provider_root = (mirror_root / "providers" / PROVIDER_ID).resolve()
    for page in pages:
        if page.provider_id != PROVIDER_ID:
            raise ValueError("Taipower source manifest cannot include another provider")
        records = [
            (
                "attachment",
                a.download_url_source,
                a.file_type,
                a.title,
                "",
                "",
                a.storage_key,
                a.checksum,
            )
            for a in page.attachments
        ]
        records.extend(
            (
                "paper",
                url,
                role,
                p.subject_name_raw,
                p.subject_code,
                p.source_category_code or p.category_code,
                p.mirror_files.get(role, {}).get("storage_key", ""),
                p.mirror_files.get(role, {}).get("checksum", ""),
            )
            for p in page.papers
            for role, url in p.files.items()
        )
        for kind, url, role, title, subject, category, key, checksum in records:
            parts = PurePosixPath(key)
            if (
                parts.is_absolute()
                or ".." in parts.parts
                or "\\" in key
                or parts.as_posix() != key
                or parts.parts[:2] != ("providers", PROVIDER_ID)
            ):
                raise ValueError("Invalid Taipower original mirror locator")
            entry = dict(
                storage_key=key,
                source_url=url,
                source_exam_id=page.source_exam_id,
                year_ad=page.year_ad,
                year_roc=page.year_roc,
                subject_code=subject,
                category_code=category,
                file_type=role,
                label=title,
                record_type=kind,
                sha256=checksum,
            )
            if key in files:
                previous = {
                    k: v for k, v in files[key].items() if k not in {"bytes", "verified_at"}
                }
                if previous != entry:
                    raise ValueError(
                        "Competing Taipower original source references share a locator"
                    )
                continue
            native = mirror_root / key
            if (
                native.is_symlink()
                or not native.is_file()
                or not native.resolve().is_relative_to(provider_root)
                or not checksum
                or payload_checksum(native) != checksum
            ):
                raise ValueError("Taipower manifest cannot approve unavailable original bytes")
            files[key] = {**entry, "bytes": native.stat().st_size, "verified_at": now}
    return files


def refresh_source_manifest(
    manifest: SourceManifest,
    pages: list[SourceExamPage],
    mirror_root: Path,
    now: str,
    *,
    listed_source_urls: set[str],
) -> SourceManifest:
    """Verify retained originals; never equate shared job references with files."""
    if manifest.provider_id != PROVIDER_ID or not listed_source_urls:
        raise ValueError(
            "Taipower source manifest requires its provider and observed archive links"
        )
    files = _verified_files(pages, mirror_root, now)
    papers = [entry for entry in files.values() if entry["record_type"] == "paper"]
    acquired_urls = {entry["source_url"] for entry in papers}
    listed = [entry for entry in papers if entry["source_url"] in listed_source_urls]
    missing = sorted(listed_source_urls - acquired_urls)
    policy = dict(manifest.probe_policy)
    native_review = policy.pop("native_review", None)
    if native_review is not None:
        policy.setdefault("previous_native_review", native_review)
    policy.update(
        last_discovery_at=now,
        coverage_status="partial" if missing else "complete",
        discovery_scope_note=(
            "Current official archive links and retained originals are separate scopes. "
            "Shared native job references reuse one verified original. Delisted URLs "
            "and earlier payloads remain retained; coverage does not prove that every "
            "historically administered paper was published."
        ),
        current_archive={
            "verified_at": now,
            "events": len({entry["source_exam_id"] for entry in listed}),
            "source_file_count": len(listed_source_urls),
            "acquired_source_file_count": len(listed),
            "unique_checksums": len({entry["sha256"] for entry in listed}),
            "source_listing_roles": dict(Counter(entry["file_type"] for entry in listed)),
            "unacquired_source_urls": missing,
        },
        retained_local_state={
            "events": len(pages),
            "original_files": len(files),
            "paper_files": len(papers),
            "reference_documents": len(files) - len(papers),
            "paper_references": sum(len(p.files) for page in pages for p in page.papers),
            "unlisted_paper_originals": len(acquired_urls - listed_source_urls),
            "all_originals_SHA256_verified": True,
        },
    )
    manifest.files = files
    manifest.probe_policy = policy
    return manifest
