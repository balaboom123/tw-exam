"""Hakka material and native identity, independent of listing/storage groups."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlsplit

from app.source_material import SourceDate, SourceMaterial

LISTING_URL = "https://elearning.hakka.gov.tw/hakka/download-files"
ACADEMY_DOWNLOAD_URL = "https://elearning.hakka.gov.tw/mooc/download.php"
DIALECTS = {
    "sixian": "四縣腔",
    "hailu": "海陸腔",
    "dapu": "大埔腔",
    "raoping": "饒平腔",
    "zhaoan": "詔安腔",
}


def _label_key(label: str) -> str:
    return "".join(unicodedata.normalize("NFKC", label).split())


def is_hakka_academy_asset(url: str) -> bool:
    parts = urlsplit(url)
    return (
        parts.netloc == "elearning.hakka.gov.tw"
        and re.fullmatch(
            r"/(?:hakka/api/download|base/10001/download)/[0-9]+/[^/]+", unquote(parts.path)
        )
        is not None
    )


def hakka_material(label: str, source_exam_id: str, source_url: str = "") -> SourceMaterial | None:
    """Official annual resources carry edition years, never sitting years."""
    text = _label_key(label)
    if "題庫" in text:
        kind = "practice_collection" if "樣卷" in text else "question_bank"
    elif "樣卷" in text or "試題範例" in text:
        kind = "sample"
    elif "語料選粹" in text or "通過標準與題型說明" in text:
        kind = "reference"
    else:
        kind = "unknown"
    if not text:
        return None
    year_match = re.search(r"(?<!\d)(\d{3})年度", text)
    date = (
        SourceDate("edition_year", int(year_match.group(1)) + 1911)
        if year_match
        else SourceDate("undated", None)
    )
    category = "2"
    if "advanced" in source_exam_id:
        category = "5"
    elif "intermediate" in source_exam_id:
        category = "3"
    evidence_url = (
        ACADEMY_DOWNLOAD_URL
        if is_hakka_academy_asset(source_url)
        else f"{LISTING_URL}?c={category}"
    )
    if kind == "unknown":
        return SourceMaterial(
            kind,
            SourceDate("unknown", None),
            evidence_url,
            label,
            "Hakka official download lacks reviewed material/date meaning",
        )
    return SourceMaterial(kind, date, evidence_url, label)


def hakka_level(
    label: str,
    material: SourceMaterial | None,
    checksum: str = "",
    subject_code: str = "",
) -> tuple[str, str, str]:
    """Resolve the download's own grade; page groups are navigation headings."""
    text = _label_key(label)
    for marker, level, display in (
        ("基礎級暨初級", "basic-elementary", "基礎級暨初級"),
        ("中級暨中高級", "intermediate-high-intermediate", "中級暨中高級"),
        ("初級", "hakka-elementary", "初級"),
        ("高級", "advanced", "高級"),
    ):
        if marker not in text:
            continue
        if marker == "高級" and "中高級" in text:
            return "unknown", "待確認", "Unsupported single Hakka intermediate-high grade"
        # Unsupported single 中高級 is not an advanced 高級 resource.
        rest = text.replace(marker, "")
        if any(token in rest for token in ("基礎級", "初級", "中級", "中高級", "高級", "專業級")):
            return "unknown", "待確認", "Conflicting or unsupported Hakka grade markers"
        if (
            level == "hakka-elementary"
            and material is not None
            and material.date.year_ad is not None
            and material.date.year_ad >= 2023
        ):
            for fact in _historical_grades():
                if (
                    text == _label_key(fact["title"])
                    and checksum == fact["checksum"]
                    and subject_code == fact["subject_code"]
                ):
                    return (
                        "basic-elementary",
                        "基礎級暨初級",
                        f"Reviewed Hakka historical grade: {fact['id']}; {fact['reason']}",
                    )
            return (
                "unknown",
                "待確認",
                "Elementary-only label after the 2023 shared-format reform requires native review",
            )
        return level, display, f"Hakka download grade: {marker}"
    return "unknown", "待確認", "Hakka download lacks a supported native grade"


def hakka_dialect(label: str, category_code: str) -> tuple[str, str] | None:
    text = _label_key(label)
    if "南四縣" in text:
        return None
    matches = [code for code, display in DIALECTS.items() if display.removesuffix("腔") in text]
    if len(matches) != 1 or matches[0] != category_code:
        return None
    return matches[0], DIALECTS[matches[0]]


def hakka_conflicts_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/hakka/native-conflicts-v1.json"


def hakka_historical_grades_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/hakka/historical-grades-v1.json"


@lru_cache(maxsize=1)
def _historical_grades() -> tuple[dict[str, Any], ...]:
    document = json.loads(
        hakka_historical_grades_path(Path(__file__).resolve().parents[1]).read_text()
    )
    return tuple(cast(list[dict[str, Any]], document["facts"]))


