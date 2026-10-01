"""Local archive integrity and source-conservation checks; never mutates releases."""

from __future__ import annotations

import hashlib
import json
import re
import stat
import unicodedata
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.bundler import WINDOWS_RESERVED_NAMES, PaperKey, _paper_bundle_key
from app.mirror_repair import colliding_mirror_records
from app.paths import site_paths
from app.publisher import load_site_catalog
from app.state import load_site_bundles


def portable_archive_name(name: str) -> bool:
    parts = name.split("/")
    return all(
        part not in {"", ".", ".."}
        and not any(char in '\\:*?"<>|' or ord(char) < 32 for char in part)
        and not part.endswith((" ", "."))
        and part.split(".", 1)[0].upper() not in WINDOWS_RESERVED_NAMES
        and len(part.encode("utf-8")) <= 255
        for part in parts
    )


def inspect_archive(path: Path, *, verify_content: bool) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        folded = [unicodedata.normalize("NFC", name).casefold() for name in names]
        if len(set(folded)) != len(names) or not all(map(portable_archive_name, names)):
            raise ValueError("unsafe, nonportable, or duplicate ZIP entry paths")
        if any(stat.S_ISLNK(info.external_attr >> 16) for info in infos):
            raise ValueError("ZIP contains symbolic links")
        manifest: dict[str, Any] = json.loads(archive.read("bundle.json"))
        if not isinstance(manifest, dict):
            raise ValueError("archive manifest must be an object")
        version = manifest.get("manifest_version", 1)
        if type(version) is not int or version not in (1, 2, 3):
            raise ValueError("unsupported archive manifest version")
        papers = manifest["papers"]
        if not isinstance(papers, list) or any(not isinstance(paper, dict) for paper in papers):
            raise ValueError("archive papers must be source record objects")
        entries: dict[str, str] = {}
        keys: set[PaperKey] = set()
        for paper in papers:
            key = _paper_bundle_key(paper)
            if key in keys and version == 3:
                raise ValueError("duplicate source paper key")
            keys.add(key)
            name, checksum = paper["bundle_entry"], paper["checksum"]
            if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
                raise ValueError("paper has no valid SHA-256 checksum")
            if not isinstance(name, str):
                raise ValueError("paper has no valid bundle_entry")
            if name in entries and entries[name] != checksum:
                raise ValueError("shared ZIP entry has conflicting checksums")
            entries[name] = checksum
        if set(names) != {*entries, "bundle.json"}:
            raise ValueError("ZIP entries differ from manifest")
        if manifest["file_count"] != len(entries):
            raise ValueError("manifest file_count differs from physical file count")
        if verify_content:
            for name, checksum in entries.items():
                with archive.open(name) as source:
                    digest = hashlib.sha256()
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(block)
                    actual = digest.hexdigest()
                if actual != checksum:
                    raise ValueError(f"payload checksum mismatch: {name}")
        return manifest


def audit_site_archives(
    repo_root: Path,
    *,
    site_id: str,
    verify_content: bool = False,
    release_tag: str | None = None,
) -> dict[str, Any]:
    site = site_paths(repo_root, site_id)
    all_bundles = load_site_bundles(site)
    if not all_bundles:
        raise ValueError("Cannot audit an empty publication inventory")
    bundles = [
        bundle for bundle in all_bundles if release_tag is None or bundle.release_tag == release_tag
    ]
    if not bundles:
        raise ValueError(f"No archives are assigned to release {release_tag}")
    outside = {
        bundle.bundle_id or bundle.canonical_id
        for bundle in all_bundles
        if release_tag is not None and bundle.release_tag != release_tag
    }
    catalog, _ = load_site_catalog(repo_root, site_id=site_id)
    expected: dict[str, dict[PaperKey, str]] = defaultdict(dict)
    for paper in catalog.papers:
        expected[paper.bundle_id or paper.canonical_id][_paper_bundle_key(paper)] = paper.checksum
    observed: dict[str, dict[PaperKey, str]] = defaultdict(dict)
    collisions = colliding_mirror_records(catalog.papers)
    errors: list[dict[str, str]] = [
        {
            "asset": group[0].storage_key,
            "error": "multiple source URLs share a mirror locator; run repair-mirror-collisions",
        }
        for group in collisions
    ]
    file_count = source_count = 0
    for bundle in bundles:
        key = bundle.bundle_id or bundle.canonical_id
        path = site.bundle_dir / bundle.asset_name
        try:
            if path.is_symlink() or path.name != bundle.asset_name:
                raise ValueError("invalid archive asset path")
            manifest = inspect_archive(path, verify_content=verify_content)
            for field, value in (
                ("bundle_id", key),
                ("canonical_name", bundle.canonical_name),
                ("file_count", bundle.file_count),
                ("years", bundle.years),
                ("part_index", bundle.part_index),
                ("part_count", bundle.part_count),
            ):
                if manifest.get(field, 1 if field.startswith("part_") else None) != value:
                    raise ValueError(f"archive {field} differs from site inventory")
            for paper in manifest["papers"]:
                paper_key = _paper_bundle_key(paper)
                if paper_key in observed[key]:
                    raise ValueError("source paper repeated across multipart archives")
                observed[key][paper_key] = paper["checksum"]
            if verify_content:
                with path.open("rb") as source:
                    checksum = hashlib.file_digest(source, "sha256").hexdigest()
                if checksum != bundle.checksum:
                    raise ValueError("archive checksum differs from site inventory")
            file_count += manifest["file_count"]
            source_count += len(manifest["papers"])
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
            errors.append({"asset": bundle.asset_name, "error": str(exc)})
    for key in {bundle.bundle_id or bundle.canonical_id for bundle in bundles}:
        # A multipart group may span shards. Every observed record must still
        # match the catalog; only completeness waits for all parts.
        if any(
            expected[key].get(paper_key) != checksum
            for paper_key, checksum in observed[key].items()
        ) or (key not in outside and observed[key] != expected[key]):
            errors.append({"asset": key, "error": "source records/checksums differ from catalog"})
    active = {bundle.asset_name for bundle in bundles}
    active.update(name for bundle in bundles for name in bundle.legacy_asset_names)
    extras = (
        []
        if release_tag is not None
        else sorted(path.name for path in site.bundle_dir.glob("*.zip") if path.name not in active)
    )
    return {
        "site_id": site_id,
        "release_tag": release_tag,
        "verified_content": verify_content,
        "archive_count": len(bundles),
        "file_count": file_count,
        "source_record_count": source_count,
        "mirror_collision_count": len(collisions),
        "errors": errors,
        "unreferenced_archives": extras,
    }


