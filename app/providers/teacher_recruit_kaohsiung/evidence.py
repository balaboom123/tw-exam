"""Annual official recruitment posts and complete native booklet bindings."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.models import ExamAttachment, NormalizedPaper, ParsedPaper, SourceExamPage
from app.providers.teacher_recruit_kaohsiung.archives import (
    NativeZip,
    NativeZipMember,
    attachment_notice,
    validate_zip_contract,
)
from app.source_material import SourceDate, SourceMaterial

PROVIDER_ID = "teacher_recruit_kaohsiung"
PAPER_ROLES = {"question", "answer", "corrected_answer"}
REFERENCE_ROLES = {"instructions", "source_document"}


def _text(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


@dataclass(frozen=True)
class NativeAttachment:
    label: str
    url: str


@dataclass(frozen=True)
class NativeNotice:
    notice_id: str
    title: str
    publication_date: str
    attachments: tuple[NativeAttachment, ...]
    purpose: str

    @property
    def url(self) -> str:
        return "https://bulletin.kh.edu.tw/view.php?sn=" + self.notice_id


@dataclass(frozen=True)
class RecruitmentCategory:
    code: str
    label: str
    track_id: str
    booklets: tuple[str, ...]
    weights: tuple[int, ...]
    school_level: str
    registration_groups: tuple[str, ...]


@dataclass(frozen=True)
class NativeFile:
    file_id: str
    subject_code: str
    subject_name: str
    file_type: str
    notice_id: str
    source_url: str
    size: int
    checksum: str
    page_count: int
    inspected_pages: tuple[int, ...]
    native_header: str
    multiple_choice_count: int
    essay_count: int
    archive_id: str = ""
    member_path: str = ""


@dataclass(frozen=True)
class LegacySource:
    native_file_id: str
    source_record: dict[str, Any]


@dataclass(frozen=True)
class AnnualExam:
    year_roc: int
    source_exam_id: str
    scope: str
    programme_name: str
    written_test_date: str
    reviewed_at: str
    review_notes: str
    notices: tuple[NativeNotice, ...]
    jobs: tuple[RecruitmentCategory, ...]
    archives: tuple[NativeZip, ...]
    files: tuple[NativeFile, ...]
    legacy_sources: tuple[LegacySource, ...]

    @property
    def series_id(self) -> str:
        return f"kaohsiung-{self.scope}-teacher-recruitment"

    @property
    def series_label(self) -> str:
        return (
            "高雄市國小正式教師聯合甄選"
            if self.scope == "elementary"
            else "高雄市國中小暨學前特殊教育班教師甄選"
        )

    def papers_for(self, job: RecruitmentCategory) -> tuple[NativeFile, ...]:
        return tuple(
            file
            for file in self.files
            if file.file_type in PAPER_ROLES and file.subject_code in job.booklets
        )

    def material(self, file: NativeFile) -> SourceMaterial:
        return SourceMaterial(
            "administered",
            SourceDate("exam_year", self.year_roc + 1911),
            file.source_url,
            f"{self.programme_name}｜{file.subject_name}",
        )

    def attachments(self) -> list[ExamAttachment]:
        notices = {notice.notice_id: notice for notice in self.notices}
        attachments = [
            ExamAttachment(
                file.native_header.splitlines()[0],
                file.file_type,
                file.source_url,
                source_material=SourceMaterial(
                    "reference",
                    SourceDate(
                        "publication_year",
                        date.fromisoformat(notices[file.notice_id].publication_date).year,
                    ),
                    file.source_url,
                    file.native_header.splitlines()[0],
                ),
            )
            for file in self.files
            if file.file_type in REFERENCE_ROLES
        ]
        labels = {
            "initial-questions": "初試試題原始壓縮檔",
            "suggested-answers": "參考答案原始壓縮檔",
            "final-answers": "正確答案原始壓縮檔",
        }
        for archive in self.archives:
            title = f"{self.programme_name}｜{labels[archive.archive_id]}"
            attachments.append(
                ExamAttachment(
                    title,
                    "source_archive",
                    archive.source_url,
                    source_material=SourceMaterial(
                        "reference",
                        SourceDate("publication_year", self.year_roc + 1911),
                        archive.source_url,
                        title,
                    ),
                )
            )
        return attachments

    def source_page(self) -> SourceExamPage:
        return SourceExamPage(
            self.source_exam_id,
            self.year_roc + 1911,
            self.year_roc,
            self.programme_name,
            self.attachments(),
            [
                ParsedPaper(
                    category_raw=job.label,
                    category_code=job.code,
                    subject_code=file.subject_code,
                    subject_name_raw=file.subject_name,
                    files={file.file_type: file.source_url},
                    source_category_code=self.scope,
                    source_material=self.material(file),
                )
                for job in self.jobs
                for file in self.papers_for(job)
            ],
            PROVIDER_ID,
        )


def native_programmes_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/teacher-recruit-kaohsiung/native-programmes-v1.json"


def _bound_file(row: dict[str, Any], archives: tuple[NativeZip, ...]) -> NativeFile:
    fields = dict(row)
    source = fields.pop("source")
    if set(source) == {"url", "size", "checksum"}:
        fields.update(source_url=source["url"], size=source["size"], checksum=source["checksum"])
    elif set(source) == {"archive_id", "member_path"}:
        archive = next((a for a in archives if a.archive_id == source["archive_id"]), None)
        if archive is None:
            raise ValueError("Kaohsiung native file lacks its exact parent ZIP")
        member = next((m for m in archive.members if m.path == source["member_path"]), None)
        if member is None:
            raise ValueError("Kaohsiung native file lacks its exact parent ZIP member")
        fields.update(
            source_url=archive.locator(member),
            size=member.size,
            checksum=member.checksum,
            archive_id=archive.archive_id,
            member_path=member.path,
        )
    else:
        raise ValueError("Unsupported Kaohsiung native PDF source binding")
    fields["inspected_pages"] = tuple(fields["inspected_pages"])
    return NativeFile(**fields)


def _validate_exam(exam: AnnualExam) -> None:
    written = date.fromisoformat(exam.written_test_date)
    if (
        type(exam.year_roc) is not int
        or not 1 <= exam.year_roc <= 999
        or exam.scope not in {"elementary", "special"}
        or exam.source_exam_id != f"teacher-recruit-kaohsiung-{exam.year_roc}-{exam.scope}"
        or str(exam.year_roc) not in exam.programme_name
        or not exam.programme_name.startswith("高雄市")
        or written.year != exam.year_roc + 1911
        or written > date.fromisoformat(exam.reviewed_at)
        or not exam.review_notes.strip()
    ):
        raise ValueError("Invalid Kaohsiung annual programme or event year")
    notices = {n.notice_id: n for n in exam.notices}
    if not notices or len(notices) != len(exam.notices):
        raise ValueError("Missing or repeated Kaohsiung native notice")
    for notice in exam.notices:
        if (
            re.fullmatch(rf"B{exam.year_roc:03d}[0-9]{{6}}", notice.notice_id) is None
            or not notice.title.strip()
            or date.fromisoformat(notice.publication_date).year != written.year
            or notice.purpose not in {"native-resources", "administration"}
            or any(
                not a.label.strip() or attachment_notice(a.url) != notice.notice_id
                for a in notice.attachments
            )
            or len({a.url for a in notice.attachments}) != len(notice.attachments)
        ):
            raise ValueError("Invalid Kaohsiung notice or complete attachment population")
    archives = {a.archive_id: a for a in exam.archives}
    expected_archives = {"initial-questions", "suggested-answers", "final-answers"}
    if len(archives) != len(exam.archives) or set(archives) != (
        expected_archives if exam.scope == "elementary" else set()
    ):
        raise ValueError("Kaohsiung native ZIP roles require their complete original population")
    for archive in exam.archives:
        validate_zip_contract(archive)
        archive_notice_id = attachment_notice(archive.source_url)
        if archive_notice_id not in notices or archive.source_url not in {
            a.url for a in notices[archive_notice_id].attachments
        }:
            raise ValueError("Kaohsiung native ZIP lacks its official notice")
    files = {f.file_id: f for f in exam.files}
    if len(files) != len(exam.files) or not files:
        raise ValueError("Missing or repeated Kaohsiung native PDF")
    by_booklet: dict[str, list[NativeFile]] = {}
    member_bindings: Counter[tuple[str, str]] = Counter()
    for file in exam.files:
        if file.notice_id not in notices:
            raise ValueError("Kaohsiung native PDF lacks its official notice")
        notice = notices[file.notice_id]
        if (
            notice.purpose != "native-resources"
            or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", file.file_id) is None
            or not file.native_header.strip()
            or type(file.size) is not int
            or file.size < 1
            or re.fullmatch(r"[0-9a-f]{64}", file.checksum) is None
            or type(file.page_count) is not int
            or file.page_count < 1
            or not file.inspected_pages
            or tuple(sorted(set(file.inspected_pages))) != file.inspected_pages
            or any(
                type(n) is not int or not 1 <= n <= file.page_count for n in file.inspected_pages
            )
            or type(file.multiple_choice_count) is not int
            or type(file.essay_count) is not int
            or file.multiple_choice_count < 0
            or file.essay_count < 0
        ):
            raise ValueError("Invalid Kaohsiung native PDF byte or focused review context")
        if file.archive_id:
            member_bindings[file.archive_id, file.member_path] += 1
            archive_roles = {
                "initial-questions": "question",
                "suggested-answers": "answer",
                "final-answers": "corrected_answer",
            }
            if (
                file.archive_id not in archives
                or file.file_type != archive_roles[file.archive_id]
                or file.notice_id != attachment_notice(archives[file.archive_id].source_url)
            ):
                raise ValueError("Invalid Kaohsiung native archive paper role")
        elif (
            attachment_notice(file.source_url) != notice.notice_id
            or file.source_url not in {a.url for a in notice.attachments}
            or file.member_path
        ):
            raise ValueError("Kaohsiung native PDF differs from its official attachment")
        if file.file_type in PAPER_ROLES:
            if not file.subject_code or not file.subject_name.strip():
                raise ValueError("Kaohsiung native paper lacks its booklet meaning")
            by_booklet.setdefault(file.subject_code, []).append(file)
            if file.file_type == "question":
                if (
                    not file.multiple_choice_count + file.essay_count
                    or notice.publication_date != exam.written_test_date
                ):
                    raise ValueError("Kaohsiung initial question scope or date changed")
            elif file.multiple_choice_count or file.essay_count:
                raise ValueError("Kaohsiung answer keys are not additional examination questions")
        elif (
            file.file_type not in REFERENCE_ROLES
            or file.subject_code
            or file.subject_name
            or file.multiple_choice_count
            or file.essay_count
        ):
            raise ValueError("Kaohsiung reference document has paper semantics")
    expected_members = Counter((a.archive_id, m.path) for a in exam.archives for m in a.members)
    if member_bindings != expected_members:
        raise ValueError("Kaohsiung native PDF bindings omit or duplicate an upstream ZIP member")
    if len({f.source_url for f in exam.files}) != len(exam.files):
        raise ValueError("Kaohsiung native files have competing source locators")
    for booklet in by_booklet.values():
        questions = [f for f in booklet if f.file_type == "question"]
        if len(questions) != 1:
            raise ValueError("Kaohsiung booklet requires exactly one initial question paper")
        roles = [f.file_type for f in booklet]
        expected_roles = PAPER_ROLES if questions[0].multiple_choice_count else {"question"}
        if len(roles) != len(expected_roles) or set(roles) != expected_roles:
            raise ValueError("Kaohsiung native booklet omitted or duplicated a required key role")
    selected: set[str] = set()
    codes = set()
    for job in exam.jobs:
        if (
            not job.label.strip()
            or job.code in codes
            or not job.track_id.startswith(f"kaohsiung-{exam.scope}-")
            or job.code != "job-" + job.track_id.removeprefix("kaohsiung-")
            or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", job.track_id) is None
            or job.school_level not in {"elementary", "junior", "preschool"}
            or not job.booklets
            or len(set(job.booklets)) != len(job.booklets)
            or set(job.booklets) - by_booklet.keys()
            or len(job.weights) != len(job.booklets)
            or any(type(n) is not int or not 0 < n <= 100 for n in job.weights)
            or sum(job.weights) != 100
            or len(set(job.registration_groups)) != len(job.registration_groups)
        ):
            raise ValueError(
                "Invalid Kaohsiung post, shared-booklet applicability or score weights"
            )
        codes.add(job.code)
        selected.update(job.booklets)
    if not codes or selected != by_booklet.keys():
        raise ValueError("Kaohsiung posts do not cover every native written booklet")
    if sum(f.file_type == "instructions" for f in exam.files) != 1 or not any(
        f.file_type == "source_document" for f in exam.files
    ):
        raise ValueError("Kaohsiung recruitment meaning needs its annual rule and vacancy evidence")
    for legacy in exam.legacy_sources:
        legacy_file = files.get(legacy.native_file_id)
        old = legacy.source_record
        required_context = {
            "provider_id",
            "source_exam_id",
            "year_roc",
            "exam_name_raw",
            "category_code",
            "category_raw",
            "subject_code",
            "subject_name_raw",
            "file_type",
            "download_url_source",
            "checksum",
        }
        if (
            set(old) != required_context
            or legacy_file is None
            or (
                old.get("provider_id"),
                old.get("source_exam_id"),
                old.get("year_roc"),
                old.get("file_type"),
                old.get("checksum"),
            )
            != (
                PROVIDER_ID,
                exam.source_exam_id,
                exam.year_roc,
                legacy_file.file_type,
                legacy_file.checksum,
            )
        ):
            raise ValueError(
                "Kaohsiung legacy source must retain the exact native event, role and bytes"
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
        raise ValueError("Unsupported Kaohsiung native programme evidence")
    exams = []
    try:
        for row in document["events"]:
            fields = dict(row)
            fields["notices"] = tuple(
                NativeNotice(
                    **{**n, "attachments": tuple(NativeAttachment(**a) for a in n["attachments"])}
                )
                for n in fields["notices"]
            )
            fields["jobs"] = tuple(
                RecruitmentCategory(
                    **{
                        **j,
                        **{k: tuple(j[k]) for k in ("booklets", "weights", "registration_groups")},
                    }
                )
                for j in fields["jobs"]
            )
            fields["archives"] = tuple(
                NativeZip(**{**a, "members": tuple(NativeZipMember(**m) for m in a["members"])})
                for a in fields["archives"]
            )
            fields["files"] = tuple(_bound_file(f, fields["archives"]) for f in fields["files"])
            fields["legacy_sources"] = tuple(LegacySource(**s) for s in fields["legacy_sources"])
            exam = AnnualExam(**fields)
            _validate_exam(exam)
            exams.append(exam)
    except (KeyError, TypeError, StopIteration) as exc:
        raise ValueError("Invalid Kaohsiung native programme fields") from exc
    if len({e.source_exam_id for e in exams}) != len(exams):
        raise ValueError("Repeated Kaohsiung annual programme scope")
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
) -> tuple[AnnualExam, RecruitmentCategory, NativeFile] | None:
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
                if (
                    subject_code,
                    _text(subject_name_raw),
                    source_url,
                    checksum,
                    source_material,
                ) == (
                    file.subject_code,
                    _text(file.subject_name),
                    file.source_url,
                    file.checksum,
                    exam.material(file),
                ):
                    return exam, job, file
    return None


def _paper_context(
    paper: NormalizedPaper,
) -> tuple[AnnualExam, RecruitmentCategory, NativeFile] | None:
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
    exam, job, file = context
    if (
        paper.file_type != file.file_type
        or paper.classification_confidence != "high"
        or (
            paper.domain_id,
            paper.exam_family_id,
            paper.exam_series_id,
            paper.track_id,
            paper.level_id,
            paper.stage_id,
            tuple(paper.variant_ids),
        )
        != (
            "teacher",
            "teacher-exam",
            exam.series_id,
            job.track_id,
            "not-applicable",
            "stage-1",
            (),
        )
    ):
        return None
    return context


def validate_public_papers(
    papers: Iterable[NormalizedPaper], *, complete_history: bool = False
) -> None:
    observed: Counter[tuple[str, str, str]] = Counter()
    active = set()
    for paper in papers:
        if paper.provider_id != PROVIDER_ID:
            continue
        context = _paper_context(paper)
        if context is None:
            raise ValueError(
                "Kaohsiung publication requires the exact native programme, post, booklet and role"
            )
        exam, job, file = context
        active.add((exam.source_exam_id, job.code))
        observed[exam.source_exam_id, job.code, file.file_id] += 1
    if complete_history:
        active = {(e.source_exam_id, j.code) for e in annual_exams() for j in e.jobs}
    expected = Counter(
        (e.source_exam_id, j.code, f.file_id)
        for e in annual_exams()
        for j in e.jobs
        if (e.source_exam_id, j.code) in active
        for f in e.papers_for(j)
    )
    if observed != expected:
        raise ValueError("Kaohsiung publication omitted or duplicated a native post or file role")


def _legacy_context(record: dict[str, Any]) -> tuple[AnnualExam, NativeFile] | None:
    if record.get("provider_id") != PROVIDER_ID or record.get("source_material") is not None:
        return None
    for exam in annual_exams():
        for legacy in exam.legacy_sources:
            if all(record.get(key) == value for key, value in legacy.source_record.items()):
                return exam, next(
                    file for file in exam.files if file.file_id == legacy.native_file_id
                )
    return None


def legacy_category_targets(
    previous: NormalizedPaper, current: Iterable[NormalizedPaper]
) -> list[NormalizedPaper]:
    native = _legacy_context(vars(previous))
    if native is None:
        return []
    exam, file = native
    targets = []
    for paper in current:
        if paper.provider_id != PROVIDER_ID:
            continue
        context = _paper_context(paper)
        if context is not None and context[0] == exam and context[2] == file:
            targets.append(paper)
    expected = {j.code for j in exam.jobs if file in exam.papers_for(j)}
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
        anchor = (
            record["source_exam_id"],
            record["year_roc"],
            record["download_url_source"],
            record["checksum"],
        )
        anchors.setdefault(anchor, set()).add(key)
    count = 0
    for exam in read_annual_exams(native_programmes_path(repo_root)):
        resources: tuple[NativeFile | NativeZip, ...] = (*exam.files, *exam.archives)
        for resource in resources:
            keys = anchors.get(
                (exam.source_exam_id, exam.year_roc, resource.source_url, resource.checksum), set()
            )
            if not keys or "" in keys:
                raise ValueError(
                    "Kaohsiung native resource lacks its retained provider source anchor"
                )
            if verify_mirror:
                for key in keys:
                    if payload_checksum(repo_root / "mirror" / key) != resource.checksum:
                        raise ValueError("Kaohsiung native mirror differs from the reviewed bytes")
            count += 1
    return count
