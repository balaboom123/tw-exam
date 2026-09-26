from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from app.models import BundleAsset

GITHUB_RELEASE_ASSET_LIMIT = 1000
RELEASE_SAFETY_TARGET = 900


def physical_asset_names(bundle: BundleAsset) -> set[str]:
    """Return every ZIP name that may occupy a release asset slot."""
    return {
        name
        for name in (bundle.asset_name, *bundle.legacy_asset_names)
        if name and name.endswith(".zip")
    }


def validate_release_capacity(
    bundles: list[BundleAsset], *, limit: int = GITHUB_RELEASE_ASSET_LIMIT
) -> None:
    if limit < 1 or limit > GITHUB_RELEASE_ASSET_LIMIT:
        raise ValueError(f"release asset limit must be between 1 and {GITHUB_RELEASE_ASSET_LIMIT}")
    by_tag: dict[str, set[str]] = {}
    for bundle in bundles:
        if not bundle.release_tag:
            continue
        by_tag.setdefault(bundle.release_tag, set()).update(physical_asset_names(bundle))
    over = {tag: len(names) for tag, names in by_tag.items() if len(names) > limit}
    if over:
        details = ", ".join(f"{tag}={count}" for tag, count in sorted(over.items()))
        raise ValueError(f"release asset cap exceeded ({limit}): {details}")


def _is_active_release_tag(release_tag_prefix: str, release_tag: str) -> bool:
    return release_tag.startswith(f"{release_tag_prefix}-")


def assign_release_tags(
    *,
    release_tag_prefix: str,
    existing_bundles: list[BundleAsset],
    bundles: list[BundleAsset],
    max_assets_per_release: int = 900,
) -> list[BundleAsset]:
    if max_assets_per_release < 1 or max_assets_per_release > GITHUB_RELEASE_ASSET_LIMIT:
        raise ValueError(
            f"max_assets_per_release must be between 1 and {GITHUB_RELEASE_ASSET_LIMIT}"
        )

    preserved = {
        bundle.asset_name: bundle.release_tag
        for bundle in existing_bundles
        if bundle.release_tag and _is_active_release_tag(release_tag_prefix, bundle.release_tag)
    }
    ordered = sorted(bundles, key=lambda item: item.asset_name)
    counts: dict[str, int] = defaultdict(int)

    for bundle in ordered:
        release_tag = preserved.get(bundle.asset_name)
        if release_tag:
            counts[release_tag] += len(physical_asset_names(bundle))

    def shard_name(index: int) -> str:
        return f"{release_tag_prefix}-{index:03d}"

    next_shard = 1
    assigned: list[BundleAsset] = []
    for bundle in ordered:
        release_tag = preserved.get(bundle.asset_name)
        if not release_tag:
            asset_count = len(physical_asset_names(bundle))
            if asset_count > max_assets_per_release:
                raise ValueError(
                    f"bundle {bundle.asset_name} has {asset_count} physical "
                    f"assets, exceeding shard target {max_assets_per_release}"
                )
            while counts[shard_name(next_shard)] + asset_count > max_assets_per_release:
                next_shard += 1
            release_tag = shard_name(next_shard)
            counts[release_tag] += asset_count
        assigned.append(replace(bundle, release_tag=release_tag))
    return assigned


def compatibility_alias_conflicts(bundles: list[BundleAsset]) -> list[dict[str, object]]:
    """Return aliases that cannot safely map to more than one logical bundle."""
    owners: dict[str, set[str]] = defaultdict(set)
    primary_owner: dict[str, set[str]] = defaultdict(set)
    for bundle in bundles:
        owner = bundle.bundle_id or bundle.canonical_id or bundle.asset_name
        primary_owner[bundle.asset_name].add(owner)
        for name in bundle.legacy_asset_names:
            owners[name].add(owner)
    conflicts: list[dict[str, object]] = []
    for name, alias_owners in sorted(owners.items()):
        all_owners = alias_owners | primary_owner.get(name, set())
        if len(all_owners) > 1 or name in primary_owner:
            conflicts.append({"asset_name": name, "bundle_ids": sorted(all_owners)})
    return conflicts


def strip_ambiguous_legacy_assets(
    bundles: list[BundleAsset],
) -> tuple[list[BundleAsset], list[dict[str, object]]]:
    """Drop ambiguous aliases from a new projection; v1 assets remain untouched."""
    conflicts = compatibility_alias_conflicts(bundles)
    names = {item["asset_name"] for item in conflicts}
    if not names:
        return list(bundles), conflicts
    return [
        replace(
            bundle,
            legacy_asset_names=[name for name in bundle.legacy_asset_names if name not in names],
        )
        for bundle in bundles
    ], conflicts


def project_release_assets(
    bundles: list[BundleAsset], *, retain_legacy_asset_names: bool
) -> tuple[list[BundleAsset], list[dict[str, object]]]:
    """Apply the site's alias policy before planning physical release capacity."""
    projected, conflicts = strip_ambiguous_legacy_assets(bundles)
    if not retain_legacy_asset_names:
        projected = [replace(bundle, legacy_asset_names=[]) for bundle in projected]
    return projected, conflicts
