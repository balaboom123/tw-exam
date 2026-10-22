"""Reviewed annual meaning and exact native PDF selections for NSYSU."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from app.models import ExamAttachment, ParsedPaper, SourceExamPage, file_type_label
from app.providers.hce_archive import HCE_CONFIGS
from app.providers.hce_nsysu.selection_plan import MAX_SELECTION_BYTES
from app.source_material import SourceDate, SourceMaterial


@dataclass(frozen=True)
class SelectedPaper:
    subject_code: str
    subject_name_raw: str
    file_type: str
    pages: tuple[int, ...]
    checksum: str
    size: int

    def material(self, year_roc: int, document: NativeDocument) -> SourceMaterial:
        span = f"{self.pages[0]}–{self.pages[-1]}"
        return SourceMaterial(
            "administered",
            SourceDate("exam_year", year_roc + 1911),
            document.source_url,
            f"{year_roc}學年度國立中山大學學士後醫學系｜{self.subject_name_raw}｜"
            f"{file_type_label(self.file_type)}｜來源PDF第{span}頁",
        )


@dataclass(frozen=True)
class NativeDocument:
    source_url: str
    checksum: str
    size: int
    page_count: int
    selections: tuple[SelectedPaper, ...]


@dataclass(frozen=True)
class AdmissionGroup:
    category_code: str
    track_id: str
    label: str
    subjects: tuple[str, ...]

    def accepts(self, selection: SelectedPaper) -> bool:
        return selection.file_type == "all_answers" or selection.subject_code in self.subjects


@dataclass(frozen=True)
class AnnualExam:
    year_roc: int
    source_exam_id: str
    written_test_date: date
    stage_id: str
    groups: tuple[AdmissionGroup, ...]
    documents: tuple[NativeDocument, ...]
    active_parent_checksum: str
    rule_urls: tuple[str, ...]


def native_selections_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/nsysu/native-selections-v1.json"


def _mapping(value: object, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Invalid NSYSU native selection fields")
    return cast(dict[str, Any], value)


def _array(value: object) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError("NSYSU native evidence requires a nonempty array")
    return value


def _integer(value: object) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("Invalid NSYSU native positive integer")
    return value


def _text(value: object, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not value.strip() and not allow_empty):
        raise ValueError("Invalid NSYSU native text")
    return value


def _checksum(value: object) -> str:
    text = _text(value)
    if re.fullmatch(r"[0-9a-f]{64}", text) is None:
        raise ValueError("NSYSU native evidence requires SHA-256 checksums")
    return text


def _official_url(value: object) -> str:
    url = _text(value)
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or parsed.netloc != host
        or not (host == "nsysu.edu.tw" or host.endswith(".nsysu.edu.tw"))
        or parsed.fragment
    ):
        raise ValueError("NSYSU evidence requires an official HTTPS source URL")
    return url


def read_annual_exams(path: Path) -> tuple[AnnualExam, ...]:
    document = _mapping(
        json.loads(path.read_text(encoding="utf-8")), {"schema_version", "provider_id", "events"}
    )
    if (
        type(document["schema_version"]) is not int
        or document["schema_version"] != 1
        or document["provider_id"] != "hce_nsysu"
    ):
        raise ValueError("Unsupported NSYSU native evidence version")
    exams = []
    years: set[int] = set()
    group_meanings: dict[str, tuple[str, str]] = {}
    subject_meanings: dict[str, str] = {}
    for raw in _array(document["events"]):
        row = _mapping(
            raw,
            {
                "year_roc",
                "source_exam_id",
                "written_test_date",
                "stage_id",
                "reviewed_at",
                "rule_evidence",
                "groups",
                "active_parent_checksum",
                "documents",
            },
        )
        year = _integer(row["year_roc"])
        written = date.fromisoformat(_text(row["written_test_date"]))
        date.fromisoformat(_text(row["reviewed_at"]))
        if (
            year > 999
            or year in years
            or written.year != year + 1911
            or row["source_exam_id"] != f"hce-nsysu-{year}"
            or row["stage_id"] != "stage-1"
        ):
            raise ValueError("Conflicting NSYSU annual event, date or stage")
        years.add(year)
        rule_urls = []
        for raw_rule in _array(row["rule_evidence"]):
            rule = _mapping(raw_rule, {"url", "checksum", "kind", "PDF_pages", "label"})
            rule_urls.append(_official_url(rule["url"]))
            _checksum(rule["checksum"])
            _text(rule["label"])
            pages = rule["PDF_pages"]
            if (
                rule["kind"] not in {"html", "pdf"}
                or not isinstance(pages, list)
                or (rule["kind"] == "pdf" and not pages)
                or (rule["kind"] == "html" and pages)
                or any(type(number) is not int or number < 1 for number in pages)
                or len(set(pages)) != len(pages)
            ):
                raise ValueError("Invalid NSYSU native rule-page evidence")
        groups = []
        categories: set[str] = set()
        tracks: set[str] = set()
        for raw_group in _array(row["groups"]):
            group = _mapping(raw_group, {"category_code", "track_id", "label", "subjects"})
            category, track = _text(group["category_code"]), _text(group["track_id"])
            label = _text(group["label"], allow_empty=True)
            subjects = tuple(_text(subject) for subject in _array(group["subjects"]))
            if (
                re.fullmatch(r"post-bacc-medicine(?:-[a-z0-9-]+)?", category) is None
                or re.fullmatch(r"hce-nsysu(?:-[a-z0-9-]+)?", track) is None
                or category in categories
                or track in tracks
                or len(set(subjects)) != len(subjects)
                or any(
                    re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", item) is None
                    for item in subjects
                )
            ):
                raise ValueError("Invalid NSYSU admissions group identity or subjects")
            meaning = track, label
            if category in group_meanings and group_meanings[category] != meaning:
                raise ValueError("NSYSU group code changes its reviewed meaning")
            group_meanings[category] = meaning
            categories.add(category)
            tracks.add(track)
            groups.append(AdmissionGroup(category, track, label, subjects))
        if len(groups) > 1 and any(not group.label for group in groups):
            raise ValueError("Distinct NSYSU groups require native labels")
        documents = []
        contexts: set[tuple[str, str]] = set()
        for raw_document in _array(row["documents"]):
            source = _mapping(
                raw_document, {"source_url", "checksum", "bytes", "page_count", "selections"}
            )
            url, checksum = _official_url(source["source_url"]), _checksum(source["checksum"])
            context = url, checksum
            if context in contexts:
                raise ValueError("Duplicate NSYSU native document version")
            contexts.add(context)
            size, page_count = _integer(source["bytes"]), _integer(source["page_count"])
            selections = []
            physical_pages: list[int] = []
            subjects_seen: set[str] = set()
            output_checksums: set[str] = set()
            for raw_selection in _array(source["selections"]):
                item = _mapping(
                    raw_selection,
                    {"subject_code", "subject_name_raw", "file_type", "pages", "checksum", "bytes"},
                )
                subject = _text(item["subject_code"])
                label = _text(item["subject_name_raw"])
                role = _text(item["file_type"])
                pages = tuple(_integer(number) for number in _array(item["pages"]))
                output_checksum = _checksum(item["checksum"])
                output_size = _integer(item["bytes"])
                if (
                    re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", subject) is None
                    or role not in {"question", "question_answer", "all_answers"}
                    or (subject == "all") != (role == "all_answers")
                    or subject in subjects_seen
                    or output_checksum in output_checksums
                    or output_size > MAX_SELECTION_BYTES
                    or pages != tuple(range(pages[0], pages[-1] + 1))
                    or pages[-1] > page_count
                ):
                    raise ValueError("Invalid NSYSU native subject/role/page selection")
                if subject in subject_meanings and subject_meanings[subject] != label:
                    raise ValueError("NSYSU subject code changes its native wording")
                subject_meanings[subject] = label
                subjects_seen.add(subject)
                output_checksums.add(output_checksum)
                physical_pages.extend(pages)
                selections.append(
                    SelectedPaper(subject, label, role, pages, output_checksum, output_size)
                )
            if sorted(physical_pages) != list(range(1, page_count + 1)):
                raise ValueError("NSYSU selections must conserve every native page exactly once")
            questions = {
                paper.subject_code for paper in selections if paper.file_type != "all_answers"
            }
            required = {subject for group in groups for subject in group.subjects}
            if questions != required:
                raise ValueError("NSYSU groups and native question coverage disagree")
            if any(paper.file_type == "question" for paper in selections) and not any(
                paper.file_type == "all_answers" for paper in selections
            ):
                raise ValueError(
                    "NSYSU separate questions require the native complete answer tables"
                )
            documents.append(NativeDocument(url, checksum, size, page_count, tuple(selections)))
        active = _checksum(row["active_parent_checksum"])
        if sum(source.checksum == active for source in documents) != 1:
            raise ValueError("NSYSU annual context needs one active reviewed native document")
        exams.append(
            AnnualExam(
                year,
                row["source_exam_id"],
                written,
                row["stage_id"],
                tuple(groups),
                tuple(documents),
                active,
                tuple(rule_urls),
            )
        )
    return tuple(exams)


@lru_cache(maxsize=1)
def annual_exams() -> tuple[AnnualExam, ...]:
    return read_annual_exams(native_selections_path(Path(__file__).resolve().parents[3]))


def selection_locator(document: NativeDocument, selection: SelectedPaper) -> str:
    fragment = urlencode(
        {
            "document-sha256": document.checksum,
            "pdf-pages": f"{selection.pages[0]}-{selection.pages[-1]}",
        }
    )
    return f"{document.source_url}#{fragment}"


def resolve_selection_locator(
    url: str, exams: tuple[AnnualExam, ...] | None = None
) -> tuple[AnnualExam, NativeDocument, SelectedPaper] | None:
    parsed = urlsplit(url)
    try:
        values = parse_qs(parsed.fragment, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None
    if set(values) != {"document-sha256", "pdf-pages"} or any(
        len(items) != 1 for items in values.values()
    ):
        return None
    parent_url = urlunsplit(parsed._replace(fragment=""))
    for annual in annual_exams() if exams is None else exams:
        for document in annual.documents:
            if (
                document.source_url != parent_url
                or document.checksum != values["document-sha256"][0]
            ):
                continue
            for selection in document.selections:
                if selection_locator(document, selection) == url:
                    return annual, document, selection
    return None


def project_native_page(
    page: SourceExamPage, *, parent_checksum: str | None = None
) -> SourceExamPage:
    config = HCE_CONFIGS["hce_nsysu"]
    if page.provider_id != "hce_nsysu" or page.year_ad != page.year_roc + 1911:
        raise ValueError("Invalid NSYSU source event context")
    if len(page.papers) != 1:
        raise ValueError("NSYSU native archive must provide one annual combined PDF")
    paper = page.papers[0]
    if (
        paper.category_code != config.category_code
        or paper.source_category_code
        or paper.subject_code != "all"
        or set(paper.files) != {"question_answer"}
    ):
        raise ValueError("NSYSU projection requires its native combined-document context")
    source_url = paper.files["question_answer"]
    annual = next(
        (
            item
            for item in annual_exams()
            if item.source_exam_id == page.source_exam_id and item.year_roc == page.year_roc
        ),
        None,
    )
    document = None
    if annual is not None:
        expected = parent_checksum or annual.active_parent_checksum
        document = next(
            (
                document
                for document in annual.documents
                if document.source_url == source_url and document.checksum == expected
            ),
            None,
        )
    if annual is None or document is None:
        material = SourceMaterial(
            "administered",
            SourceDate("unknown", None),
            source_url,
            paper.subject_name_raw,
            "NSYSU native document has no reviewed annual selections",
        )
        return replace(page, papers=[replace(paper, source_material=material)])
    projected = []
    for selection in document.selections:
        for group in annual.groups:
            if group.accepts(selection):
                projected.append(
                    ParsedPaper(
                        config.category_name,
                        group.category_code,
                        selection.subject_code,
                        selection.subject_name_raw,
                        {selection.file_type: selection_locator(document, selection)},
                        source_material=selection.material(page.year_roc, document),
                        source_category_code=config.category_code,
                    )
                )
    original = ExamAttachment(
        f"{page.year_roc}學年度{config.exam_name}｜各科合併來源原檔",
        "source_document",
        source_url,
        source_material=SourceMaterial(
            "administered",
            SourceDate("exam_year", page.year_ad),
            source_url,
            f"{page.year_roc}學年度{config.exam_name}｜各科合併來源原檔",
        ),
    )
    return replace(page, papers=projected, attachments=[*page.attachments, original])


def resolve_identity(
    *,
    source_exam_id: str,
    year_ad: int,
    category_code: str,
    category_raw: str,
    subject_code: str,
    source_material: SourceMaterial | None,
    checksum: str,
) -> tuple[AnnualExam, AdmissionGroup] | None:
    if category_raw != HCE_CONFIGS["hce_nsysu"].category_name or source_material is None:
        return None
    for annual in annual_exams():
        if annual.source_exam_id != source_exam_id or annual.year_roc + 1911 != year_ad:
            continue
        for document in annual.documents:
            for selection in document.selections:
                if (
                    selection.subject_code != subject_code
                    or selection.checksum != checksum
                    or selection.material(annual.year_roc, document) != source_material
                ):
                    continue
                group = next(
                    (
                        group
                        for group in annual.groups
                        if group.category_code == category_code and group.accepts(selection)
                    ),
                    None,
                )
                return (annual, group) if group is not None else None
    return None


def validate_native_selections(repo_root: Path, *, verify_mirror: bool = False) -> int:
    """Join facts, generated plans, current records and immutable source history."""
    import hashlib

    from app.paths import provider_paths
    from app.providers.hce_nsysu.selection_plan import (
        read_compiled_selection,
        selection_plan_path,
    )
    from app.source_revisions import load_source_revisions
    from app.state import load_provider_state

    provider = provider_paths(repo_root, "hce_nsysu")
    pages, catalog, _failures = load_provider_state(provider)
    exams = read_annual_exams(native_selections_path(repo_root))
    anchors: dict[tuple[str, int, str, str], set[str]] = {}

    def add_anchor(event: str, year: int, url: str, checksum: str, key: str) -> None:
        anchors.setdefault((event, year, url, checksum), set()).add(key)

    for paper in catalog.papers:
        add_anchor(
            paper.source_exam_id,
            paper.year_roc,
            paper.download_url_source,
            paper.checksum,
            paper.storage_key,
        )
    for page in pages:
        for attachment in page.attachments:
            add_anchor(
                page.source_exam_id,
                page.year_roc,
                attachment.download_url_source,
                attachment.checksum,
                attachment.storage_key,
            )
    for revision in load_source_revisions(provider):
        source = revision["source_record"]
        key = revision["blob_storage_key"]
        if key is not None:
            add_anchor(
                source["source_exam_id"],
                source["year_roc"],
                source["download_url_source"],
                source["checksum"],
                key,
            )
    expected_plans: set[str] = set()
    expected_references: set[tuple[str, int, str, str, str, str, str]] = set()
    checked = 0
    for annual in exams:
        for document in annual.documents:
            matches = anchors.get(
                (annual.source_exam_id, annual.year_roc, document.source_url, document.checksum),
                set(),
            )
            if not matches:
                raise ValueError("NSYSU native document lacks a retained source anchor")
            original = None
            if verify_mirror:
                for key in matches:
                    path = repo_root / "mirror" / key
                    data = path.read_bytes()
                    if (
                        len(data) != document.size
                        or hashlib.sha256(data).hexdigest() != document.checksum
                    ):
                        raise ValueError(
                            "NSYSU retained native document bytes do not match evidence"
                        )
                    original = data
            for selection in document.selections:
                plan_path = selection_plan_path(repo_root, selection.checksum)
                expected_plans.add(plan_path.name)
                plan = read_compiled_selection(
                    plan_path,
                    parent_checksum=document.checksum,
                    parent_bytes=document.size,
                    output_checksum=selection.checksum,
                    output_bytes=selection.size,
                )
                if original is not None:
                    plan.apply(original)
                checked += 1
                if document.checksum == annual.active_parent_checksum:
                    for group in annual.groups:
                        if group.accepts(selection):
                            expected_references.add(
                                (
                                    annual.source_exam_id,
                                    annual.year_roc,
                                    group.category_code,
                                    selection.subject_code,
                                    selection.file_type,
                                    selection_locator(document, selection),
                                    selection.checksum,
                                )
                            )
    plan_dir = provider.data_dir / "native-selections"
    if {path.name for path in plan_dir.iterdir()} != expected_plans:
        raise ValueError("NSYSU generated selection plans are missing or orphaned")
    projected = [paper for paper in catalog.papers if urlsplit(paper.download_url_source).fragment]
    if projected:
        actual_references = set()
        for paper in projected:
            context = resolve_selection_locator(paper.download_url_source, exams)
            if context is None:
                raise ValueError("NSYSU current paper has an unreviewed native page locator")
            annual, document, selection = context
            matched_group = next(
                (
                    group
                    for group in annual.groups
                    if group.category_code == paper.category_code and group.accepts(selection)
                ),
                None,
            )
            if (
                matched_group is None
                or paper.source_exam_id != annual.source_exam_id
                or paper.year_roc != annual.year_roc
                or paper.category_raw != HCE_CONFIGS["hce_nsysu"].category_name
                or paper.subject_code != selection.subject_code
                or paper.subject_name_raw != selection.subject_name_raw
                or paper.file_type != selection.file_type
                or paper.checksum != selection.checksum
                or paper.source_material != selection.material(annual.year_roc, document)
            ):
                raise ValueError(
                    "NSYSU selected paper conflicts with native identity/role evidence"
                )
            actual_references.add(
                (
                    paper.source_exam_id,
                    paper.year_roc,
                    paper.category_code,
                    paper.subject_code,
                    paper.file_type,
                    paper.download_url_source,
                    paper.checksum,
                )
            )
            if verify_mirror:
                payload = (repo_root / "mirror" / paper.storage_key).read_bytes()
                if (
                    len(payload) != selection.size
                    or hashlib.sha256(payload).hexdigest() != selection.checksum
                ):
                    raise ValueError("NSYSU current selected PDF bytes do not match evidence")
        if len(actual_references) != len(projected) or actual_references != expected_references:
            raise ValueError("Migrate complete NSYSU annual/group history before publication")
    return checked


def reviewed_native_revisions(
    entries: list[dict[str, Any]], *, repo_root: Path
) -> list[dict[str, Any]]:
    """Describe retained originals/selections without rewriting their old records."""
    from app.models import to_plain_data

    exams = read_annual_exams(native_selections_path(repo_root))
    views = []
    for entry in entries:
        source = entry["source_record"]
        annual = next(
            (
                annual
                for annual in exams
                if annual.source_exam_id == source["source_exam_id"]
                and annual.year_roc == source["year_roc"]
            ),
            None,
        )
        if annual is None:
            continue
        document = next(
            (
                document
                for document in annual.documents
                if document.source_url == source["download_url_source"]
                and document.checksum == source["checksum"]
            ),
            None,
        )
        native_original = document is not None and (
            (entry["record_type"] == "attachment" and source["file_type"] == "source_document")
            or (
                entry["record_type"] == "paper"
                and source["category_code"] == HCE_CONFIGS["hce_nsysu"].category_code
                and source["category_raw"] == HCE_CONFIGS["hce_nsysu"].category_name
                and source["subject_code"] == "all"
                and source["subject_name_raw"] == "各科試題與參考答案"
                and source["file_type"] == "question_answer"
            )
        )
        if native_original:
            material = SourceMaterial(
                "administered",
                SourceDate("exam_year", annual.year_roc + 1911),
                source["download_url_source"],
                f"{annual.year_roc}學年度國立中山大學學士後醫學系｜各科合併來源原檔",
            )
            views.append(
                {
                    "revision_id": entry["id"],
                    "disposition": "native_source_document",
                    "source_material": to_plain_data(material),
                    "stage_id": annual.stage_id,
                    "admission_groups": [
                        {"track_id": group.track_id, "label": group.label or "無分組"}
                        for group in annual.groups
                    ],
                    "reason": (
                        "Original combined native PDF is retained whole; reviewed subject "
                        "selections own individual paper/group applicability. "
                        "Earlier source metadata remains immutable."
                    ),
                }
            )
            continue
        if entry["record_type"] != "paper":
            continue
        selected = resolve_selection_locator(source["download_url_source"], exams)
        if selected is None:
            continue
        selected_annual, selected_document, selection = selected
        group = next(
            (
                group
                for group in selected_annual.groups
                if group.category_code == source["category_code"] and group.accepts(selection)
            ),
            None,
        )
        if (
            group is None
            or selected_annual != annual
            or source["subject_code"] != selection.subject_code
            or source["subject_name_raw"] != selection.subject_name_raw
            or source["file_type"] != selection.file_type
            or source["checksum"] != selection.checksum
            or source["category_raw"] != HCE_CONFIGS["hce_nsysu"].category_name
        ):
            continue
        views.append(
            {
                "revision_id": entry["id"],
                "disposition": "reviewed_native_paper_selection",
                "source_material": to_plain_data(
                    selection.material(annual.year_roc, selected_document)
                ),
                "stage_id": annual.stage_id,
                "track_id": group.track_id,
                "parent_checksum": selected_document.checksum,
                "native_pages": list(selection.pages),
            }
        )
    return views
