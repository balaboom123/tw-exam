from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import unicodedata
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.models import (
    BundleAsset,
    BundleBuildResult,
    NormalizedCatalog,
    NormalizedPaper,
    SyncFailure,
    file_type_label,
    to_plain_data,
)
from app.normalizer import hashed_fallback_canonical_id, legacy_fallback_canonical_id
from app.provider_index import (
    PAPER_LEGACY_CANDIDATE,
    PAPER_MATERIAL_KIND,
    paper_index_bundle_id,
    paper_index_canonical_id,
    paper_index_public_year_roc,
)
from app.publication_metadata import (
    derive_public_metadata,
    file_subject_label,
    publication_subject_label,
)
from app.source_material import (
    MaterialSummary,
    material_summary,
    publication_year_roc,
    publication_years,
    source_date_folder,
    source_material,
    summarize_materials,
    summarize_papers,
)

PaperKey = tuple[int, str, str, str, str, str]

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

# These payloads already carry compression or are deliberately distributed
# as PDFs. Recompressing them adds CPU work to every changed bundle.
STORED_BUNDLE_SUFFIXES = frozenset(
    {
        ".pdf",
        ".zip",
        ".rar",
        ".7z",
        ".gz",
        ".bz2",
        ".xz",
        ".mp3",
        ".m4a",
        ".mp4",
        ".ogg",
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".docx",
        ".xlsx",
        ".pptx",
    }
)


def _bundle_compression(arcname: str) -> int:
    return (
        zipfile.ZIP_STORED
        if Path(arcname).suffix.lower() in STORED_BUNDLE_SUFFIXES
        else zipfile.ZIP_DEFLATED
    )


def _manifest_paper(paper: NormalizedPaper, arcname: str) -> dict[str, Any]:
    record = {
        "year_roc": paper.year_roc,
        "download_url_source": paper.download_url_source,
        "source_exam_id": paper.source_exam_id,
        "category_code": paper.category_code,
        "subject_code": paper.subject_code,
        "file_type": paper.file_type,
        "checksum": paper.checksum,
        "bundle_entry": arcname,
    }
    if paper.source_material is not None:
        record["source_material"] = to_plain_data(paper.source_material)
    return record


