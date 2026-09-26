"""Small generated projection of provider event and paper state.

The index is written after its source files. Readers fall back to the source
files if the index is absent or its file-size snapshot has changed. The full
catalog audit remains the authority for classification and record integrity.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from app.paths import ProviderPaths

INDEX_SCHEMA_VERSION = 1
PAPER_FIELDS = (
    "source_exam_id",
    "category_code",
    "subject_code",
    "file_type",
    "year_roc",
    "bundle_id",
    "canonical_id",
    "checksum",
    "storage_key",
    "paper_code",
)
PAPER_SOURCE_EXAM_ID = 0
PAPER_FILE_TYPE = 3
PAPER_YEAR_ROC = 4
PAPER_BUNDLE_ID = 5
PAPER_CANONICAL_ID = 6
PAPER_STORAGE_KEY = 8
PAPER_CODE = 9
PAPER_LEGACY_CANDIDATE = len(PAPER_FIELDS)
_PAPER_DEFAULTS: dict[str, Any] = {
    "source_exam_id": "",
    "category_code": "",
    "subject_code": "",
    "bundle_id": "",
    "checksum": "",
    "storage_key": "",
}
_MISSING = object()


def paper_index_bundle_id(index: dict[str, Any], row: list[Any]) -> str:
    return index["bundle_ids"][row[PAPER_BUNDLE_ID]]


def paper_index_canonical_id(index: dict[str, Any], row: list[Any]) -> str:
    return index["canonical_ids"][row[PAPER_CANONICAL_ID]]


def _field(record: Any, name: str, default: Any = _MISSING) -> Any:
    if isinstance(record, dict):
        if default is _MISSING:
            return record[name]
        return record.get(name, default)
    if default is _MISSING:
        return getattr(record, name)
    return getattr(record, name, default)


def _source_file_sizes(provider: ProviderPaths) -> dict[str, dict[str, int]]:
    return {
        label: {path.name: path.stat().st_size for path in sorted(directory.glob("*.json"))}
        for label, directory in (("exams", provider.exams_dir), ("papers", provider.papers_dir))
    }


def _legacy_candidate(paper: Any) -> bool:
    canonical_name = _field(paper, "canonical_name")
    return (
        _field(paper, "schema_version", 1) != 2
        and not _field(paper, "bundle_id", "")
        and not _field(paper, "domain_id", "")
        and not _field(paper, "exam_series_id", "")
        and bool(canonical_name)
        and canonical_name.isascii()
    )


def _append_raw(index: dict[str, Any], records: Iterable[Any]) -> None:
    index["raw_events"].extend(
        [
            _field(page, "source_exam_id"),
            _field(page, "year_ad"),
            bool(_field(page, "papers", []) or _field(page, "attachments", [])),
        ]
        for page in records
    )


def _append_papers(index: dict[str, Any], records: Iterable[Any]) -> None:
    bundle_ids = {value: position for position, value in enumerate(index["bundle_ids"])}
    canonical_ids = {value: position for position, value in enumerate(index["canonical_ids"])}
    for paper in records:
        row = [_field(paper, name, _PAPER_DEFAULTS.get(name, _MISSING)) for name in PAPER_FIELDS]
        for position, table_name, positions in (
            (PAPER_BUNDLE_ID, "bundle_ids", bundle_ids),
            (PAPER_CANONICAL_ID, "canonical_ids", canonical_ids),
        ):
            value = row[position]
            if value not in positions:
                positions[value] = len(index[table_name])
                index[table_name].append(value)
            row[position] = positions[value]
        row.append(_legacy_candidate(paper))
        index["papers"].append(row)


def _empty_index(provider: ProviderPaths) -> dict[str, Any]:
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "provider_id": provider.provider_id,
        "source_files": {},
        "bundle_ids": [],
        "canonical_ids": [],
        "raw_events": [],
        "papers": [],
    }


def build_provider_index(
    provider: ProviderPaths,
    raw_pages: Iterable[Any],
    papers: Iterable[Any],
) -> dict[str, Any]:
    """Project freshly written in-memory state without rereading large files."""
    index = _empty_index(provider)
    _append_raw(index, sorted(raw_pages, key=lambda page: _field(page, "year_ad")))
    _append_papers(index, sorted(papers, key=lambda paper: _field(paper, "year_roc")))
    index["source_files"] = _source_file_sizes(provider)
    return index


def build_provider_index_from_files(provider: ProviderPaths) -> dict[str, Any]:
    """Rebuild historical indexes one yearly source file at a time."""
    index = _empty_index(provider)
    for directory, append in (
        (provider.exams_dir, _append_raw),
        (provider.papers_dir, _append_papers),
    ):
        for path in sorted(directory.glob("*.json")):
            records = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(records, list):
                raise ValueError(f"provider state is not an array: {path}")
            append(index, records)
    index["source_files"] = _source_file_sizes(provider)
    return index


def write_provider_index(provider: ProviderPaths, index: dict[str, Any]) -> None:
    provider.index_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = provider.index_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(provider.index_path)


def load_provider_index(provider: ProviderPaths) -> dict[str, Any] | None:
    """Return a current index, or None when source files need a fresh scan."""
    if not provider.index_path.exists():
        return None
    try:
        index = json.loads(provider.index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid provider index {provider.index_path}: {exc}") from exc
    if not isinstance(index, dict) or index.get("schema_version") != INDEX_SCHEMA_VERSION:
        raise ValueError(f"unsupported provider index schema: {provider.index_path}")
    if index.get("provider_id") != provider.provider_id:
        raise ValueError(f"provider index owner mismatch: {provider.index_path}")
    if not isinstance(index.get("raw_events"), list) or not isinstance(index.get("papers"), list):
        raise ValueError(f"provider index rows are invalid: {provider.index_path}")
    if any(
        not isinstance(index.get(name), list)
        or any(not isinstance(value, str) for value in index[name])
        for name in ("bundle_ids", "canonical_ids")
    ):
        raise ValueError(f"provider index string tables are invalid: {provider.index_path}")
    if any(
        not isinstance(row, list)
        or len(row) != 3
        or not isinstance(row[0], str)
        or isinstance(row[1], bool)
        or not isinstance(row[1], int)
        or not isinstance(row[2], bool)
        for row in index["raw_events"]
    ):
        raise ValueError(f"provider index raw event row is invalid: {provider.index_path}")
    if any(
        not isinstance(row, list)
        or len(row) != len(PAPER_FIELDS) + 1
        or any(not isinstance(row[position], str) for position in (0, 1, 2, 3, 7, 8, 9))
        or isinstance(row[PAPER_YEAR_ROC], bool)
        or not isinstance(row[PAPER_YEAR_ROC], int)
        or any(
            isinstance(row[position], bool)
            or not isinstance(row[position], int)
            or not 0 <= row[position] < len(index[table_name])
            for position, table_name in (
                (PAPER_BUNDLE_ID, "bundle_ids"),
                (PAPER_CANONICAL_ID, "canonical_ids"),
            )
        )
        or not isinstance(row[PAPER_LEGACY_CANDIDATE], bool)
        for row in index["papers"]
    ):
        raise ValueError(f"provider index paper row is invalid: {provider.index_path}")
    if index.get("source_files") != _source_file_sizes(provider):
        return None
    return index
