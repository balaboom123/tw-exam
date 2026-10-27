"""Annual written-stage evidence, independent of compound-paper occupations."""

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
class WrittenExam:
    year_roc: int
    source_exam_id: str
    exam_name_raw: str
    subject_code: str
    title: str
    source_url: str
    checksum: str
    rule_title: str
    rule_url: str
    rule_checksum: str
    rule_publication_date: str
    written_test_date: str
    rule_pdf_pages: tuple[int, ...]
    reviewed_at: str
    reason: str

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
        return ExamAttachment(
            title=self.rule_title,
            file_type="instructions",
            download_url_source=self.rule_url,
            source_material=SourceMaterial(
                kind="reference",
                date=SourceDate(
                    "publication_year", date.fromisoformat(self.rule_publication_date).year
                ),
                evidence_url=self.rule_url,
                evidence_label=self.rule_title,
            ),
        )


def annual_exams_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/taisugar/annual-written-exams-v1.json"


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


def read_written_exams(path: Path) -> tuple[WrittenExam, ...]:
    from app.providers.taisugar_recruit.client import source_subject_code

    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        set(document) != {"schema_version", "provider_id", "facts"}
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 1
        or document["provider_id"] != "taisugar_recruit"
        or not isinstance(document["facts"], list)
        or not document["facts"]
    ):
        raise ValueError("Unsupported Taiwan Sugar annual written evidence")
    facts = []
    contexts = set()
    for row in document["facts"]:
        if set(row) != set(WrittenExam.__dataclass_fields__):
            raise ValueError("Invalid Taiwan Sugar annual evidence fields")
        fact = WrittenExam(**{**row, "rule_pdf_pages": tuple(row["rule_pdf_pages"])})
        for url in (fact.source_url, fact.rule_url):
            _official_pdf(url)
        for checksum in (fact.checksum, fact.rule_checksum):
            if not isinstance(checksum, str) or re.fullmatch(r"[0-9a-f]{64}", checksum) is None:
                raise ValueError("Taiwan Sugar annual evidence requires SHA-256 checksums")
        for value in (fact.rule_publication_date, fact.written_test_date, fact.reviewed_at):
            if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
                raise ValueError("Taiwan Sugar annual evidence requires ISO dates")
            date.fromisoformat(value)
        if (
            type(fact.year_roc) is not int
            or not 1 <= fact.year_roc <= 999
            or fact.source_exam_id != f"taisugar-recruit-{fact.year_roc}"
            or fact.subject_code != source_subject_code(fact.source_url)
            or not fact.rule_pdf_pages
            or any(type(p) is not int or p < 1 for p in fact.rule_pdf_pages)
            or len(set(fact.rule_pdf_pages)) != len(fact.rule_pdf_pages)
            or fact.rule_publication_date > fact.written_test_date
            or any(
                not isinstance(value, str) or not value.strip()
                for value in (fact.title, fact.exam_name_raw, fact.rule_title, fact.reason)
            )
        ):
            raise ValueError("Invalid Taiwan Sugar annual source context")
        context = fact.source_exam_id, fact.subject_code, fact.source_url, fact.checksum
        if context in contexts:
            raise ValueError("Duplicate Taiwan Sugar annual source context")
        contexts.add(context)
        facts.append(fact)
    return tuple(facts)


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
    return next(
        (
            f
            for f in written_exams()
            if (
                f.source_exam_id,
                f.year_roc,
                f.exam_name_raw,
                f.subject_code,
                f.title,
                f.source_url,
                f.checksum,
            )
            == (
                source_exam_id,
                year_roc,
                exam_name_raw,
                subject_code,
                title,
                source_url,
                checksum,
            )
        ),
        None,
    )


def validate_written_exams(repo_root: Path, *, verify_mirror: bool = False) -> int:
    """Match exact native papers and retained rule attachments, including history."""
    from app.paths import provider_paths
    from app.source_revisions import load_source_revisions, payload_checksum
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
    for fact in read_written_exams(annual_exams_path(repo_root)):
        expected = (
            fact.source_exam_id,
            fact.year_roc,
            fact.exam_name_raw,
            str(fact.year_roc),
            "台糖新進工員甄試",
            fact.subject_code,
            fact.title,
            fact.source_url,
            fact.checksum,
        )
        matching_papers = [row for row in papers if row[:-1] == expected]
        expected_rule = (
            fact.source_exam_id,
            fact.year_roc,
            fact.rule_title,
            "instructions",
            fact.rule_url,
            fact.rule_checksum,
        )
        matching_rules = [row for row in rules if row[:-1] == expected_rule]
        if not matching_papers or not matching_rules:
            raise ValueError(
                "Taiwan Sugar annual evidence lacks a retained original or rule anchor"
            )
        if verify_mirror:
            for row in [*matching_papers, *matching_rules]:
                storage_key = row[-1]
                if not isinstance(storage_key, str):
                    raise ValueError("Taiwan Sugar annual evidence has an invalid mirror locator")
                key = PurePosixPath(storage_key)
                if (
                    key.is_absolute()
                    or ".." in key.parts
                    or "\\" in storage_key
                    or key.parts[:2] != ("providers", "taisugar_recruit")
                ):
                    raise ValueError("Taiwan Sugar annual evidence has an invalid mirror locator")
                path = repo_root / "mirror" / storage_key
                if (
                    not path.resolve().is_relative_to(provider.mirror_dir.resolve())
                    or path.is_symlink()
                    or not path.is_file()
                    or payload_checksum(path) != row[-2]
                ):
                    raise ValueError(
                        "Taiwan Sugar annual evidence has missing or changed mirror bytes"
                    )
        count += 1
    return count