def _bundle_entry_info(arcname: str, *, compress_type: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(arcname, date_time=BUNDLE_ENTRY_TIMESTAMP)
    info.compress_type = compress_type
    info.external_attr = BUNDLE_ENTRY_EXTERNAL_ATTR
    return info


def _bundle_source_entry_info(
    source_path: Path, arcname: str, *, compress_type: int
) -> zipfile.ZipInfo:
    # Carry the source size so ZipFile.open picks the same ZIP64 framing that
    # ZipFile.write would. ZipInfo.from_file is deliberately avoided: it reads
    # the mtime this function exists to discard, and rejects any mirrored file
    # stamped before 1980.
    info = _bundle_entry_info(arcname, compress_type=compress_type)
    info.file_size = source_path.stat().st_size
    return info


def _safe_segment(value: str, max_length: int | None = None) -> str:
    cleaned = unicodedata.normalize("NFC", value or "").strip()
    cleaned = "".join("_" if char in '\\/:*?"<>|' or ord(char) < 32 else char for char in cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.rstrip(" .")
    if max_length is not None:
        cleaned = cleaned[:max_length].rstrip(" .")
    if not cleaned or not cleaned.strip(" ._-"):
        return "unknown"
    stem = cleaned.split(".", 1)[0].upper()
    if stem in WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def _bundle_arcname(paper: NormalizedPaper) -> str:
    suffix = Path(paper.storage_key).suffix or ".bin"
    subject = paper.subject_name_raw
    if paper.provider_id == "taipower_recruit":
        subject = publication_subject_label(subject, provider_id=paper.provider_id)
    file_name = "_".join(
        [
            _safe_segment(paper.category_code or "category", max_length=24),
            _safe_segment(paper.subject_code or "subject", max_length=24),
            _safe_segment(subject or "subject", max_length=60),
            _safe_segment(file_type_label(paper.file_type), max_length=20),
        ]
    )
    if len((file_name + suffix).encode("utf-8")) > 180:
        digest = hashlib.sha256(file_name.encode("utf-8")).hexdigest()[:12]
        file_name = file_name.encode("utf-8")[:150].decode("utf-8", errors="ignore")
        file_name = f"{file_name}_{digest}"
    folder = (
        source_date_folder(paper.source_material)
        if paper.source_material is not None
        else str(paper.year_roc)
    )
    if paper.provider_id == "taipower_recruit" and paper.source_material is None:
        session = re.fullmatch(
            rf"taipower-recruit-{paper.year_roc}-(0?[1-9]|1[0-2])", paper.source_exam_id
        )
        if session:
            folder = f"{folder}/{int(session.group(1)):02d}月"
    return f"{folder}/{file_name}{suffix}"


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
        if name.casefold() not in used:
            used.add(name.casefold())
            final.append(name)
        else:
            counter = 2
            while True:
                dot = name.rfind(".")
                candidate = (
                    f"{name[:dot]}_{counter}{name[dot:]}" if dot > 0 else f"{name}_{counter}"
                )
                if candidate.casefold() not in used:
                    used.add(candidate.casefold())
                    final.append(candidate)
                    break
                counter += 1
    # Retain every source record in the manifest, but store an identical
    # subject/year/role payload only once. Never merge revised answers,
    # different years, different subjects, or records without real hashes.
    payloads: dict[tuple[str, str, str, str, str], str] = {}
    for index, paper in enumerate(ordered):
        if not re.fullmatch(r"[0-9a-f]{64}", paper.checksum):
            continue
        key = (
            source_date_folder(paper.source_material)
            if paper.source_material is not None
            else str(paper.year_roc),
            paper.file_type,
            file_subject_label(paper.subject_name_raw),
            Path(paper.storage_key).suffix.lower(),
            paper.checksum,
        )
        final[index] = payloads.setdefault(key, final[index])
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
    return "/".join(
        [
            str(paper.year_roc),
            _safe_segment(paper.source_exam_id or "unknown-exam"),
            f"{file_name}{suffix}",
        ]
    )


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


def _paper_bundle_key(paper: NormalizedPaper | dict[str, Any]) -> PaperKey:
    if isinstance(paper, dict):
        # Older compact manifests stored the year only in the ZIP path.
        year = paper.get("year_roc")
        if year is None:
            folder = str(paper.get("bundle_entry", "")).split("/", 1)[0]
            year = int(folder) if folder.isdigit() else 0
        return (
            int(year),
            str(paper.get("source_exam_id", "")),
            str(paper.get("category_code", "")),
            str(paper.get("subject_code", "")),
            str(paper.get("file_type", "")),
            str(paper.get("download_url_source", "")),
        )
    return (
        paper.year_roc,
        paper.source_exam_id,
        paper.category_code,
        paper.subject_code,
        paper.file_type,
        paper.download_url_source,
    )


def _bundle_asset_name(canonical_id: str, *, structured: bool = False) -> str:
    stable = _safe_segment(canonical_id, max_length=120)
    if structured:
        # v2 IDs can share the same first 80 characters. Keep a readable
        # prefix but append a digest of the complete identity so two logical
        # bundles can never overwrite one another on disk or in a release.
        digest = hashlib.sha256(canonical_id.encode("utf-8")).hexdigest()[:12]
        stable = f"{stable}--{digest}"
    return f"{stable}.zip"


def _lookup_canonical_ids(
    canonical_id: str, canonical_name: str, canonical_alias_ids: list[str] | None = None
) -> list[str]:
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
    seen: set[str] = set()
    for paper, arcname in entries:
        if arcname in seen:
            continue
        seen.add(arcname)
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
    bundle_entries_by_paper_key: dict[PaperKey, str],
    max_bytes: int,
) -> list[tuple[Path, str, list[NormalizedPaper]]]:
    """Split an oversized archive into independently downloadable ZIP parts."""
    if bundle_path.stat().st_size <= max_bytes:
        return [(bundle_path, asset_name, included_papers)]

    entries = [
        (paper, bundle_entries_by_paper_key[_paper_bundle_key(paper)]) for paper in included_papers
    ]
    with zipfile.ZipFile(bundle_path, "r") as source:
        groups = _partition_bundle_entries(source, entries, max_bytes=max_bytes)
        base_manifest = json.loads(source.read("bundle.json").decode("utf-8"))
        for stale_part in bundle_path.parent.glob(f"{bundle_path.stem}--part-*.zip"):
            stale_part.unlink(missing_ok=True)
        part_paths: list[tuple[Path, str, list[NormalizedPaper]]] = []
        part_count = len(groups)
        for part_index, group in enumerate(groups, 1):
            group_names = {arcname for _paper, arcname in group}
            group_papers = [(paper, name) for paper, name in entries if name in group_names]
            part_name = _part_asset_name(asset_name, part_index, part_count)
            part_path = bundle_path.with_name(part_name)
            with zipfile.ZipFile(
                part_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True
            ) as destination:
                for _paper, arcname in group:
                    entry_info = _bundle_entry_info(
                        arcname, compress_type=_bundle_compression(arcname)
                    )
                    with (
                        source.open(arcname, "r") as source_entry,
                        destination.open(entry_info, "w", force_zip64=True) as destination_entry,
                    ):
                        shutil.copyfileobj(source_entry, destination_entry, length=1024 * 1024)
                part_manifest = dict(base_manifest)
                part_manifest["part_index"] = part_index
                part_manifest["part_count"] = part_count
                part_manifest["part_label"] = f"第 {part_index}/{part_count} 部分"
                part_manifest["file_count"] = len(group)
                part_manifest["years"] = sorted(
                    {
                        year
                        for paper, _arcname in group
                        if (year := publication_year_roc(paper)) is not None
                    },
                    reverse=True,
                )
                part_manifest["papers"] = [
                    _manifest_paper(paper, arcname) for paper, arcname in group_papers
                ]
                summary = summarize_papers(paper for paper, _arcname in group_papers)
                if summary is not None:
                    part_manifest["source_material"] = to_plain_data(summary)
                destination.writestr(
                    _bundle_entry_info("bundle.json", compress_type=zipfile.ZIP_DEFLATED),
                    json.dumps(part_manifest, ensure_ascii=False, indent=2),
                )
            if part_path.stat().st_size >= 2_147_483_648:
                raise ValueError(
                    f"generated multipart asset still exceeds GitHub's 2 GiB limit: {part_path}"
                )
            part_paths.append((part_path, part_name, [paper for paper, _arcname in group_papers]))

    bundle_path.unlink()
    return part_paths


_EntryRef = tuple[Path, str]
_ArchiveSignature = tuple[bytes, frozenset[str], int]


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
    for paper, arcname in zip(papers, arcnames, strict=True):
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
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[
    dict[str, dict[str, _EntryRef]],
    dict[str, dict[PaperKey, _EntryRef]],
    dict[Path, _ArchiveSignature],
]:
    existing_entries_by_name: dict[str, dict[str, _EntryRef]] = {}
    existing_entries_by_key: dict[str, dict[PaperKey, _EntryRef]] = {}
    archive_signatures: dict[Path, _ArchiveSignature] = {}
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
                manifest_bytes = archive.read("bundle.json")
                manifest = json.loads(manifest_bytes.decode("utf-8"))
                if not isinstance(manifest, dict):
                    continue
                version = manifest.get("manifest_version", 1)
                if type(version) is not int or version not in (1, 2, 3, 4):
                    continue
                canonical_id = manifest.get("bundle_id") or manifest.get("canonical_id")
                if not canonical_id:
                    continue
                entry_names = [name for name in names if name != "bundle.json"]
                manifest_digest = _manifest_digest(manifest)
                if manifest_digest is not None:
                    archive_signatures[archive_path] = (
                        manifest_digest,
                        frozenset(entry_names),
                        len(entry_names),
                    )
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
                            entries_by_key[_paper_bundle_key(paper_data)] = entries_by_name[
                                entry_name
                            ]
        except (OSError, ValueError, zipfile.BadZipFile):
            continue
    return existing_entries_by_name, existing_entries_by_key, archive_signatures


class _MirrorChecksumMismatch(ValueError):
    def __init__(self, paper: NormalizedPaper) -> None:
        self.paper = paper
        super().__init__(f"Mirrored file does not match its recorded checksum: {paper.storage_key}")


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
        if (
            canonical_id.startswith(prefix)
            or provider_hint.startswith(prefix)
            or legacy_hint.startswith(prefix)
        ):
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
        summary = summarize_papers(papers)
        if not _enough_public_years(
            publication_years(papers),
            required_years,
            undated=summary is not None and any(date.basis == "undated" for date in summary.dates),
        ):
            continue
        public_ids.add(canonical_id)
    return public_ids


def _enough_public_years(years: Iterable[int], required: int, *, undated: bool = False) -> bool:
    return len(set(years)) >= required or (required == 1 and undated)


@dataclass(slots=True)
class _IndexedBundleGroup:
    provider_hint: str
    legacy_hint: str
    legacy_candidate: bool
    years: set[int] = field(default_factory=set)
    canonical_ids: set[str] = field(default_factory=set)
    material_modes: set[bool] = field(default_factory=set)
    material_kinds: set[str] = field(default_factory=set)
    undated: bool = False


def public_bundle_ids_from_indexes(
    indexes: Iterable[dict[str, Any]],
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
            year = paper_index_public_year_roc(index, row)
            if year is None:
                group.undated = True
            else:
                group.years.add(year)
            has_material = index["schema_version"] == 2 or (
                index["schema_version"] == 3 and row[PAPER_MATERIAL_KIND] is not None
            )
            group.material_modes.add(has_material)
            if index["schema_version"] == 3 and has_material:
                group.material_kinds.add(row[PAPER_MATERIAL_KIND])
            group.canonical_ids.add(canonical_id)

    public_ids: set[str] = set()
    for bundle_id, group in grouped.items():
        if len(group.material_modes) > 1:
            raise ValueError("Migrate the complete bundle history before publishing material facts")
        if len(group.material_kinds) > 1:
            raise ValueError("A publication bundle cannot mix material kinds")
        required_years = _required_years_for_hints(
            bundle_id,
            group.provider_hint,
            group.legacy_hint,
            min_years=min_years,
            min_years_by_canonical_prefix=min_years_by_canonical_prefix,
        )
        if not _enough_public_years(group.years, required_years, undated=group.undated):
            continue
        public_ids.add(
            group.legacy_hint
            if group.legacy_candidate and len(group.canonical_ids) == 1
            else bundle_id
        )
    return public_ids


def _bundle_manifest_data(
    canonical_id: str,
    canonical_name: str,
    included_papers: list[NormalizedPaper],
    bundle_entries_by_paper_key: dict[PaperKey, str],
) -> dict[str, Any]:
    if not included_papers:
        manifest = {
            "schema_version": 2,
            "bundle_id": canonical_id,
            "canonical_id": canonical_id,
            "canonical_name": canonical_name,
            "years": [],
            "file_count": 0,
            "papers": [],
        }
    else:
        manifest_papers = [
            _manifest_paper(paper, bundle_entries_by_paper_key[_paper_bundle_key(paper)])
            for paper in included_papers
        ]
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
            "years": publication_years(included_papers),
            "file_count": len(set(bundle_entries_by_paper_key.values())),
            "papers": manifest_papers,
        }
    manifest["manifest_version"] = 3
    summary = summarize_papers(included_papers)
    if summary is not None:
        manifest.update(
            schema_version=3, manifest_version=4, source_material=to_plain_data(summary)
        )
    return manifest


def _bundle_manifest_bytes(
    canonical_id: str,
    canonical_name: str,
    included_papers: list[NormalizedPaper],
    bundle_entries_by_paper_key: dict[PaperKey, str],
) -> bytes:
    return json.dumps(
        _bundle_manifest_data(
            canonical_id,
            canonical_name,
            included_papers,
            bundle_entries_by_paper_key,
        ),
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")


def _manifest_digest(manifest: dict[str, Any]) -> bytes | None:
    """Compare versioned source references without provider-only metadata.

    Legacy manifests remain recoverable, but are rebuilt into v3 because
    v2 source keys cannot distinguish multiple URLs with the same codes.
    """
    version = manifest.get("manifest_version", 1)
    if type(version) is not int or version not in (1, 2, 3, 4):
        return None
    papers = manifest.get("papers")
    if not isinstance(papers, list) or any(not isinstance(paper, dict) for paper in papers):
        return None
    fields = (
        "source_exam_id",
        "category_code",
        "subject_code",
        "file_type",
        "checksum",
        "bundle_entry",
        "download_url_source",
    )
    required = {*fields, "year_roc"}
    if version == 4:
        required.add("source_material")
    if version in {3, 4} and any(
        set(paper) != required or type(paper.get("year_roc")) is not int for paper in papers
    ):
        return None
    rows = [{key: paper.get(key, "") for key in fields} for paper in papers]
    if any(not isinstance(value, str) for row in rows for value in row.values()):
        return None
    if version == 4:
        try:
            summary = material_summary(manifest.get("source_material"))
            materials = [source_material(paper["source_material"]) for paper in papers]
            if summary is None or any(material is None for material in materials):
                return None
            reviewed = [material for material in materials if material is not None]
            if (
                summarize_materials(reviewed) != summary
                or manifest.get("years") != summary.years
                or type(manifest.get("schema_version")) is not int
                or manifest.get("schema_version") != 3
            ):
                return None
            for row, paper, material in zip(rows, papers, reviewed, strict=True):
                if paper["bundle_entry"].split("/", 1)[0] != source_date_folder(material):
                    return None
                row["source_material"] = to_plain_data(material)
        except (TypeError, ValueError, KeyError):
            return None
    projected = {
        **manifest,
        "manifest_version": version,
        "papers": sorted(
            [
                {**row, "year_roc": _paper_bundle_key(paper)[0]}
                for row, paper in zip(rows, papers, strict=True)
            ],
            key=lambda row: (row["year_roc"], *(row[key] for key in fields)),
        ),
    }
    return hashlib.sha256(
        json.dumps(
            projected,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).digest()


def _can_reuse_bundle(
    bundle_path: Path,
    archive_signatures: dict[Path, _ArchiveSignature],
    *,
    mirror_dir: Path,
    canonical_id: str,
    canonical_name: str,
    ordered: list[NormalizedPaper],
    arcnames: list[str],
    max_bytes: int,
) -> bool:
    signature = archive_signatures.get(bundle_path)
    if signature is None or bundle_path.stat().st_size > max_bytes:
        return False
    manifest_digest, entry_names, entry_count = signature
    if not all(paper.checksum for paper in ordered):
        return False
    if entry_count != len(set(arcnames)) or entry_names != frozenset(arcnames):
        return False
    entries_by_key = {
        _paper_bundle_key(paper): arcname for paper, arcname in zip(ordered, arcnames, strict=True)
    }
    expected_manifest = _bundle_manifest_data(canonical_id, canonical_name, ordered, entries_by_key)
    if _manifest_digest(expected_manifest) != manifest_digest:
        return False
    missing_mirror_entries = [
        arcname
        for paper, arcname in zip(ordered, arcnames, strict=True)
        if _resolve_mirror_source_path(mirror_dir, paper) is None
    ]
    if not missing_mirror_entries:
        return True
    try:
        with zipfile.ZipFile(bundle_path) as archive:
            for arcname in missing_mirror_entries:
                with archive.open(arcname) as entry:
                    while entry.read(1024 * 1024):
                        pass
    except (OSError, ValueError, zipfile.BadZipFile, KeyError):
        return False
    return True


def validate_public_materials(
    papers: Iterable[NormalizedPaper],
) -> dict[str, MaterialSummary]:
    """Validate the complete public population before any archive mutation.

    Reviewed facts use versioned archive, index, site and frontend readers.
    Unresolved evidence or partially migrated logical history stays blocked.
    Source restrictions are still enforced by the site's provider selection.
    """
    grouped: dict[str, list[NormalizedPaper]] = {}
    for paper in papers:
        grouped.setdefault(paper.bundle_id or paper.canonical_id, []).append(paper)
    return {
        key: summary
        for key, records in grouped.items()
        if (summary := summarize_papers(records)) is not None
    }


def build_bundles(
    bundle_dir: Path,
    mirror_dir: Path,
    normalized: NormalizedCatalog,
    bundle_base_url: str,
    canonical_aliases: dict[str, list[str]] | None = None,
    on_progress: Callable[[int, int, str, int], None] | None = None,
    on_load_progress: Callable[[int, int], None] | None = None,
    min_years: int = 1,
    min_years_by_canonical_prefix: dict[str, int] | None = None,
    max_bundle_bytes: int = MAX_BUNDLE_BYTES,
    published_checksums: dict[str, str] | None = None,
) -> BundleBuildResult:
    validate_public_materials(normalized.papers)
    if max_bundle_bytes < 1 or max_bundle_bytes >= 2_147_483_648:
        raise ValueError("max_bundle_bytes must be below GitHub's 2 GiB per-asset limit")
    bundle_dir.mkdir(parents=True, exist_ok=True)
    existing_entries_by_canonical, existing_entries_by_paper_key, archive_signatures = (
        _load_existing_entries_by_canonical(bundle_dir, on_progress=on_load_progress)
    )
    grouped: dict[str, list[NormalizedPaper]] = {}
    for paper in normalized.papers:
        # v2 records carry a complete identity-derived bundle_id. Legacy test
        # and migration records safely fall back to canonical_id.
        grouped.setdefault(paper.bundle_id or paper.canonical_id, []).append(paper)

    total_groups = len(grouped)
    bundle_assets: list[BundleAsset] = []
    failures: list[SyncFailure] = []
    for group_index, (canonical_id, papers) in enumerate(sorted(grouped.items()), 1):
        # Official track labels can change over time (e.g. 統測 群/類).
        # Choose the latest retained label independent of catalog load order.
        latest = max(
            papers, key=lambda paper: (publication_year_roc(paper) or 0, paper.source_exam_id)
        )
        canonical_name = latest.bundle_name or latest.canonical_name
        # Naming follows the record's identity contract, independent of the
        # display label's language. Direct v1 callers retain their URL names.
        structured = papers[0].schema_version in {2, 3} or bool(papers[0].bundle_id)
        public_bundle_id = canonical_id
        required_years = _required_years_for_group(
            canonical_id,
            papers,
            min_years=min_years,
            min_years_by_canonical_prefix=min_years_by_canonical_prefix,
        )
        summary = summarize_papers(papers)
        undated = summary is not None and any(date.basis == "undated" for date in summary.dates)
        if not _enough_public_years(publication_years(papers), required_years, undated=undated):
            if on_progress:
                on_progress(group_index, total_groups, f"[skipped] {canonical_name}", 0)
            continue
        asset_name = _bundle_asset_name(public_bundle_id, structured=structured)
        compatibility_ids = (
            list(canonical_aliases.get(canonical_id, [])) if canonical_aliases else []
        )
        for fallback_id in (
            legacy_fallback_canonical_id(canonical_name),
            hashed_fallback_canonical_id(canonical_name),
        ):
            if (
                fallback_id != canonical_id
                and fallback_id in existing_entries_by_canonical
                and fallback_id not in compatibility_ids
            ):
                compatibility_ids.append(fallback_id)
        legacy_asset_names = _legacy_asset_names(
            canonical_id, canonical_name, asset_name, compatibility_ids
        )
        bundle_path = bundle_dir / asset_name

        existing_entries: dict[str, _EntryRef] = {}
        existing_entries_by_key: dict[PaperKey, _EntryRef] = {}
        for lookup_id in _lookup_canonical_ids(canonical_id, canonical_name, compatibility_ids):
            existing_entries.update(existing_entries_by_canonical.get(lookup_id, {}))
            existing_entries_by_key.update(existing_entries_by_paper_key.get(lookup_id, {}))

        included_papers: list[NormalizedPaper] = []
        bundle_entries_by_paper_key: dict[PaperKey, str] = {}
        file_count = 0

        ordered = sorted(
            papers,
            key=lambda item: (
                -(publication_year_roc(item) or 0),
                item.source_exam_id,
                item.category_code,
                item.subject_code,
                item.file_type,
            ),
        )
        resolved_names = _resolve_arcnames(ordered)
        _validate_source_entry_sizes(
            mirror_dir,
            ordered,
            resolved_names,
            max_bytes=max_bundle_bytes,
        )
        reuse_existing = _can_reuse_bundle(
            bundle_path,
            archive_signatures,
            mirror_dir=mirror_dir,
            canonical_id=canonical_id,
            canonical_name=canonical_name,
            ordered=ordered,
            arcnames=resolved_names,
            max_bytes=max_bundle_bytes,
        )
        reused_digest = None
        if reuse_existing:
            with bundle_path.open("rb") as existing_file:
                reused_digest = hashlib.file_digest(existing_file, "sha256").hexdigest()
            published_digest = (published_checksums or {}).get(asset_name)
            if published_checksums is not None and (
                not published_digest or reused_digest != published_digest
            ):
                reuse_existing = False
        if reuse_existing:
            included_papers.extend(ordered)
            bundle_entries_by_paper_key.update(
                {
                    _paper_bundle_key(paper): arcname
                    for paper, arcname in zip(ordered, resolved_names, strict=True)
                }
            )
            file_count = len(set(resolved_names))
        else:
            # Keep the previous archive readable for recovery and preserve it
            # if a source checksum or an I/O operation fails during rebuilding.
            previous_failure_count = len(failures)
            # Any retained source reference can supply a shared payload. A
            # missing first alias must not hide an available equivalent mirror.
            sources_by_entry: dict[str, Path] = {}
            for paper, arcname in zip(ordered, resolved_names, strict=True):
                source = _resolve_mirror_source_path(mirror_dir, paper)
                if source is not None:
                    sources_by_entry.setdefault(arcname, source)
            with tempfile.NamedTemporaryFile(
                prefix=f".{asset_name}.", suffix=".tmp", dir=bundle_dir, delete=False
            ) as temporary:
                staged_path = Path(temporary.name)
            try:
                with zipfile.ZipFile(staged_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    written_entries: set[str] = set()
                    for paper, arcname in zip(ordered, resolved_names, strict=True):
                        if arcname in written_entries:
                            included_papers.append(paper)
                            bundle_entries_by_paper_key[_paper_bundle_key(paper)] = arcname
                            continue
                        source_path = sources_by_entry.get(arcname)
                        if source_path is not None:
                            entry_info = _bundle_source_entry_info(
                                source_path, arcname, compress_type=_bundle_compression(arcname)
                            )
                            with (
                                open(source_path, "rb") as source_file,
                                archive.open(entry_info, "w") as archive_entry,
                            ):
                                checksum = (
                                    hashlib.sha256()
                                    if published_checksums is not None and paper.checksum
                                    else None
                                )
                                for block in iter(lambda: source_file.read(1024 * 1024), b""):
                                    archive_entry.write(block)
                                    if checksum is not None:
                                        checksum.update(block)
                                if checksum is not None and checksum.hexdigest() != paper.checksum:
                                    raise _MirrorChecksumMismatch(paper)
                            included_papers.append(paper)
                            bundle_entries_by_paper_key[_paper_bundle_key(paper)] = arcname
                            written_entries.add(arcname)
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
                        if existing_ref is None:
                            # v1/v2 recovery remains checksum-verified by the
                            # publication caller even when the source URL was absent.
                            key = _paper_bundle_key(paper)
                            existing_ref = existing_entries_by_key.get((*key[:-1], ""))
                        existing_bytes = (
                            _resolve_entry_ref(existing_ref) if existing_ref is not None else None
                        )
                        if existing_bytes is not None:
                            if (
                                published_checksums is not None
                                and paper.checksum
                                and hashlib.sha256(existing_bytes).hexdigest() != paper.checksum
                            ):
                                failures.append(
                                    SyncFailure(
                                        stage="bundle",
                                        source_exam_id=paper.source_exam_id,
                                        year_roc=paper.year_roc,
                                        paper_code=paper.paper_code,
                                        file_type=paper.file_type,
                                        url=paper.download_url_source,
                                        message=(
                                            "Previous bundle entry does not match its recorded "
                                            f"checksum: {paper.storage_key}"
                                        ),
                                    )
                                )
                                continue
                            archive.writestr(
                                _bundle_entry_info(
                                    arcname, compress_type=_bundle_compression(arcname)
                                ),
                                existing_bytes,
                            )
                            included_papers.append(paper)
                            bundle_entries_by_paper_key[_paper_bundle_key(paper)] = arcname
                            written_entries.add(arcname)
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
                                message=(
                                    f"Missing mirrored file for bundle entry: {paper.storage_key}"
                                ),
                            )
                        )

                    archive.writestr(
                        _bundle_entry_info("bundle.json", compress_type=zipfile.ZIP_DEFLATED),
                        _bundle_manifest_bytes(
                            canonical_id,
                            canonical_name,
                            included_papers,
                            bundle_entries_by_paper_key,
                        ),
                    )
                if published_checksums is not None and len(failures) > previous_failure_count:
                    continue
                staged_path.replace(bundle_path)
            except _MirrorChecksumMismatch as exc:
                failed_paper = exc.paper
                failures.append(
                    SyncFailure(
                        stage="bundle",
                        source_exam_id=failed_paper.source_exam_id,
                        year_roc=failed_paper.year_roc,
                        paper_code=failed_paper.paper_code,
                        file_type=failed_paper.file_type,
                        url=failed_paper.download_url_source,
                        message=str(exc),
                    )
                )
                continue
            finally:
                staged_path.unlink(missing_ok=True)

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
            if reuse_existing and part_path == bundle_path and reused_digest is not None:
                part_digest = reused_digest
            else:
                with part_path.open("rb") as part_file:
                    part_digest = hashlib.file_digest(part_file, "sha256").hexdigest()
            part_years = publication_years(part_papers)
            part_summary = summarize_papers(part_papers)
            bundle_assets.append(
                BundleAsset(
                    canonical_id=legacy_ids[0] if legacy_ids else canonical_id,
                    canonical_name=canonical_name,
                    years=part_years,
                    file_count=len(
                        {
                            bundle_entries_by_paper_key[_paper_bundle_key(paper)]
                            for paper in part_papers
                        }
                    ),
                    storage_key=f"bundles/{part_name}",
                    asset_name=part_name,
                    release_tag="",
                    download_url="",
                    checksum=part_digest,
                    legacy_asset_names=[] if split_bundle else legacy_asset_names,
                    schema_version=3 if part_summary is not None else (2 if structured else 1),
                    source_material=part_summary,
                    bundle_id="" if not structured else canonical_id,
                    catalog_version="" if not structured else exemplar.catalog_version,
                    domain_id="" if not structured else exemplar.domain_id,
                    exam_family_id="" if not structured else exemplar.exam_family_id,
                    exam_series_id="" if not structured else exemplar.exam_series_id,
                    level_id="" if not structured else exemplar.level_id,
                    track_id="" if not structured else exemplar.track_id,
                    variant_ids=[] if not structured else list(exemplar.variant_ids),
                    stage_id="" if not structured else exemplar.stage_id,
                    bundle_policy_id="" if not structured else exemplar.bundle_policy_id,
                    classification_confidence=""
                    if not structured
                    else exemplar.classification_confidence,
                    classification_reason="" if not structured else exemplar.classification_reason,
                    exam_class="" if not structured else exemplar.exam_class,
                    exam_subclass="" if not structured else exemplar.exam_subclass,
                    search_aliases=search_aliases,
                    subject_labels=subject_labels,
                    legacy_canonical_ids=sorted(
                        set([*compatibility_ids, *legacy_ids])
                        - {legacy_ids[0] if legacy_ids else canonical_id}
                    ),
                    part_index=part_index,
                    part_count=part_count,
                    part_label=f"第 {part_index}/{part_count} 部分" if split_bundle else "",
                )
            )
        if on_progress:
            on_progress(group_index, total_groups, asset_name, file_count)
    return BundleBuildResult(bundles=bundle_assets, failures=failures)
