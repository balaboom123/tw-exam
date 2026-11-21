"""Native annual recruitment meaning and stable original source references."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from functools import cached_property, lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.models import ExamAttachment, NormalizedPaper, ParsedPaper, SourceExamPage
from app.source_material import SourceDate, SourceMaterial

PROVIDER_ID = "taipower_recruit"
SERIES_ID = "taipower-hiring-training"
SERIES_LABEL = "台電新進僱用人員甄試"


def _text(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


@dataclass(frozen=True)
class EvidenceReference:
    source_url: str
    checksum: str
    physical_page: int | None


@dataclass(frozen=True)
class Occupation:
    occupation_id: str
    label: str
    native_labels: tuple[str, ...]
    correspondence_reason: str
    correspondence_evidence: tuple[EvidenceReference, ...]

    @property
    def track_id(self) -> str:
        return "taipower-" + self.occupation_id


@dataclass(frozen=True)
class SourceFile:
    subject_code: str
    label: str
    source_file_type: str
    source_url: str
    checksum: str
    size: int


@dataclass(frozen=True)
class SourceEvent:
    source_exam_id: str
    year_roc: int
    exam_name_raw: str
    source_category_code: str
    legacy_category_raw: str
    files: tuple[SourceFile, ...]
    selection_stage: str
    source_evidence: tuple[EvidenceReference, ...]
    unresolved_occupation_reason: str

    @cached_property
    def files_by_code(self) -> dict[str, SourceFile]:
        return {file.subject_code: file for file in self.files}

    @cached_property
    def files_by_url(self) -> dict[str, SourceFile]:
        return {file.source_url: file for file in self.files}

    def material(self, file: SourceFile) -> SourceMaterial:
        return SourceMaterial(
            "administered",
            SourceDate("exam_year", self.year_roc + 1911),
            file.source_url,
            file.label,
        )

    def file_role(self, file: SourceFile) -> str:
        from app.taipower_file_roles import taipower_file_role

        return taipower_file_role(
            file.source_file_type,
            source_exam_id=self.source_exam_id,
            year_roc=self.year_roc,
            category_code=self.source_category_code,
            subject_code=file.subject_code,
            title=file.label,
            source_url=file.source_url,
            checksum=file.checksum,
        )

    def legacy_source(self, file: SourceFile) -> dict[str, Any]:
        return {
            "provider_id": PROVIDER_ID,
            "source_exam_id": self.source_exam_id,
            "year_roc": self.year_roc,
            "exam_name_raw": self.exam_name_raw,
            "category_code": self.source_category_code,
            "category_raw": self.legacy_category_raw,
            "subject_code": file.subject_code,
            "subject_name_raw": file.label,
            "file_type": self.file_role(file),
            "download_url_source": file.source_url,
            "checksum": file.checksum,
        }


@dataclass(frozen=True)
class NativeRule:
    title: str
    source_url: str
    checksum: str
    size: int
    page_count: int
    eligibility_page: int
    initial_written_page: int
    reexamination_page: int

    def attachment(self) -> ExamAttachment:
        return ExamAttachment(
            self.title,
            "instructions",
            self.source_url,
            source_material=SourceMaterial(
                "reference", SourceDate("undated", None), self.source_url, self.title
            ),
        )


@dataclass(frozen=True)
class NativeBooklet:
    booklet_id: str
    subject_name: str
    written_period: str
    timetable_period: int
    duration_minutes: int
    question_source_code: str
    answer_source_code: str | None
    native_question_page: int
    native_answer_page: int | None
    answer_coverage_note: str

    @property
    def source_codes(self) -> tuple[str, ...]:
        return (self.question_source_code,) + (
            (self.answer_source_code,) if self.answer_source_code else ()
        )

    @property
    def public_subject_label(self) -> str:
        period = "共同科目" if self.written_period == "common" else f"專業科目{self.written_period}"
        return f"{period}（{self.subject_name}）"


@dataclass(frozen=True)
class RecruitmentCategory:
    category_code: str
    occupation_id: str
    native_label: str
    population: str
    native_table_order: int
    native_rule_page: int
    booklets: tuple[str, ...]
    weights: tuple[int, ...]


@dataclass(frozen=True)
class AnnualExam:
    source_exam_id: str
    reviewed_at: str
    rule: NativeRule
    review_notes: str
    booklets: tuple[NativeBooklet, ...]
    jobs: tuple[RecruitmentCategory, ...]

    @cached_property
    def booklets_by_id(self) -> dict[str, NativeBooklet]:
        return {booklet.booklet_id: booklet for booklet in self.booklets}

    @cached_property
    def jobs_by_code(self) -> dict[str, RecruitmentCategory]:
        return {job.category_code: job for job in self.jobs}

    def files_for(self, event: SourceEvent, job: RecruitmentCategory) -> tuple[SourceFile, ...]:
        return tuple(
            event.files_by_code[code]
            for booklet_id in job.booklets
            for code in self.booklets_by_id[booklet_id].source_codes
        )


@dataclass(frozen=True)
class NativeCatalogue:
    reviewed_at: str
    occupations: tuple[Occupation, ...]
    source_events: tuple[SourceEvent, ...]
    annual_exams: tuple[AnnualExam, ...]

    @cached_property
    def occupations_by_id(self) -> dict[str, Occupation]:
        return {occupation.occupation_id: occupation for occupation in self.occupations}

    @cached_property
    def events_by_id(self) -> dict[str, SourceEvent]:
        return {event.source_exam_id: event for event in self.source_events}

    @cached_property
    def exams_by_id(self) -> dict[str, AnnualExam]:
        return {exam.source_exam_id: exam for exam in self.annual_exams}

    @cached_property
    def resources_by_url(self) -> dict[str, SourceFile | NativeRule]:
        return {
            **{file.source_url: file for event in self.source_events for file in event.files},
            **{exam.rule.source_url: exam.rule for exam in self.annual_exams},
        }


@dataclass(frozen=True)
class NativePaperContext:
    event: SourceEvent
    exam: AnnualExam
    job: RecruitmentCategory
    occupation: Occupation
    booklet: NativeBooklet
    file: SourceFile


def native_programmes_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/taipower/native-programmes-v1.json"


def _identifier(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value) is not None


def _positive_integer(value: Any) -> bool:
    return type(value) is int and value > 0


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _checksum(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _validate_review_date(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise ValueError("Taipower native review date requires YYYY-MM-DD")
    date.fromisoformat(value)


def validate_source_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.taipower.com.tw"
        or not parsed.path.startswith("/media/")
        or not parsed.path.lower().endswith(".pdf")
        or parsed.query not in {"", "mediaDL=true"}
        or parsed.fragment
    ):
        raise ValueError("Taipower recruitment requires an official native PDF source")


def _validate_reference(reference: EvidenceReference) -> None:
    url = urlsplit(reference.source_url)
    if (
        url.scheme not in {"http", "https"}
        or not url.netloc
        or not _checksum(reference.checksum)
        or (reference.physical_page is not None and not _positive_integer(reference.physical_page))
    ):
        raise ValueError("Invalid Taipower native source evidence")


def _validate_catalogue(catalogue: NativeCatalogue) -> None:
    _validate_review_date(catalogue.reviewed_at)
    if (
        not catalogue.occupations
        or len(catalogue.occupations_by_id) != len(catalogue.occupations)
        or not catalogue.source_events
        or len(catalogue.events_by_id) != len(catalogue.source_events)
        or not catalogue.annual_exams
        or len(catalogue.exams_by_id) != len(catalogue.annual_exams)
    ):
        raise ValueError("Missing or repeated Taipower native catalogue context")
    _validate_occupations(catalogue)
    all_urls = _validate_source_events(catalogue)
    selected_occupations: set[str] = set()
    for exam in catalogue.annual_exams:
        selected_occupations.update(_validate_annual_exam(catalogue, exam, all_urls))
    if selected_occupations != catalogue.occupations_by_id.keys():
        raise ValueError("Taipower native occupations have orphaned definitions")
    _validate_rule_evidence(catalogue)


def _validate_rule_evidence(catalogue: NativeCatalogue) -> None:
    exams_by_url = {exam.rule.source_url: exam for exam in catalogue.annual_exams}
    for occupation in catalogue.occupations:
        for reference in occupation.correspondence_evidence:
            exam = exams_by_url.get(reference.source_url)
            if (
                exam is None
                or reference.checksum != exam.rule.checksum
                or reference.physical_page is None
                or reference.physical_page > exam.rule.page_count
                or not any(job.occupation_id == occupation.occupation_id for job in exam.jobs)
            ):
                raise ValueError("Taipower occupation evidence differs from its annual native rule")
    for exam in catalogue.annual_exams:
        event = catalogue.events_by_id[exam.source_exam_id]
        if not any(
            reference.source_url == exam.rule.source_url
            and reference.checksum == exam.rule.checksum
            and reference.physical_page is not None
            and reference.physical_page <= exam.rule.page_count
            for reference in event.source_evidence
        ):
            raise ValueError("Taipower stage evidence differs from its exact annual native rule")


def _validate_occupations(catalogue: NativeCatalogue) -> None:
    for occupation in catalogue.occupations:
        if (
            not _identifier(occupation.occupation_id)
            or not _nonempty(occupation.label)
            or not _nonempty(occupation.correspondence_reason)
            or not occupation.native_labels
            or any(not _nonempty(label) for label in occupation.native_labels)
            or len(set(occupation.native_labels)) != len(occupation.native_labels)
        ):
            raise ValueError("Invalid Taipower native occupation correspondence")
        for reference in occupation.correspondence_evidence:
            _validate_reference(reference)


def _validate_source_events(catalogue: NativeCatalogue) -> set[str]:
    all_urls: set[str] = set()
    for event in catalogue.source_events:
        if (
            not _positive_integer(event.year_roc)
            or re.fullmatch(
                rf"taipower-recruit-{event.year_roc}(?:-(?:[1-9]|1[0-2]))?",
                event.source_exam_id,
            )
            is None
            or event.source_category_code != str(event.year_roc)
            or not _nonempty(event.exam_name_raw)
            or not _nonempty(event.legacy_category_raw)
            or not event.files
            or len(event.files_by_code) != len(event.files)
            or len(event.files_by_url) != len(event.files)
            or event.selection_stage not in {"stage-1", "unknown"}
            or not isinstance(event.unresolved_occupation_reason, str)
            or bool(event.unresolved_occupation_reason)
            == (event.source_exam_id in catalogue.exams_by_id)
            or (event.selection_stage == "stage-1" and not event.source_evidence)
        ):
            raise ValueError("Invalid Taipower original source event or review disposition")
        for reference in event.source_evidence:
            _validate_reference(reference)
        for file in event.files:
            validate_source_url(file.source_url)
            if (
                not _identifier(file.subject_code)
                or not _nonempty(file.label)
                or file.source_file_type not in {"question", "answer"}
                or not _checksum(file.checksum)
                or not _positive_integer(file.size)
                or file.source_url in all_urls
            ):
                raise ValueError("Invalid or repeated Taipower original source reference")
            all_urls.add(file.source_url)
    return all_urls


def _validate_annual_exam(
    catalogue: NativeCatalogue, exam: AnnualExam, all_urls: set[str]
) -> set[str]:
    selected_occupations: set[str] = set()
    annual_event = catalogue.events_by_id.get(exam.source_exam_id)
    if annual_event is None or annual_event.selection_stage != "stage-1":
        raise ValueError("Taipower annual programme lacks its exact initial-written event")
    _validate_review_date(exam.reviewed_at)
    rule = exam.rule
    validate_source_url(rule.source_url)
    if (
        rule.source_url in all_urls
        or not _nonempty(rule.title)
        or not _checksum(rule.checksum)
        or not _positive_integer(rule.size)
        or not _positive_integer(rule.page_count)
        or any(
            not _positive_integer(page) or page > rule.page_count
            for page in (
                rule.eligibility_page,
                rule.initial_written_page,
                rule.reexamination_page,
            )
        )
        or not _nonempty(exam.review_notes)
        or not exam.booklets
        or len(exam.booklets_by_id) != len(exam.booklets)
        or not exam.jobs
        or len(exam.jobs_by_code) != len(exam.jobs)
    ):
        raise ValueError("Invalid Taipower annual rule, booklet or category population")
    all_urls.add(rule.source_url)
    used_files: list[str] = []
    for booklet in exam.booklets:
        if (
            not _identifier(booklet.booklet_id)
            or not _nonempty(booklet.subject_name)
            or booklet.written_period not in {"common", "A", "B"}
            or type(booklet.timetable_period) is not int
            or booklet.timetable_period != {"common": 1, "A": 2, "B": 3}[booklet.written_period]
            or not _positive_integer(booklet.duration_minutes)
            or not _positive_integer(booklet.native_question_page)
            or not _nonempty(booklet.answer_coverage_note)
        ):
            raise ValueError("Invalid Taipower native written booklet or timetable period")
        question = annual_event.files_by_code.get(booklet.question_source_code)
        answer = annual_event.files_by_code.get(booklet.answer_source_code or "")
        if (
            question is None
            or question.source_file_type != "question"
            or (booklet.answer_source_code is not None and answer is None)
            or (answer is not None and answer.source_file_type != "answer")
            or (answer is None) != (booklet.native_answer_page is None)
            or (
                booklet.native_answer_page is not None
                and not _positive_integer(booklet.native_answer_page)
            )
        ):
            raise ValueError("Taipower booklet has missing or competing authentic file roles")
        used_files.extend(booklet.source_codes)
    if Counter(used_files) != Counter(annual_event.files_by_code.keys()):
        raise ValueError("Taipower annual booklet bindings omit or duplicate a source file")
    selected_booklets: set[str] = set()
    orders: set[int] = set()
    occupation_populations: set[tuple[str, str]] = set()
    for job in exam.jobs:
        job_occupation = catalogue.occupations_by_id.get(job.occupation_id)
        expected_code = (
            "job-" + job.occupation_id + ("-disability" if job.population == "disability" else "")
        )
        if (
            job_occupation is None
            or job.category_code != expected_code
            or job.population not in {"general", "disability"}
            or job.native_label not in job_occupation.native_labels
            or not _positive_integer(job.native_table_order)
            or job.native_table_order in orders
            or not _positive_integer(job.native_rule_page)
            or job.native_rule_page > rule.page_count
            or len(job.booklets) != 3
            or len(set(job.booklets)) != 3
            or set(job.booklets) - exam.booklets_by_id.keys()
            or len(job.weights) != 4
            or any(not _positive_integer(weight) or weight > 100 for weight in job.weights)
            or sum(job.weights) != 100
            or (job.occupation_id, job.population) in occupation_populations
        ):
            raise ValueError("Invalid Taipower job, native category, weights or applicability")
        if [exam.booklets_by_id[key].written_period for key in job.booklets] != [
            "common",
            "A",
            "B",
        ]:
            raise ValueError("Taipower recruitment category requires common, A and B booklets")
        if job.population == "disability":
            ordinary = [
                other
                for other in exam.jobs
                if other.occupation_id == job.occupation_id and other.population == "general"
            ]
            if (
                len(ordinary) != 1
                or ordinary[0].booklets != job.booklets
                or ordinary[0].weights != job.weights
            ):
                raise ValueError("Changed Taipower population content requires a paper-form review")
        orders.add(job.native_table_order)
        selected_booklets.update(job.booklets)
        selected_occupations.add(job.occupation_id)
        occupation_populations.add((job.occupation_id, job.population))
    if (
        orders != set(range(1, len(exam.jobs) + 1))
        or selected_booklets != exam.booklets_by_id.keys()
    ):
        raise ValueError("Taipower annual categories do not cover the complete native population")
    return selected_occupations


def _rows(value: Any, *, allow_empty: bool = False) -> list[dict[str, Any]]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError("Taipower native evidence requires an array of objects")
    if any(not isinstance(row, dict) for row in value):
        raise ValueError("Invalid Taipower native evidence row")
    return value


def read_native_catalogue(path: Path) -> NativeCatalogue:
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(document, dict)
        or set(document)
        != {
            "schema_version",
            "provider_id",
            "reviewed_at",
            "occupations",
            "source_events",
            "annual_exams",
        }
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 1
        or document["provider_id"] != PROVIDER_ID
    ):
        raise ValueError("Unsupported Taipower native programme evidence")
    try:
        occupations = []
        for row in _rows(document["occupations"]):
            fields = dict(row)
            fields["native_labels"] = tuple(fields["native_labels"])
            fields["correspondence_evidence"] = tuple(
                EvidenceReference(**r)
                for r in _rows(fields["correspondence_evidence"], allow_empty=True)
            )
            occupations.append(Occupation(**fields))
        events = []
        for row in _rows(document["source_events"]):
            fields = dict(row)
            fields["files"] = tuple(SourceFile(**r) for r in _rows(fields["files"]))
            fields["source_evidence"] = tuple(
                EvidenceReference(**r) for r in _rows(fields["source_evidence"], allow_empty=True)
            )
            events.append(SourceEvent(**fields))
        exams = []
        for row in _rows(document["annual_exams"]):
            fields = dict(row)
            fields["rule"] = NativeRule(**fields["rule"])
            fields["booklets"] = tuple(NativeBooklet(**r) for r in _rows(fields["booklets"]))
            fields["jobs"] = tuple(
                RecruitmentCategory(
                    **{**r, "booklets": tuple(r["booklets"]), "weights": tuple(r["weights"])}
                )
                for r in _rows(fields["jobs"])
            )
            exams.append(AnnualExam(**fields))
        catalogue = NativeCatalogue(
            document["reviewed_at"], tuple(occupations), tuple(events), tuple(exams)
        )
        _validate_catalogue(catalogue)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Invalid Taipower native programme fields") from exc
    return catalogue


@lru_cache(maxsize=1)
def native_catalogue() -> NativeCatalogue:
    return read_native_catalogue(native_programmes_path(Path(__file__).resolve().parents[3]))


def stable_subject_code(source_exam_id: str, source_url: str) -> str:
    """Preserve reviewed original locators; new references never shift siblings."""
    validate_source_url(source_url)
    event = native_catalogue().events_by_id.get(source_exam_id)
    file = event.files_by_url.get(source_url) if event is not None else None
    if file is not None:
        return file.subject_code
    return "src-" + hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:20]


def project_native_page(page: SourceExamPage) -> SourceExamPage:
    catalogue = native_catalogue()
    exam = catalogue.exams_by_id.get(page.source_exam_id)
    if exam is None:
        return page
    event = catalogue.events_by_id[page.source_exam_id]
    expected = Counter(
        (file.subject_code, file.label, file.source_file_type, file.source_url)
        for file in event.files
    )
    observed = Counter(
        (paper.subject_code, paper.subject_name_raw, role, url)
        for paper in page.papers
        for role, url in paper.files.items()
        if paper.category_code == event.source_category_code
        and paper.category_raw == event.legacy_category_raw
        and not paper.source_category_code
        and paper.source_material is None
    )
    if (
        page.provider_id != PROVIDER_ID
        or page.year_roc != event.year_roc
        or page.year_ad != event.year_roc + 1911
        or page.exam_name_raw != event.exam_name_raw
        or page.source_material is not None
        or page.attachments
        or observed != expected
        or sum(len(p.files) for p in page.papers) != sum(observed.values())
    ):
        raise ValueError("Taipower annual archive changed; review its complete native population")
    return SourceExamPage(
        page.source_exam_id,
        page.year_ad,
        page.year_roc,
        page.exam_name_raw,
        [exam.rule.attachment()],
        [
            ParsedPaper(
                category_raw=job.native_label,
                category_code=job.category_code,
                subject_code=file.subject_code,
                subject_name_raw=file.label,
                files={file.source_file_type: file.source_url},
                source_material=event.material(file),
                source_category_code=event.source_category_code,
            )
            for job in exam.jobs
            for file in exam.files_for(event, job)
        ],
        provider_id=PROVIDER_ID,
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
) -> NativePaperContext | None:
    catalogue = native_catalogue()
    event = catalogue.events_by_id.get(source_exam_id)
    exam = catalogue.exams_by_id.get(source_exam_id)
    if (
        event is None
        or exam is None
        or year_ad != event.year_roc + 1911
        or _text(exam_name_raw) != _text(event.exam_name_raw)
    ):
        return None
    job = exam.jobs_by_code.get(category_code)
    file = event.files_by_code.get(subject_code)
    if (
        job is None
        or file is None
        or _text(category_raw) != _text(job.native_label)
        or _text(subject_name_raw) != _text(file.label)
        or source_url != file.source_url
        or checksum != file.checksum
        or source_material != event.material(file)
    ):
        return None
    for booklet_id in job.booklets:
        booklet = exam.booklets_by_id[booklet_id]
        if subject_code in booklet.source_codes:
            return NativePaperContext(
                event, exam, job, catalogue.occupations_by_id[job.occupation_id], booklet, file
            )
    return None


def original_category_code(
    *,
    source_exam_id: str,
    year_roc: int,
    category_code: str,
    subject_code: str,
    title: str,
    source_url: str,
) -> str | None:
    """Bridge reviewed job contexts to the existing native answer-role owner."""
    catalogue = native_catalogue()
    event = catalogue.events_by_id.get(source_exam_id)
    exam = catalogue.exams_by_id.get(source_exam_id)
    if event is None or exam is None or year_roc != event.year_roc:
        return None
    job = exam.jobs_by_code.get(category_code)
    file = event.files_by_code.get(subject_code)
    if (
        job is None
        or file is None
        or _text(title) != _text(file.label)
        or source_url != file.source_url
        or file not in exam.files_for(event, job)
    ):
        return None
    return event.source_category_code


def _paper_context(paper: NormalizedPaper) -> NativePaperContext | None:
    context = resolve_identity(
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
    if context is None:
        return None
    expected = (
        "employment",
        "employment-exam",
        SERIES_ID,
        "not-applicable",
        context.occupation.track_id,
        (),
        "stage-1",
    )
    actual = (
        paper.domain_id,
        paper.exam_family_id,
        paper.exam_series_id,
        paper.level_id,
        paper.track_id,
        tuple(paper.variant_ids),
        paper.stage_id,
    )
    if (
        actual != expected
        or paper.classification_confidence != "high"
        or paper.file_type != context.event.file_role(context.file)
    ):
        return None
    return context


def validate_public_papers(
    papers: Iterable[NormalizedPaper], *, complete_history: bool = False
) -> None:
    observed: Counter[tuple[str, str, str]] = Counter()
    active: set[tuple[str, str]] = set()
    for paper in papers:
        if paper.provider_id != PROVIDER_ID:
            continue
        context = _paper_context(paper)
        if context is None:
            if paper.classification_confidence == "review":
                continue
            raise ValueError(
                "Taipower publication requires exact native job, booklet, role and bytes"
            )
        event, exam, job = context.event, context.exam, context.job
        observed[event.source_exam_id, job.category_code, context.file.subject_code] += 1
        active.add((exam.source_exam_id, job.category_code))
    catalogue = native_catalogue()
    if complete_history:
        active = {
            (e.source_exam_id, j.category_code) for e in catalogue.annual_exams for j in e.jobs
        }
    expected = Counter(
        (exam.source_exam_id, job.category_code, file.subject_code)
        for exam in catalogue.annual_exams
        for job in exam.jobs
        if (exam.source_exam_id, job.category_code) in active
        for file in exam.files_for(catalogue.events_by_id[exam.source_exam_id], job)
    )
    if observed != expected:
        raise ValueError("Taipower publication omitted or duplicated native job/booklet references")


def _legacy_context(record: dict[str, Any]) -> tuple[SourceEvent, SourceFile] | None:
    if record.get("provider_id") != PROVIDER_ID or record.get("source_material") is not None:
        return None
    catalogue = native_catalogue()
    event = catalogue.events_by_id.get(record.get("source_exam_id", ""))
    if event is None or event.source_exam_id not in catalogue.exams_by_id:
        return None
    file = event.files_by_code.get(record.get("subject_code", ""))
    if file is not None and all(
        record.get(key) == value for key, value in event.legacy_source(file).items()
    ):
        return event, file
    return None


def legacy_category_targets(
    previous: NormalizedPaper, current: Iterable[NormalizedPaper]
) -> list[NormalizedPaper]:
    native = _legacy_context(vars(previous))
    if native is None:
        return []
    event, file = native
    exam = native_catalogue().exams_by_id[event.source_exam_id]
    expected = {job.category_code for job in exam.jobs if file in exam.files_for(event, job)}
    targets = []
    for paper in current:
        if (
            paper.provider_id != PROVIDER_ID
            or paper.source_exam_id != event.source_exam_id
            or paper.subject_code != file.subject_code
            or paper.download_url_source != file.source_url
            or paper.checksum != file.checksum
        ):
            continue
        context = _paper_context(paper)
        if context is not None and context.event == event and context.file == file:
            targets.append(paper)
    if len(targets) != len(expected) or {p.category_code for p in targets} != expected:
        return []
    return targets


def reviewed_native_revisions(entries: Iterable[dict[str, Any]]) -> list[str]:
    return sorted(
        row["id"]
        for row in entries
        if row["record_type"] == "paper" and _legacy_context(row["source_record"]) is not None
    )


def validate_native_programmes(repo_root: Path, *, verify_mirror: bool = False) -> int:
    from app.paths import provider_paths
    from app.source_revisions import load_source_revisions, payload_checksum
    from app.state import load_provider_state

    provider = provider_paths(repo_root, PROVIDER_ID)
    pages, current, _ = load_provider_state(provider)
    # A shared booklet can keep every physical source anchor present while an
    # entire applicable category disappears. Validate the logical population
    # as well, including when publication reads compact indexes.
    validate_public_papers(current.papers, complete_history=True)
    records = [(vars(paper), paper.storage_key) for paper in current.papers]
    records.extend(
        (
            vars(attachment) | {"source_exam_id": page.source_exam_id, "year_roc": page.year_roc},
            attachment.storage_key,
        )
        for page in pages
        for attachment in page.attachments
    )
    records.extend(
        (r["source_record"], r["blob_storage_key"]) for r in load_source_revisions(provider)
    )
    anchors: dict[tuple[str, int, str, str], set[str]] = {}
    for record, key in records:
        anchor = (
            record["source_exam_id"],
            record["year_roc"],
            record["download_url_source"],
            record["checksum"],
        )
        anchors.setdefault(anchor, set()).add(key)
    catalogue = read_native_catalogue(native_programmes_path(repo_root))
    count = 0
    for event in catalogue.source_events:
        resources: tuple[SourceFile | NativeRule, ...] = event.files
        exam = catalogue.exams_by_id.get(event.source_exam_id)
        if exam is not None:
            resources = (*resources, exam.rule)
        for resource in resources:
            keys = anchors.get(
                (event.source_exam_id, event.year_roc, resource.source_url, resource.checksum),
                set(),
            )
            if not keys or any(not isinstance(key, str) or not key for key in keys):
                raise ValueError(
                    "Taipower native resource lacks its retained provider source anchor"
                )
            if verify_mirror:
                for key in keys:
                    if payload_checksum(repo_root / "mirror" / key) != resource.checksum:
                        raise ValueError(
                            "Taipower native mirror differs from its reviewed source bytes"
                        )
            count += 1
    return count
