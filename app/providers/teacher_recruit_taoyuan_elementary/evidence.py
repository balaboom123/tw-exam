"""Native recruitment categories and shared written-paper applicability.

Annual category numbers are source facts. Stable tracks describe the posts;
neither booklet letters nor prerequisite language certificates are exam levels.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.models import ExamAttachment, NormalizedPaper, SourceExamPage, file_type_label
from app.providers.base import UnreviewedSourceFile
from app.source_material import SourceDate, SourceMaterial

PROVIDER_ID = "teacher_recruit_taoyuan_elementary"
CANONICAL_CATEGORY = "桃園市國小教師甄試"
_ROLES = {"question", "answer", "corrected_answer"}


def _text(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


@dataclass(frozen=True)
class WeightedBooklet:
    subject_code: str
    weight: int


@dataclass(frozen=True)
class RecruitmentCategory:
    category_code: str
    label: str
    track_id: str
    eligibility_note: str
    booklets: tuple[WeightedBooklet, ...]


@dataclass(frozen=True)
class NativePaper:
    subject_code: str
    subject_name_raw: str
    file_type: str
    source_url: str
    checksum: str
    native_header: str
    page_count: int
    inspected_pages: tuple[int, ...]


@dataclass(frozen=True)
class AnnualExam:
    year_roc: int
    source_exam_id: str
    programme_name: str
    rule_title: str
    rule_url: str
    rule_checksum: str
    rule_publication_date: str
    written_test_date: str
    stage_id: str
    rule_page_count: int
    rule_inspected_pages: tuple[int, ...]
    notice_urls: tuple[str, ...]
    reviewed_at: str
    review_notes: str
    jobs: tuple[RecruitmentCategory, ...]
    files: tuple[NativePaper, ...]

    def material(self, paper: NativePaper) -> SourceMaterial:
        return SourceMaterial(
            "administered",
            SourceDate("exam_year", date.fromisoformat(self.written_test_date).year),
            paper.source_url,
            f"{self.programme_name}｜{paper.subject_name_raw}｜{file_type_label(paper.file_type)}",
        )

    def rule_attachment(self) -> ExamAttachment:
        return ExamAttachment(
            title=self.rule_title,
            file_type="instructions",
            download_url_source=self.rule_url,
            source_material=SourceMaterial(
                "reference",
                SourceDate("publication_year", date.fromisoformat(self.rule_publication_date).year),
                self.rule_url,
                self.rule_title,
            ),
        )


def native_papers_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/teacher-recruit-taoyuan-elementary/native-papers-v1.json"


def _checksum(value: str) -> bool:
    return re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _download_url(value: str) -> bool:
    return (
        re.fullmatch(
            r"https://elementary\.tyc\.edu\.tw/web/download_file\.aspx\?ids=[0-9a-f]{31,32}"
            r"(?:&openExternalBrowser=1)?",
            value,
        )
        is not None
    )


def read_annual_exams(path: Path) -> tuple[AnnualExam, ...]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        set(document) != {"schema_version", "provider_id", "events"}
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 1
        or document["provider_id"] != PROVIDER_ID
        or not document["events"]
    ):
        raise ValueError("Unsupported Taoyuan recruitment native evidence")
    exams = []
    years: set[int] = set()
    track_meanings: dict[str, str] = {}
    for row in document["events"]:
        fields = dict(row)
        fields["jobs"] = tuple(
            RecruitmentCategory(
                **{**job, "booklets": tuple(WeightedBooklet(**b) for b in job["booklets"])}
            )
            for job in fields["jobs"]
        )
        fields["files"] = tuple(
            NativePaper(**{**f, "inspected_pages": tuple(f["inspected_pages"])})
            for f in fields["files"]
        )
        for name in ("rule_inspected_pages", "notice_urls"):
            fields[name] = tuple(fields[name])
        exam = AnnualExam(**fields)
        year = exam.year_roc
        written = date.fromisoformat(exam.written_test_date)
        published = date.fromisoformat(exam.rule_publication_date)
        reviewed = date.fromisoformat(exam.reviewed_at)
        if (
            type(year) is not int
            or not 1 <= year <= 999
            or year in years
            or exam.source_exam_id != f"teacher-recruit-taoyuan-elementary-{year}"
            or exam.stage_id != "stage-1"
            or written.year != year + 1911
            or not published <= written <= reviewed
            or not exam.programme_name.strip()
            or not exam.rule_title.strip()
            or not exam.review_notes.strip()
            or not _download_url(exam.rule_url)
            or not _checksum(exam.rule_checksum)
            or type(exam.rule_page_count) is not int
            or exam.rule_page_count < 1
            or not exam.rule_inspected_pages
            or tuple(sorted(set(exam.rule_inspected_pages))) != exam.rule_inspected_pages
            or any(
                type(p) is not int or not 1 <= p <= exam.rule_page_count
                for p in exam.rule_inspected_pages
            )
            or not exam.notice_urls
            or len(set(exam.notice_urls)) != len(exam.notice_urls)
            or any(
                re.fullmatch(
                    r"https://elementary\.tyc\.edu\.tw/web/news-info\.aspx\?id=[0-9a-f]{31,32}", u
                )
                is None
                for u in exam.notice_urls
            )
        ):
            raise ValueError("Taoyuan annual stage, date or rule lacks reviewed native evidence")
        years.add(year)
        subjects: dict[str, str] = {}
        roles: dict[str, set[str]] = {}
        urls: set[str] = set()
        for paper in exam.files:
            expected_code = (
                "subject-" + hashlib.sha1(paper.subject_name_raw.encode("utf-8")).hexdigest()[:8]
            )
            if (
                not paper.subject_name_raw.strip()
                or paper.subject_code != expected_code
                or paper.file_type not in _ROLES
                or not _download_url(paper.source_url)
                or paper.source_url in urls
                or paper.source_url == exam.rule_url
                or not _checksum(paper.checksum)
                or not paper.native_header.strip()
                or type(paper.page_count) is not int
                or paper.page_count < 1
                or paper.inspected_pages != tuple(range(1, paper.page_count + 1))
                or any(type(p) is not int for p in paper.inspected_pages)
                or paper.file_type in roles.setdefault(paper.subject_code, set())
            ):
                raise ValueError("Invalid Taoyuan native paper, role or complete page review")
            urls.add(paper.source_url)
            roles[paper.subject_code].add(paper.file_type)
            subjects[paper.subject_code] = paper.subject_name_raw
        if not roles or any(observed != _ROLES for observed in roles.values()):
            raise ValueError("Taoyuan reviewed booklets require all three distinct file roles")
        codes: set[str] = set()
        tracks: set[str] = set()
        applicable: set[str] = set()
        for job in exam.jobs:
            selected = {b.subject_code for b in job.booklets}
            if (
                re.fullmatch(r"[0-9]{2}", job.category_code) is None
                or job.category_code == "00"
                or job.category_code in codes
                or not job.label.strip()
                or not job.eligibility_note.strip()
                or re.fullmatch(r"taoyuan-elementary-[a-z0-9]+(?:-[a-z0-9]+)*", job.track_id)
                is None
                or job.track_id in tracks
                or len(selected) != len(job.booklets)
                or len(selected) != 2
                or not selected <= subjects.keys()
                or any(type(b.weight) is not int or not 0 < b.weight < 100 for b in job.booklets)
                or sum(b.weight for b in job.booklets) != 100
                or job.track_id in track_meanings
                and track_meanings[job.track_id] != _text(job.label)
            ):
                raise ValueError("Taoyuan category or weighted booklet applicability is invalid")
            codes.add(job.category_code)
            tracks.add(job.track_id)
            applicable.update(selected)
            track_meanings[job.track_id] = _text(job.label)
        if not codes or applicable != subjects.keys():
            raise ValueError("Taoyuan native booklets lack complete category applicability")
        exams.append(exam)
    return tuple(exams)


@lru_cache(maxsize=1)
def annual_exams() -> tuple[AnnualExam, ...]:
    return read_annual_exams(native_papers_path(Path(__file__).resolve().parents[3]))


def resolve_identity(
    *,
    source_exam_id: str,
    year_ad: int,
    exam_name_raw: str,
    category_code: str,
    category_raw: str,
    subject_code: str,
    subject_name_raw: str,
    source_url: str,
    checksum: str,
    source_material: SourceMaterial | None,
) -> tuple[AnnualExam, RecruitmentCategory, NativePaper] | None:
    for exam in annual_exams():
        if (source_exam_id, year_ad, _text(exam_name_raw)) != (
            exam.source_exam_id,
            exam.year_roc + 1911,
            _text(exam.programme_name),
        ):
            continue
        for job in exam.jobs:
            if (category_code, _text(category_raw)) != (job.category_code, _text(job.label)):
                continue
            if subject_code not in {b.subject_code for b in job.booklets}:
                continue
            for paper in exam.files:
                if (subject_code, subject_name_raw, source_url, checksum) == (
                    paper.subject_code,
                    paper.subject_name_raw,
                    paper.source_url,
                    paper.checksum,
                ) and source_material == exam.material(paper):
                    return exam, job, paper
    return None


def project_native_page(page: SourceExamPage, checksums: dict[str, str]) -> SourceExamPage:
    """Project a complete native listing without duplicating its stored payloads.

    A changed annual source must be reviewed before replacing retained state.
    Never carry yesterday's category applicability onto an unknown paper.
    """
    exam = next(
        (
            e
            for e in annual_exams()
            if (page.provider_id, page.source_exam_id, page.year_roc, page.year_ad)
            == (PROVIDER_ID, e.source_exam_id, e.year_roc, e.year_roc + 1911)
        ),
        None,
    )
    if exam is None:
        raise UnreviewedSourceFile(
            "Taoyuan annual recruitment requires native rule and paper review"
        )
    if _text(page.exam_name_raw) not in {
        _text(exam.programme_name),
        _text(f"{exam.year_roc}年度{CANONICAL_CATEGORY}"),
        _text(f"{exam.year_roc}學年度{CANONICAL_CATEGORY}"),
    }:
        raise ValueError("Taoyuan source programme does not match its annual rule")
    observed = []
    raw_by_url = {}
    for raw in page.papers:
        if raw.category_raw != CANONICAL_CATEGORY or raw.category_code != str(exam.year_roc):
            raise ValueError("Taoyuan projection requires native acquisition categories")
        for role, url in raw.files.items():
            observed.append((raw.subject_code, raw.subject_name_raw, role, url, checksums.get(url)))
            raw_by_url[url] = raw
    expected = [
        (p.subject_code, p.subject_name_raw, p.file_type, p.source_url, p.checksum)
        for p in exam.files
    ]
    if Counter(observed) != Counter(expected):
        raise UnreviewedSourceFile(
            "Taoyuan native listing or payload changed; retain state pending review"
        )
    projected = []
    for job in exam.jobs:
        for paper in exam.files:
            if paper.subject_code not in {b.subject_code for b in job.booklets}:
                continue
            raw = raw_by_url[paper.source_url]
            projected.append(
                replace(
                    raw,
                    category_raw=job.label,
                    category_code=job.category_code,
                    source_category_code=raw.category_code,
                    files={paper.file_type: paper.source_url},
                    mirror_files={paper.file_type: raw.mirror_files[paper.file_type]}
                    if paper.file_type in raw.mirror_files
                    else {},
                    source_material=exam.material(paper),
                )
            )
    return replace(page, exam_name_raw=exam.programme_name, papers=projected)


def validate_public_papers(
    papers: Iterable[NormalizedPaper],
    *,
    complete_history: bool = False,
) -> None:
    selected = [p for p in papers if p.provider_id == PROVIDER_ID]
    observed: Counter[tuple[int, str, str, str]] = Counter()
    active: set[tuple[int, str]] = set()
    for paper in selected:
        native = resolve_identity(
            source_exam_id=paper.source_exam_id,
            year_ad=paper.year_roc + 1911,
            exam_name_raw=paper.exam_name_raw,
            category_code=paper.category_code,
            category_raw=paper.category_raw,
            subject_code=paper.subject_code,
            subject_name_raw=paper.subject_name_raw,
            source_url=paper.download_url_source,
            checksum=paper.checksum,
            source_material=paper.source_material,
        )
        if native is None or paper.file_type != native[2].file_type:
            raise ValueError("Taoyuan publication requires exact native category, paper and role")
        exam, job, file = native
        active.add((exam.year_roc, job.category_code))
        observed[exam.year_roc, job.category_code, file.source_url, file.checksum] += 1
    if complete_history:
        active = {(e.year_roc, j.category_code) for e in annual_exams() for j in e.jobs}
    expected = Counter(
        (e.year_roc, j.category_code, f.source_url, f.checksum)
        for e in annual_exams()
        for j in e.jobs
        for f in e.files
        if (e.year_roc, j.category_code) in active
        and f.subject_code in {b.subject_code for b in j.booklets}
    )
    if observed != expected:
        raise ValueError("Taoyuan publication omitted or duplicated a native category or file role")


def validate_native_papers(repo_root: Path, *, verify_mirror: bool = False) -> int:
    from app.paths import provider_paths
    from app.source_revisions import load_source_revisions, payload_checksum
    from app.state import load_provider_state

    provider = provider_paths(repo_root, PROVIDER_ID)
    pages, catalog, _ = load_provider_state(provider)
    anchors: dict[tuple[str, int, str, str], set[str]] = {}
    records = [(vars(p), p.storage_key) for p in catalog.papers]
    records.extend(
        (vars(a) | {"source_exam_id": p.source_exam_id, "year_roc": p.year_roc}, a.storage_key)
        for p in pages
        for a in p.attachments
    )
    records.extend(
        (r["source_record"], r["blob_storage_key"]) for r in load_source_revisions(provider)
    )
    for record, key in records:
        anchors.setdefault(
            (
                record["source_exam_id"],
                record["year_roc"],
                record["download_url_source"],
                record["checksum"],
            ),
            set(),
        ).add(key)
    count = 0
    for exam in read_annual_exams(native_papers_path(repo_root)):
        files = [(f.source_url, f.checksum) for f in exam.files]
        files.append((exam.rule_url, exam.rule_checksum))
        for url, checksum in files:
            keys = anchors.get((exam.source_exam_id, exam.year_roc, url, checksum), set())
            if not keys or "" in keys:
                raise ValueError(
                    "Taoyuan native evidence lacks retained source or annual-rule bytes"
                )
            if verify_mirror:
                for key in keys:
                    if payload_checksum(repo_root / "mirror" / key) != checksum:
                        raise ValueError("Taoyuan native mirror differs from reviewed bytes")
            count += 1
    return count


def legacy_category_targets(
    previous: NormalizedPaper, current: Iterable[NormalizedPaper]
) -> list[NormalizedPaper]:
    """Follow the reviewed generic acquisition category to its applicable posts.

    Matching bytes alone cannot establish a successor. Both the complete old
    acquisition context and every new category's native applicability must match.
    """
    if previous.provider_id != PROVIDER_ID or previous.source_material is not None:
        return []
    for exam in annual_exams():
        if (
            previous.source_exam_id != exam.source_exam_id
            or previous.year_roc != exam.year_roc
            or previous.category_code != str(exam.year_roc)
            or previous.category_raw != CANONICAL_CATEGORY
            or previous.exam_name_raw
            not in {
                f"{exam.year_roc}學年度{CANONICAL_CATEGORY}",
                f"{exam.year_roc}年度{CANONICAL_CATEGORY}",
            }
        ):
            continue
        native = next(
            (
                f
                for f in exam.files
                if (f.subject_code, f.subject_name_raw, f.file_type, f.source_url, f.checksum)
                == (
                    previous.subject_code,
                    previous.subject_name_raw,
                    previous.file_type,
                    previous.download_url_source,
                    previous.checksum,
                )
            ),
            None,
        )
        if native is None:
            continue
        targets = []
        for paper in current:
            if paper.provider_id != PROVIDER_ID or paper.file_type != native.file_type:
                continue
            identity = resolve_identity(
                source_exam_id=paper.source_exam_id,
                year_ad=paper.year_roc + 1911,
                exam_name_raw=paper.exam_name_raw,
                category_code=paper.category_code,
                category_raw=paper.category_raw,
                subject_code=paper.subject_code,
                subject_name_raw=paper.subject_name_raw,
                source_url=paper.download_url_source,
                checksum=paper.checksum,
                source_material=paper.source_material,
            )
            if (
                identity is not None
                and identity[2] == native
                and paper.classification_confidence == "high"
                and paper.track_id == identity[1].track_id
                and paper.stage_id == "stage-1"
            ):
                targets.append(paper)
        expected = {
            j.category_code
            for j in exam.jobs
            if native.subject_code in {b.subject_code for b in j.booklets}
        }
        if {p.category_code for p in targets} == expected:
            return targets
    return []


def reviewed_native_revisions(entries: Iterable[dict[str, Any]]) -> list[str]:
    """Keep old generic claims immutable while explaining their native originals."""
    native = {
        (e.source_exam_id, e.year_roc, f.source_url, f.checksum)
        for e in annual_exams()
        for f in e.files
    }
    return sorted(
        r["id"]
        for r in entries
        if r["record_type"] == "paper"
        and (
            r["source_record"]["source_exam_id"],
            r["source_record"]["year_roc"],
            r["source_record"]["download_url_source"],
            r["source_record"]["checksum"],
        )
        in native
    )
