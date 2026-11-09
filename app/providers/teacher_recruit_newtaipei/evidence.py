"""Annual recruitment meaning and written-booklet applicability.

The archive contract owns native members. This catalogue owns the programmes,
posts, shared-paper relationships, rules and answer-review documents.
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

from app.models import ExamAttachment, NormalizedPaper, ParsedPaper, SourceExamPage
from app.providers.base import UnreviewedSourceFile
from app.providers.teacher_recruit_newtaipei.archive_plan import (
    PROVIDER_ID,
    ReviewedArchive,
    native_archives,
)
from app.providers.teacher_recruit_newtaipei.client import NewTaipeiEvent, NewTaipeiNotice
from app.source_material import SourceDate, SourceMaterial

CANONICAL_CATEGORY = "新北市教師甄試"
_PAPER_ROLES = {"question", "answer", "corrected_answer", "answer_clarification"}
_REFERENCE_ROLES = {"instructions", "source_document"}


def _text(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


@dataclass(frozen=True)
class NativeNotice:
    uuid: str
    title: str
    answer_review: bool


@dataclass(frozen=True)
class RecruitmentCategory:
    code: str
    label: str
    track_id: str
    booklets: tuple[str, ...]
    registration_groups: tuple[str, ...]
    eligibility_note: str


@dataclass(frozen=True)
class NativeDocument:
    document_id: str
    subject_name: str
    file_type: str
    title: str
    source_url: str
    checksum: str
    size: int
    publication_date: str
    page_count: int
    inspected_pages: tuple[int, ...]
    native_header: str
    applies_to_booklets: tuple[str, ...]

    @property
    def subject_code(self) -> str:
        return "subject-" + hashlib.sha1(self.subject_name.encode("utf-8")).hexdigest()[:8]


@dataclass(frozen=True)
class NativePaper:
    subject_code: str
    subject_name: str
    file_type: str
    source_url: str
    checksum: str


@dataclass(frozen=True)
class AnnualExam:
    source_exam_id: str
    year_roc: int
    programme_name: str
    series_id: str
    series_label: str
    domain_id: str
    family_id: str
    written_test_date: str
    reviewed_at: str
    rule_document_id: str
    category_document_id: str
    archive_checksum: str
    review_notes: str
    notices: tuple[NativeNotice, ...]
    documents: tuple[NativeDocument, ...]
    jobs: tuple[RecruitmentCategory, ...]

    @property
    def archive(self) -> ReviewedArchive | None:
        return next((a for a in native_archives() if a.checksum == self.archive_checksum), None)

    def acquisition_event(self) -> NewTaipeiEvent:
        notices = tuple(
            NewTaipeiNotice(
                n.uuid,
                n.title,
                self.programme_name,
                self.year_roc + 1911,
                self.year_roc,
                self.source_exam_id.rsplit("-", 1)[-1],
                self.programme_name,
                n.answer_review,
            )
            for n in self.notices
        )
        return NewTaipeiEvent(notices[0], (notices[0], *sorted(notices[1:], key=lambda n: n.uuid)))

    def papers_for(self, job: RecruitmentCategory) -> tuple[NativePaper, ...]:
        papers = []
        if self.archive is not None:
            for member in self.archive.files:
                if member.file_type in _PAPER_ROLES and member.subject_name in job.booklets:
                    papers.append(
                        NativePaper(
                            member.subject_code,
                            member.subject_name,
                            member.file_type,
                            self.archive.locator(member),
                            member.checksum,
                        )
                    )
        for document in self.documents:
            if document.file_type in _PAPER_ROLES and set(document.applies_to_booklets) & set(
                job.booklets
            ):
                papers.append(
                    NativePaper(
                        document.subject_code,
                        document.subject_name,
                        document.file_type,
                        document.source_url,
                        document.checksum,
                    )
                )
        return tuple(papers)

    def material(self, paper: NativePaper) -> SourceMaterial:
        return SourceMaterial(
            "administered",
            SourceDate("exam_year", date.fromisoformat(self.written_test_date).year),
            paper.source_url,
            f"{self.programme_name}｜{paper.subject_name}",
        )

    def attachments(self) -> list[ExamAttachment]:
        attachments = [
            ExamAttachment(
                d.title,
                d.file_type,
                d.source_url,
                source_material=SourceMaterial(
                    "reference",
                    SourceDate("publication_year", date.fromisoformat(d.publication_date).year),
                    d.source_url,
                    d.title,
                ),
            )
            for d in self.documents
            if d.file_type in _REFERENCE_ROLES
        ]
        archive = self.archive
        if archive is not None:
            originals = [("source_archive", archive.source_url, archive.programme_name)]
            originals.extend(
                (
                    "source_document",
                    archive.locator(m),
                    f"{m.subject_name}｜來源原檔（未提供參考答案）",
                )
                for m in archive.files
                if m.file_type == "source_document"
            )
            for role, url, title in originals:
                attachments.append(
                    ExamAttachment(
                        title,
                        role,
                        url,
                        source_material=SourceMaterial(
                            "reference",
                            SourceDate("publication_year", self.year_roc + 1911),
                            url,
                            title,
                        ),
                    )
                )
        return attachments


def native_programmes_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/teacher-recruit-newtaipei/native-programmes-v1.json"


def read_annual_exams(path: Path) -> tuple[AnnualExam, ...]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        set(document) != {"schema_version", "provider_id", "events"}
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 1
        or document["provider_id"] != PROVIDER_ID
        or not document["events"]
    ):
        raise ValueError("Unsupported New Taipei native programme evidence")
    exams = []
    events: set[str] = set()
    contexts: set[tuple[str, str]] = set()
    for row in document["events"]:
        fields = dict(row)
        fields["notices"] = tuple(NativeNotice(**n) for n in fields["notices"])
        fields["documents"] = tuple(
            NativeDocument(
                **{
                    **d,
                    "inspected_pages": tuple(d["inspected_pages"]),
                    "applies_to_booklets": tuple(d["applies_to_booklets"]),
                }
            )
            for d in fields["documents"]
        )
        fields["jobs"] = tuple(
            RecruitmentCategory(
                **{
                    **j,
                    "booklets": tuple(j["booklets"]),
                    "registration_groups": tuple(j["registration_groups"]),
                }
            )
            for j in fields["jobs"]
        )
        exam = AnnualExam(**fields)
        written = date.fromisoformat(exam.written_test_date)
        if (
            exam.source_exam_id in events
            or type(exam.year_roc) is not int
            or not 1 <= exam.year_roc <= 999
            or written.year != exam.year_roc + 1911
            or date.fromisoformat(exam.reviewed_at) < written
            or not exam.programme_name.strip()
            or not exam.series_label.strip()
            or not exam.review_notes.strip()
            or (exam.domain_id, exam.family_id, exam.series_id)
            not in {
                ("teacher", "teacher-exam", "newtaipei-junior-teacher-recruitment"),
                ("employment", "employment-exam", "newtaipei-preschool-care-recruitment"),
            }
            or exam.source_exam_id
            != f"teacher-recruit-newtaipei-{exam.year_roc}-"
            + ("junior" if exam.domain_id == "teacher" else "preschool")
            or not exam.notices
            or exam.notices[0].answer_review
            or any(not n.answer_review for n in exam.notices[1:])
            or len({n.uuid for n in exam.notices}) != len(exam.notices)
            or any(
                re.fullmatch(r"[0-9a-f]{31,32}", n.uuid) is None or not n.title.strip()
                for n in exam.notices
            )
        ):
            raise ValueError("New Taipei programme, notice, purpose or date lacks native evidence")
        events.add(exam.source_exam_id)
        archive = exam.archive
        if bool(exam.archive_checksum) != (archive is not None) or (
            archive is not None
            and (archive.source_exam_id, archive.year_roc, archive.programme_name)
            != (exam.source_exam_id, exam.year_roc, exam.programme_name)
        ):
            raise ValueError("New Taipei annual programme does not match its reviewed archive")
        documents = {d.document_id: d for d in exam.documents}
        if len(documents) != len(exam.documents):
            raise ValueError("Repeated New Taipei native document ID")
        for d in exam.documents:
            if (
                re.fullmatch(r"[a-z][a-z0-9-]*", d.document_id) is None
                or not d.subject_name.strip()
                or not d.title.strip()
                or not d.native_header.strip()
                or d.file_type not in _PAPER_ROLES | _REFERENCE_ROLES
                or re.fullmatch(
                    r"https://career\.ntpc\.edu\.tw/web-elec-bulletin/open/oauth_data/op_api/download/[0-9a-f]{31,32}",
                    d.source_url,
                )
                is None
                or re.fullmatch(r"[0-9a-f]{64}", d.checksum) is None
                or (exam.source_exam_id, d.source_url) in contexts
                or type(d.size) is not int
                or not 0 < d.size <= 25 * 1024 * 1024
                or not date.fromisoformat(d.publication_date)
                <= date.fromisoformat(exam.reviewed_at)
                or type(d.page_count) is not int
                or d.page_count < 1
                or not d.inspected_pages
                or tuple(sorted(set(d.inspected_pages))) != d.inspected_pages
                or any(type(p) is not int or not 1 <= p <= d.page_count for p in d.inspected_pages)
                or bool(d.applies_to_booklets) != (d.file_type in _PAPER_ROLES)
                or len(set(d.applies_to_booklets)) != len(d.applies_to_booklets)
            ):
                raise ValueError("Invalid New Taipei native document, role or page review")
            contexts.add((exam.source_exam_id, d.source_url))
        if (
            exam.rule_document_id not in documents
            or exam.category_document_id not in documents
            or documents[exam.rule_document_id].file_type != "instructions"
            or documents[exam.category_document_id].file_type not in _REFERENCE_ROLES
        ):
            raise ValueError(
                "New Taipei programme needs its exact rule and native category evidence"
            )
        subjects = (
            {m.subject_name for m in archive.files if m.file_type == "question"}
            if archive
            else set()
        )
        subjects.update(
            b for d in exam.documents if d.file_type == "question" for b in d.applies_to_booklets
        )
        original_roles: dict[str, set[str]] = {}
        omitted_answers = set()
        if archive is not None:
            for member in archive.files:
                if member.file_type == "source_document":
                    omitted_answers.add(member.subject_name)
                else:
                    original_roles.setdefault(member.subject_name, set()).add(member.file_type)
        for d in exam.documents:
            if d.file_type in {"question", "answer"}:
                if d.applies_to_booklets != (d.subject_name,):
                    raise ValueError("New Taipei original PDF lacks its exact booklet subject")
                roles = original_roles.setdefault(d.subject_name, set())
                if d.file_type in roles:
                    raise ValueError("Repeated New Taipei original booklet role")
                roles.add(d.file_type)
        if any(
            roles != ({"question"} if subject in omitted_answers else {"question", "answer"})
            for subject, roles in original_roles.items()
        ):
            raise ValueError("New Taipei native original question/key coverage is incomplete")
        jobs: set[str] = set()
        tracks: set[str] = set()
        used: set[str] = set()
        for job in exam.jobs:
            if (
                re.fullmatch(r"job-[a-z0-9]+(?:-[a-z0-9]+)*", job.code) is None
                or job.code in jobs
                or job.track_id in tracks
                or not job.track_id.startswith("newtaipei-")
                or not job.label.strip()
                or not job.eligibility_note.strip()
                or not job.registration_groups
                or len(set(job.registration_groups)) != len(job.registration_groups)
                or any(not g.strip() for g in job.registration_groups)
                or not job.booklets
                or len(set(job.booklets)) != len(job.booklets)
                or not set(job.booklets) <= subjects
            ):
                raise ValueError(
                    "Invalid New Taipei native recruitment category or paper applicability"
                )
            jobs.add(job.code)
            tracks.add(job.track_id)
            used.update(job.booklets)
            papers = exam.papers_for(job)
            keys = {(p.subject_code, p.file_type) for p in papers}
            if not papers or len(keys) != len(papers):
                raise ValueError("New Taipei category duplicates or omits native written files")
        if (
            not jobs
            or used != subjects
            or any(not set(d.applies_to_booklets) <= used for d in exam.documents)
        ):
            raise ValueError("New Taipei native booklets lack complete recruitment applicability")
        exams.append(exam)
    return tuple(exams)


@lru_cache(maxsize=1)
def annual_exams() -> tuple[AnnualExam, ...]:
    return read_annual_exams(native_programmes_path(Path(__file__).resolve().parents[3]))


def project_native_page(page: SourceExamPage, checksums: dict[str, str]) -> SourceExamPage:
    exam = next(
        (
            e
            for e in annual_exams()
            if (page.provider_id, page.source_exam_id, page.year_roc, page.year_ad)
            == (PROVIDER_ID, e.source_exam_id, e.year_roc, e.year_roc + 1911)
        ),
        None,
    )
    if exam is None or _text(page.exam_name_raw) != _text(
        f"{exam.year_roc}學年度{CANONICAL_CATEGORY}"
    ):
        raise UnreviewedSourceFile(
            "New Taipei programme requires native annual review before projection"
        )
    observed: list[tuple[str, str, str | None]] = []
    for raw in page.papers:
        if (raw.category_code, raw.category_raw) != (str(exam.year_roc), CANONICAL_CATEGORY):
            raise ValueError("New Taipei projection requires the original acquisition context")
        observed.extend((role, url, checksums.get(url)) for role, url in raw.files.items())
    expected = [
        (d.file_type, d.source_url, d.checksum)
        for d in exam.documents
        if d.file_type in _PAPER_ROLES
    ]
    if exam.archive is not None:
        expected.append(("question_answer", exam.archive.source_url, exam.archive.checksum))
    if Counter(observed) != Counter(expected):
        raise UnreviewedSourceFile(
            "New Taipei listing or payload changed; retain previous event pending review"
        )
    projected = []
    for job in exam.jobs:
        for paper in exam.papers_for(job):
            projected.append(
                ParsedPaper(
                    job.label,
                    job.code,
                    paper.subject_code,
                    paper.subject_name,
                    {paper.file_type: paper.source_url},
                    source_material=exam.material(paper),
                    source_category_code=str(exam.year_roc),
                )
            )
    return replace(
        page, exam_name_raw=exam.programme_name, papers=projected, attachments=exam.attachments()
    )


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
            if (category_code, _text(category_raw)) != (job.code, _text(job.label)):
                continue
            for paper in exam.papers_for(job):
                if (subject_code, subject_name_raw, source_url, checksum, source_material) == (
                    paper.subject_code,
                    paper.subject_name,
                    paper.source_url,
                    paper.checksum,
                    exam.material(paper),
                ):
                    return exam, job, paper
    return None


def validate_public_papers(
    papers: Iterable[NormalizedPaper], *, complete_history: bool = False
) -> None:
    selected = [p for p in papers if p.provider_id == PROVIDER_ID]
    observed: Counter[tuple[str, str, str, str]] = Counter()
    active: set[tuple[str, str]] = set()
    for p in selected:
        context = resolve_identity(
            source_exam_id=p.source_exam_id,
            year_ad=p.year_roc + 1911,
            exam_name_raw=p.exam_name_raw,
            category_code=p.category_code,
            category_raw=p.category_raw,
            subject_code=p.subject_code,
            subject_name_raw=p.subject_name_raw,
            source_url=p.download_url_source,
            checksum=p.checksum,
            source_material=p.source_material,
        )
        if context is None or p.file_type != context[2].file_type:
            raise ValueError(
                "New Taipei publication requires exact native programme, category and file role"
            )
        exam, job, native = context
        if (
            p.domain_id,
            p.exam_family_id,
            p.exam_series_id,
            p.level_id,
            p.track_id,
            p.stage_id,
            p.variant_ids,
            p.classification_confidence,
        ) != (
            exam.domain_id,
            exam.family_id,
            exam.series_id,
            "not-applicable",
            job.track_id,
            "stage-1",
            [],
            "high",
        ):
            raise ValueError(
                "New Taipei published identity differs from reviewed programme and post"
            )
        active.add((exam.source_exam_id, job.code))
        observed[exam.source_exam_id, job.code, native.source_url, native.checksum] += 1
    if complete_history:
        active = {(e.source_exam_id, j.code) for e in annual_exams() for j in e.jobs}
    expected = Counter(
        (e.source_exam_id, j.code, p.source_url, p.checksum)
        for e in annual_exams()
        for j in e.jobs
        if (e.source_exam_id, j.code) in active
        for p in e.papers_for(j)
    )
    if observed != expected:
        raise ValueError(
            "New Taipei publication omitted or duplicated a native category or written role"
        )


def reviewed_document_checksum(url: str) -> str:
    return next(
        (d.checksum for e in annual_exams() for d in e.documents if d.source_url == url), ""
    )


def validate_native_programmes(repo_root: Path, *, verify_mirror: bool = False) -> int:
    from app.paths import provider_paths
    from app.source_revisions import _source_records, load_source_revisions, payload_checksum
    from app.state import load_provider_state

    provider = provider_paths(repo_root, PROVIDER_ID)
    pages, catalog, _ = load_provider_state(provider)
    validate_public_papers(catalog.papers, complete_history=True)
    records = [r for _, r in _source_records(PROVIDER_ID, pages, catalog.papers)]
    records.extend(
        {**r["source_record"], "storage_key": r["blob_storage_key"]}
        for r in load_source_revisions(provider)
    )
    count = 0
    for exam in read_annual_exams(native_programmes_path(repo_root)):
        page = next((p for p in pages if p.source_exam_id == exam.source_exam_id), None)
        expected_attachments = exam.attachments()
        if page is None or Counter(
            (a.file_type, a.download_url_source) for a in page.attachments
        ) != Counter((a.file_type, a.download_url_source) for a in expected_attachments):
            raise ValueError("New Taipei current event omitted native reference context")
        for expected in expected_attachments:
            observed = next(
                a for a in page.attachments if a.download_url_source == expected.download_url_source
            )
            if observed.source_material != expected.source_material:
                raise ValueError(
                    "New Taipei reference material differs from native programme context"
                )
        for document in exam.documents:
            anchors = [
                r
                for r in records
                if (
                    r.get("source_exam_id"),
                    r.get("year_roc"),
                    r.get("download_url_source"),
                    r.get("checksum"),
                    r.get("file_type"),
                )
                == (
                    exam.source_exam_id,
                    exam.year_roc,
                    document.source_url,
                    document.checksum,
                    document.file_type,
                )
            ]
            if not anchors or any(not r.get("storage_key") for r in anchors):
                raise ValueError("New Taipei native document lacks retained source bytes")
            if verify_mirror:
                for record in anchors:
                    if (
                        payload_checksum(repo_root / "mirror" / record["storage_key"])
                        != document.checksum
                    ):
                        raise ValueError("New Taipei native rule or document mirror changed")
            count += 1
    return count


def _legacy_exam(previous: NormalizedPaper) -> AnnualExam | None:
    if previous.provider_id != PROVIDER_ID or previous.source_material is not None:
        return None
    for exam in annual_exams():
        if (
            previous.source_exam_id,
            previous.year_roc,
            previous.category_code,
            previous.category_raw,
            previous.exam_name_raw,
            previous.subject_code,
            previous.subject_name_raw,
        ) != (
            exam.source_exam_id,
            exam.year_roc,
            str(exam.year_roc),
            CANONICAL_CATEGORY,
            f"{exam.year_roc}學年度{CANONICAL_CATEGORY}",
            exam.source_exam_id.rsplit("-", 1)[-1],
            exam.programme_name,
        ):
            continue
        originals = {
            (d.source_url, d.checksum, d.file_type)
            for d in exam.documents
            if d.file_type in {"question", "answer"}
        }
        if exam.archive is not None:
            originals.add((exam.archive.source_url, exam.archive.checksum, "question_answer"))
        if (previous.download_url_source, previous.checksum, previous.file_type) in originals:
            return exam
    return None


def legacy_category_targets(
    previous: NormalizedPaper, current: Iterable[NormalizedPaper]
) -> list[NormalizedPaper]:
    """Follow exact old source contexts through the reviewed native transformation."""
    exam = _legacy_exam(previous)
    if exam is None:
        return []
    selected = [p for p in current if p.provider_id == PROVIDER_ID]
    try:
        validate_public_papers(selected, complete_history=True)
    except ValueError:
        return []
    if exam.archive is not None:
        locators = {
            exam.archive.locator(m) for m in exam.archive.files if m.file_type in _PAPER_ROLES
        }
        return [
            p
            for p in selected
            if p.source_exam_id == exam.source_exam_id and p.download_url_source in locators
        ]
    return [
        p
        for p in selected
        if (p.source_exam_id, p.download_url_source, p.checksum, p.file_type)
        == (
            exam.source_exam_id,
            previous.download_url_source,
            previous.checksum,
            previous.file_type,
        )
    ]


def reviewed_native_revisions(entries: Iterable[dict[str, Any]]) -> list[str]:
    return sorted(
        r["id"]
        for r in entries
        if r["record_type"] == "paper"
        and _legacy_exam(NormalizedPaper(**r["source_record"])) is not None
    )
