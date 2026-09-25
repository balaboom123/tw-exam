from __future__ import annotations

import hashlib
import json
import re
import shutil
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from app.normalizer import hashed_fallback_canonical_id, legacy_fallback_canonical_id
from app.models import BundleAsset, BundleBuildResult, NormalizedCatalog, NormalizedPaper, SyncFailure, file_type_label, to_plain_data
from app.publication_metadata import derive_public_metadata
from app.provider_index import (
    PAPER_LEGACY_CANDIDATE,
    PAPER_YEAR_ROC,
    paper_index_bundle_id,
    paper_index_canonical_id,
)

WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}

# GitHub rejects release assets at 2 GiB or larger. Keep a generous margin
# because the API limit is strict and ZIP metadata adds a small amount.
MAX_BUNDLE_BYTES = 1_900_000_000
BUNDLE_PART_OVERHEAD = 4096

# Bundle archives are content-addressed by the publication pipeline: the
# checksum recorded in the site catalog is what users verify a download
# against, and the release upload compares it to decide what to re-publish.
# ZIP entries default to the wall-clock time of the build and to the source
# file's permission bits, so an unchanged bundle used to hash differently on
# every rebuild and to differ between a workstation (umask 002) and CI
# (umask 022). Pin both so archive bytes depend only on entry names, order,
# and content.
BUNDLE_ENTRY_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
BUNDLE_ENTRY_EXTERNAL_ATTR = 0o644 << 16


