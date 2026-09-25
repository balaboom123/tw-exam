from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProviderPaths:
    provider_id: str
    data_dir: Path
    exams_dir: Path
    papers_dir: Path
    index_path: Path
    review_queue_path: Path
    sync_failures_path: Path
    aliases_path: Path
    source_manifest_path: Path
    mirror_dir: Path


@dataclass(frozen=True)
class SitePaths:
    site_id: str
    data_dir: Path
    bundles_path: Path
    release_assets_path: Path
    frontend_bundles_path: Path
    bundle_dir: Path


@dataclass(frozen=True)
class LegacyPaths:
    data_dir: Path
    bundles_path: Path
    release_assets_path: Path
    bundle_dir: Path


def provider_paths(repo_root: Path, provider_id: str) -> ProviderPaths:
    data_dir = repo_root / "data" / "providers" / provider_id
    return ProviderPaths(
        provider_id=provider_id,
        data_dir=data_dir,
        exams_dir=data_dir / "exams",
        papers_dir=data_dir / "papers",
        index_path=data_dir / "index.json",
        review_queue_path=data_dir / "review-queue.json",
        sync_failures_path=data_dir / "sync-failures.json",
        aliases_path=data_dir / "aliases.json",
        source_manifest_path=data_dir / "source-manifest.json",
        mirror_dir=repo_root / "mirror" / "providers" / provider_id,
    )


def site_paths(repo_root: Path, site_id: str) -> SitePaths:
    data_dir = repo_root / "data" / "sites" / site_id
    return SitePaths(
        site_id=site_id,
        data_dir=data_dir,
        bundles_path=data_dir / "bundles.json",
        release_assets_path=data_dir / "release-assets.json",
        frontend_bundles_path=data_dir / "frontend-bundles.json",
        bundle_dir=repo_root / "bundles" / "sites" / site_id,
    )


def legacy_paths(repo_root: Path) -> LegacyPaths:
    return LegacyPaths(
        data_dir=repo_root / "data",
        bundles_path=repo_root / "data" / "bundles.json",
        release_assets_path=repo_root / "data" / "release-assets.json",
        bundle_dir=repo_root / "bundles",
    )
