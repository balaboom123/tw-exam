"""Native TCU domestic papers and annual admission rules, bound to their bytes.

An entrance requirement is not an examination grade. Annual domestic rules
cannot approve another admission route, an unreviewed year or a revised key.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from app.models import NormalizedPaper, SourceExamPage
from app.source_material import SourceDate, SourceMaterial


@dataclass(frozen=True)
class NativePaper:
    subject_code: str
    subject_name_raw: str
    file_type: str
    source_url: str
    checksum: str


@dataclass(frozen=True)
class AnnualExam:
    year_roc: int
    source_exam_id: str
    category_code: str
    category_raw: str
    exam_name_raw: str
    written_test_date: date
    guide_url: str
    guide_checksum: str
    papers: tuple[NativePaper, ...]

    def material(self, paper: NativePaper) -> SourceMaterial:
        return SourceMaterial(
            "administered",
            SourceDate("exam_year", self.written_test_date.year),
            paper.source_url,
            f"{self.exam_name_raw}｜{paper.subject_name_raw}｜{paper.file_type}；"
            f"國內招生筆試 {self.written_test_date.isoformat()}；簡章 {self.guide_url}",
        )


def annual_papers_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/tcu/annual-papers-v1.json"


def _native_url(value: str) -> bool:
    url = urlsplit(value)
    return (
        url.scheme == "https"
        and url.netloc == "admissions.tcu.edu.tw"
        and url.path.startswith("/wp-content/uploads/")
        and url.path.endswith(".pdf")
        and not url.query
        and not url.fragment
    )


def _checksum(value: str) -> bool:
    return re.fullmatch(r"[0-9a-f]{64}", value) is not None


def read_annual_papers(path: Path) -> tuple[AnnualExam, ...]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        type(document.get("schema_version")) is not int
        or document["schema_version"] != 1
        or document.get("provider_id") != "hce_tcu"
    ):
        raise ValueError("Unsupported TCU annual paper evidence")
    exams = []
    years: set[int] = set()
    for row in document["exams"]:
        year = row["year_roc"]
        if type(year) is not int or not 1 <= year <= 999 or year in years:
            raise ValueError("Invalid or duplicate TCU annual year")
        if (
            row["source_exam_id"] != f"hce-tcu-{year}"
            or row["selection_model"] != "single-written"
            or not _native_url(row["guide_url"])
            or not _checksum(row["guide_checksum"])
            or not row["guide_pages"]
            or any(type(page) is not int or page < 1 for page in row["guide_pages"])
            or not row["rationale"].strip()
        ):
            raise ValueError("TCU domestic selection requires native annual guide evidence")
        date.fromisoformat(row["reviewed_at"])
        sitting = date.fromisoformat(row["written_test_date"])
        papers = tuple(NativePaper(**paper) for paper in row["papers"])
        versions: set[tuple[str, str, str, str]] = set()
        urls: dict[str, tuple[str, str]] = {}
        for paper in papers:
            context = (paper.subject_code, paper.file_type)
            if (
                (*context, paper.source_url, paper.checksum) in versions
                or urls.get(paper.source_url, context) != context
                or not _native_url(paper.source_url)
                or not _checksum(paper.checksum)
                or not paper.subject_code.strip()
                or not paper.subject_name_raw.strip()
                or paper.file_type not in {"question", "answer", "answer_clarification"}
            ):
                raise ValueError("Invalid or duplicate TCU native paper context")
            versions.add((*context, paper.source_url, paper.checksum))
            urls[paper.source_url] = context
        questions = {subject for subject, role, _url, _sha in versions if role == "question"}
        answers = {subject for subject, role, _url, _sha in versions if role == "answer"}
        if not questions or questions != answers:
            raise ValueError("TCU domestic papers require their original subject answer keys")
        exam = AnnualExam(
            year,
            row["source_exam_id"],
            row["category_code"],
            row["category_raw"],
            row["exam_name_raw"],
            sitting,
            row["guide_url"],
            row["guide_checksum"],
            papers,
        )
        for paper in papers:
            _ = exam.material(paper)
        exams.append(exam)
        years.add(year)
    if not exams:
        raise ValueError("TCU annual evidence cannot be empty")
    return tuple(exams)


@lru_cache(maxsize=1)
def annual_exams() -> tuple[AnnualExam, ...]:
    return read_annual_papers(annual_papers_path(Path(__file__).resolve().parents[3]))


def resolve_native_paper(
    *,
    source_exam_id: str,
    year_roc: int,
    category_code: str,
    subject_code: str,
    source_url: str,
    checksum: str,
    file_type: str | None = None,
) -> tuple[AnnualExam, NativePaper] | None:
    for exam in annual_exams():
        if (source_exam_id, year_roc, category_code) != (
            exam.source_exam_id,
            exam.year_roc,
            exam.category_code,
        ):
            continue
        for paper in exam.papers:
            if (subject_code, source_url, checksum) == (
                paper.subject_code,
                paper.source_url,
                paper.checksum,
            ) and (file_type is None or file_type == paper.file_type):
                return exam, paper
    return None


def native_material(
    *,
    source_exam_id: str,
    year_roc: int,
    category_code: str,
    subject_code: str,
    source_url: str,
    checksum: str,
    file_type: str,
) -> SourceMaterial:
    native = resolve_native_paper(
        source_exam_id=source_exam_id,
        year_roc=year_roc,
        category_code=category_code,
        subject_code=subject_code,
        source_url=source_url,
        checksum=checksum,
        file_type=file_type,
    )
    if native is not None:
        return native[0].material(native[1])
    return SourceMaterial(
        "unknown",
        SourceDate("unknown", None),
        source_url,
        f"TCU {subject_code}/{file_type}",
        "TCU paper lacks reviewed native bytes and domestic annual admission evidence",
    )


def separate_file_roles(page: SourceExamPage) -> SourceExamPage:
    """Each question and key must classify using its own downloaded checksum."""
    return replace(
        page,
        papers=[
            replace(
                paper,
                files={role: url},
                mirror_files={role: paper.mirror_files[role]} if role in paper.mirror_files else {},
            )
            for paper in page.papers
            for role, url in paper.files.items()
        ],
    )


def validate_public_papers(papers: Iterable[NormalizedPaper]) -> None:
    """Do not lose an original key or clarification when the live listing shrinks."""
    selected = [paper for paper in papers if paper.provider_id == "hce_tcu"]
    by_year: dict[int, set[tuple[str, str]]] = {}
    for paper in selected:
        native = resolve_native_paper(
            source_exam_id=paper.source_exam_id,
            year_roc=paper.year_roc,
            category_code=paper.category_code,
            subject_code=paper.subject_code,
            source_url=paper.download_url_source,
            checksum=paper.checksum,
            file_type=paper.file_type,
        )
        if (
            native is None
            or paper.source_material != native[0].material(native[1])
            or (paper.category_raw, paper.subject_name_raw, paper.exam_name_raw)
            != (native[0].category_raw, native[1].subject_name_raw, native[0].exam_name_raw)
        ):
            raise ValueError("TCU publication requires reviewed native paper and annual evidence")
        key = (paper.subject_code, paper.file_type)
        population = by_year.setdefault(paper.year_roc, set())
        if key in population:
            raise ValueError("Duplicate TCU public source reference")
        population.add(key)
    for exam in annual_exams():
        if exam.year_roc in by_year and by_year[exam.year_roc] != {
            (paper.subject_code, paper.file_type) for paper in exam.papers
        }:
            raise ValueError(
                "TCU publication cannot omit reviewed questions, keys or clarifications"
            )


def validate_annual_papers(repo_root: Path, *, verify_mirror: bool = False) -> int:
    from app.paths import provider_paths
    from app.source_revisions import load_source_revisions
    from app.state import load_provider_state

    provider = provider_paths(repo_root, "hce_tcu")
    _, catalog, _ = load_provider_state(provider)
    records = [
        (
            paper.year_roc,
            paper.source_exam_id,
            paper.category_code,
            paper.subject_code,
            paper.subject_name_raw,
            paper.file_type,
            paper.download_url_source,
            paper.checksum,
            paper.storage_key,
        )
        for paper in catalog.papers
    ]
    for entry in load_source_revisions(provider):
        if entry["record_type"] == "paper":
            record = entry["source_record"]
            records.append(
                (
                    record["year_roc"],
                    record["source_exam_id"],
                    record["category_code"],
                    record["subject_code"],
                    record["subject_name_raw"],
                    record["file_type"],
                    record["download_url_source"],
                    record["checksum"],
                    entry["blob_storage_key"],
                )
            )
    count = 0
    for exam in read_annual_papers(annual_papers_path(repo_root)):
        for paper in exam.papers:
            matches = [
                key
                for *context, key in records
                if tuple(context)
                == (
                    exam.year_roc,
                    exam.source_exam_id,
                    exam.category_code,
                    paper.subject_code,
                    paper.subject_name_raw,
                    paper.file_type,
                    paper.source_url,
                    paper.checksum,
                )
            ]
            if not matches:
                raise ValueError("TCU native paper evidence lacks a retained source anchor")
            if verify_mirror:
                for key in matches:
                    path = repo_root / "mirror" / key
                    with path.open("rb") as stream:
                        digest = hashlib.file_digest(stream, "sha256").hexdigest()
                    if digest != paper.checksum:
                        raise ValueError("TCU native paper mirror checksum mismatch")
            count += 1
    return count