def validate_hakka_historical_grades(repo_root: Path, *, verify_mirror: bool = False) -> int:
    """Anchor legacy grade facts to both retained references, including retired material."""
    from app.models import to_plain_data
    from app.paths import provider_paths
    from app.source_revisions import load_source_revisions
    from app.state import load_provider_state

    document = json.loads(hakka_historical_grades_path(repo_root).read_text())
    provider = provider_paths(repo_root, "hakka_cert")
    _, current, _ = load_provider_state(provider)
    revisions = load_source_revisions(provider)
    records = [(to_plain_data(paper), paper.storage_key) for paper in current.papers]
    historical = [
        (entry["source_record"], entry["blob_storage_key"])
        for entry in revisions
        if entry["record_type"] == "paper"
    ]
    ids = [fact["id"] for fact in document["facts"]]
    keys = [(fact["subject_code"], fact["checksum"]) for fact in document["facts"]]
    if len(set(ids)) != len(ids) or len(set(keys)) != len(keys):
        raise ValueError("Duplicate Hakka historical grade fact")
    for fact in document["facts"]:
        matching_grade = hakka_level(
            fact["matching_title"], hakka_material(fact["matching_title"], "")
        )[0]
        if matching_grade != "basic-elementary":
            raise ValueError(f"{fact['id']}: Hakka historical grade counterpart is unsupported")
        for candidates, prefix in ((historical, ""), (records + historical, "matching_")):
            matches = [
                key
                for record, key in candidates
                if record["download_url_source"] == fact[f"{prefix}source_url"]
                and _label_key(record["subject_name_raw"]) == _label_key(fact[f"{prefix}title"])
                and record["checksum"] == fact[f"{prefix}checksum"]
                and record["subject_code"] == fact[f"{prefix}subject_code"]
                and record["category_code"] == fact["dialect_code"]
                and record["year_roc"] + 1911 == fact["edition_year_ad"]
                and record["file_type"] == "question"
            ]
            if not matches:
                raise ValueError(
                    f"{fact['id']}: Hakka historical grade anchor is missing or changed"
                )
            if verify_mirror:
                with (repo_root / "mirror" / matches[0]).open("rb") as stream:
                    checksum = hashlib.file_digest(stream, "sha256").hexdigest()
                if checksum != fact[f"{prefix}checksum"]:
                    raise ValueError(f"{fact['id']}: Hakka historical grade mirror differs")
    return len(document["facts"])


@lru_cache(maxsize=1)
def _conflicts() -> tuple[dict[str, Any], ...]:
    document = json.loads(hakka_conflicts_path(Path(__file__).resolve().parents[1]).read_text())
    return tuple(cast(list[dict[str, Any]], document["facts"]))


def hakka_conflict_reason(label: str, checksum: str, subject_code: str = "") -> str:
    for fact in _conflicts():
        if _label_key(label) != _label_key(fact["title"]):
            continue
        # The official page uses the same 詔安 title for both 307.zip (the
        # conflicting rhw2 package) and 310.zip (the distinct zhw2 package).
        # An incidental duplicate label cannot quarantine another source key.
        if subject_code and subject_code != fact["subject_code"]:
            continue
        if checksum != fact["checksum"]:
            return f"{fact['id']}: previously conflicting source label has unreviewed bytes"
        return f"{fact['id']}: {fact['reason']}"
    return ""


def validate_hakka_conflicts(repo_root: Path, *, verify_mirror: bool = False) -> int:
    """Require both official source references supporting every conflict fact."""
    from app.models import to_plain_data
    from app.paths import provider_paths
    from app.source_revisions import load_source_revisions
    from app.state import load_provider_state

    document = json.loads(hakka_conflicts_path(repo_root).read_text())
    ids = [fact["id"] for fact in document["facts"]]
    titles = [_label_key(fact["title"]) for fact in document["facts"]]
    if len(set(ids)) != len(ids) or len(set(titles)) != len(titles):
        raise ValueError("Duplicate Hakka conflict fact id or title")
    provider = provider_paths(repo_root, "hakka_cert")
    _, current, _ = load_provider_state(provider)
    records = [(to_plain_data(paper), paper.storage_key) for paper in current.papers]
    records.extend(
        (entry["source_record"], entry["blob_storage_key"])
        for entry in load_source_revisions(provider)
        if entry["record_type"] == "paper"
    )
    for fact in document["facts"]:
        if fact["source_url"] == fact["matching_source_url"]:
            raise ValueError(f"{fact['id']}: Hakka conflict needs two source references")
        for url, title in (
            (fact["source_url"], fact["title"]),
            (fact["matching_source_url"], fact["matching_title"]),
        ):
            matches = [
                key
                for record, key in records
                if record["download_url_source"] == url
                and _label_key(record["subject_name_raw"]) == _label_key(title)
                and record["checksum"] == fact["checksum"]
                and (url != fact["source_url"] or record["subject_code"] == fact["subject_code"])
            ]
            if not matches:
                raise ValueError(
                    f"{fact['id']}: retained Hakka conflict anchor is missing or changed"
                )
            if verify_mirror:
                with (repo_root / "mirror" / matches[0]).open("rb") as stream:
                    checksum = hashlib.file_digest(stream, "sha256").hexdigest()
                if checksum != fact["checksum"]:
                    raise ValueError(f"{fact['id']}: Hakka conflict mirror differs")
    return len(document["facts"])
