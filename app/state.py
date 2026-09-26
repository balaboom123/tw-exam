from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from app.models import (
    BundleAsset,
    ExamAttachment,
    NormalizedCatalog,
    NormalizedPaper,
    ParsedPaper,
    ReviewItem,
    SourceExamPage,
    SyncFailure,
)
from app.paths import ProviderPaths, SitePaths


def load_existing_state(
    data_dir: Path,
) -> tuple[list[SourceExamPage], NormalizedCatalog, list[BundleAsset], list[SyncFailure]]:
    if not data_dir.exists():
        return [], NormalizedCatalog(papers=[], review_queue=[]), [], []
    return (
        _load_raw_pages_dir(data_dir / "exams"),
        _load_catalog_dir(data_dir / "papers", data_dir / "review-queue.json"),
        _load_bundles(data_dir / "bundles.json"),
        _load_failures(data_dir / "sync-failures.json"),
    )


def load_provider_state(
    provider: ProviderPaths,
) -> tuple[list[SourceExamPage], NormalizedCatalog, list[SyncFailure]]:
    if not provider.data_dir.exists():
        return [], NormalizedCatalog(papers=[], review_queue=[]), []
    return (
        _load_raw_pages_dir(provider.exams_dir, provider_id=provider.provider_id),
        _load_catalog_dir(
            provider.papers_dir, provider.review_queue_path, provider_id=provider.provider_id
        ),
        load_provider_failures(provider),
    )


def load_provider_failures(provider: ProviderPaths) -> list[SyncFailure]:
    """Read the small failure ledger without loading retained event and paper files."""
    return _load_failures(provider.sync_failures_path)