def prune_redundant_archives(repo_root: Path, *, site_id: str, report: dict[str, Any]) -> None:
    """Remove only verified extras whose complete source records remain active."""
    if (
        report["site_id"] != site_id
        or report.get("release_tag") is not None
        or not report["verified_content"]
        or report["errors"]
    ):
        raise ValueError("Cleanup requires a successful full content audit of this site")
    site = site_paths(repo_root, site_id)
    bundles = load_site_bundles(site)
    retained: set[tuple[PaperKey, str]] = set()
    for bundle in bundles:
        manifest = inspect_archive(site.bundle_dir / bundle.asset_name, verify_content=False)
        retained.update(
            (_paper_bundle_key(paper), paper["checksum"]) for paper in manifest["papers"]
        )
    removed: list[str] = []
    preserved: list[dict[str, str]] = []
    for name in report["unreferenced_archives"]:
        path = site.bundle_dir / name
        try:
            if path.name != name or path.is_symlink():
                raise ValueError("not a regular site-owned archive")
            manifest = inspect_archive(path, verify_content=False)
            records = {
                (_paper_bundle_key(paper), paper["checksum"]) for paper in manifest["papers"]
            }
            if manifest.get("manifest_version", 1) < 3:
                covered = {(key[:-1], checksum) for key, checksum in retained}
                redundant = all((key[:-1], checksum) in covered for key, checksum in records)
            else:
                redundant = records <= retained
            if not records or not redundant:
                raise ValueError("contains source records not covered by active archives")
            # Check coverage before the expensive read, but never delete a
            # candidate until every one of its payloads has been verified.
            inspect_archive(path, verify_content=True)
            path.unlink()
            removed.append(name)
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
            preserved.append({"asset": name, "reason": str(exc)})
    report["removed_redundant_archives"] = removed
    report["preserved_unreferenced_archives"] = preserved
    report["unreferenced_archives"] = [row["asset"] for row in preserved]


def isolate_unreferenced_archives(repo_root: Path, *, site_id: str, report: dict[str, Any]) -> None:
    """Move obsolete or suspect archives out of the builder's active input set."""
    if (
        report["site_id"] != site_id
        or report.get("release_tag") is not None
        or not report["verified_content"]
        or report["errors"]
    ):
        raise ValueError("Isolation requires a successful full content audit of this site")
    site = site_paths(repo_root, site_id)
    recovery = site.bundle_dir / "recovery"
    if recovery.is_symlink():
        raise ValueError("Recovery directory must not be a symbolic link")
    moves = []
    for name in report["unreferenced_archives"]:
        source, destination = site.bundle_dir / name, recovery / name
        if source.name != name or source.is_symlink() or not source.is_file():
            raise ValueError(f"Not a regular site-owned archive: {name}")
        if destination.exists():
            raise ValueError(f"Recovery archive already exists; refusing to overwrite: {name}")
        moves.append((source, destination))
    recovery.mkdir(parents=True, exist_ok=True)
    isolated = []
    for source, destination in moves:
        source.rename(destination)
        isolated.append(
            {"asset": source.name, "storage_key": str(destination.relative_to(repo_root))}
        )
    report["isolated_archives"] = isolated
    report["unreferenced_archives"] = []
