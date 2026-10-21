"""Reviewed TABF paper contexts, independent of capture years and file order."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from app.providers.tabf_cert.identity import Programme, normalized_label, programme_for_code
from app.providers.tabf_cert.listing import HISTORY_INDEX_URL, NativeEventLink
from app.source_material import SourceDate, SourceMaterial


def native_papers_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/tabf/native-papers-v1.json"


def validate_paper_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "service.tabf.org.tw"
        or re.fullmatch(r"/BEExam/Doc/ExamHistoryEdit/\d+-\d+\.pdf", parsed.path) is None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"Unreviewed TABF paper endpoint: {url}")


@dataclass(frozen=True)
class NativeEvent:
    phid: str
    programme_code: str
    ordinal: int
    native_ordinal_kind: str
    listing_label: str
    year_ad: int
    year_basis: str
    year_evidence_url: str
    year_evidence_checksum: str
    native_year_label: str
    calendar_evidence_url: str | None
    reviewed_at: str
    notes: str

    @property
    def programme(self) -> Programme:
        return programme_for_code(self.programme_code)

    @property
    def source_exam_id(self) -> str:
        return f"tabf-cert-{self.programme_code}-{self.year_ad}-phid-{self.phid}"

    def matches_listing(self, link: NativeEventLink) -> bool:
        return (
            link.phid == self.phid
            and link.programme_code == self.programme_code
            and link.ordinal == self.ordinal
            and normalized_label(link.label) == normalized_label(self.listing_label)
            and link.explicit_year_ad in {None, self.year_ad}
        )


@dataclass(frozen=True)
class NativePaper:
    id: str
    phid: str
    source_url: str
    checksum: str
    file_type: str
    scope_kind: str
    subjects: tuple[str, ...]
    source_label: str
    native_heading: str
    native_subject_label: str
    page_count: int
    inspected_pages: tuple[int, ...]
    review_basis: str
    reviewed_at: str
    notes: str

    @property
    def subject_code(self) -> str:
        return hashlib.sha256(self.source_url.encode("utf-8")).hexdigest()[:24]

    @property
    def subject_label(self) -> str:
        return "、".join(self.subjects)


@dataclass(frozen=True)
class NativeContext:
    event: NativeEvent
    paper: NativePaper

    @property
    def material(self) -> SourceMaterial:
        return SourceMaterial(
            "administered",
            SourceDate("exam_year", self.event.year_ad),
            self.paper.source_url,
            self.paper.native_heading,
        )


@dataclass(frozen=True)
class NativeEvidence:
    events: tuple[NativeEvent, ...]
    papers: tuple[NativePaper, ...]

    def event(self, phid: str) -> NativeEvent:
        for event in self.events:
            if event.phid == phid:
                return event
        raise ValueError(f"TABF native event requires review: PHID {phid}")

    def context(self, phid: str, url: str, checksum: str) -> NativeContext | None:
        for paper in self.papers:
            if (paper.phid, paper.source_url, paper.checksum) == (phid, url, checksum):
                return NativeContext(self.event(phid), paper)
        return None


def _validate_event(event: NativeEvent) -> None:
    programme_for_code(event.programme_code)
    validate_paper_url(event.year_evidence_url)
    if (
        re.fullmatch(r"\d+", event.phid) is None
        or type(event.ordinal) is not int
        or event.ordinal < 1
        or type(event.year_ad) is not int
        or not 1912 <= event.year_ad <= 2910
        or event.native_ordinal_kind not in {"期", "屆", "次"}
        or event.year_basis
        not in {
            "native_answer_period",
            "native_answer_period_and_calendar",
            "native_answer_period_and_handbook",
        }
        or re.fullmatch(r"[0-9a-f]{64}", event.year_evidence_checksum) is None
        or f"{event.year_ad - 1911}年" not in event.native_year_label
        or not event.notes.strip()
    ):
        raise ValueError(f"Invalid TABF native event: {event.phid}")
    label = normalized_label(event.listing_label)
    match = re.fullmatch(r"(?:(\d{3})年)?第(\d+)([期屆次])試題", label)
    if (
        match is None
        or int(match[2]) != event.ordinal
        or (match[1] is not None and int(match[1]) + 1911 != event.year_ad)
    ):
        raise ValueError("TABF event contradicts its reviewed listing label")
    if match[3] != event.native_ordinal_kind and not (
        match[3] == "屆"
        and event.native_ordinal_kind == "期"
        and event.programme_code in {"family-trust-advisor", "senior-financial-planning-advisor"}
    ):
        raise ValueError("TABF native event unit requires its scoped review")
    if event.year_basis == "native_answer_period":
        if event.calendar_evidence_url is not None:
            raise ValueError("An uncorroborated TABF calendar day cannot claim calendar evidence")
    else:
        parsed = urlsplit(event.calendar_evidence_url or "")
        if parsed.scheme != "https" or parsed.netloc not in {
            "www.tabf.org.tw",
            "service.tabf.org.tw",
        }:
            raise ValueError("TABF calendar evidence must belong to the official source")
    date.fromisoformat(event.reviewed_at)


def _validate_paper(paper: NativePaper, event: NativeEvent) -> None:
    validate_paper_url(paper.source_url)
    expected_id = (
        "tabf-native-"
        + hashlib.sha256(paper.source_url.encode("utf-8")).hexdigest()[:20]
        + "-"
        + paper.checksum[:20]
    )
    if (
        paper.id != expected_id
        or re.fullmatch(r"[0-9a-f]{64}", paper.checksum) is None
        or paper.file_type not in {"question", "answer"}
        or not paper.subjects
        or len(set(paper.subjects)) != len(paper.subjects)
        or any(not isinstance(s, str) or not s.strip() for s in paper.subjects)
        or not paper.native_heading
        or not paper.native_subject_label
        or not paper.source_label
        or type(paper.page_count) is not int
        or paper.page_count < 1
        or not paper.inspected_pages
        or len(set(paper.inspected_pages)) != len(paper.inspected_pages)
        or any(type(p) is not int or not 1 <= p <= paper.page_count for p in paper.inspected_pages)
        or paper.review_basis
        not in {
            "native_text_and_official_row",
            "native_key_table_and_official_answer_label",
        }
        or (paper.file_type == "answer") != (normalized_label(paper.source_label) == "答案")
    ):
        raise ValueError(f"Invalid TABF native paper: {paper.id}")
    heading = normalized_label(paper.native_heading)
    programme = event.programme
    native_name = "金融人員基礎學科測驗" if programme.is_aptitude else programme.canonical_label
    if native_name not in heading or (
        re.search(rf"第{event.ordinal}{event.native_ordinal_kind}", heading) is None
    ):
        raise ValueError("TABF native programme or event contradicts the paper heading")
    if programme.grade and programme.level_label not in heading:
        raise ValueError("TABF native grade contradicts the paper heading")
    context_text = heading + normalized_label(paper.native_subject_label)
    for _variant_id, label in programme.variants:
        plain = re.sub(r"[【】()（）]", "", context_text)
        if normalized_label(label) not in plain:
            raise ValueError("TABF qualification variant contradicts its native scope")
    if paper.scope_kind in {"test_section", "combined_test_sections"}:
        if event.programme_code not in {
            "family-trust-advisor",
            "senior-financial-planning-advisor",
        } or not set(paper.subjects) <= {"第一節", "第二節"}:
            raise ValueError("TABF test sections require their native programme context")
    elif programme.is_aptitude:
        if paper.scope_kind != "assessment_module":
            raise ValueError("FIT papers require their independently selectable module")
        module = {"i": 1, "ii": 2, "iii": 3, "iv": 4}[programme.code.rsplit("-", 1)[-1]]
        roman = {1: "I", 2: "II", 3: "III", 4: "IV"}[module]
        if not re.search(rf"考科(?:{roman}|{module})(?![IV0-9])", context_text):
            raise ValueError("FIT native module contradicts the paper context")
    elif paper.scope_kind not in {"subject", "combined_subjects"}:
        raise ValueError("Invalid TABF native subject scope")
    if paper.file_type == "answer" and "答案" not in heading:
        if paper.review_basis != "native_key_table_and_official_answer_label":
            raise ValueError("Headerless TABF answer key requires its exact scoped review")
    if (
        paper.review_basis == "native_key_table_and_official_answer_label"
        and paper.file_type != "answer"
    ):
        raise ValueError("A TABF answer-key review cannot approve a question paper")
    date.fromisoformat(paper.reviewed_at)


def read_native_evidence(path: Path) -> NativeEvidence:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        type(payload.get("schema_version")) is not int
        or payload["schema_version"] != 1
        or payload.get("provider_id") != "tabf_cert"
        or payload.get("evidence_url") != HISTORY_INDEX_URL
        or not payload.get("events")
        or not payload.get("papers")
    ):
        raise ValueError("Unsupported TABF native evidence")
    events = tuple(NativeEvent(**row) for row in payload["events"])
    by_phid = {event.phid: event for event in events}
    if len(by_phid) != len(events):
        raise ValueError("Duplicate TABF native event")
    for event in events:
        _validate_event(event)
    papers = []
    keys = set()
    ids = set()
    for row in payload["papers"]:
        fields: dict[str, Any] = dict(row)
        fields["subjects"] = tuple(fields["subjects"])
        fields["inspected_pages"] = tuple(fields["inspected_pages"])
        paper = NativePaper(**fields)
        if paper.phid not in by_phid:
            raise ValueError("TABF native paper lacks its reviewed event")
        _validate_paper(paper, by_phid[paper.phid])
        key = (paper.phid, paper.source_url, paper.checksum)
        if key in keys or paper.id in ids:
            raise ValueError("Duplicate TABF native paper anchor")
        keys.add(key)
        ids.add(paper.id)
        papers.append(paper)
    for event in events:
        if not any(
            p.phid == event.phid
            and p.file_type == "answer"
            and p.source_url == event.year_evidence_url
            and p.checksum == event.year_evidence_checksum
            for p in papers
        ):
            raise ValueError("TABF event year lacks its exact native answer evidence")
    return NativeEvidence(events, tuple(papers))


@lru_cache(maxsize=1)
def native_evidence() -> NativeEvidence:
    return read_native_evidence(native_papers_path(Path(__file__).resolve().parents[3]))


def native_context(
    source_exam_id: str,
    year_ad: int,
    category_code: str,
    checksum: str,
    source_url: str,
) -> NativeContext | None:
    evidence = native_evidence()
    for event in evidence.events:
        if (event.source_exam_id, event.year_ad, event.programme_code) == (
            source_exam_id,
            year_ad,
            category_code,
        ):
            return evidence.context(event.phid, source_url, checksum)
    return None


def reviewed_native_revisions(
    entries: list[dict[str, Any]],
    *,
    repo_root: Path,
) -> list[dict[str, Any]]:
    evidence = read_native_evidence(native_papers_path(repo_root))
    reviewed = []
    for entry in entries:
        record = entry["source_record"]
        match = re.search(r"-phid-(\d+)$", record.get("source_exam_id", ""))
        if record.get("provider_id") != "tabf_cert" or match is None:
            continue
        context = evidence.context(
            match[1],
            record.get("download_url_source", ""),
            record.get("checksum", ""),
        )
        if context is None:
            continue
        reviewed.append(
            {
                "revision_id": entry["id"],
                "native_paper_id": context.paper.id,
                "programme_code": context.event.programme_code,
                "native_event": context.event.source_exam_id,
                "year_ad": context.event.year_ad,
                "file_type": context.paper.file_type,
                "level_id": context.event.programme.level_id,
                "variant_ids": [key for key, _label in context.event.programme.variants],
                "original_source_record_unchanged": True,
            }
        )
    return reviewed


def validate_native_papers(repo_root: Path, *, verify_mirror: bool = False) -> dict[str, int]:
    from app.models import to_plain_data
    from app.paths import provider_paths
    from app.source_revisions import load_source_revisions
    from app.state import load_provider_state

    evidence = read_native_evidence(native_papers_path(repo_root))
    provider = provider_paths(repo_root, "tabf_cert")
    _pages, catalog, _failures = load_provider_state(provider)
    seen = set()
    paths = set()

    def check(record: dict[str, Any], key: str) -> NativeContext:
        match = re.search(r"-phid-(\d+)$", record.get("source_exam_id", ""))
        context = (
            evidence.context(
                match[1],
                record.get("download_url_source", ""),
                record.get("checksum", ""),
            )
            if match is not None
            else None
        )
        if record.get("provider_id") != "tabf_cert" or context is None:
            raise ValueError("TABF source record lacks its exact native context")
        parts = PurePosixPath(key).parts
        path = repo_root / "mirror" / key
        if (
            parts[:2] != ("providers", "tabf_cert")
            or ".." in parts
            or "\\" in key
            or not key.endswith(".pdf")
            or path.is_symlink()
            or not path.resolve().is_relative_to(provider.mirror_dir.resolve())
        ):
            raise ValueError("TABF native locator escapes its provider owner")
        if verify_mirror and key not in paths:
            if not path.is_file():
                raise ValueError("TABF native payload is missing")
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != context.paper.checksum:
                    raise ValueError("TABF native payload checksum changed")
            paths.add(key)
        seen.add((context.paper.phid, context.paper.source_url, context.paper.checksum))
        return context

    current_anchors = set()
    for paper in catalog.papers:
        context = check(to_plain_data(paper), paper.storage_key)
        event, fact = context.event, context.paper
        anchor = (fact.phid, fact.source_url, fact.checksum)
        if anchor in current_anchors:
            raise ValueError("Duplicate TABF current native paper projection")
        current_anchors.add(anchor)
        if (
            paper.source_exam_id != event.source_exam_id
            or paper.year_roc + 1911 != event.year_ad
            or paper.category_code != event.programme_code
            or paper.subject_code != fact.subject_code
            or paper.subject_name_raw != fact.subject_label
            or paper.file_type != fact.file_type
            or paper.canonical_id != event.programme.canonical_id
            or paper.source_material != context.material
        ):
            raise ValueError("TABF normalized paper contradicts its native context")
    revisions = load_source_revisions(provider)
    for entry in revisions:
        check(entry["source_record"], entry["blob_storage_key"] or "")
    expected = {(p.phid, p.source_url, p.checksum) for p in evidence.papers}
    if seen != expected:
        raise ValueError("TABF native papers lack current or conserved historical coverage")
    return {
        "native_events": len(evidence.events),
        "native_source_anchors": len(expected),
        "native_unique_payloads": len({p.checksum for p in evidence.papers}),
        "paper_projections": len(catalog.papers),
        "retired_source_references": len(revisions),
        "verified_payload_paths": len(paths),
    }
