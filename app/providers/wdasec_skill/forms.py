"""Native paper forms, independent of occupation, grade and material purpose.

The public masters for foreign assistance are Chinese papers with their own
questions. A translated language cannot be inferred from the download label.
Exact source contexts and bytes bind this distinction across retained history.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

FORM_LABELS = {
    "ordinary": ("paper-form-ordinary", "一般試卷"),
    "foreign-assistance-master": (
        "paper-form-foreign-assistance-master",
        "外語輔助學科試卷（中文公告版）",
    ),
}


@dataclass(frozen=True)
class NativeForm:
    year_roc: int
    source_exam_id: str
    exam_name_raw: str
    category_code: str
    subject_code: str
    subject_name_raw: str
    source_url: str
    checksum: str
    form: str
    native_header: str

    @property
    def key(self) -> tuple[int, str, str, str, str, str, str, str]:
        return (
            self.year_roc,
            self.source_exam_id,
            self.exam_name_raw,
            self.category_code,
            self.subject_code,
            self.subject_name_raw,
            self.source_url,
            self.checksum,
        )

    @property
    def family(self) -> tuple[str, str]:
        return self.category_code, self.subject_code.rsplit("-", 1)[0]


def native_forms_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/wdasec/native-paper-forms-v1.json"


def read_native_forms(path: Path) -> tuple[NativeForm, ...]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        set(document)
        != {"schema_version", "provider_id", "reviewed_at", "rules", "review_notes", "papers"}
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 1
        or document["provider_id"] != "wdasec_skill"
        or not document["review_notes"].strip()
        or not document["rules"]
    ):
        raise ValueError("Unsupported WDA native paper-form evidence")
    date.fromisoformat(document["reviewed_at"])
    for rule in document["rules"]:
        url = urlsplit(rule["source_url"])
        if (
            set(rule) != {"source_url", "checksum", "evidence"}
            or url.scheme != "https"
            or url.netloc not in {"ws.wda.gov.tw", "news.immigration.gov.tw"}
            or url.fragment
            or re.fullmatch(r"[0-9a-f]{64}", rule["checksum"]) is None
            or not rule["evidence"].strip()
        ):
            raise ValueError("WDA paper forms require their native policy evidence")
    papers = tuple(NativeForm(**row) for row in document["papers"])
    keys = set()
    for paper in papers:
        url = urlsplit(paper.source_url)
        if (
            paper.key in keys
            or type(paper.year_roc) is not int
            or not 1 <= paper.year_roc <= 999
            or not paper.source_exam_id.isdigit()
            or re.fullmatch(r"[0-9]{5}", paper.category_code) is None
            or re.fullmatch(
                re.escape(paper.category_code) + r"-(?:class_[abc]|single)-(?:question|practical)",
                paper.subject_code,
            )
            is None
            or not paper.exam_name_raw.strip()
            or not paper.subject_name_raw.strip()
            or url.scheme != "https"
            or url.netloc != "owinform.wdasec.gov.tw"
            or not url.path.startswith("/ExamNet/owInform/DLowFileQ/")
            or not url.path.lower().endswith((".pdf", ".doc", ".zip", ".rar"))
            or url.query
            or url.fragment
            or re.fullmatch(r"[0-9a-f]{64}", paper.checksum) is None
            or paper.form not in FORM_LABELS
            or (paper.form == "ordinary" and "外語翻譯" in paper.native_header)
            or (
                not paper.native_header.strip()
                and paper.year_roc >= 108
                and paper.subject_code.endswith("-question")
            )
            or (
                paper.form == "foreign-assistance-master"
                and (
                    "外語翻譯" not in paper.native_header
                    or not url.path.endswith(".pdf")
                    or not paper.subject_code.endswith("-question")
                )
            )
        ):
            raise ValueError("Invalid or duplicate WDA native paper-form context")
        keys.add(paper.key)
    families: dict[tuple[str, str], set[str]] = {}
    for paper in papers:
        families.setdefault(paper.family, set()).add(paper.form)
    if not papers or any(
        "foreign-assistance-master" in forms and "ordinary" not in forms
        for forms in families.values()
    ):
        raise ValueError("A corrected WDA family requires both ordinary and assisted forms")
    return papers


@lru_cache(maxsize=1)
def native_forms() -> tuple[NativeForm, ...]:
    return read_native_forms(native_forms_path(Path(__file__).resolve().parents[3]))


@lru_cache(maxsize=1)
def _index() -> dict[tuple[int, str, str, str, str, str, str, str], NativeForm]:
    return {paper.key: paper for paper in native_forms()}


@lru_cache(maxsize=1)
def _families() -> frozenset[tuple[str, str]]:
    return frozenset(
        paper.family for paper in native_forms() if paper.form == "foreign-assistance-master"
    )


def form_variant(
    *,
    year_roc: int,
    source_exam_id: str,
    exam_name_raw: str,
    category_code: str,
    subject_code: str,
    subject_name_raw: str,
    source_url: str,
    checksum: str,
) -> tuple[str, str] | None:
    """Keep unaffected IDs stable; hold any unreviewed source or new bytes."""
    paper = _index().get(
        (
            year_roc,
            source_exam_id,
            exam_name_raw,
            category_code,
            subject_code,
            subject_name_raw,
            source_url,
            checksum,
        )
    )
    if paper is None:
        return "paper-form-unknown", "試卷形式待確認"
    if paper.form == "ordinary" and paper.family not in _families():
        return None
    return FORM_LABELS[paper.form]


def validate_native_forms(repo_root: Path) -> None:
    from app.paths import provider_paths
    from app.source_revisions import load_source_revisions
    from app.state import load_provider_state

    forms = read_native_forms(native_forms_path(repo_root))
    provider = provider_paths(repo_root, "wdasec_skill")
    _raw, catalog, _failures = load_provider_state(provider)
    records = [
        {
            "year_roc": paper.year_roc,
            "source_exam_id": paper.source_exam_id,
            "exam_name_raw": paper.exam_name_raw,
            "category_code": paper.category_code,
            "subject_code": paper.subject_code,
            "subject_name_raw": paper.subject_name_raw,
            "download_url_source": paper.download_url_source,
            "checksum": paper.checksum,
        }
        for paper in catalog.papers
    ]
    records.extend(
        entry["source_record"]
        for entry in load_source_revisions(provider)
        if entry["record_type"] == "paper"
    )
    keys = {
        (
            record["year_roc"],
            record["source_exam_id"],
            record["exam_name_raw"],
            record["category_code"],
            record["subject_code"],
            record["subject_name_raw"],
            record["download_url_source"],
            record["checksum"],
        )
        for record in records
    }
    if any(paper.key not in keys for paper in forms):
        raise ValueError("WDA form evidence lacks its exact retained native source anchor")