def load_site_bundles(site: SitePaths) -> list[BundleAsset]:
    if not site.bundles_path.exists():
        return []
    payload = json.loads(site.bundles_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        schema_version = payload.get("schema_version")
        if schema_version is not None and schema_version not in {1, 2}:
            raise ValueError(f"Unsupported site bundles schema_version: {schema_version}")
        if schema_version == 2 and payload.get("catalog_version") != "exam-identity-v2":
            # A v2 wrapper must identify the taxonomy version.
            raise ValueError("Unsupported site bundles schema_version: missing catalog_version")
        payload_site_id = payload.get("site_id")
        if payload_site_id is not None and payload_site_id != site.site_id:
            raise ValueError(
                f"Site bundles site_id mismatch: expected {site.site_id}, got {payload_site_id}"
            )
    bundles = payload.get("bundles", payload)
    return [BundleAsset(**bundle) for bundle in bundles]


def _paper_publication_key(paper: NormalizedPaper) -> str:
    """Return the key used to select a paper's publication group.

    Legacy records use canonical_id as their bundle key. Versioned
    records retain that field for URL/migration compatibility, but their
    publication grouping is the identity-derived bundle_id.
    """
    return paper.bundle_id or paper.canonical_id


def _bundle_key(bundle: BundleAsset) -> str:
    return bundle.bundle_id or bundle.canonical_id


def _parsed_paper_key(paper: ParsedPaper) -> tuple[str, str]:
    return paper.category_code, paper.subject_code


def _normalized_paper_key(paper: NormalizedPaper) -> tuple[str, str]:
    # ParsedPaper owns one source-paper identity per category/subject pair and
    # carries its file roles inside ``files``. Match that same identity here:
    # including file_type would resurrect an obsolete normalized role after a
    # parser correction even though the refreshed raw page replaced it.
    return paper.category_code, paper.subject_code


def _retain_delisted_papers(
    existing_raw_pages: list[SourceExamPage],
    refreshed_raw_pages: list[SourceExamPage],
) -> list[SourceExamPage]:
    """Carry forward papers a still-listed event has stopped offering.

    daddce4 stopped a source dropping a whole event from deleting its papers,
    but left the case where the event survives and only some of its papers
    disappear. CPC re-scoped its 2009/2012/2019 archive pages to doctoral entries
    on 2026-08-08 and the three delisted files still returned HTTP 200, so a
    plain replace would have destroyed records whose bytes were still being
    served. Only the source-floor guard caught it.

    This runs solely over events this sync actually refreshed, so the explicit
    removal path in merge_targeted_state keeps deleting what it is told to.
    """
    existing_by_exam = {page.source_exam_id: page for page in existing_raw_pages}
    merged = []
    for page in refreshed_raw_pages:
        previous = existing_by_exam.get(page.source_exam_id)
        if previous is None:
            merged.append(page)
            continue
        refreshed_keys = {_parsed_paper_key(paper) for paper in page.papers}
        delisted = [
            paper for paper in previous.papers if _parsed_paper_key(paper) not in refreshed_keys
        ]
        merged.append(replace(page, papers=[*page.papers, *delisted]) if delisted else page)
    return merged


def _merge_state(
    existing_raw_pages: list[SourceExamPage],
    existing_catalog: NormalizedCatalog,
    existing_bundles: list[BundleAsset],
    refreshed_raw_pages: list[SourceExamPage],
    refreshed_catalog: NormalizedCatalog,
    replaced_exam_ids: set[str],
) -> tuple[
    list[SourceExamPage], NormalizedCatalog, list[BundleAsset], set[str], dict[str, list[str]]
]:
    merged_raw_pages = [
        page for page in existing_raw_pages if page.source_exam_id not in replaced_exam_ids
    ] + _retain_delisted_papers(existing_raw_pages, refreshed_raw_pages)
    # The normalized side has to retain exactly what the raw side did, or the
    # catalog and the pages it is derived from disagree and the retained file
    # loses the reference that keeps --prune-orphaned-mirror away from it.
    refreshed_exam_ids = {page.source_exam_id for page in refreshed_raw_pages}
    refreshed_paper_keys = {
        (paper.source_exam_id, _normalized_paper_key(paper)) for paper in refreshed_catalog.papers
    }
    delisted_papers = [
        paper
        for paper in existing_catalog.papers
        if paper.source_exam_id in refreshed_exam_ids
        and (paper.source_exam_id, _normalized_paper_key(paper)) not in refreshed_paper_keys
    ]
    merged_papers = (
        [
            paper
            for paper in existing_catalog.papers
            if paper.source_exam_id not in replaced_exam_ids
        ]
        + refreshed_catalog.papers
        + delisted_papers
    )
    merged_review_queue = [
        item
        for item in existing_catalog.review_queue
        if item.source_exam_id not in replaced_exam_ids
    ] + refreshed_catalog.review_queue
    migrations = _derive_canonical_migrations(
        existing_catalog, refreshed_catalog, replaced_exam_ids
    )
    merged_catalog = _apply_canonical_migrations(
        NormalizedCatalog(papers=merged_papers, review_queue=merged_review_queue),
        migrations,
    )

    affected_canonical_ids = {
        _paper_publication_key(paper)
        for paper in existing_catalog.papers
        if paper.source_exam_id in replaced_exam_ids
    }
    affected_canonical_ids.update(
        _paper_publication_key(paper) for paper in refreshed_catalog.papers
    )
    # v1 state has no identity-derived publication key, so canonical
    # migrations must still invalidate both sides of the legacy group. In
    # v2, canonical_id is deliberately not a publication key: the same
    # legacy subject can appear in several independent exam identities.
    if not any(paper.bundle_id for paper in merged_catalog.papers):
        affected_canonical_ids.update(migrations)
        affected_canonical_ids.update(migration[0] for migration in migrations.values())
    active_canonical_ids = {paper.canonical_id for paper in merged_catalog.papers}
    active_publication_keys = {_paper_publication_key(paper) for paper in merged_catalog.papers}
    preserved_bundles = [
        bundle
        for bundle in existing_bundles
        if (
            (
                _bundle_key(bundle) in active_publication_keys
                or bundle.canonical_id in active_canonical_ids
            )
            and _bundle_key(bundle) not in affected_canonical_ids
            and bundle.canonical_id not in affected_canonical_ids
        )
    ]
    return (
        merged_raw_pages,
        merged_catalog,
        preserved_bundles,
        affected_canonical_ids,
        _canonical_aliases_from_migrations(migrations),
    )


def merge_incremental_state(
    existing_raw_pages: list[SourceExamPage],
    existing_catalog: NormalizedCatalog,
    existing_bundles: list[BundleAsset],
    refreshed_raw_pages: list[SourceExamPage],
    refreshed_catalog: NormalizedCatalog,
) -> tuple[
    list[SourceExamPage], NormalizedCatalog, list[BundleAsset], set[str], dict[str, list[str]]
]:
    replaced_exam_ids = {page.source_exam_id for page in refreshed_raw_pages}
    return _merge_state(
        existing_raw_pages,
        existing_catalog,
        existing_bundles,
        refreshed_raw_pages,
        refreshed_catalog,
        replaced_exam_ids,
    )


def merge_targeted_state(
    existing_raw_pages: list[SourceExamPage],
    existing_catalog: NormalizedCatalog,
    existing_bundles: list[BundleAsset],
    refreshed_raw_pages: list[SourceExamPage],
    refreshed_catalog: NormalizedCatalog,
    removed_exam_ids: set[str],
) -> tuple[
    list[SourceExamPage], NormalizedCatalog, list[BundleAsset], set[str], dict[str, list[str]]
]:
    refreshed_exam_ids = {page.source_exam_id for page in refreshed_raw_pages}
    replaced_exam_ids = refreshed_exam_ids | removed_exam_ids
    return _merge_state(
        existing_raw_pages,
        existing_catalog,
        existing_bundles,
        refreshed_raw_pages,
        refreshed_catalog,
        replaced_exam_ids,
    )


def filter_catalog_by_canonical_ids(
    catalog: NormalizedCatalog, canonical_ids: set[str]
) -> NormalizedCatalog:
    def matches(paper: NormalizedPaper) -> bool:
        return paper.canonical_id in canonical_ids or bool(
            paper.bundle_id and paper.bundle_id in canonical_ids
        )

    return NormalizedCatalog(
        papers=[paper for paper in catalog.papers if matches(paper)],
        review_queue=[
            item
            for item in catalog.review_queue
            if item.normalized_candidate in canonical_ids
            or item.raw_category in canonical_ids
            or item.bundle_id in canonical_ids
        ],
    )


def _paper_family_key(paper: NormalizedPaper) -> tuple[str, str]:
    family = paper.category_code or paper.category_raw or paper.canonical_name
    return paper.source_exam_id, family


def _derive_canonical_migrations(
    existing_catalog: NormalizedCatalog,
    refreshed_catalog: NormalizedCatalog,
    replaced_exam_ids: set[str],
) -> dict[str, tuple[str, str]]:
    existing_by_family: dict[tuple[str, str], set[tuple[str, str]]] = {}
    refreshed_by_family: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for paper in existing_catalog.papers:
        if paper.source_exam_id in replaced_exam_ids:
            existing_by_family.setdefault(_paper_family_key(paper), set()).add(
                (paper.canonical_id, paper.canonical_name)
            )
    for paper in refreshed_catalog.papers:
        if paper.source_exam_id in replaced_exam_ids:
            refreshed_by_family.setdefault(_paper_family_key(paper), set()).add(
                (paper.canonical_id, paper.canonical_name)
            )

    migrations: dict[str, tuple[str, str]] = {}
    conflicts: set[str] = set()
    for family_key, refreshed_targets in refreshed_by_family.items():
        existing_targets = existing_by_family.get(family_key)
        if not existing_targets or len(existing_targets) != 1 or len(refreshed_targets) != 1:
            continue
        old_id, _old_name = next(iter(existing_targets))
        new_id, new_name = next(iter(refreshed_targets))
        if old_id == new_id:
            continue
        previous = migrations.get(old_id)
        if previous is None or previous == (new_id, new_name):
            migrations[old_id] = (new_id, new_name)
            continue
        conflicts.add(old_id)
    for old_id in conflicts:
        migrations.pop(old_id, None)
    return migrations


def _apply_canonical_migrations(
    catalog: NormalizedCatalog,
    migrations: dict[str, tuple[str, str]],
) -> NormalizedCatalog:
    if not migrations:
        return catalog
    migrated_papers = []
    for paper in catalog.papers:
        migration = migrations.get(paper.canonical_id)
        if migration is None:
            migrated_papers.append(paper)
            continue
        migrated_papers.append(
            replace(paper, canonical_id=migration[0], canonical_name=migration[1])
        )
    return NormalizedCatalog(papers=migrated_papers, review_queue=catalog.review_queue)


def _canonical_aliases_from_migrations(
    migrations: dict[str, tuple[str, str]],
) -> dict[str, list[str]]:
    aliases: dict[str, list[str]] = {}
    for old_id, (new_id, _new_name) in sorted(migrations.items()):
        aliases.setdefault(new_id, []).append(old_id)
    for alias_ids in aliases.values():
        alias_ids.sort()
    return aliases


def _load_json(path: Path) -> dict[str, Any] | list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, (dict, list)):
        raise ValueError(f"Expected a JSON object or record list: {path}")
    return cast(dict[str, Any] | list[dict[str, Any]], payload)


