"""Elementary recruitment posts, shared initial booklets and scoped corrections.

The basic booklet applies to every post. Specialist booklets can serve more
than one post; neither sharing bytes nor bilingual eligibility merges posts.
Unavailable historical evidence remains explicit and is never a download claim.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from app.models import ExamAttachment, NormalizedPaper, ParsedPaper, SourceExamPage
from app.providers.taipei_downloads import download_name
from app.source_material import SourceDate, SourceMaterial

PROVIDER_ID = "teacher_recruit_taipei_elementary"
CANONICAL_CATEGORY = "臺北市國小教師甄試"
SERIES_ID = "taipei-elementary-teacher-recruitment"
SERIES_LABEL = "臺北市國小正式教師聯合甄選"
BASIC_SUBJECT = "basic-category-knowledge"


def _text(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


@dataclass(frozen=True)
class RecruitmentCategory:
    code: str
    label: str
    track_id: str
    source_subject_code: str
    bilingual: bool
    basic_specialist_weights: tuple[int, ...]


@dataclass(frozen=True)
class NativePaper:
    subject_code: str
    subject_name_raw: str
    file_type: str
    source_url: str
    checksum: str
    notice_url: str
    native_header: str
    page_count: int
    inspected_pages: tuple[int, ...]
    applies_to_subjects: tuple[str, ...]
    multiple_choice_count: int
    essay_count: int
    public_section_labels: tuple[str, ...]
    amends_subject_code: str
    amended_items: tuple[int, ...]
    review_notes: str


@dataclass(frozen=True)
class NativeDocument:
    document_id: str
    title: str
    source_url: str
    checksum: str
    notice_url: str
    publication_date: str
    page_count: int
    inspected_pages: tuple[int, ...]


@dataclass(frozen=True)
class UnavailableDocument:
    document_id: str
    source_url: str
    publication_date: str
    checked_at: str
    evidence_basis: str
    observation: str
    review_notes: str


@dataclass(frozen=True)
class AnnualExam:
    year_roc: int
    source_exam_id: str
    programme_name: str
    paper_article_url: str
    written_test_date: str
    reviewed_at: str
    review_notes: str
    jobs: tuple[RecruitmentCategory, ...]
    files: tuple[NativePaper, ...]
    documents: tuple[NativeDocument, ...]
    unavailable_documents: tuple[UnavailableDocument, ...]

    @property
    def resources(self) -> tuple[NativePaper | NativeDocument, ...]:
        return (*self.files, *self.documents)

    def papers_for(self, job: RecruitmentCategory) -> tuple[NativePaper, ...]:
        selected = {BASIC_SUBJECT, job.source_subject_code}
        return tuple(f for f in self.files if selected.intersection(f.applies_to_subjects))

    def material(self, paper: NativePaper) -> SourceMaterial:
        return SourceMaterial(
            "administered",
            SourceDate("exam_year", self.year_roc + 1911),
            paper.source_url,
            f"{self.programme_name}｜{paper.subject_name_raw}",
        )

    def attachments(self) -> list[ExamAttachment]:
        return [
            ExamAttachment(
                title=d.title,
                file_type="instructions" if d.document_id == "rule" else "source_document",
                download_url_source=d.source_url,
                source_material=SourceMaterial(
                    "reference",
                    SourceDate("publication_year", date.fromisoformat(d.publication_date).year),
                    d.source_url,
                    d.title,
                ),
            )
            for d in self.documents
        ]


def native_programmes_path(repo_root: Path) -> Path:
    return (
        repo_root / "catalog/mappings/teacher-recruit-taipei-elementary/native-programmes-v1.json"
    )


def _notice_url(url: str) -> bool:
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    if parts.scheme != "https" or parts.fragment:
        return False
    if parts.netloc == "primary.tp.edu.tw":
        return parts.path == "/Home/Index_Ann" and not parts.query
    categories = {
        "www.gov.taipei": {"D0042A87C2F0270A"},
        "www.doe.gov.taipei": {"188A8633D469F97E"},
    }
    return (
        parts.netloc in categories
        and parts.path == "/News_Content.aspx"
        and query.get("n", [""])[0] in categories[parts.netloc]
        and len(query.get("n", [])) == 1
        and len(query.get("s", [])) == 1
        and re.fullmatch(r"[0-9A-F]{16}", query["s"][0]) is not None
        and set(query) <= {"n", "s", "sms"}
    )


def _download_url(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.fragment:
        return False
    if parts.netloc == "primary.tp.edu.tw" and parts.path == "/Home/AnnDownload":
        query = parse_qs(parts.query)
        return (
            set(query) == {"id", "file"}
            and len(query["id"]) == len(query["file"]) == 1
            and query["id"][0].isdigit()
            and query["file"][0].endswith(".pdf")
            and "/" not in query["file"][0]
            and "\\" not in query["file"][0]
        )
    if parts.netloc != "www-ws.gov.taipei" or parts.path != "/Download.ashx":
        return False
    try:
        return download_name(url).lower().endswith(".pdf")
    except (ValueError, UnicodeError):
        return False


def _native_bytes(resource: NativePaper | NativeDocument) -> bool:
    return (
        _download_url(resource.source_url)
        and _notice_url(resource.notice_url)
        and re.fullmatch(r"[0-9a-f]{64}", resource.checksum) is not None
        and type(resource.page_count) is int
        and resource.page_count > 0
        and bool(resource.inspected_pages)
        and tuple(sorted(set(resource.inspected_pages))) == resource.inspected_pages
        and all(type(p) is int and 1 <= p <= resource.page_count for p in resource.inspected_pages)
    )


def _validate_exam(exam: AnnualExam) -> None:
    written = date.fromisoformat(exam.written_test_date)
    reviewed = date.fromisoformat(exam.reviewed_at)
    if (
        type(exam.year_roc) is not int
        or not 1 <= exam.year_roc <= 999
        or exam.source_exam_id != f"teacher-recruit-taipei-elementary-{exam.year_roc}"
        or exam.programme_name != f"臺北市{exam.year_roc}學年度公立國民小學教師聯合甄選"
        or written.year != exam.year_roc + 1911
        or written > reviewed
        or not _notice_url(exam.paper_article_url)
        or not exam.review_notes.strip()
    ):
        raise ValueError("Invalid Taipei elementary annual recruitment context")
    subjects: set[str] = set()
    for file in exam.files:
        if (
            not _native_bytes(file)
            or not file.native_header.strip()
            or not file.review_notes.strip()
            or not file.subject_name_raw.strip()
            or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", file.subject_code) is None
            or file.subject_code in subjects
            or not file.applies_to_subjects
            or len(set(file.applies_to_subjects)) != len(file.applies_to_subjects)
            or type(file.multiple_choice_count) is not int
            or type(file.essay_count) is not int
        ):
            raise ValueError("Invalid Taipei elementary native file or applicability")
        subjects.add(file.subject_code)
        if file.file_type == "question_answer":
            expected_mcq = (
                60
                if file.subject_code == BASIC_SUBJECT
                else 15
                if file.subject_code == "counseling"
                else 50
            )
            if (
                file.notice_url != exam.paper_article_url
                or file.applies_to_subjects != (file.subject_code,)
                or file.multiple_choice_count != expected_mcq
                or file.essay_count != (3 if file.subject_code == "counseling" else 0)
                or not file.public_section_labels
                or len(set(file.public_section_labels)) != len(file.public_section_labels)
                or any(not s.strip() for s in file.public_section_labels)
                or file.amends_subject_code
                or file.amended_items
            ):
                raise ValueError("Elementary booklet or inline-answer scope is invalid")
        elif file.file_type == "corrected_answer":
            if file.multiple_choice_count or file.essay_count or file.public_section_labels:
                raise ValueError("A correction notice is not a replacement examination booklet")
        else:
            raise ValueError("Unsupported Taipei elementary native file role")
    booklets = {f.subject_code for f in exam.files if f.file_type == "question_answer"}
    if BASIC_SUBJECT not in booklets or len(booklets) < 2:
        raise ValueError("Elementary recruitment requires common and specialist booklets")
    if any(set(f.applies_to_subjects) - booklets for f in exam.files):
        raise ValueError("Elementary correction refers to a missing native booklet")
    corrections = {f.subject_code: f for f in exam.files if f.file_type == "corrected_answer"}
    for file in corrections.values():
        if not file.amends_subject_code:
            if file.amended_items:
                raise ValueError("Elementary amended items lack their original notice")
            continue
        original = corrections.get(file.amends_subject_code)
        if (
            original is None
            or original is file
            or original.amends_subject_code
            or not file.amended_items
            or tuple(sorted(set(file.amended_items))) != file.amended_items
            or any(type(n) is not int or n < 1 for n in file.amended_items)
            or not set(file.applies_to_subjects) <= set(original.applies_to_subjects)
        ):
            raise ValueError("Elementary amendment requires its complete original notice")
    codes: set[str] = set()
    selected = {BASIC_SUBJECT}
    for job in exam.jobs:
        weighted = (
            job.source_subject_code
            in {
                "english",
                "counseling",
                "information-technology",
                "special-education-disability",
                "special-education-gifted",
            }
            or job.bilingual
        )
        if (
            re.fullmatch(r"taipei-elementary-[a-z0-9]+(?:-[a-z0-9]+)*", job.track_id) is None
            or job.code != "job-" + job.track_id.removeprefix("taipei-elementary-")
            or job.code in codes
            or not job.label.strip()
            or job.source_subject_code not in booklets - {BASIC_SUBJECT}
            or type(job.bilingual) is not bool
            or job.bilingual != job.track_id.endswith("-bilingual")
            or job.bilingual != ("雙語" in job.label)
            or job.basic_specialist_weights != ((30, 70) if weighted else (50, 50))
        ):
            raise ValueError("Invalid Taipei elementary post or initial-score weights")
        codes.add(job.code)
        selected.add(job.source_subject_code)
    if not codes or selected != booklets:
        raise ValueError("Elementary recruitment lacks complete booklet applicability")
    docs: set[str] = set()
    for doc in exam.documents:
        if (
            not _native_bytes(doc)
            or doc.document_id not in {"rule", "vacancy"}
            or doc.document_id in docs
            or not doc.title.strip()
            or date.fromisoformat(doc.publication_date) > written
        ):
            raise ValueError("Invalid elementary rule or vacancy document")
        docs.add(doc.document_id)
    unavailable: set[str] = set()
    for missing in exam.unavailable_documents:
        if (
            missing.document_id not in {"rule", "vacancy", "answer-review"}
            or missing.document_id in docs | unavailable
            or not _download_url(missing.source_url)
            or missing.evidence_basis != "indexed-official-text-only"
            or not missing.observation.strip()
            or not missing.review_notes.strip()
            or date.fromisoformat(missing.publication_date) > date.fromisoformat(missing.checked_at)
        ):
            raise ValueError("Unavailable elementary evidence must keep its explicit disposition")
        unavailable.add(missing.document_id)
    if not {"rule", "vacancy"} <= docs | unavailable:
        raise ValueError("Elementary post identities need both rule and actual vacancy evidence")
    if (
        not any(f.file_type == "corrected_answer" for f in exam.files)
        and "answer-review" not in unavailable
    ):
        raise ValueError("Elementary answer-review coverage requires an explicit disposition")
    urls = [f.source_url for f in (*exam.resources, *exam.unavailable_documents)]
    if len(set(urls)) != len(urls):
        raise ValueError("Elementary native resources have competing source slots")


def read_annual_exams(path: Path) -> tuple[AnnualExam, ...]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        set(document) != {"schema_version", "provider_id", "events"}
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 1
        or document["provider_id"] != PROVIDER_ID
        or not document["events"]
    ):
        raise ValueError("Unsupported Taipei elementary native programme evidence")
    exams = []
    for row in document["events"]:
        fields = dict(row)
        fields["jobs"] = tuple(
            RecruitmentCategory(
                **{**j, "basic_specialist_weights": tuple(j["basic_specialist_weights"])}
            )
            for j in fields["jobs"]
        )
        fields["files"] = tuple(
            NativePaper(
                **{
                    **f,
                    **{
                        n: tuple(f[n])
                        for n in (
                            "inspected_pages",
                            "applies_to_subjects",
                            "public_section_labels",
                            "amended_items",
                        )
                    },
                }
            )
            for f in fields["files"]
        )
        fields["documents"] = tuple(
            NativeDocument(**{**d, "inspected_pages": tuple(d["inspected_pages"])})
            for d in fields["documents"]
        )
        fields["unavailable_documents"] = tuple(
            UnavailableDocument(**d) for d in fields["unavailable_documents"]
        )
        exam = AnnualExam(**fields)
        _validate_exam(exam)
        exams.append(exam)
    if len({e.year_roc for e in exams}) != len(exams):
        raise ValueError("Repeated Taipei elementary annual programme")
    labels: dict[str, str] = {}
    payloads: dict[str, str] = {}
    for exam in exams:
        for job in exam.jobs:
            if labels.setdefault(job.track_id, job.label) != job.label:
                raise ValueError("Elementary cross-year post identity has competing labels")
        for resource in exam.resources:
            if payloads.setdefault(resource.source_url, resource.checksum) != resource.checksum:
                raise ValueError("Elementary source URL has competing reviewed checksums")
    return tuple(exams)


@lru_cache(maxsize=1)
def annual_exams() -> tuple[AnnualExam, ...]:
    return read_annual_exams(native_programmes_path(Path(__file__).resolve().parents[3]))


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
            for file in exam.papers_for(job):
                if (subject_code, subject_name_raw, source_url, checksum, source_material) == (
                    file.subject_code,
                    file.subject_name_raw,
                    file.source_url,
                    file.checksum,
                    exam.material(file),
                ):
                    return exam, job, file
    return None


def project_native_page(page: SourceExamPage, checksums: Mapping[str, str]) -> SourceExamPage:
    exam = next(
        (
            e
            for e in annual_exams()
            if (page.provider_id, page.source_exam_id, page.year_roc, page.year_ad)
            == (PROVIDER_ID, e.source_exam_id, e.year_roc, e.year_roc + 1911)
        ),
        None,
    )
    if exam is None or page.exam_name_raw != f"{exam.year_roc}學年度{CANONICAL_CATEGORY}":
        raise ValueError("Elementary native projection requires the exact annual source context")
    observed = []
    raw_by_url = {}
    for raw in page.papers:
        if (raw.category_raw, raw.category_code) != (CANONICAL_CATEGORY, str(exam.year_roc)):
            raise ValueError(
                "Elementary native projection requires original acquisition categories"
            )
        for role, url in raw.files.items():
            observed.append((raw.subject_code, raw.subject_name_raw, role, url, checksums.get(url)))
            raw_by_url[url] = raw
    expected = [
        (f.subject_code, f.subject_name_raw, f.file_type, f.source_url, f.checksum)
        for f in exam.files
        if f.file_type == "question_answer"
    ]
    if Counter(observed) != Counter(expected):
        raise ValueError("Elementary native listing or bytes changed; retain state for review")
    projected = []
    for job in exam.jobs:
        for file in exam.papers_for(job):
            if checksums.get(file.source_url) != file.checksum:
                raise ValueError("Elementary native correction changed; review required")
            raw = raw_by_url.get(file.source_url) or ParsedPaper(
                CANONICAL_CATEGORY,
                str(exam.year_roc),
                file.subject_code,
                file.subject_name_raw,
                {file.file_type: file.source_url},
            )
            projected.append(
                replace(
                    raw,
                    category_raw=job.label,
                    category_code=job.code,
                    source_category_code=str(exam.year_roc),
                    files={file.file_type: file.source_url},
                    mirror_files={file.file_type: raw.mirror_files[file.file_type]}
                    if file.file_type in raw.mirror_files
                    else {},
                    source_material=exam.material(file),
                )
            )
    return replace(page, exam_name_raw=exam.programme_name, papers=projected)


def validate_public_papers(
    papers: Iterable[NormalizedPaper], *, complete_history: bool = False
) -> None:
    observed: Counter[tuple[int, str, str, str]] = Counter()
    active: set[tuple[int, str]] = set()
    for paper in papers:
        if paper.provider_id != PROVIDER_ID:
            continue
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
        if context is None or paper.file_type != context[2].file_type:
            raise ValueError("Elementary publication requires exact native post, booklet and role")
        exam, job, file = context
        active.add((exam.year_roc, job.code))
        observed[exam.year_roc, job.code, file.source_url, file.checksum] += 1
    if complete_history:
        active = {(e.year_roc, j.code) for e in annual_exams() for j in e.jobs}
    expected = Counter(
        (e.year_roc, j.code, f.source_url, f.checksum)
        for e in annual_exams()
        for j in e.jobs
        if (e.year_roc, j.code) in active
        for f in e.papers_for(j)
    )
    if observed != expected:
        raise ValueError("Elementary publication omitted or duplicated a native post or role")


def validate_native_programmes(repo_root: Path, *, verify_mirror: bool = False) -> int:
    from app.paths import provider_paths
    from app.source_revisions import load_source_revisions, payload_checksum
    from app.state import load_provider_state

    provider = provider_paths(repo_root, PROVIDER_ID)
    pages, catalog, _ = load_provider_state(provider)
    records = [(vars(p), p.storage_key) for p in catalog.papers]
    records.extend(
        (vars(a) | {"source_exam_id": p.source_exam_id, "year_roc": p.year_roc}, a.storage_key)
        for p in pages
        for a in p.attachments
    )
    records.extend(
        (r["source_record"], r["blob_storage_key"]) for r in load_source_revisions(provider)
    )
    anchors: dict[tuple[str, int, str, str], set[str]] = {}
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
    for exam in read_annual_exams(native_programmes_path(repo_root)):
        for resource in exam.resources:
            keys = anchors.get(
                (exam.source_exam_id, exam.year_roc, resource.source_url, resource.checksum), set()
            )
            if not keys or "" in keys:
                raise ValueError("Elementary native evidence lacks retained source bytes")
            if verify_mirror:
                for key in keys:
                    if payload_checksum(repo_root / "mirror" / key) != resource.checksum:
                        raise ValueError("Elementary native mirror differs from reviewed bytes")
            count += 1
    return count


def _legacy_file(record: Mapping[str, Any]) -> tuple[AnnualExam, NativePaper] | None:
    if record.get("provider_id") != PROVIDER_ID or record.get("source_material") is not None:
        return None
    for exam in annual_exams():
        if tuple(
            record.get(k)
            for k in (
                "source_exam_id",
                "year_roc",
                "category_code",
                "category_raw",
                "exam_name_raw",
            )
        ) != (
            exam.source_exam_id,
            exam.year_roc,
            str(exam.year_roc),
            CANONICAL_CATEGORY,
            f"{exam.year_roc}學年度{CANONICAL_CATEGORY}",
        ):
            continue
        for file in exam.files:
            if file.file_type == "question_answer" and tuple(
                record.get(k)
                for k in (
                    "subject_code",
                    "subject_name_raw",
                    "file_type",
                    "download_url_source",
                    "checksum",
                )
            ) == (
                file.subject_code,
                file.subject_name_raw,
                file.file_type,
                file.source_url,
                file.checksum,
            ):
                return exam, file
    return None


def legacy_category_targets(
    previous: NormalizedPaper, current: Iterable[NormalizedPaper]
) -> list[NormalizedPaper]:
    native = _legacy_file(vars(previous))
    if native is None:
        return []
    exam, file = native
    targets = []
    for paper in current:
        if paper.provider_id != PROVIDER_ID or paper.file_type != file.file_type:
            continue
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
        if (
            context is not None
            and context[0] == exam
            and context[2] == file
            and paper.classification_confidence == "high"
            and paper.track_id == context[1].track_id
            and paper.stage_id == "stage-1"
        ):
            targets.append(paper)
    expected = {j.code for j in exam.jobs if file in exam.papers_for(j)}
    return (
        targets
        if len(targets) == len(expected) and {p.category_code for p in targets} == expected
        else []
    )


def reviewed_native_revisions(entries: Iterable[dict[str, Any]]) -> list[str]:
    return sorted(
        r["id"]
        for r in entries
        if r["record_type"] == "paper" and _legacy_file(r["source_record"]) is not None
    )
