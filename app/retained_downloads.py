"""Keep an existing historical ZIP URL independently of current exam grouping.

These are frozen downloads, never aliases for a successor bundle. Site-owned
snapshots bind their exact original bytes; provider history anchors their papers.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from app.models import BundleAsset, NormalizedPaper
from app.paths import SitePaths, provider_paths


@dataclass(frozen=True)
class RetainedDownload:
    provider_id: str
    bundle_id: str
    release_tag: str
    asset_name: str
    storage_key: str
    checksum: str
    retained_at: str

    def validate(self, site_id: str) -> None:
        from app.site_registry import get_site_config

        if any(not isinstance(value, str) for value in asdict(self).values()):
            raise ValueError("Retained download fields must be strings")
        if (
            not self.provider_id
            or not self.bundle_id
            or not self.release_tag
            or self.asset_name in {"", ".", ".."}
            or "/" in self.asset_name
            or "\\" in self.asset_name
            or not self.asset_name.endswith(".zip")
            or self.storage_key != f"bundles/sites/{site_id}/{self.asset_name}"
            or re.fullmatch(r"[0-9a-f]{64}", self.checksum) is None
        ):
            raise ValueError("Invalid retained download identity or site-owned locator")
        if datetime.fromisoformat(self.retained_at).tzinfo is None:
            raise ValueError("Retained downloads require a timezone-aware snapshot date")
        prefix = get_site_config(site_id).release_tag_prefix + "-v2-"
        if not self.release_tag.startswith(prefix) or not self.release_tag[len(prefix) :].isdigit():
            raise ValueError(
                "Retained downloads must stay in their site's current v2 release namespace"
            )


def retained_downloads_path(site: SitePaths) -> Path:
    return site.data_dir / "retained-downloads.json"


def load_retained_downloads(site: SitePaths) -> list[RetainedDownload]:
    path = retained_downloads_path(site)
    if not path.exists():
        return []
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "site_id", "downloads"}
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 1
        or document["site_id"] != site.site_id
        or not isinstance(document["downloads"], list)
        or not document["downloads"]
    ):
        raise ValueError("Unsupported retained download inventory or site ownership")
    downloads = [RetainedDownload(**row) for row in document["downloads"]]
    names = set()
    for download in downloads:
        download.validate(site.site_id)
        if download.asset_name.casefold() in names:
            raise ValueError("Duplicate retained download asset name")
        names.add(download.asset_name.casefold())
    return downloads


def write_retained_downloads(
    site: SitePaths,
    downloads: list[RetainedDownload],
    *,
    approved_retirements: dict[str, str] | None = None,
    primary_promotions: list[BundleAsset] | None = None,
) -> None:
    path = retained_downloads_path(site)
    previous = load_retained_downloads(site)
    by_name = {download.asset_name: download for download in downloads}
    if len({name.casefold() for name in by_name}) != len(downloads):
        raise ValueError("Duplicate retained download")
    removed = {
        download.asset_name: download.checksum
        for download in previous
        if download.asset_name not in by_name
    }
    allowed_removals = dict(approved_retirements or {})
    promotions = {bundle.asset_name: bundle for bundle in primary_promotions or []}
    for previous_download in previous:
        if previous_download.asset_name not in removed:
            continue
        primary = promotions.get(previous_download.asset_name)
        if primary is None:
            continue
        if (primary.bundle_id, primary.release_tag, primary.storage_key, primary.checksum) != (
            previous_download.bundle_id,
            previous_download.release_tag,
            previous_download.storage_key,
            previous_download.checksum,
        ):
            raise ValueError("A primary promotion must preserve the exact frozen URL and bytes")
        verify_retained_download(
            site.data_dir.parents[2], site, previous_download, verify_content=True
        )
        if previous_download.asset_name in allowed_removals and (
            allowed_removals[previous_download.asset_name] != previous_download.checksum
        ):
            raise ValueError("Retirement checksum differs from frozen bytes")
        allowed_removals[previous_download.asset_name] = previous_download.checksum
    if removed != allowed_removals or any(
        by_name[download.asset_name] != download
        for download in previous
        if download.asset_name in by_name
    ):
        raise ValueError("Publication cannot remove or rewrite a frozen retained download")
    if not downloads:
        if previous:
            path.unlink()
        return
    for download in downloads:
        download.validate(site.site_id)
    site.data_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "site_id": site.site_id,
                "downloads": [asdict(download) for download in downloads],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def reserved_asset_names(downloads: list[RetainedDownload]) -> dict[str, set[str]]:
    reserved: dict[str, set[str]] = defaultdict(set)
    for download in downloads:
        reserved[download.release_tag].add(download.asset_name)
    return dict(reserved)


def validate_primary_separation(
    downloads: list[RetainedDownload],
    bundles: list[BundleAsset],
) -> None:
    from app.release_tags import physical_asset_names

    protected = {download.asset_name.casefold() for download in downloads}
    if any(
        name.casefold() in protected for bundle in bundles for name in physical_asset_names(bundle)
    ):
        raise ValueError("A frozen historical download cannot alias a current primary bundle")


def _source_anchors(repo_root: Path, provider_id: str) -> set[tuple[Any, ...]]:
    from app.bundler import _paper_bundle_key
    from app.source_revisions import load_source_revisions
    from app.state import load_provider_state

    provider = provider_paths(repo_root, provider_id)
    _, catalog, _ = load_provider_state(provider)
    anchors = {(*_paper_bundle_key(paper), paper.checksum) for paper in catalog.papers}
    anchors.update(
        (*_paper_bundle_key(entry["source_record"]), entry["source_record"]["checksum"])
        for entry in load_source_revisions(provider)
        if entry["record_type"] == "paper"
    )
    return anchors


def verify_retained_download(
    repo_root: Path,
    site: SitePaths,
    download: RetainedDownload,
    *,
    verify_content: bool,
) -> dict[str, Any]:
    from app.archive_audit import inspect_archive
    from app.bundler import _paper_bundle_key
    from app.publication_quarantine import quarantined_provider_ids
    from app.site_registry import get_site_config

    download.validate(site.site_id)
    path = repo_root / download.storage_key
    if path.is_symlink() or path.parent != site.bundle_dir:
        raise ValueError("Retained download is not a regular site-owned archive")
    manifest = inspect_archive(path, verify_content=verify_content)
    if manifest.get("bundle_id") != download.bundle_id:
        raise ValueError("Retained archive identity differs from its frozen snapshot")
    if verify_content:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != download.checksum:
            raise ValueError("Retained download checksum differs from frozen original bytes")
    allowed = get_site_config(site.site_id).provider_ids
    if download.provider_id not in allowed:
        raise ValueError("Retained download has no current site provider owner")
    if download.provider_id in quarantined_provider_ids(repo_root, site_id=site.site_id):
        raise ValueError("Retained download belongs to a quarantined provider")
    anchors = _source_anchors(repo_root, download.provider_id)
    for paper in manifest["papers"]:
        if (*_paper_bundle_key(paper), paper["checksum"]) not in anchors:
            raise ValueError("Retained archive paper lacks a current or immutable history anchor")
    return manifest


def snapshot_existing_downloads(
    repo_root: Path,
    site: SitePaths,
    existing: list[BundleAsset],
    bundle_ids: set[str],
) -> list[RetainedDownload]:
    from app.site_registry import get_site_config

    downloads = load_retained_downloads(site)
    selected = [bundle for bundle in existing if bundle.bundle_id in bundle_ids]
    if {bundle.bundle_id for bundle in selected} != bundle_ids:
        raise ValueError("Only existing published bundle identities can be retained")
    by_name = {download.asset_name: download for download in downloads}
    for bundle in selected:
        # V2 bundle identities begin with their registered provider slug. This
        # identifies the owner to query; every native source key and checksum
        # must independently match that owner's current or immutable history.
        owners = [
            provider_id
            for provider_id in get_site_config(site.site_id).provider_ids
            if bundle.bundle_id.startswith(provider_id.replace("_", "-") + "-")
        ]
        if len(owners) != 1:
            raise ValueError("Existing publication has no unambiguous registered provider owner")
        candidate = RetainedDownload(
            owners[0],
            bundle.bundle_id,
            bundle.release_tag,
            bundle.asset_name,
            bundle.storage_key,
            bundle.checksum,
            datetime.now(UTC).isoformat(),
        )
        if candidate.asset_name in by_name:
            previous = by_name[candidate.asset_name]
            candidate = RetainedDownload(
                candidate.provider_id,
                candidate.bundle_id,
                candidate.release_tag,
                candidate.asset_name,
                candidate.storage_key,
                candidate.checksum,
                previous.retained_at,
            )
            if candidate != previous:
                raise ValueError("An existing retained download cannot change its original bytes")
        verify_retained_download(repo_root, site, candidate, verify_content=True)
        by_name[candidate.asset_name] = candidate
    return sorted(by_name.values(), key=lambda download: download.asset_name)


def derive_compatibility_pages(
    repo_root: Path,
    site: SitePaths,
    downloads: list[RetainedDownload],
    papers: list[NormalizedPaper],
    bundles: list[BundleAsset],
) -> list[dict[str, Any]]:
    """Follow exact conserved sources from a frozen ZIP to current groups.

    This is a publication projection, not another identity mapping. More
    complex source transformations retain their separately reviewed pages.
    """
    from app.bundler import _paper_bundle_key

    public = {bundle.bundle_id for bundle in bundles}
    by_source: dict[tuple[Any, ...], set[str]] = defaultdict(set)
    for paper in papers:
        by_source[(paper.provider_id, *_paper_bundle_key(paper), paper.checksum)].add(
            paper.bundle_id
        )
    pages = []
    for download in downloads:
        manifest = verify_retained_download(repo_root, site, download, verify_content=False)
        sources = [
            (download.provider_id, *_paper_bundle_key(paper), paper["checksum"])
            for paper in manifest["papers"]
        ]
        if not sources or any(key not in by_source for key in sources):
            continue
        successors = {bundle_id for key in sources for bundle_id in by_source[key]}
        if not successors or not successors <= public:
            continue
        pages.append(
            {
                "id": download.bundle_id,
                "name": manifest["canonical_name"],
                "successorIds": sorted(successors),
            }
        )
    return sorted(pages, key=lambda page: page["id"])


def release_download_records(
    path: Path,
    *,
    expected_site_id: str | None = None,
) -> list[dict[str, Any]]:
    """One strict reader for the release helper and publication inventory gates."""
    document = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(document, list):
        return cast(list[dict[str, Any]], document)  # Historical flat release inventories.
    if not isinstance(document, dict):
        raise ValueError("Release download inventory must be an object or historical array")
    version = document.get("schema_version")
    if type(version) is not int or version not in {1, 2, 3}:
        raise ValueError("Unsupported release download inventory version")
    if expected_site_id is not None and document.get("site_id") != expected_site_id:
        raise ValueError("Release download inventory belongs to another site")
    primary = document.get("assets")
    retained = document.get("retained_downloads", [])
    if not isinstance(primary, list) or not isinstance(retained, list):
        raise ValueError("Release download inventory requires explicit asset arrays")
    if (version == 3) != bool(retained):
        raise ValueError("Retained downloads require release inventory v3")
    if version == 3:
        for row in retained:
            RetainedDownload(**row).validate(document["site_id"])
    records = [*primary, *retained]
    keys = [(row.get("release_tag", ""), row["asset_name"]) for row in records]
    if len(keys) != len(set(keys)):
        raise ValueError("Release download inventory contains duplicate physical assets")
    return records
