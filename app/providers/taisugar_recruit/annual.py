"""Annual rules own stages; exact native PDFs own their applicability anchors."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from app.models import ExamAttachment
from app.source_material import SourceDate, SourceMaterial


@dataclass(frozen=True)
class NativePaper:
    subject_code: str
    title: str
    source_url: str
    checksum: str


@dataclass(frozen=True)
class WrittenExam:
    year_roc: int
    source_exam_id: str
    exam_name_raw: str
    rule_title: str
    rule_url: str
    rule_checksum: str
    rule_publication_date: str
    rule_revision_date: str | None
    written_test_date: str
    rule_pdf_pages: tuple[int, ...]
    reviewed_at: str
    reason: str
    papers: tuple[NativePaper, ...]

    @property
    def material(self) -> SourceMaterial:
        return SourceMaterial(
            kind="administered",
            date=SourceDate("exam_year", date.fromisoformat(self.written_test_date).year),
            evidence_url=self.rule_url,
            evidence_label=self.rule_title,
            review_reason="Compound original occupation and applicant applicability require review",
        )

    def rule_attachment(self) -> ExamAttachment:
        edition_date = self.rule_revision_date or self.rule_publication_date
        return ExamAttachment(
            title=self.rule_title,
            file_type="instructions",
            download_url_source=self.rule_url,
            source_material=SourceMaterial(
                kind="reference",
                date=SourceDate("publication_year", date.fromisoformat(edition_date).year),
                evidence_url=self.rule_url,
                evidence_label=self.rule_title,
            ),
        )


def annual_exams_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/taisugar/annual-written-exams-v2.json"


def _official_pdf(url: str) -> None:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.netloc != "www.taisugar.com.tw"
        or re.fullmatch(r"/upload/(?:UserFiles|RelFile)/News/\d+/[^/]+\.pdf", parts.path) is None
        or parts.query
        or parts.fragment
    ):
        raise ValueError("Taiwan Sugar annual evidence requires an exact official PDF URL")


def _checksum(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("Taiwan Sugar annual evidence requires SHA-256 checksums")


def _native_date(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise ValueError("Taiwan Sugar annual evidence requires ISO dates")
    date.fromisoformat(value)


def read_written_exams(path: Path) -> tuple[WrittenExam, ...]:
    from app.providers.taisugar_recruit.client import source_subject_code

    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        set(document) != {"schema_version", "provider_id", "events"}
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 2
        or document["provider_id"] != "taisugar_recruit"
        or not isinstance(document["events"], list)
        or not document["events"]
    ):
        raise ValueError("Unsupported Taiwan Sugar annual written evidence")
    exams = []
    rule_urls: set[str] = set()
    paper_contexts: set[tuple[str, int, str, str]] = set()
    for row in document["events"]:
        if set(row) != set(WrittenExam.__dataclass_fields__):
            raise ValueError("Invalid Taiwan Sugar annual evidence fields")
        papers = []
        source_slots = set()
        for item in row["papers"]:
            if set(item) != set(NativePaper.__dataclass_fields__):
                raise ValueError("Invalid Taiwan Sugar native paper evidence fields")
            paper = NativePaper(**item)
            _official_pdf(paper.source_url)
            _checksum(paper.checksum)
            if (
                paper.subject_code != source_subject_code(paper.source_url)
                or not isinstance(paper.title, str)
                or not paper.title.strip()
                or paper.subject_code in source_slots
            ):
                raise ValueError("Invalid or duplicate Taiwan Sugar native paper context")
            source_slots.add(paper.subject_code)
            papers.append(paper)
        fact = WrittenExam(
            **{**row, "papers": tuple(papers), "rule_pdf_pages": tuple(row["rule_pdf_pages"])}
        )
        _official_pdf(fact.rule_url)
        _checksum(fact.rule_checksum)
        for value in (fact.rule_publication_date, fact.written_test_date, fact.reviewed_at):
            _native_date(value)
        if fact.rule_revision_date is not None:
            _native_date(fact.rule_revision_date)
        if (
            type(fact.year_roc) is not int
            or not 1 <= fact.year_roc <= 999
            or fact.rule_url in rule_urls
            or fact.source_exam_id != f"taisugar-recruit-{fact.year_roc}"
            or not fact.papers
            or not fact.rule_pdf_pages
            or any(type(p) is not int or p < 1 for p in fact.rule_pdf_pages)
            or len(set(fact.rule_pdf_pages)) != len(fact.rule_pdf_pages)
            or any(
                not isinstance(value, str) or not value.strip()
                for value in (fact.exam_name_raw, fact.rule_title, fact.reason)
            )
        ):
            raise ValueError("Invalid or duplicate Taiwan Sugar annual source context")
        # A later official revision can document an earlier sitting. Neither
        # the initial publication nor the revision date dates the paper.
        # One retained source event may collect independently governed recruitment
        # programmes. Their primary rules and native paper slots must be disjoint;
        # otherwise resolution would silently depend on configuration order.
        for paper in fact.papers:
            context = (
                fact.source_exam_id,
                fact.year_roc,
                fact.exam_name_raw,
                paper.subject_code,
            )
            if context in paper_contexts:
                raise ValueError("Duplicate Taiwan Sugar native paper rule context")
            paper_contexts.add(context)
        rule_urls.add(fact.rule_url)
        exams.append(fact)
    return tuple(exams)


@lru_cache(maxsize=1)
def written_exams() -> tuple[WrittenExam, ...]:
    return read_written_exams(annual_exams_path(Path(__file__).resolve().parents[3]))


def resolve_written_exam(
    *,
    source_exam_id: str,
    year_roc: int,
    exam_name_raw: str,
    category_code: str,
    category_raw: str,
    subject_code: str,
    title: str,
    source_url: str,
    checksum: str,
) -> WrittenExam | None:
    if category_code != str(year_roc) or category_raw != "台糖新進工員甄試":
        return None
    for annual in written_exams():
        if (annual.source_exam_id, annual.year_roc, annual.exam_name_raw) != (
            source_exam_id,
            year_roc,
            exam_name_raw,
        ):
            continue
        if any(
            (p.subject_code, p.title, p.source_url, p.checksum)
            == (subject_code, title, source_url, checksum)
            for p in annual.papers
        ):
            return annual
    return None


def _verify_anchor(repo_root: Path, storage_key: str, checksum: str) -> None:
    key = PurePosixPath(storage_key)
    if (
        key.is_absolute()
        or ".." in key.parts
        or "\\" in storage_key
        or key.parts[:2] != ("providers", "taisugar_recruit")
    ):
        raise ValueError("Taiwan Sugar annual evidence has an invalid mirror locator")
    path = repo_root / "mirror" / storage_key
    provider_root = repo_root / "mirror/providers/taisugar_recruit"
    if (
        not path.resolve().is_relative_to(provider_root.resolve())
        or path.is_symlink()
        or not path.is_file()
    ):
        raise ValueError("Taiwan Sugar annual evidence has missing or changed mirror bytes")
    from app.source_revisions import payload_checksum

    if payload_checksum(path) != checksum:
        raise ValueError("Taiwan Sugar annual evidence has missing or changed mirror bytes")


def validate_written_exams(repo_root: Path, *, verify_mirror: bool = False) -> int:
    """Match every exact paper and its retained primary rule, including history."""
    from app.paths import provider_paths
    from app.source_revisions import load_source_revisions
    from app.state import load_provider_state

    provider = provider_paths(repo_root, "taisugar_recruit")
    pages, catalog, _ = load_provider_state(provider)
    papers = [
        (
            p.source_exam_id,
            p.year_roc,
            p.exam_name_raw,
            p.category_code,
            p.category_raw,
            p.subject_code,
            p.subject_name_raw,
            p.download_url_source,
            p.checksum,
            p.storage_key,
        )
        for p in catalog.papers
    ]
    rules = [
        (
            p.source_exam_id,
            p.year_roc,
            a.title,
            a.file_type,
            a.download_url_source,
            a.checksum,
            a.storage_key,
        )
        for p in pages
        for a in p.attachments
    ]
    for revision in load_source_revisions(provider):
        row = revision["source_record"]
        if revision["record_type"] == "paper":
            papers.append(
                (
                    row["source_exam_id"],
                    row["year_roc"],
                    row["exam_name_raw"],
                    row["category_code"],
                    row["category_raw"],
                    row["subject_code"],
                    row["subject_name_raw"],
                    row["download_url_source"],
                    row["checksum"],
                    revision["blob_storage_key"],
                )
            )
        elif revision["record_type"] == "attachment":
            rules.append(
                (
                    row["source_exam_id"],
                    row["year_roc"],
                    row["title"],
                    row["file_type"],
                    row["download_url_source"],
                    row["checksum"],
                    revision["blob_storage_key"],
                )
            )
    count = 0
    for annual in read_written_exams(annual_exams_path(repo_root)):
        expected_rule = (
            annual.source_exam_id,
            annual.year_roc,
            annual.rule_title,
            "instructions",
            annual.rule_url,
            annual.rule_checksum,
        )
        matching_rules = [row for row in rules if row[:-1] == expected_rule]
        if not matching_rules:
            raise ValueError(
                "Taiwan Sugar annual evidence lacks a retained original or rule anchor"
            )
        if verify_mirror:
            for rule_row in matching_rules:
                _verify_anchor(repo_root, rule_row[-1], rule_row[-2])
        for paper in annual.papers:
            expected = (
                annual.source_exam_id,
                annual.year_roc,
                annual.exam_name_raw,
                str(annual.year_roc),
                "台糖新進工員甄試",
                paper.subject_code,
                paper.title,
                paper.source_url,
                paper.checksum,
            )
            matching_papers = [row for row in papers if row[:-1] == expected]
            if not matching_papers:
                raise ValueError(
                    "Taiwan Sugar annual evidence lacks a retained original or rule anchor"
                )
            if verify_mirror:
                for paper_row in matching_papers:
                    _verify_anchor(repo_root, paper_row[-1], paper_row[-2])
            count += 1
    return count
