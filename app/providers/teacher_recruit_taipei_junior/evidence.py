"""Annual Taipei recruitment posts, native written files and correction notices.

The source's combined booklets contain education and specialist sections. Posts
remain distinct when their initial papers or answer tables happen to agree.
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
from app.providers.base import UnreviewedSourceFile
from app.providers.taipei_downloads import download_name
from app.source_material import SourceDate, SourceMaterial

PROVIDER_ID = "teacher_recruit_taipei_junior"
CANONICAL_CATEGORY = "臺北市國中教師甄試"
SERIES_ID = "taipei-junior-teacher-recruitment"
SERIES_LABEL = "臺北市國中正式教師聯合甄選"
REVIEW_SUBJECT = "筆試釋疑答案修訂彙整表"


def _text(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


@dataclass(frozen=True)
class RecruitmentCategory:
    code: str
    label: str
    track_id: str
    source_subject_code: str
    source_subject_name: str
    bilingual: bool
    public_section_labels: tuple[str, ...]


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
class NativeDocument:
    document_id: str
    title: str
    source_url: str
    checksum: str
    publication_date: str
    page_count: int
    inspected_pages: tuple[int, ...]


@dataclass(frozen=True)
class AnnualExam:
    year_roc: int
    source_exam_id: str
    programme_name: str
    paper_article_url: str
    rule_notice_url: str
    review_notice_url: str
    written_test_date: str
    review_publication_date: str
    reviewed_at: str
    bilingual_written_language: str
    education_specialist_weights: tuple[int, ...]
    question_counts: tuple[int, ...]
    review_notes: str
    jobs: tuple[RecruitmentCategory, ...]
    files: tuple[NativePaper, ...]
    documents: tuple[NativeDocument, ...]

    @property
    def resources(self) -> tuple[NativePaper | NativeDocument, ...]:
        return (*self.files, *self.documents)

    def papers_for(self, job: RecruitmentCategory) -> tuple[NativePaper, ...]:
        # Every full annual review discusses the shared education section.
        return tuple(
            f for f in self.files if f.subject_code in {job.source_subject_code, "answer-review"}
        )

    def variants(self, job: RecruitmentCategory) -> tuple[tuple[str, str], ...]:
        if not job.bilingual:
            return ()
        if self.bilingual_written_language == "chinese-and-english":
            return (("written-language-chinese-english", "學科中英命題"),)
        return (("written-language-chinese", "學科中文命題"),)

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
    return repo_root / "catalog/mappings/teacher-recruit-taipei-junior/native-programmes-v1.json"


def _notice_url(url: str) -> bool:
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    return (
        parts.scheme == "https"
        and parts.netloc == "www.doe.gov.taipei"
        and parts.path == "/News_Content.aspx"
        and not parts.fragment
        and query.get("n") == ["E831CA0A5CD0193D"]
        and len(query.get("s", [])) == 1
        and re.fullmatch(r"[0-9A-F]{16}", query["s"][0]) is not None
        and set(query) <= {"n", "s", "sms"}
    )


def _download_url(url: str) -> bool:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.netloc != "www-ws.gov.taipei"
        or parts.path != "/Download.ashx"
        or parts.fragment
    ):
        return False
    try:
        return download_name(url).lower().endswith(".pdf")
    except (ValueError, UnicodeError):
        return False


def _native_bytes(file: NativePaper | NativeDocument) -> bool:
    return (
        _download_url(file.source_url)
        and re.fullmatch(r"[0-9a-f]{64}", file.checksum) is not None
        and type(file.page_count) is int
        and file.page_count > 0
        and bool(file.inspected_pages)
        and tuple(sorted(set(file.inspected_pages))) == file.inspected_pages
        and all(type(p) is int and 1 <= p <= file.page_count for p in file.inspected_pages)
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
        raise ValueError("Unsupported Taipei junior native programme evidence")
    exams = []
    years: set[int] = set()
    labels: dict[str, str] = {}
    payloads: dict[str, str] = {}
    for row in document["events"]:
        fields = dict(row)
        fields["jobs"] = tuple(
            RecruitmentCategory(**{**j, "public_section_labels": tuple(j["public_section_labels"])})
            for j in fields["jobs"]
        )
        for name, cls in (("files", NativePaper), ("documents", NativeDocument)):
            fields[name] = tuple(
                cls(**{**f, "inspected_pages": tuple(f["inspected_pages"])}) for f in fields[name]
            )
        for name in ("education_specialist_weights", "question_counts"):
            fields[name] = tuple(fields[name])
        exam = AnnualExam(**fields)
        year = exam.year_roc
        written = date.fromisoformat(exam.written_test_date)
        reviewed = date.fromisoformat(exam.reviewed_at)
        corrected = date.fromisoformat(exam.review_publication_date)
        if (
            type(year) is not int
            or not 1 <= year <= 999
            or year in years
            or exam.source_exam_id != f"teacher-recruit-taipei-junior-{year}"
            or exam.programme_name != f"臺北市{year}學年度市立國民中學正式教師聯合甄選"
            or written.year != year + 1911
            or not written <= corrected <= reviewed
            or not all(
                _notice_url(u)
                for u in (exam.paper_article_url, exam.rule_notice_url, exam.review_notice_url)
            )
            or len({exam.paper_article_url, exam.rule_notice_url, exam.review_notice_url}) != 3
            or exam.bilingual_written_language not in {"chinese", "chinese-and-english"}
            or exam.education_specialist_weights != (30, 70)
            or exam.question_counts != (40, 40)
            or not exam.review_notes.strip()
        ):
            raise ValueError("Taipei junior annual programme, rule or written stage is invalid")
        years.add(year)
        urls: set[str] = set()
        roles: dict[str, set[str]] = {}
        subjects: dict[str, str] = {}
        for file in exam.files:
            if (
                not _native_bytes(file)
                or not file.native_header.strip()
                or not file.subject_name_raw.strip()
                or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", file.subject_code) is None
                or file.file_type not in {"question", "answer", "corrected_answer"}
                or file.source_url in urls
                or file.file_type in roles.setdefault(file.subject_code, set())
                or file.subject_code in subjects
                and subjects[file.subject_code] != file.subject_name_raw
                or (file.subject_code == "answer-review") != (file.file_type == "corrected_answer")
                or file.subject_code == "answer-review"
                and file.subject_name_raw != REVIEW_SUBJECT
            ):
                raise ValueError("Invalid Taipei junior native file, role or source context")
            urls.add(file.source_url)
            roles[file.subject_code].add(file.file_type)
            subjects[file.subject_code] = file.subject_name_raw
        if (
            roles.pop("answer-review", None) != {"corrected_answer"}
            or not roles
            or any(r != {"question", "answer"} for r in roles.values())
        ):
            raise ValueError(
                "Taipei junior requires paired initial files and the full annual review"
            )
        documents: set[str] = set()
        for doc in exam.documents:
            published = date.fromisoformat(doc.publication_date)
            if (
                not _native_bytes(doc)
                or doc.document_id not in {"rule", "vacancy"}
                or doc.document_id in documents
                or doc.source_url in urls
                or not doc.title.strip()
                or doc.document_id == "rule"
                and not published <= written
                or doc.document_id == "vacancy"
                and published != corrected
            ):
                raise ValueError("Taipei junior rule or recruitment-category document is invalid")
            documents.add(doc.document_id)
            urls.add(doc.source_url)
        if documents != {"rule", "vacancy"}:
            raise ValueError("Taipei junior requires both annual rule and native category context")
        codes: set[str] = set()
        selected: set[str] = set()
        for job in exam.jobs:
            if (
                re.fullmatch(r"taipei-junior-[a-z0-9]+(?:-[a-z0-9]+)*", job.track_id) is None
                or job.code != "job-" + job.track_id.removeprefix("taipei-junior-")
                or job.code in codes
                or not job.label.strip()
                or job.source_subject_code not in roles
                or job.source_subject_code in selected
                or subjects[job.source_subject_code] != job.source_subject_name
                or type(job.bilingual) is not bool
                or job.bilingual != ("雙語" in job.source_subject_name)
                or job.bilingual != job.track_id.endswith("-bilingual")
                or len(job.public_section_labels) != 2
                or job.public_section_labels[0] != "教育專業科目"
                or any(not s.strip() for s in job.public_section_labels)
                or len(set(job.public_section_labels)) != 2
                or job.track_id in labels
                and labels[job.track_id] != job.label
            ):
                raise ValueError("Taipei junior native recruitment category is invalid")
            codes.add(job.code)
            selected.add(job.source_subject_code)
            labels[job.track_id] = job.label
        if not codes or selected != roles.keys():
            raise ValueError("Taipei junior native papers lack complete recruitment applicability")
        for resource in exam.resources:
            if (
                resource.source_url in payloads
                and payloads[resource.source_url] != resource.checksum
            ):
                raise ValueError("Taipei junior shared source URL has competing reviewed checksums")
            payloads[resource.source_url] = resource.checksum
        exams.append(exam)
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
        raise ValueError("Taipei junior projection requires its native annual acquisition context")
    observed = []
    raw_by_url = {}
    for raw in page.papers:
        if (raw.category_raw, raw.category_code) != (CANONICAL_CATEGORY, str(exam.year_roc)):
            raise ValueError("Taipei junior projection requires unmodified acquisition categories")
        for role, url in raw.files.items():
            observed.append((raw.subject_code, raw.subject_name_raw, role, url, checksums.get(url)))
            raw_by_url[url] = raw
    expected = [
        (f.subject_code, f.subject_name_raw, f.file_type, f.source_url, f.checksum)
        for f in exam.files
        if f.file_type != "corrected_answer"
    ]
    if Counter(observed) != Counter(expected):
        raise UnreviewedSourceFile(
            "Taipei junior native listing or payload changed; retain state for review"
        )
    projected = []
    for job in exam.jobs:
        for file in exam.papers_for(job):
            if checksums.get(file.source_url) != file.checksum:
                raise UnreviewedSourceFile("Taipei junior annual correction payload changed")
            raw = raw_by_url.get(file.source_url) or ParsedPaper(
                category_raw=CANONICAL_CATEGORY,
                category_code=str(exam.year_roc),
                subject_code=file.subject_code,
                subject_name_raw=file.subject_name_raw,
                files={file.file_type: file.source_url},
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
            raise ValueError("Taipei junior publication requires exact native post, paper and role")
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
        raise ValueError("Taipei junior publication omitted or duplicated a native post or role")


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
        for file in exam.resources:
            keys = anchors.get(
                (exam.source_exam_id, exam.year_roc, file.source_url, file.checksum), set()
            )
            if not keys or "" in keys:
                raise ValueError("Taipei junior native evidence lacks retained source bytes")
            if verify_mirror:
                for key in keys:
                    if payload_checksum(repo_root / "mirror" / key) != file.checksum:
                        raise ValueError("Taipei junior native mirror differs from reviewed bytes")
            count += 1
    return count


def _legacy_file(record: Mapping[str, Any]) -> tuple[AnnualExam, NativePaper] | None:
    if record.get("provider_id") != PROVIDER_ID or record.get("source_material") is not None:
        return None
    for exam in annual_exams():
        if (
            record.get("source_exam_id") != exam.source_exam_id
            or record.get("year_roc") != exam.year_roc
            or record.get("category_code") != str(exam.year_roc)
            or record.get("category_raw") != CANONICAL_CATEGORY
            or record.get("exam_name_raw") != f"{exam.year_roc}學年度{CANONICAL_CATEGORY}"
        ):
            continue
        for file in exam.files:
            if file.file_type == "corrected_answer":
                continue
            if tuple(
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
    expected = {j.code for j in exam.jobs if j.source_subject_code == file.subject_code}
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