def _bundle_entry_info(arcname: str, *, compress_type: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(arcname, date_time=BUNDLE_ENTRY_TIMESTAMP)
    info.compress_type = compress_type
    info.external_attr = BUNDLE_ENTRY_EXTERNAL_ATTR
    return info


def _bundle_source_entry_info(source_path: Path, arcname: str, *, compress_type: int) -> zipfile.ZipInfo:
    # Carry the source size so ZipFile.open picks the same ZIP64 framing that
    # ZipFile.write would. ZipInfo.from_file is deliberately avoided: it reads
    # the mtime this function exists to discard, and rejects any mirrored file
    # stamped before 1980.
    info = _bundle_entry_info(arcname, compress_type=compress_type)
    info.file_size = source_path.stat().st_size
    return info


def _safe_segment(value: str, max_length: int | None = None) -> str:
    cleaned = (value or "").strip()
    cleaned = "".join("_" if char in '\\/:*?"<>|' or ord(char) < 32 else char for char in cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.rstrip(" .")
    if max_length is not None:
        cleaned = cleaned[:max_length].rstrip(" .")
    if not cleaned or not cleaned.strip(" ._-"):
        return "unknown"
    stem = Path(cleaned).stem.upper()
    if stem in WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def _bundle_arcname(paper: NormalizedPaper) -> str:
    suffix = Path(paper.storage_key).suffix or ".bin"
    file_name = "_".join(
        [
            _safe_segment(paper.category_code or "category", max_length=24),
            _safe_segment(paper.subject_code or "subject", max_length=24),
            _safe_segment(paper.subject_name_raw or "subject", max_length=60),
            _safe_segment(file_type_label(paper.file_type), max_length=20),
        ]
    )
    return f"{paper.year_roc}/{file_name}{suffix}"


def _resolve_arcnames(ordered: list[NormalizedPaper]) -> list[str]:
    base = [_bundle_arcname(p) for p in ordered]
    counts: dict[str, int] = {}
    for name in base:
        counts[name] = counts.get(name, 0) + 1
    resolved: list[str] = []
    for i, paper in enumerate(ordered):
        name = base[i]
        if counts[name] > 1:
            suffix = Path(paper.storage_key).suffix or ".bin"
            stem = name[: -len(suffix)]
            exam_tag = _safe_segment(paper.source_exam_id or "unknown", max_length=20)
            name = f"{stem}_{exam_tag}{suffix}"
        resolved.append(name)
    used: set[str] = set()
    final: list[str] = []
    for name in resolved:
        if name not in used:
            used.add(name)
            final.append(name)
        else:
            counter = 2
            while True:
                dot = name.rfind(".")
                candidate = f"{name[:dot]}_{counter}{name[dot:]}" if dot > 0 else f"{name}_{counter}"
                if candidate not in used:
                    used.add(candidate)
                    final.append(candidate)
                    break
                counter += 1
    return final


def _code_bundle_arcname(paper: NormalizedPaper) -> str:
    suffix = Path(paper.storage_key).suffix or ".bin"
    file_name = "_".join(
        [
            _safe_segment(paper.category_code or "category"),
            _safe_segment(paper.subject_code or "subject"),
            _safe_segment(paper.file_type or "file"),
        ]
    )
    return "/".join([str(paper.year_roc), _safe_segment(paper.source_exam_id or "unknown-exam"), f"{file_name}{suffix}"])


def _legacy_bundle_arcname(paper: NormalizedPaper) -> str:
    suffix = Path(paper.storage_key).suffix or ".bin"
    return "/".join(
        [
            str(paper.year_roc),
            paper.source_exam_id or "unknown-exam",
            _safe_segment(paper.category_raw or paper.exam_name_raw),
            f"{paper.subject_code}_{_safe_segment(paper.subject_name_raw)}",
            f"{paper.file_type}{suffix}",
        ]
    )


def _paper_bundle_key(paper: NormalizedPaper | dict[str, object]) -> tuple[str, str, str, str]:
    if isinstance(paper, dict):
        return tuple(str(paper.get(field, "")) for field in ("source_exam_id", "category_code", "subject_code", "file_type"))
    return (paper.source_exam_id, paper.category_code, paper.subject_code, paper.file_type)


def _bundle_asset_name(canonical_id: str, *, structured: bool = False) -> str:
    stable = _safe_segment(canonical_id, max_length=120)
    if structured:
        # v2 IDs can share the same first 80 characters. Keep a readable
        # prefix but append a digest of the complete identity so two logical
        # bundles can never overwrite one another on disk or in a release.
        digest = hashlib.sha256(canonical_id.encode("utf-8")).hexdigest()[:12]
        stable = f"{stable}--{digest}"
    return f"{stable}.zip"


def _lookup_canonical_ids(canonical_id: str, canonical_name: str, canonical_alias_ids: list[str] | None = None) -> list[str]:
    lookup_ids: list[str] = []
    for alias_id in canonical_alias_ids or []:
        if alias_id != canonical_id and alias_id not in lookup_ids:
            lookup_ids.append(alias_id)
    hashed_fallback = hashed_fallback_canonical_id(canonical_name)
    legacy_id = legacy_fallback_canonical_id(canonical_name)
    for fallback_id in (legacy_id, hashed_fallback):
        if fallback_id != canonical_id and fallback_id not in lookup_ids:
            lookup_ids.append(fallback_id)
    if canonical_id not in lookup_ids:
        lookup_ids.append(canonical_id)
    return lookup_ids


def _legacy_asset_names(
    canonical_id: str,
    canonical_name: str,
    asset_name: str,
    canonical_alias_ids: list[str] | None = None,
) -> list[str]:
    names: list[str] = []
    friendly = _safe_segment(canonical_name, max_length=80)
    stable = _safe_segment(canonical_id, max_length=80)
    if friendly != "unknown":
        names.append(f"{friendly}__{stable}.zip")
    public_ids: list[str] = []
    public_ids.extend(canonical_alias_ids or [])
    hashed_fallback = hashed_fallback_canonical_id(canonical_name)
    if canonical_id == hashed_fallback:
        public_ids.append(legacy_fallback_canonical_id(canonical_name))
    public_ids.append(canonical_id)
    names.extend(f"{_safe_segment(public_id, max_length=80)}.zip" for public_id in public_ids)
    return [name for name in dict.fromkeys(names) if name != asset_name]


def _part_asset_name(asset_name: str, part_index: int, part_count: int) -> str:
    suffix = Path(asset_name).suffix or ".zip"
    stem = asset_name[: -len(suffix)] if asset_name.endswith(suffix) else asset_name
    return f"{stem}--part-{part_index:02d}-of-{part_count:02d}{suffix}"


def _partition_bundle_entries(
    archive: zipfile.ZipFile,
    entries: list[tuple[NormalizedPaper, str]],
    *,
    max_bytes: int,
) -> list[list[tuple[NormalizedPaper, str]]]:
    groups: list[list[tuple[NormalizedPaper, str]]] = []
    current: list[tuple[NormalizedPaper, str]] = []
    current_size = BUNDLE_PART_OVERHEAD
    for paper, arcname in entries:
        info = archive.getinfo(arcname)
        contribution = info.file_size + BUNDLE_PART_OVERHEAD
        if contribution > max_bytes:
            raise ValueError(
                f"bundle entry {arcname} is {info.file_size} bytes and exceeds the "
                f"{max_bytes}-byte multipart target"
            )
        if current and current_size + contribution > max_bytes:
            groups.append(current)
            current = []
            current_size = BUNDLE_PART_OVERHEAD
        current.append((paper, arcname))
        current_size += contribution
    if current:
        groups.append(current)
    return groups


def _split_bundle_archive(
    bundle_path: Path,
    asset_name: str,
    *,
    included_papers: list[NormalizedPaper],
    bundle_entries_by_paper_key: dict[tuple[str, str, str, str], str],
    max_bytes: int,
) -> list[tuple[Path, str, list[NormalizedPaper]]]:
    """Split an oversized archive into independently downloadable ZIP parts."""
    if bundle_path.stat().st_size <= max_bytes:
        return [(bundle_path, asset_name, included_papers)]

    entries = [
        (paper, bundle_entries_by_paper_key[_paper_bundle_key(paper)])
        for paper in included_papers
    ]
    with zipfile.ZipFile(bundle_path, "r") as source:
        groups = _partition_bundle_entries(source, entries, max_bytes=max_bytes)
        base_manifest = json.loads(source.read("bundle.json").decode("utf-8"))
        for stale_part in bundle_path.parent.glob(f"{bundle_path.stem}--part-*.zip"):
            stale_part.unlink(missing_ok=True)
        part_paths: list[tuple[Path, str, list[NormalizedPaper]]] = []
        part_count = len(groups)
        for part_index, group in enumerate(groups, 1):
            part_name = _part_asset_name(asset_name, part_index, part_count)
            part_path = bundle_path.with_name(part_name)
            with zipfile.ZipFile(part_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as destination:
                for _paper, arcname in group:
                    entry_info = _bundle_entry_info(arcname, compress_type=zipfile.ZIP_STORED)
                    with source.open(arcname, "r") as source_entry, destination.open(
                        entry_info, "w", force_zip64=True
                    ) as destination_entry:
                        shutil.copyfileobj(source_entry, destination_entry, length=1024 * 1024)
                part_manifest = dict(base_manifest)
                part_manifest["part_index"] = part_index
                part_manifest["part_count"] = part_count
                part_manifest["part_label"] = f"第 {part_index}/{part_count} 部分"
                part_manifest["file_count"] = len(group)
                part_manifest["years"] = sorted({paper.year_roc for paper, _arcname in group}, reverse=True)
                part_manifest["papers"] = [
                    {**to_plain_data(paper), "bundle_entry": arcname}
                    for paper, arcname in group
                ]
                destination.writestr(
                    _bundle_entry_info("bundle.json", compress_type=zipfile.ZIP_STORED),
                    json.dumps(part_manifest, ensure_ascii=False, indent=2),
                )
            if part_path.stat().st_size >= 2_147_483_648:
                raise ValueError(f"generated multipart asset still exceeds GitHub's 2 GiB limit: {part_path}")
            part_paths.append((part_path, part_name, [paper for paper, _arcname in group]))

    bundle_path.unlink()
    return part_paths


_EntryRef = tuple[Path, str]


def _resolve_entry_ref(ref: _EntryRef) -> bytes | None:
    archive_path, entry_name = ref
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            return zf.read(entry_name)
    except (OSError, ValueError, zipfile.BadZipFile, KeyError):
        return None


def _resolve_mirror_source_path(mirror_dir: Path, paper: NormalizedPaper) -> Path | None:
    return resolve_mirror_storage_path(mirror_dir, paper.storage_key, paper.provider_id)


def resolve_mirror_storage_path(
    mirror_dir: Path, storage_key: str, provider_id: str
) -> Path | None:
    """Resolve a provider mirror entry from its stored locator fields."""
    if not storage_key:
        return None

    storage_path = Path(storage_key)
    direct_path = mirror_dir / storage_path
    if direct_path.exists():
        return direct_path

    if provider_id:
        provider_scoped_path = mirror_dir / "providers" / provider_id / storage_path
        if provider_scoped_path.exists():
            return provider_scoped_path

    return None


def _validate_source_entry_sizes(
    mirror_dir: Path,
    papers: list[NormalizedPaper],
    arcnames: list[str],
    *,
    max_bytes: int,
) -> None:
    """Reject mirror entries that cannot fit in a release part before writing."""
    for paper, arcname in zip(papers, arcnames):
        source_path = _resolve_mirror_source_path(mirror_dir, paper)
        if source_path is None:
            continue
        size = source_path.stat().st_size
        if size + BUNDLE_PART_OVERHEAD > max_bytes:
            raise ValueError(
                f"bundle entry {arcname} is {size} bytes and exceeds the "
                f"{max_bytes}-byte multipart target"
            )


def _load_existing_entries_by_canonical(
    bundle_dir: Path,
    on_progress: Callable | None = None,
) -> tuple[dict[str, dict[str, _EntryRef]], dict[str, dict[tuple[str, str, str, str], _EntryRef]]]:
    existing_entries_by_name: dict[str, dict[str, _EntryRef]] = {}
    existing_entries_by_key: dict[str, dict[tuple[str, str, str, str], _EntryRef]] = {}
    archives = sorted(bundle_dir.glob("*.zip"))
    total = len(archives)
    for idx, archive_path in enumerate(archives, 1):
        if on_progress and (idx == 1 or idx % 500 == 0 or idx == total):
            on_progress(idx, total)
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                names = archive.namelist()
                if "bundle.json" not in names:
                    continue
                manifest = json.loads(archive.read("bundle.json").decode("utf-8"))
                canonical_id = manifest.get("bundle_id") or manifest.get("canonical_id")
                if not canonical_id:
                    continue
                entries_by_name = existing_entries_by_name.setdefault(canonical_id, {})
                for name in names:
                    if name != "bundle.json":
                        entries_by_name[name] = (archive_path, name)
                manifest_papers = manifest.get("papers", [])
                if isinstance(manifest_papers, list):
                    entries_by_key = existing_entries_by_key.setdefault(canonical_id, {})
                    for paper_data in manifest_papers:
                        if isinstance(paper_data, dict):
                            entry_name = paper_data.get("bundle_entry")
                            if not isinstance(entry_name, str) or entry_name not in entries_by_name:
                                continue
                            entries_by_key[_paper_bundle_key(paper_data)] = entries_by_name[entry_name]
        except (OSError, ValueError, zipfile.BadZipFile):
            continue
    return existing_entries_by_name, existing_entries_by_key


def _preserve_rewrite_sources(
    bundle_path: Path,
    existing_entries: dict[str, _EntryRef],
    existing_entries_by_key: dict[tuple[str, str, str, str], _EntryRef],
) -> tuple[dict[str, _EntryRef], dict[tuple[str, str, str, str], _EntryRef], Path | None]:
    refs = [*existing_entries.values(), *existing_entries_by_key.values()]
    if not any(archive_path == bundle_path for archive_path, _entry_name in refs):
        return existing_entries, existing_entries_by_key, None

    preserved_path = bundle_path.with_name(f".{bundle_path.name}.preserve")
    shutil.copyfile(bundle_path, preserved_path)

    def rewrite(ref: _EntryRef) -> _EntryRef:
        archive_path, entry_name = ref
        if archive_path == bundle_path:
            return preserved_path, entry_name
        return ref

    return (
        {name: rewrite(ref) for name, ref in existing_entries.items()},
        {key: rewrite(ref) for key, ref in existing_entries_by_key.items()},
        preserved_path,
    )


def _is_legacy_projection(papers: list[NormalizedPaper]) -> bool:
    paper = papers[0]
    return (
        paper.schema_version != 2
        and not paper.bundle_id
        and not paper.domain_id
        and not paper.exam_series_id
        and bool(paper.canonical_name)
        and paper.canonical_name.isascii()
        and len({item.canonical_id for item in papers}) == 1
    )


def _required_years_for_group(
    canonical_id: str,
    papers: list[NormalizedPaper],
    *,
    min_years: int,
    min_years_by_canonical_prefix: dict[str, int] | None,
) -> int:
    return _required_years_for_hints(
        canonical_id,
        papers[0].provider_id,
        papers[0].canonical_id,
        min_years=min_years,
        min_years_by_canonical_prefix=min_years_by_canonical_prefix,
    )


def _required_years_for_hints(
    canonical_id: str,
    provider_hint: str,
    legacy_hint: str,
    *,
    min_years: int,
    min_years_by_canonical_prefix: dict[str, int] | None,
) -> int:
    required_years = min_years
    for prefix, prefix_min_years in (min_years_by_canonical_prefix or {}).items():
        if canonical_id.startswith(prefix) or provider_hint.startswith(prefix) or legacy_hint.startswith(prefix):
            required_years = prefix_min_years
            break
    return required_years


def public_bundle_ids(
    normalized: NormalizedCatalog,
    *,
    min_years: int = 1,
    min_years_by_canonical_prefix: dict[str, int] | None = None,
) -> set[str]:
    """Return logical bundle IDs eligible for a public site projection.

    This is intentionally the same grouping and year policy used by
    build_bundles; publication validation can therefore detect stale
    generated inventories without inventing a second eligibility rule.
    """
    grouped: dict[str, list[NormalizedPaper]] = {}
    for paper in normalized.papers:
        grouped.setdefault(paper.bundle_id or paper.canonical_id, []).append(paper)

    public_ids: set[str] = set()
    for canonical_id, papers in grouped.items():
        required_years = _required_years_for_group(
            canonical_id,
            papers,
            min_years=min_years,
            min_years_by_canonical_prefix=min_years_by_canonical_prefix,
        )
        if len({paper.year_roc for paper in papers}) < required_years:
            continue
        public_ids.add(papers[0].canonical_id if _is_legacy_projection(papers) else canonical_id)
    return public_ids


@dataclass(slots=True)
class _IndexedBundleGroup:
    provider_hint: str
    legacy_hint: str
    legacy_candidate: bool
    years: set[int] = field(default_factory=set)
    canonical_ids: set[str] = field(default_factory=set)


def public_bundle_ids_from_indexes(
    indexes: Iterable[dict],
    *,
    min_years: int = 1,
    min_years_by_canonical_prefix: dict[str, int] | None = None,
) -> set[str]:
    """Apply the same public year and legacy rules to provider index rows."""
    grouped: dict[str, _IndexedBundleGroup] = {}
    for index in indexes:
        for row in index["papers"]:
            canonical_id = paper_index_canonical_id(index, row)
            bundle_id = paper_index_bundle_id(index, row) or canonical_id
            group = grouped.get(bundle_id)
            if group is None:
                group = grouped[bundle_id] = _IndexedBundleGroup(
                    provider_hint=index["provider_id"],
                    legacy_hint=canonical_id,
                    legacy_candidate=row[PAPER_LEGACY_CANDIDATE],
                )
            group.years.add(row[PAPER_YEAR_ROC])
            group.canonical_ids.add(canonical_id)

    public_ids: set[str] = set()
    for bundle_id, group in grouped.items():
        required_years = _required_years_for_hints(
            bundle_id,
            group.provider_hint,
            group.legacy_hint,
            min_years=min_years,
            min_years_by_canonical_prefix=min_years_by_canonical_prefix,
        )
        if len(group.years) < required_years:
            continue
        public_ids.add(
            group.legacy_hint
            if group.legacy_candidate and len(group.canonical_ids) == 1
            else bundle_id
        )
    return public_ids


def build_bundles(
    bundle_dir: Path,
    mirror_dir: Path,
    normalized: NormalizedCatalog,
    bundle_base_url: str,
    canonical_aliases: dict[str, list[str]] | None = None,
    on_progress: Callable | None = None,
    on_load_progress: Callable | None = None,
    min_years: int = 1,
    min_years_by_canonical_prefix: dict[str, int] | None = None,
    max_bundle_bytes: int = MAX_BUNDLE_BYTES,
) -> BundleBuildResult:
    if max_bundle_bytes < 1 or max_bundle_bytes >= 2_147_483_648:
        raise ValueError("max_bundle_bytes must be below GitHub's 2 GiB per-asset limit")
    bundle_dir.mkdir(parents=True, exist_ok=True)
    existing_entries_by_canonical, existing_entries_by_paper_key = _load_existing_entries_by_canonical(bundle_dir, on_progress=on_load_progress)
    grouped: dict[str, list[NormalizedPaper]] = {}
    for paper in normalized.papers:
        # v2 records carry a complete identity-derived bundle_id. Legacy test
        # and migration records safely fall back to canonical_id.
        grouped.setdefault(paper.bundle_id or paper.canonical_id, []).append(paper)

    total_groups = len(grouped)
    bundle_assets: list[BundleAsset] = []
    failures: list[SyncFailure] = []
    for group_index, (canonical_id, papers) in enumerate(sorted(grouped.items()), 1):
        canonical_name = papers[0].bundle_name or papers[0].canonical_name
        # Legacy fixtures and hand-authored v1 records often use an ASCII
        # display label with no official identity evidence. Keep their public
        # asset name stable; real catalog records use the structured ID.
        legacy_projection = _is_legacy_projection(papers)
        public_bundle_id = papers[0].canonical_id if legacy_projection else canonical_id
        required_years = _required_years_for_group(
            canonical_id,
            papers,
            min_years=min_years,
            min_years_by_canonical_prefix=min_years_by_canonical_prefix,
        )
        if required_years > 1:
            distinct_years = {p.year_roc for p in papers}
            if len(distinct_years) < required_years:
                if on_progress:
                    on_progress(group_index, total_groups, f"[skipped] {canonical_name}", 0)
                continue
        asset_name = _bundle_asset_name(public_bundle_id, structured=not legacy_projection)
        compatibility_ids = list(canonical_aliases.get(canonical_id, [])) if canonical_aliases else []
        for fallback_id in (legacy_fallback_canonical_id(canonical_name), hashed_fallback_canonical_id(canonical_name)):
            if fallback_id != canonical_id and fallback_id in existing_entries_by_canonical and fallback_id not in compatibility_ids:
                compatibility_ids.append(fallback_id)
        legacy_asset_names = _legacy_asset_names(canonical_id, canonical_name, asset_name, compatibility_ids)
        storage_key = f"bundles/{asset_name}"
        bundle_path = bundle_dir / asset_name

        existing_entries: dict[str, _EntryRef] = {}
        existing_entries_by_key: dict[tuple[str, str, str, str], _EntryRef] = {}
        for lookup_id in _lookup_canonical_ids(canonical_id, canonical_name, compatibility_ids):
            existing_entries.update(existing_entries_by_canonical.get(lookup_id, {}))
            existing_entries_by_key.update(existing_entries_by_paper_key.get(lookup_id, {}))

        included_papers: list[NormalizedPaper] = []
        bundle_entries_by_paper_key: dict[tuple[str, str, str, str], str] = {}
        included_years: set[int] = set()
        file_count = 0

        ordered = sorted(
            papers,
            key=lambda item: (-item.year_roc, item.source_exam_id, item.category_code, item.subject_code, item.file_type),
        )
        resolved_names = _resolve_arcnames(ordered)
        _validate_source_entry_sizes(
            mirror_dir,
            ordered,
            resolved_names,
            max_bytes=max_bundle_bytes,
        )
        existing_entries, existing_entries_by_key, preserved_archive = _preserve_rewrite_sources(
            bundle_path,
            existing_entries,
            existing_entries_by_key,
        )
        try:
            with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for paper, arcname in zip(ordered, resolved_names):
                    source_path = _resolve_mirror_source_path(mirror_dir, paper)
                    if source_path is not None:
                        entry_info = _bundle_source_entry_info(
                            source_path, arcname, compress_type=zipfile.ZIP_DEFLATED
                        )
                        with open(source_path, "rb") as source_file, archive.open(
                            entry_info, "w"
                        ) as archive_entry:
                            shutil.copyfileobj(source_file, archive_entry, 1024 * 1024)
                        included_papers.append(paper)
                        bundle_entries_by_paper_key[_paper_bundle_key(paper)] = arcname
                        included_years.add(paper.year_roc)
                        file_count += 1
                        continue
                    legacy_arcname = _legacy_bundle_arcname(paper)
                    existing_ref = existing_entries.get(arcname)
                    if existing_ref is None:
                        base_arcname = _bundle_arcname(paper)
                        if base_arcname != arcname:
                            existing_ref = existing_entries.get(base_arcname)
                    if existing_ref is None:
                        existing_ref = existing_entries.get(_code_bundle_arcname(paper))
                    if existing_ref is None:
                        existing_ref = existing_entries.get(legacy_arcname)
                    if existing_ref is None:
                        existing_ref = existing_entries_by_key.get(_paper_bundle_key(paper))
                    existing_bytes = _resolve_entry_ref(existing_ref) if existing_ref is not None else None
                    if existing_bytes is not None:
                        archive.writestr(
                            _bundle_entry_info(arcname, compress_type=zipfile.ZIP_DEFLATED),
                            existing_bytes,
                        )
                        included_papers.append(paper)
                        bundle_entries_by_paper_key[_paper_bundle_key(paper)] = arcname
                        included_years.add(paper.year_roc)
                        file_count += 1
                        continue
                    failures.append(
                        SyncFailure(
                            stage="bundle",
                            source_exam_id=paper.source_exam_id,
                            year_roc=paper.year_roc,
                            paper_code=paper.paper_code,
                            file_type=paper.file_type,
                            url=paper.download_url_source,
                            message=f"Missing mirrored file for bundle entry: {paper.storage_key}",
                        )
                    )

                if not included_papers:
                    archive.writestr(
                        _bundle_entry_info("bundle.json", compress_type=zipfile.ZIP_DEFLATED),
                        json.dumps(
                            {
                                "schema_version": 2,
                                "bundle_id": canonical_id,
                                "canonical_id": canonical_id,
                                "canonical_name": canonical_name,
                                "years": [],
                                "file_count": 0,
                                "papers": [],
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                    )
                else:
                    manifest_papers = []
                    for paper in included_papers:
                        paper_data = to_plain_data(paper)
                        paper_data["bundle_entry"] = bundle_entries_by_paper_key[_paper_bundle_key(paper)]
                        manifest_papers.append(paper_data)
                    exemplar = included_papers[0]
                    manifest = {
                        "schema_version": 2,
                        "bundle_id": canonical_id,
                        "canonical_id": canonical_id,
                        "canonical_name": canonical_name,
                        "domain_id": exemplar.domain_id,
                        "exam_family_id": exemplar.exam_family_id,
                        "exam_series_id": exemplar.exam_series_id,
                        "level_id": exemplar.level_id,
                        "track_id": exemplar.track_id,
                        "variant_ids": exemplar.variant_ids,
                        "stage_id": exemplar.stage_id,
                        "bundle_policy_id": exemplar.bundle_policy_id,
                        "classification_confidence": exemplar.classification_confidence,
                        "classification_reason": exemplar.classification_reason,
                        "exam_class": exemplar.exam_class,
                        "exam_subclass": exemplar.exam_subclass,
                        "years": sorted(included_years, reverse=True),
                        "file_count": file_count,
                        "papers": manifest_papers,
                    }
                    archive.writestr(
                        _bundle_entry_info("bundle.json", compress_type=zipfile.ZIP_DEFLATED),
                        json.dumps(manifest, ensure_ascii=False, indent=2),
                    )
        finally:
            if preserved_archive is not None:
                preserved_archive.unlink(missing_ok=True)

        if not included_papers:
            bundle_path.unlink(missing_ok=True)
            if on_progress:
                on_progress(group_index, total_groups, asset_name, 0)
            continue

        part_specs = _split_bundle_archive(
            bundle_path,
            asset_name,
            included_papers=included_papers,
            bundle_entries_by_paper_key=bundle_entries_by_paper_key,
            max_bytes=max_bundle_bytes,
        )
        exemplar = included_papers[0]
        search_aliases, subject_labels = derive_public_metadata(
            included_papers,
            bundle_id=canonical_id,
            canonical_name=canonical_name,
        )
        legacy_ids = sorted({paper.canonical_id for paper in papers if paper.canonical_id})
        split_bundle = len(part_specs) > 1
        part_count = len(part_specs)
        for part_index, (part_path, part_name, part_papers) in enumerate(part_specs, 1):
            with part_path.open("rb") as part_file:
                part_digest = hashlib.file_digest(part_file, "sha256").hexdigest()
            part_years = sorted({paper.year_roc for paper in part_papers}, reverse=True)
            bundle_assets.append(
                BundleAsset(
                    canonical_id=legacy_ids[0] if legacy_ids else canonical_id,
                    canonical_name=canonical_name,
                    years=part_years,
                    file_count=len(part_papers),
                    storage_key=f"bundles/{part_name}",
                    asset_name=part_name,
                    release_tag="",
                    download_url="",
                    checksum=part_digest,
                    legacy_asset_names=[] if split_bundle else legacy_asset_names,
                    schema_version=1 if legacy_projection else 2,
                    bundle_id="" if legacy_projection else canonical_id,
                    catalog_version="" if legacy_projection else exemplar.catalog_version,
                    domain_id="" if legacy_projection else exemplar.domain_id,
                    exam_family_id="" if legacy_projection else exemplar.exam_family_id,
                    exam_series_id="" if legacy_projection else exemplar.exam_series_id,
                    level_id="" if legacy_projection else exemplar.level_id,
                    track_id="" if legacy_projection else exemplar.track_id,
                    variant_ids=[] if legacy_projection else list(exemplar.variant_ids),
                    stage_id="" if legacy_projection else exemplar.stage_id,
                    bundle_policy_id="" if legacy_projection else exemplar.bundle_policy_id,
                    classification_confidence="" if legacy_projection else exemplar.classification_confidence,
                    classification_reason="" if legacy_projection else exemplar.classification_reason,
                    exam_class="" if legacy_projection else exemplar.exam_class,
                    exam_subclass="" if legacy_projection else exemplar.exam_subclass,
                    search_aliases=search_aliases,
                    subject_labels=subject_labels,
                    legacy_canonical_ids=sorted(set([*compatibility_ids, *legacy_ids]) - {legacy_ids[0] if legacy_ids else canonical_id}),
                    part_index=part_index,
                    part_count=part_count,
                    part_label=f"第 {part_index}/{part_count} 部分" if split_bundle else "",
                )
            )
        if on_progress:
            on_progress(group_index, total_groups, asset_name, file_count)
    return BundleBuildResult(bundles=bundle_assets, failures=failures)