def _load_json_records(path: Path) -> list[dict[str, Any]]:
    payload = _load_json(path)
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise ValueError(f"Expected a JSON record list: {path}")
    return payload


def _load_json_dir(directory: Path) -> list[dict[str, Any]]:
    if not directory.exists():
        return []
    items: list[dict[str, Any]] = []
    for f in sorted(directory.glob("*.json")):
        items.extend(_load_json_records(f))
    return items


def _load_raw_pages_dir(exams_dir: Path, provider_id: str = "") -> list[SourceExamPage]:
    pages = []
    for item in _load_json_dir(exams_dir):
        pages.append(
            SourceExamPage(
                provider_id=item.get("provider_id", provider_id),
                source_exam_id=item["source_exam_id"],
                year_ad=item["year_ad"],
                year_roc=item["year_roc"],
                exam_name_raw=item["exam_name_raw"],
                attachments=[
                    ExamAttachment(**attachment) for attachment in item.get("attachments", [])
                ],
                papers=[
                    ParsedPaper(
                        category_raw=paper["category_raw"],
                        category_code=paper["category_code"],
                        subject_code=paper["subject_code"],
                        subject_name_raw=paper["subject_name_raw"],
                        files=paper.get("files", {}),
                        mirror_files=paper.get("mirror_files", {}),
                    )
                    for paper in item.get("papers", [])
                ],
            )
        )
    return pages


def _load_catalog_dir(
    papers_dir: Path, review_queue_path: Path, provider_id: str = ""
) -> NormalizedCatalog:
    papers = [
        NormalizedPaper(
            provider_id=paper.get("provider_id", provider_id),
            **{k: v for k, v in paper.items() if k != "provider_id"},
        )
        for paper in _load_json_dir(papers_dir)
    ]
    review_queue = [
        ReviewItem(
            provider_id=item.get("provider_id") or provider_id,
            **{key: value for key, value in item.items() if key != "provider_id"},
        )
        for item in _load_json_records(review_queue_path)
    ]
    return NormalizedCatalog(papers=papers, review_queue=review_queue)


def _load_bundles(path: Path) -> list[BundleAsset]:
    payload = _load_json(path)
    bundles = payload.get("bundles", payload) if isinstance(payload, dict) else payload
    return [BundleAsset(**bundle) for bundle in bundles]


def _load_failures(path: Path) -> list[SyncFailure]:
    return [SyncFailure(**failure) for failure in _load_json_records(path)]
