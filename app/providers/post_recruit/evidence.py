"""Reviewed postal programmes, native employment grades and paper applicability.

Acquisition keys identify source files. A common paper can serve several jobs;
the annual evidence owns that relationship, independently of mirror storage.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.models import NormalizedPaper, SourceExamPage, file_type_label
from app.providers.post_recruit.client import source_file_code
from app.source_material import SourceDate, SourceMaterial

_GRADES = {
    "營運職": ("postal-operational", "營運職"),
    "專業職(一)": ("postal-professional-1", "專業職(一)"),
    "專業職(二)內勤": ("postal-professional-2", "專業職(二)"),
    "專業職(二)外勤": ("postal-professional-2", "專業職(二)"),
}
_SERIES = {
    "postal-recruitment": "中華郵政職階人員甄試",
    "postal-information-recruitment": "中華郵政職階人員資訊類科甄試",
}
_RULE_HOSTS = {"svc.tabf.org.tw", "www.post.gov.tw", "www.motc.gov.tw", "www.cna.com.tw"}


def _text(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


@dataclass(frozen=True)
class PostalJob:
    category_code: str
    grade_scope: str
    occupation: str
    track_id: str

    @property
    def level_id(self) -> str:
        return _GRADES[self.grade_scope][0]

    @property
    def level_label(self) -> str:
        return _GRADES[self.grade_scope][1]

    @property
    def category_label(self) -> str:
        return f"{self.grade_scope}／{self.occupation}"

    @property
    def track_label(self) -> str:
        if self.grade_scope.endswith("內勤"):
            return f"內勤｜{self.occupation}"
        if self.grade_scope.endswith("外勤"):
            return f"外勤｜{self.occupation}"
        return self.occupation

    @property
    def required_slots(self) -> set[str]:
        if self.grade_scope == "營運職":
            common = {"common-1", "common-2", "common-3"}
        elif self.grade_scope == "專業職(一)":
            common = {"common-1", "common-2"}
        else:
            common = {"common"}
        return common | {"professional-1", "professional-2"}


@dataclass(frozen=True)
class NativePaper:
    source_urls: tuple[str, ...]
    source_label: str
    source_context: str
    native_header: str
    subject_name_raw: str
    slot: str
    file_type: str
    checksum: str
    applies_to: tuple[str, ...]
    answer_notes: tuple[str, ...]


@dataclass(frozen=True)
class AnnualExam:
    programme_year_roc: int
    source_exam_id: str
    programme_name: str
    exam_series_id: str
    written_test_date: date
    rule_urls: tuple[str, ...]
    jobs: tuple[PostalJob, ...]
    files: tuple[NativePaper, ...]

    @property
    def series_label(self) -> str:
        return _SERIES[self.exam_series_id]

    def material(self, paper: NativePaper, source_url: str) -> SourceMaterial:
        return SourceMaterial(
            "administered",
            SourceDate("exam_year", self.written_test_date.year),
            source_url,
            f"{self.programme_name}｜{paper.subject_name_raw}｜{file_type_label(paper.file_type)}",
        )


def native_papers_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/post/native-papers-v1.json"


def read_annual_exams(path: Path) -> tuple[AnnualExam, ...]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        set(document) != {"schema_version", "provider_id", "events"}
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 1
        or document["provider_id"] != "post_recruit"
    ):
        raise ValueError("Unsupported postal native evidence")
    years: set[int] = set()
    meanings: dict[str, PostalJob] = {}
    exams = []
    for row in document["events"]:
        year = row["programme_year_roc"]
        if type(year) is not int or not 1 <= year <= 999 or year in years:
            raise ValueError("Invalid or duplicate postal programme year")
        years.add(year)
        written = date.fromisoformat(row["written_test_date"])
        date.fromisoformat(row["reviewed_at"])
        if (
            row["source_exam_id"] != f"post-recruit-{year}"
            or row["exam_series_id"] not in _SERIES
            or row["stage_id"] != "stage-1"
            or not year + 1911 <= written.year <= year + 1912
            or not row["programme_name"]
            or not row["review_notes"]
            or not row["rule_urls"]
        ):
            raise ValueError(
                "Postal annual programme, stage or actual date lacks reviewed evidence"
            )
        for url in row["rule_urls"]:
            parsed = urlsplit(url)
            if parsed.scheme != "https" or parsed.netloc not in _RULE_HOSTS or parsed.fragment:
                raise ValueError("Postal rules require a reviewed issuer document or notice")
        jobs = tuple(PostalJob(**job) for job in row["jobs"])
        by_code = {job.category_code: job for job in jobs}
        if not jobs or len(by_code) != len(jobs):
            raise ValueError("Postal annual job categories must be nonempty and unique")
        for job in jobs:
            if (
                job.grade_scope not in _GRADES
                or not job.occupation.strip()
                or re.fullmatch(r"postal-[a-z0-9-]+", job.category_code) is None
                or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", job.track_id) is None
            ):
                raise ValueError("Postal job lacks its native employment grade or occupation")
            if job.category_code in meanings and meanings[job.category_code] != job:
                raise ValueError("A postal category changes its reviewed native meaning")
            meanings[job.category_code] = job
        files = []
        contexts: set[tuple[str, str]] = set()
        slots: dict[str, set[str]] = defaultdict(set)
        for item in row["files"]:
            paper = NativePaper(
                **{
                    **item,
                    "source_urls": tuple(item["source_urls"]),
                    "applies_to": tuple(item["applies_to"]),
                    "answer_notes": tuple(item["answer_notes"]),
                }
            )
            if (
                not paper.source_urls
                or len(set(paper.source_urls)) != len(paper.source_urls)
                or re.fullmatch(r"[0-9a-f]{64}", paper.checksum) is None
                or not paper.source_label.strip()
                or not paper.source_context.strip()
                or not paper.native_header.strip()
                or not paper.subject_name_raw.strip()
                or paper.file_type not in {"question", "question_answer"}
                or not paper.applies_to
                or len(set(paper.applies_to)) != len(paper.applies_to)
                or not set(paper.applies_to) <= by_code.keys()
                or (paper.answer_notes and paper.file_type != "question_answer")
            ):
                raise ValueError("Invalid postal native paper or source applicability")
            for url in paper.source_urls:
                source_file_code(url)
                key = url, paper.checksum
                if key in contexts:
                    raise ValueError("Duplicate postal native URL/checksum context")
                contexts.add(key)
            grades = {by_code[code].grade_scope for code in paper.applies_to}
            if len(grades) != 1:
                raise ValueError("A postal paper crosses unreviewed native grade scopes")
            for code in paper.applies_to:
                if paper.slot not in by_code[code].required_slots:
                    raise ValueError("Postal paper session is not a slot for that employment grade")
                slots[code].add(paper.slot)
            files.append(paper)
        if any(slots[job.category_code] != job.required_slots for job in jobs):
            raise ValueError("Postal annual job lacks its complete native written-paper set")
        exams.append(
            AnnualExam(
                year,
                row["source_exam_id"],
                row["programme_name"],
                row["exam_series_id"],
                written,
                tuple(row["rule_urls"]),
                jobs,
                tuple(files),
            )
        )
    return tuple(exams)


@lru_cache(maxsize=1)
def annual_exams() -> tuple[AnnualExam, ...]:
    return read_annual_exams(native_papers_path(Path(__file__).resolve().parents[3]))


def resolve_identity(
    *,
    source_exam_id: str,
    year_ad: int,
    category_code: str,
    category_raw: str,
    exam_name_raw: str,
    subject_code: str,
    subject_name_raw: str,
    source_material: SourceMaterial | None,
    checksum: str,
) -> tuple[AnnualExam, PostalJob, NativePaper] | None:
    if source_material is None or source_material.needs_review:
        return None
    for exam in annual_exams():
        if (source_exam_id, year_ad, _text(exam_name_raw)) != (
            exam.source_exam_id,
            exam.programme_year_roc + 1911,
            _text(exam.programme_name),
        ):
            continue
        job = next((job for job in exam.jobs if job.category_code == category_code), None)
        if job is None or _text(category_raw) != _text(job.category_label):
            continue
        url = source_material.evidence_url
        for paper in exam.files:
            if (
                url in paper.source_urls
                and checksum == paper.checksum
                and subject_code == source_file_code(url)
                and subject_name_raw == paper.subject_name_raw
                and job.category_code in paper.applies_to
                and source_material == exam.material(paper, url)
            ):
                return exam, job, paper
    return None


def project_native_page(page: SourceExamPage, checksums: dict[str, str]) -> SourceExamPage:
    exam = next(
        (
            exam
            for exam in annual_exams()
            if (page.source_exam_id, page.year_roc, page.year_ad)
            == (exam.source_exam_id, exam.programme_year_roc, exam.programme_year_roc + 1911)
        ),
        None,
    )
    projected = []
    for raw in page.papers:
        if raw.category_code != "post-recruit" or set(raw.files) != {"question"}:
            raise ValueError("Postal projection requires native acquisition records")
        url = raw.files["question"]
        if raw.subject_code != source_file_code(url) or url not in checksums:
            raise ValueError("Postal projection requires a verified native source key and payload")
        candidates = []
        if exam is not None:
            candidates = [
                paper
                for paper in exam.files
                if url in paper.source_urls
                and _text(raw.subject_name_raw) == _text(paper.source_label)
                and _text(raw.category_raw) == _text(paper.source_context)
            ]
        native = next((paper for paper in candidates if checksums[url] == paper.checksum), None)
        application_sets = {frozenset(paper.applies_to) for paper in candidates}
        # Matching live source categories remain candidate context when bytes
        # change. They do not inherit approved grade, role, date or stage facts.
        applicable = frozenset[str]()
        if native is not None:
            applicable = frozenset(native.applies_to)
        elif len(application_sets) == 1:
            applicable = next(iter(application_sets))
        unknown = SourceMaterial(
            "unknown",
            SourceDate("unknown", None),
            url,
            raw.subject_name_raw,
            "Postal native payload, source applicability, role or annual rules require review",
        )
        if exam is None or not applicable:
            projected.append(replace(raw, source_material=unknown, mirror_files={}))
            continue
        for job in exam.jobs:
            if job.category_code not in applicable:
                continue
            role = native.file_type if native else "question"
            projected.append(
                replace(
                    raw,
                    category_raw=job.category_label,
                    category_code=job.category_code,
                    source_category_code=raw.category_code,
                    subject_name_raw=native.subject_name_raw if native else raw.subject_name_raw,
                    files={role: url},
                    mirror_files={},
                    source_material=exam.material(native, url) if native else unknown,
                )
            )
    return replace(
        page, papers=projected, exam_name_raw=exam.programme_name if exam else page.exam_name_raw
    )


def validate_public_papers(
    papers: Iterable[NormalizedPaper], *, complete_history: bool = False
) -> None:
    selected = [paper for paper in papers if paper.provider_id == "post_recruit"]
    slots: dict[tuple[int, str], set[str]] = defaultdict(set)
    observed: set[tuple[int, str, str, str]] = set()
    active: dict[tuple[int, str], PostalJob] = {}
    for paper in selected:
        native = resolve_identity(
            source_exam_id=paper.source_exam_id,
            year_ad=paper.year_roc + 1911,
            category_code=paper.category_code,
            category_raw=paper.category_raw,
            exam_name_raw=paper.exam_name_raw,
            subject_code=paper.subject_code,
            subject_name_raw=paper.subject_name_raw,
            source_material=paper.source_material,
            checksum=paper.checksum,
        )
        if (
            native is None
            or paper.file_type != native[2].file_type
            or paper.source_material is None
            or paper.download_url_source != paper.source_material.evidence_url
        ):
            raise ValueError("Postal publication requires reviewed native job, role and date facts")
        exam, job, file = native
        signature = exam.programme_year_roc, job.category_code, file.checksum, file.slot
        if signature in observed:
            raise ValueError("Duplicate postal native job/payload reference")
        observed.add(signature)
        active[exam.programme_year_roc, job.category_code] = job
        slots[exam.programme_year_roc, job.category_code].add(file.slot)
    if complete_history:
        active = {
            (exam.programme_year_roc, job.category_code): job
            for exam in annual_exams()
            for job in exam.jobs
        }
    for (year, code), job in active.items():
        if slots[year, code] != job.required_slots:
            raise ValueError("Postal publication omitted a native job or written-paper slot")
    expected = {
        (exam.programme_year_roc, code, file.checksum, file.slot)
        for exam in annual_exams()
        for file in exam.files
        for code in file.applies_to
        if (exam.programme_year_roc, code) in active
    }
    if observed != expected:
        raise ValueError("Postal publication omitted a reviewed native paper revision")


def validate_native_papers(repo_root: Path, *, verify_mirror: bool = False) -> int:
    from app.paths import provider_paths
    from app.source_revisions import load_source_revisions
    from app.state import load_provider_state

    provider = provider_paths(repo_root, "post_recruit")
    _, catalog, _ = load_provider_state(provider)
    anchors: dict[tuple[int, str, str, str], list[str]] = defaultdict(list)
    for retained in catalog.papers:
        anchors[
            retained.year_roc,
            retained.source_exam_id,
            retained.download_url_source,
            retained.checksum,
        ].append(retained.storage_key)
    for entry in load_source_revisions(provider):
        if entry["record_type"] == "paper":
            record: dict[str, Any] = entry["source_record"]
            anchors[
                record["year_roc"],
                record["source_exam_id"],
                record["download_url_source"],
                record["checksum"],
            ].append(entry["blob_storage_key"])
    count = 0
    for exam in read_annual_exams(native_papers_path(repo_root)):
        for paper in exam.files:
            keys = [
                key
                for url in paper.source_urls
                for key in anchors.get(
                    (exam.programme_year_roc, exam.source_exam_id, url, paper.checksum), []
                )
            ]
            if not keys:
                raise ValueError(
                    "Postal native evidence lacks a current or immutable source anchor"
                )
            if verify_mirror:
                for key in set(keys):
                    with (repo_root / "mirror" / key).open("rb") as stream:
                        if hashlib.file_digest(stream, "sha256").hexdigest() != paper.checksum:
                            raise ValueError(
                                "Postal native evidence checksum differs from mirror bytes"
                            )
            count += 1
    return count


def reviewed_native_revisions(entries: Iterable[dict[str, Any]]) -> list[str]:
    """Explain earlier generic references without rewriting their old claims."""
    native = {
        (exam.programme_year_roc, exam.source_exam_id, url, paper.checksum)
        for exam in annual_exams()
        for paper in exam.files
        for url in paper.source_urls
    }
    reviewed = []
    for entry in entries:
        record = entry["source_record"]
        if (
            entry["record_type"] == "paper"
            and (
                record["year_roc"],
                record["source_exam_id"],
                record["download_url_source"],
                record["checksum"],
            )
            in native
        ):
            reviewed.append(entry["id"])
    return sorted(reviewed)
