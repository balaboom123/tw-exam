"""CMU annual selection stages anchored to each reviewed native file version."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from datetime import date
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from app.models import ParsedPaper
from app.providers.hce_archive import HCE_CONFIGS


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
    stage_id: str
    reason: str
    files: tuple[NativePaper, ...]


def annual_papers_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/cmu/annual-papers-v1.json"


def _native_url_checksum(url: str, checksum: str, *, context: bool = False) -> None:
    parsed = urlsplit(url)
    hosts = {"adm21.cmu.edu.tw", "evaluation.cmu.edu.tw"} if context else {"adm21.cmu.edu.tw"}
    if (
        parsed.scheme != "https"
        or parsed.netloc not in hosts
        or parsed.fragment
        or re.fullmatch(r"[0-9a-f]{64}", checksum) is None
    ):
        raise ValueError("CMU evidence requires an official URL and SHA-256 checksum")


def read_annual_exams(path: Path) -> tuple[AnnualExam, ...]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        set(document) != {"schema_version", "provider_id", "references", "events"}
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 1
        or document["provider_id"] != "hce_cmu"
    ):
        raise ValueError("Unsupported CMU annual evidence")
    references = {}
    for ref in document["references"]:
        _native_url_checksum(ref["source_url"], ref["checksum"], context=True)
        if (
            set(ref) != {"id", "year_roc", "kind", "source_url", "checksum", "pdf_pages", "finding"}
            or not isinstance(ref["id"], str)
            or not ref["id"]
            or ref["id"] in references
            or type(ref["year_roc"]) is not int
            or not 1 <= ref["year_roc"] <= 999
            or ref["kind"]
            not in {"annual_interview_notice", "annual_admission_guide", "historical_context"}
            or not ref["finding"]
            or not ref["pdf_pages"]
            or len(set(ref["pdf_pages"])) != len(ref["pdf_pages"])
            or any(type(p) is not int or p < 1 for p in ref["pdf_pages"])
        ):
            raise ValueError("Invalid CMU primary reference")
        references[ref["id"]] = ref
    exams = []
    years = set()
    used = set()
    subjects = set(HCE_CONFIGS["hce_cmu"].subject_slugs.values())
    for row in document["events"]:
        year = row["year_roc"]
        ids = row["evidence_ids"]
        if (
            set(row)
            != {
                "year_roc",
                "source_exam_id",
                "stage_id",
                "reviewed_at",
                "reason",
                "evidence_ids",
                "files",
            }
            or type(year) is not int
            or not 1 <= year <= 999
            or year in years
            or row["source_exam_id"] != f"hce-cmu-{year}"
            or row["stage_id"] not in {"stage-1", "unknown"}
            or not row["reason"]
            or len(set(ids)) != len(ids)
            or any(i not in references or references[i]["year_roc"] != year for i in ids)
        ):
            raise ValueError("Invalid or conflicting CMU annual context")
        date.fromisoformat(row["reviewed_at"])
        years.add(year)
        used.update(ids)
        if row["stage_id"] == "stage-1" and not any(
            references[i]["kind"] in {"annual_interview_notice", "annual_admission_guide"}
            for i in ids
        ):
            raise ValueError("CMU stage approval requires matching annual selection evidence")
        files = []
        contexts = set()
        for item in row["files"]:
            paper = NativePaper(**item)
            _native_url_checksum(paper.source_url, paper.checksum)
            context = paper.subject_code, paper.file_type, paper.source_url, paper.checksum
            if (
                context in contexts
                or not paper.subject_name_raw
                or paper.subject_code not in subjects | {"all"}
                or paper.file_type
                not in {"question", "answer", "all_answers", "answer_clarification"}
                or (paper.file_type in {"all_answers", "answer_clarification"})
                != (paper.subject_code == "all")
            ):
                raise ValueError("Invalid or duplicate CMU native file applicability")
            contexts.add(context)
            files.append(paper)
        if {p.subject_code for p in files if p.file_type == "question"} != subjects:
            raise ValueError("CMU annual evidence requires all native written subjects")
        exams.append(
            AnnualExam(year, row["source_exam_id"], row["stage_id"], row["reason"], tuple(files))
        )
    if not exams or set(references) != used:
        raise ValueError("CMU annual evidence is empty or has unused primary references")
    return tuple(exams)


@lru_cache(maxsize=1)
def annual_exams() -> tuple[AnnualExam, ...]:
    return read_annual_exams(annual_papers_path(Path(__file__).resolve().parents[3]))


def normalization_papers(papers: Iterable[ParsedPaper]) -> Iterator[ParsedPaper]:
    """Classify each version independently without altering paired acquisition rows."""
    for paper in papers:
        for role, url in paper.files.items():
            yield replace(
                paper,
                files={role: url},
                mirror_files={role: dict(paper.mirror_files[role])}
                if role in paper.mirror_files
                else {},
            )


def resolve_identity(
    *,
    source_exam_id: str,
    year_ad: int,
    category_code: str,
    category_raw: str,
    exam_name_raw: str,
    subject_code: str,
    subject_name_raw: str,
    source_url: str,
    checksum: str,
) -> AnnualExam | None:
    config = HCE_CONFIGS["hce_cmu"]
    year = year_ad - 1911
    if (
        category_code != config.category_code
        or category_raw != config.category_name
        or exam_name_raw != f"{year}學年度{config.exam_name}"
    ):
        return None
    return next(
        (
            annual
            for annual in annual_exams()
            if annual.year_roc == year
            and annual.source_exam_id == source_exam_id
            and any(
                (p.subject_code, p.subject_name_raw, p.source_url, p.checksum)
                == (subject_code, subject_name_raw, source_url, checksum)
                for p in annual.files
            )
        ),
        None,
    )


def validate_annual_papers(repo_root: Path, *, verify_mirror: bool = False) -> int:
    """Require reviewed versions to remain in current state or immutable history."""
    from app.paths import provider_paths
    from app.source_revisions import load_source_revisions, payload_checksum
    from app.state import load_provider_state

    provider = provider_paths(repo_root, "hce_cmu")
    _, catalog, _ = load_provider_state(provider)
    anchors = [
        (
            p.source_exam_id,
            p.year_roc,
            p.category_code,
            p.category_raw,
            p.exam_name_raw,
            p.subject_code,
            p.subject_name_raw,
            p.file_type,
            p.download_url_source,
            p.checksum,
            p.storage_key,
        )
        for p in catalog.papers
    ]
    for revision in load_source_revisions(provider):
        row = revision["source_record"]
        if revision["record_type"] == "paper":
            anchors.append(
                (
                    row["source_exam_id"],
                    row["year_roc"],
                    row["category_code"],
                    row["category_raw"],
                    row["exam_name_raw"],
                    row["subject_code"],
                    row["subject_name_raw"],
                    row["file_type"],
                    row["download_url_source"],
                    row["checksum"],
                    revision["blob_storage_key"],
                )
            )
    config = HCE_CONFIGS["hce_cmu"]
    count = 0
    for annual in read_annual_exams(annual_papers_path(repo_root)):
        for paper in annual.files:
            key = (
                annual.source_exam_id,
                annual.year_roc,
                config.category_code,
                config.category_name,
                f"{annual.year_roc}學年度{config.exam_name}",
                paper.subject_code,
                paper.subject_name_raw,
                paper.file_type,
                paper.source_url,
                paper.checksum,
            )
            matches = [a[-1] for a in anchors if a[:-1] == key]
            if not matches:
                raise ValueError("CMU native evidence lacks a retained source anchor")
            if verify_mirror and any(
                payload_checksum(repo_root / "mirror" / storage_key) != paper.checksum
                for storage_key in set(matches)
            ):
                raise ValueError("CMU retained native checksum mismatch")
            count += 1
    return count
