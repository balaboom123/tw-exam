"""Project verified acquisition and current listing evidence into its source manifest."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from app.manifest import SourceManifest
from app.models import SourceExamPage
from app.source_revisions import payload_checksum


def refresh_retained_files(
    manifest: SourceManifest,
    pages: list[SourceExamPage],
    mirror_root: Path,
    now: str,
) -> SourceManifest:
    """Reconcile current verified paths without inventing a new source observation."""
    if manifest.provider_id != "taisugar_recruit":
        raise ValueError("Taiwan Sugar source manifest requires its provider owner")
    files: dict[str, dict[str, Any]] = {}
    for page in pages:
        if page.provider_id != manifest.provider_id:
            raise ValueError("Taiwan Sugar source manifest cannot include another provider")
        records = [
            (a.download_url_source, a.file_type, a.title, "", a.storage_key, a.checksum)
            for a in page.attachments
        ]
        records.extend(
            (
                url,
                role,
                p.subject_name_raw,
                p.subject_code,
                p.mirror_files.get(role, {}).get("storage_key", ""),
                p.mirror_files.get(role, {}).get("checksum", ""),
            )
            for p in page.papers
            for role, url in p.files.items()
        )
        for url, role, label, subject, key, checksum in records:
            path = PurePosixPath(key)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in key
                or path.parts[:2] != ("providers", manifest.provider_id)
                or key in files
            ):
                raise ValueError("Invalid or duplicate Taiwan Sugar mirror locator")
            native = mirror_root / key
            if (
                native.is_symlink()
                or not native.is_file()
                or not native.resolve().is_relative_to(
                    (mirror_root / "providers" / manifest.provider_id).resolve()
                )
                or not checksum
                or payload_checksum(native) != checksum
            ):
                raise ValueError("Taiwan Sugar manifest cannot approve unavailable original bytes")
            files[key] = dict(
                storage_key=key,
                source_url=url,
                source_exam_id=page.source_exam_id,
                year_ad=page.year_ad,
                year_roc=page.year_roc,
                subject_code=subject,
                file_type=role,
                label=label,
                sha256=checksum,
                bytes=native.stat().st_size,
                verified_at=now,
            )
    manifest.files = files
    originals = [row for row in files.values() if not urlsplit(row["source_url"]).fragment]
    manifest.probe_policy.update(
        accepted_asset_count=len(originals),
        accepted_asset_bytes=sum(row["bytes"] for row in originals),
        asset_reconciliation_at=now,
        retained_local_state={
            "events": len(pages),
            "original_files": len(originals),
            "archive_member_references": len(files) - len(originals),
            "paper_references": sum(len(p.files) for page in pages for p in page.papers),
            "source_archives": sum(
                a.file_type == "source_archive" for page in pages for a in page.attachments
            ),
            "all_originals_SHA256_verified": True,
        },
    )
    return manifest


def refresh_source_manifest(
    manifest: SourceManifest,
    pages: list[SourceExamPage],
    mirror_root: Path,
    now: str,
    *,
    listing_rows: list[tuple[str, str]],
    listing_page_count: int,
) -> SourceManifest:
    if not listing_rows or listing_page_count < 1:
        raise ValueError(
            "Taiwan Sugar manifest requires complete provider-owned discovery evidence"
        )
    policy = manifest.probe_policy
    previous_reconciliation = policy.get("asset_reconciliation_at", "")
    refresh_retained_files(manifest, pages, mirror_root, now)
    # Historical URL restrictions remain dated evidence, while the company
    # archive now provides the same cycle's papers without that login route.
    blocker = policy.pop("current_cycle_blocker", None)
    if blocker is not None:
        policy["previous_cycle_route_observation"] = {
            "observed_at": previous_reconciliation,
            "route_evidence": blocker,
        }
    policy.update(
        last_discovery_at=now,
        coverage_status="partial",
        discovery_scope_note=(
            "Every currently retained original is checksum-verified. Current company "
            "listing discovery and native paper identity review are separate scopes."
        ),
        native_identity_status="review_pending",
        listing_evidence={
            "declared_row_count": len(listing_rows),
            "page_count": listing_page_count,
        },
        excluded_paper_rows=[
            dict(
                title=title,
                detail_url=url,
                reason="doctoral-level recruitment is outside the new-worker identity",
            )
            for title, url in listing_rows
            if "博士" in title and "試題" in title
        ],
    )
    return manifest
