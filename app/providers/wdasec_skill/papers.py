"""Source-and-byte-bound WDA paper facts.

Paper form, native grade, displayed subject and file role are independent facts.
A reviewed form alone does not approve the other dimensions. Original source
labels remain traceability keys even when the native paper corrects them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from app.models import NormalizedPaper

FORM_LABELS = {
    "ordinary": ("paper-form-ordinary", "一般試卷"),
    "foreign-assistance-master": (
        "paper-form-foreign-assistance-master",
        "外語輔助學科試卷（中文公告版）",
    ),
}


@dataclass(frozen=True)
class ReviewedGrade:
    level_id: str
    native_grade: str
    subject_label: str
    evidence: str


@dataclass(frozen=True)
class ReviewedRole:
    file_role: str
    evidence: str
    question_count: int | None = None


@dataclass(frozen=True)
class NativePaper:
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
    reviewed_grade: ReviewedGrade | None = None
    reviewed_role: ReviewedRole | None = None

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
        if self.reviewed_grade is not None:
            grade = self.reviewed_grade.level_id.replace("-", "_")
            return self.category_code, f"{self.category_code}-{grade}"
        return self.category_code, self.subject_code.rsplit("-", 1)[0]


def native_papers_path(repo_root: Path) -> Path:
    folder = repo_root / "catalog/mappings/wdasec"
    current = folder / "native-paper-facts-v2.json"
    legacy = folder / "native-paper-facts-v1.json"
    if current.exists() and legacy.exists():
        raise ValueError("WDA native paper evidence has competing versioned owners")
    return legacy if legacy.exists() else current


def _read_native_paper(row: object, version: int) -> NativePaper:
    required = {
        "year_roc",
        "source_exam_id",
        "exam_name_raw",
        "category_code",
        "subject_code",
        "subject_name_raw",
        "source_url",
        "checksum",
        "form",
        "native_header",
    }
    optional = {"reviewed_identity"} if version == 1 else {"reviewed_grade", "reviewed_role"}
    if not isinstance(row, dict) or not required <= row.keys() <= required | optional:
        raise ValueError("Unsupported WDA native paper evidence fields")
    values = dict(row)
    if version == 1:
        legacy = values.pop("reviewed_identity", None)
        if legacy is not None:
            if not isinstance(legacy, dict) or set(legacy) != {
                "level_id",
                "native_grade",
                "subject_label",
                "file_role",
                "evidence",
            }:
                raise ValueError("Invalid legacy WDA identity evidence")
            values["reviewed_grade"] = {
                key: value for key, value in legacy.items() if key != "file_role"
            }
            values["reviewed_role"] = {
                "file_role": legacy["file_role"],
                "evidence": legacy["evidence"],
            }
    grade = values.pop("reviewed_grade", None)
    role = values.pop("reviewed_role", None)
    if grade is not None and (
        not isinstance(grade, dict)
        or set(grade) != {"level_id", "native_grade", "subject_label", "evidence"}
    ):
        raise ValueError("Invalid WDA native grade evidence")
    if role is not None and (
        not isinstance(role, dict)
        or not {"file_role", "evidence"}
        <= role.keys()
        <= {
            "file_role",
            "evidence",
            "question_count",
        }
    ):
        raise ValueError("Invalid WDA native role evidence")
    if role is not None and "question_count" in role and type(role["question_count"]) is not int:
        raise ValueError("WDA native question count requires an integer")
    return NativePaper(
        **values,
        reviewed_grade=ReviewedGrade(**grade) if grade is not None else None,
        reviewed_role=ReviewedRole(**role) if role is not None else None,
    )


def read_native_papers(path: Path) -> tuple[NativePaper, ...]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        set(document)
        != {"schema_version", "provider_id", "reviewed_at", "rules", "review_notes", "papers"}
        or type(document["schema_version"]) is not int
        or document["schema_version"] not in {1, 2}
        or document["provider_id"] != "wdasec_skill"
        or not document["review_notes"].strip()
        or not document["rules"]
    ):
        raise ValueError("Unsupported WDA native paper evidence")
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
    papers = tuple(
        _read_native_paper(row, document["schema_version"]) for row in document["papers"]
    )
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
            raise ValueError("Invalid or duplicate WDA native paper context")
        review = paper.reviewed_grade
        if review is not None:
            from app.classification import _LEVEL_LABELS

            if (
                any(
                    not isinstance(value, str)
                    for value in (
                        review.level_id,
                        review.native_grade,
                        review.subject_label,
                        review.evidence,
                    )
                )
                or review.level_id not in {"class-a", "class-b", "class-c", "single"}
                or _LEVEL_LABELS.get(review.level_id) != review.native_grade
                or review.native_grade not in {"甲級", "乙級", "丙級", "單一級"}
                or review.native_grade not in paper.native_header
                or review.native_grade not in review.subject_label
                or not review.evidence.strip()
                or not paper.subject_code.endswith("-question")
            ):
                raise ValueError("WDA grade corrections require exact native grade evidence")
        role = paper.reviewed_role
        if role is not None and (
            role.file_role != "question_answer"
            or not isinstance(role.evidence, str)
            or not role.evidence.strip()
            or not paper.subject_code.endswith("-question")
            or (
                role.question_count is not None
                and (type(role.question_count) is not int or not 1 <= role.question_count <= 1000)
            )
        ):
            raise ValueError("WDA role corrections require exact native question/answer evidence")
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
def native_papers() -> tuple[NativePaper, ...]:
    return read_native_papers(native_papers_path(Path(__file__).resolve().parents[3]))


@lru_cache(maxsize=1)
def _index() -> dict[tuple[int, str, str, str, str, str, str, str], NativePaper]:
    return {paper.key: paper for paper in native_papers()}


@lru_cache(maxsize=1)
def _families() -> frozenset[tuple[str, str]]:
    return frozenset(
        paper.family for paper in native_papers() if paper.form == "foreign-assistance-master"
    )


def resolve_native_paper(
    *,
    year_roc: int,
    source_exam_id: str,
    exam_name_raw: str,
    category_code: str,
    subject_code: str,
    subject_name_raw: str,
    source_url: str,
    checksum: str,
) -> NativePaper | None:
    """A URL or digest alone never approves a different source context."""
    return _index().get(
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


def form_variant(paper: NativePaper | None) -> tuple[str, str] | None:
    if paper is None:
        return "paper-form-unknown", "試卷形式待確認"
    if paper.form == "ordinary" and paper.family not in _families():
        return None
    return FORM_LABELS[paper.form]


def validate_native_papers(repo_root: Path) -> None:
    from app.paths import provider_paths
    from app.source_revisions import load_source_revisions
    from app.state import load_provider_state

    forms = read_native_papers(native_papers_path(repo_root))
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
        raise ValueError("WDA paper evidence lacks its exact retained native source anchor")


def native_paper_for_record(paper: NormalizedPaper) -> NativePaper | None:
    if paper.provider_id != "wdasec_skill":
        return None
    return resolve_native_paper(
        year_roc=paper.year_roc,
        source_exam_id=paper.source_exam_id,
        exam_name_raw=paper.exam_name_raw,
        category_code=paper.category_code,
        subject_code=paper.subject_code,
        subject_name_raw=paper.subject_name_raw,
        source_url=paper.download_url_source,
        checksum=paper.checksum,
    )


def native_file_role(paper: NormalizedPaper) -> str:
    native = native_paper_for_record(paper)
    if native is not None and native.reviewed_role is not None:
        if paper.file_type in {"question", "question_answer"}:
            return native.reviewed_role.file_role
    return paper.file_type
