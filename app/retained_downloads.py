"""Keep an existing historical ZIP URL independently of current exam grouping.

These are frozen downloads, never aliases for a successor bundle. Site-owned
snapshots bind their exact original bytes; provider history anchors their papers.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from app.models import BundleAsset, NormalizedPaper
from app.paths import SitePaths, provider_paths


@dataclass(frozen=True)
class FrozenPaperSource:
    year_roc: int
    source_exam_id: str
    category_code: str
    subject_code: str
    file_type: str
    download_url_source: str
    checksum: str

    def validate(self) -> None:
        if (
            type(self.year_roc) is not int
            or self.year_roc < 0
            or any(not isinstance(v, str) for k, v in vars(self).items() if k != "year_roc")
            or re.fullmatch(r"[0-9a-f]{64}", self.checksum) is None
        ):
            raise ValueError("Invalid frozen archive source context or checksum")


@dataclass(frozen=True)
class FrozenSourceSnapshot:
    archive_checksum: str
    canonical_name: str
    papers: tuple[FrozenPaperSource, ...]

    def validate(self, archive_checksum: str) -> None:
        if (
            self.archive_checksum != archive_checksum
            or not isinstance(self.canonical_name, str)
            or not self.canonical_name.strip()
            or not isinstance(self.papers, tuple)
            or not self.papers
            or any(not isinstance(paper, FrozenPaperSource) for paper in self.papers)
        ):
            raise ValueError("Frozen source snapshot differs from its archive binding")
        for paper in self.papers:
            paper.validate()


@dataclass(frozen=True)
class RetainedDownload:
    provider_id: str
    bundle_id: str
    release_tag: str
    asset_name: str
    storage_key: str
    checksum: str
    retained_at: str
    source_snapshot: FrozenSourceSnapshot | None = None

    def validate(self, site_id: str) -> None:
        from app.site_registry import get_site_config

        if any(
            not isinstance(value, str)
            for key, value in vars(self).items()
            if key != "source_snapshot"
        ):
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
        if self.source_snapshot is not None:
            if not isinstance(self.source_snapshot, FrozenSourceSnapshot):
                raise ValueError("Invalid frozen source snapshot")
            self.source_snapshot.validate(self.checksum)


def retained_release_record(download: RetainedDownload) -> dict[str, Any]:
    """Keep provenance in site state, independently of the compact release contract."""
    record = asdict(download)
    record.pop("source_snapshot")
    return record


def _download_from_plain(row: dict[str, Any]) -> RetainedDownload:
    fields = dict(row)
    snapshot = fields.get("source_snapshot")
    if snapshot is not None:
        fields["source_snapshot"] = FrozenSourceSnapshot(
            **{**snapshot, "papers": tuple(FrozenPaperSource(**p) for p in snapshot["papers"])}
        )
    return RetainedDownload(**fields)


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
        or document["schema_version"] not in {1, 2}
        or document["site_id"] != site.site_id
        or not isinstance(document["downloads"], list)
        or not document["downloads"]
    ):
        raise ValueError("Unsupported retained download inventory or site ownership")
    version = document["schema_version"]
    if any(
        not isinstance(row, dict)
        or ("source_snapshot" in row) != (version == 2)
        or version == 2
        and not isinstance(row["source_snapshot"], dict)
        for row in document["downloads"]
    ):
        raise ValueError("Frozen source snapshots require retained download inventory v2")
    try:
        downloads = [_download_from_plain(row) for row in document["downloads"]]
    except (KeyError, TypeError) as exc:
        raise ValueError("Invalid retained download or frozen source snapshot fields") from exc
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
    unchanged = all(
        by_name[download.asset_name] == download
        or download.source_snapshot is None
        and replace(by_name[download.asset_name], source_snapshot=None) == download
        for download in previous
        if download.asset_name in by_name
    )
    if removed != allowed_removals or not unchanged:
        raise ValueError("Publication cannot remove or rewrite a frozen retained download")
    if not downloads:
        if previous:
            path.unlink()
        return
    for download in downloads:
        download.validate(site.site_id)
    previous_by_name = {download.asset_name: download for download in previous}
    verified = []
    for download in downloads:
        earlier = previous_by_name.get(download.asset_name)
        if earlier is None or earlier.source_snapshot is None:
            # Adding provenance to legacy metadata is a byte-verified migration,
            # never permission to rewrite a frozen URL, checksum or source list.
            captured = capture_frozen_sources(site.data_dir.parents[2], site, download)
            if download.source_snapshot is not None and captured != download:
                raise ValueError("Frozen source snapshot differs from the verified native archive")
            download = captured
        verified.append(download)
    validate_retained_source_snapshots(site.data_dir.parents[2], site, verified)
    site.data_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "site_id": site.site_id,
                "downloads": [asdict(download) for download in verified],
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


def _validate_retained_owner(repo_root: Path, site: SitePaths, download: RetainedDownload) -> None:
    from app.publication_quarantine import quarantined_provider_ids
    from app.site_registry import get_site_config

    if download.provider_id not in get_site_config(site.site_id).provider_ids:
        raise ValueError("Retained download has no current site provider owner")
    if download.provider_id in quarantined_provider_ids(repo_root, site_id=site.site_id):
        raise ValueError("Retained download belongs to a quarantined provider")


def _frozen_sources(manifest: dict[str, Any], archive_checksum: str) -> FrozenSourceSnapshot:
    from app.bundler import _paper_bundle_key

    snapshot = FrozenSourceSnapshot(
        archive_checksum,
        manifest["canonical_name"],
        tuple(FrozenPaperSource(*_paper_bundle_key(p), p["checksum"]) for p in manifest["papers"]),
    )
    snapshot.validate(archive_checksum)
    return snapshot


def capture_frozen_sources(
    repo_root: Path, site: SitePaths, download: RetainedDownload
) -> RetainedDownload:
    """Bind navigation provenance only after checking original archive bytes."""
    manifest = verify_retained_download(repo_root, site, download, verify_content=True)
    return replace(download, source_snapshot=_frozen_sources(manifest, download.checksum))


def validate_retained_source_snapshots(
    repo_root: Path,
    site: SitePaths,
    downloads: list[RetainedDownload] | None = None,
) -> int:
    """Verify frozen metadata and history anchors; this does not audit ZIP bytes."""
    from app.bundler import _paper_bundle_key

    if downloads is None:
        downloads = load_retained_downloads(site)
    owners = {d.provider_id for d in downloads if d.source_snapshot is not None}
    anchors = {owner: _source_anchors(repo_root, owner) for owner in owners}
    count = 0
    for download in downloads:
        download.validate(site.site_id)
        _validate_retained_owner(repo_root, site, download)
        if download.source_snapshot is None:
            continue  # Legacy provenance still requires its physical archive.
        for paper in download.source_snapshot.papers:
            if (*_paper_bundle_key(asdict(paper)), paper.checksum) not in anchors[
                download.provider_id
            ]:
                raise ValueError(
                    "Frozen source snapshot lacks a current or immutable history anchor"
                )
        count += 1
    return count


def verify_retained_download(
    repo_root: Path,
    site: SitePaths,
    download: RetainedDownload,
    *,
    verify_content: bool,
) -> dict[str, Any]:
    from app.archive_audit import inspect_archive
    from app.bundler import _paper_bundle_key

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
    _validate_retained_owner(repo_root, site, download)
    anchors = _source_anchors(repo_root, download.provider_id)
    for paper in manifest["papers"]:
        if (*_paper_bundle_key(paper), paper["checksum"]) not in anchors:
            raise ValueError("Retained archive paper lacks a current or immutable history anchor")
    if download.source_snapshot is not None and (
        download.source_snapshot != _frozen_sources(manifest, download.checksum)
    ):
        raise ValueError("Frozen source snapshot differs from the verified native archive")
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
                previous.source_snapshot,
            )
            if candidate != previous:
                raise ValueError("An existing retained download cannot change its original bytes")
        by_name[candidate.asset_name] = capture_frozen_sources(repo_root, site, candidate)
    captured = []
    for download in by_name.values():
        if download.source_snapshot is None:
            try:
                download = capture_frozen_sources(repo_root, site, download)
            except FileNotFoundError as exc:
                raise ValueError(
                    "Legacy retained download needs its verified ZIP for source provenance: "
                    + download.asset_name
                ) from exc
        captured.append(download)
    return sorted(captured, key=lambda download: download.asset_name)


def _reviewed_source_history(
    repo_root: Path,
    providers: set[str],
    current: dict[tuple[Any, ...], list[NormalizedPaper]],
) -> dict[tuple[Any, ...], list[NormalizedPaper]]:
    """Follow exact reviewed role or category changes on immutable source history."""
    from app.bundler import _paper_bundle_key
    from app.models import NormalizedCatalog
    from app.normalizer import load_alias_rules, renormalize_catalog
    from app.source_revisions import load_source_revisions

    aliases: dict[tuple[Any, ...], list[NormalizedPaper]] = defaultdict(list)
    for provider_id in sorted(providers):
        provider = provider_paths(repo_root, provider_id)
        rules = load_alias_rules(provider.aliases_path)
        for entry in load_source_revisions(provider):
            if entry["record_type"] != "paper":
                continue
            previous = NormalizedPaper(**entry["source_record"])
            old_key = (provider_id, *_paper_bundle_key(previous), previous.checksum)
            if old_key in current:
                continue
            if provider_id == "teacher_recruit_taoyuan_elementary":
                from app.providers.teacher_recruit_taoyuan_elementary.evidence import (
                    legacy_category_targets,
                )

                candidates = (paper for rows in current.values() for paper in rows)
                aliases[old_key].extend(legacy_category_targets(previous, candidates))
                continue
            if provider_id == "teacher_recruit_newtaipei":
                from app.providers.teacher_recruit_newtaipei.evidence import (
                    legacy_category_targets as newtaipei_targets,
                )

                candidates = (paper for rows in current.values() for paper in rows)
                aliases[old_key].extend(newtaipei_targets(previous, candidates))
                continue
            if provider_id == "teacher_recruit_taipei_junior":
                from app.providers.teacher_recruit_taipei_junior.evidence import (
                    legacy_category_targets as taipei_junior_targets,
                )

                candidates = (paper for rows in current.values() for paper in rows)
                aliases[old_key].extend(taipei_junior_targets(previous, candidates))
                continue
            if provider_id == "teacher_recruit_taipei_elementary":
                from app.providers.teacher_recruit_taipei_elementary.evidence import (
                    legacy_category_targets as taipei_elementary_targets,
                )

                candidates = (paper for rows in current.values() for paper in rows)
                aliases[old_key].extend(taipei_elementary_targets(previous, candidates))
                continue
            if provider_id == "teacher_recruit_kaohsiung":
                from app.providers.teacher_recruit_kaohsiung.evidence import (
                    legacy_category_targets as kaohsiung_targets,
                )

                candidates = (paper for rows in current.values() for paper in rows)
                aliases[old_key].extend(kaohsiung_targets(previous, candidates))
                continue
            if provider_id == "taipower_recruit":
                from app.providers.taipower_recruit.evidence import (
                    legacy_category_targets as taipower_targets,
                )

                candidates = (paper for rows in current.values() for paper in rows)
                aliases[old_key].extend(taipower_targets(previous, candidates))
                continue
            normalized = renormalize_catalog(NormalizedCatalog([previous], []), rules).papers[0]
            if (
                normalized.file_type == previous.file_type
                or normalized.classification_confidence != "high"
                or normalized.checksum != previous.checksum
            ):
                continue
            new_key = (provider_id, *_paper_bundle_key(normalized), normalized.checksum)
            # A role change is the only permissible source-key difference.
            if old_key[:5] != new_key[:5] or old_key[6:] != new_key[6:]:
                continue
            for target in current.get(new_key, []):
                if (
                    target.bundle_id == normalized.bundle_id
                    and target.classification_confidence == "high"
                    and target.exam_name_raw == normalized.exam_name_raw
                    and target.category_raw == normalized.category_raw
                    and target.subject_name_raw == normalized.subject_name_raw
                    and target.source_material == normalized.source_material
                ):
                    aliases[old_key].append(target)
    return aliases


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

    validate_retained_source_snapshots(repo_root, site, downloads)
    public = {bundle.bundle_id for bundle in bundles}
    owners = {download.provider_id for download in downloads}
    by_source: dict[tuple[Any, ...], list[NormalizedPaper]] = defaultdict(list)
    for paper in papers:
        if paper.provider_id in owners:
            by_source[(paper.provider_id, *_paper_bundle_key(paper), paper.checksum)].append(paper)
    for key, targets in _reviewed_source_history(repo_root, owners, by_source).items():
        if targets:
            by_source[key].extend(targets)
    pages = []
    for download in downloads:
        if download.source_snapshot is None:
            manifest = verify_retained_download(repo_root, site, download, verify_content=False)
            canonical_name = manifest["canonical_name"]
            source_papers = manifest["papers"]
        else:
            canonical_name = download.source_snapshot.canonical_name
            source_papers = [asdict(paper) for paper in download.source_snapshot.papers]
        sources = [
            (download.provider_id, *_paper_bundle_key(paper), paper["checksum"])
            for paper in source_papers
        ]
        if not sources or any(key not in by_source for key in sources):
            continue
        successors = {paper.bundle_id for key in sources for paper in by_source[key]}
        available = successors & public
        if not available:
            continue
        page: dict[str, Any] = {
            "id": download.bundle_id,
            "name": canonical_name,
            "successorIds": sorted(available),
        }
        if successors - public:
            page["partial"] = True
        pages.append(page)
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
