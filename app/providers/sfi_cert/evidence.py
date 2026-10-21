"""Reviewed native PDF contexts for SFI's mutable download slots."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.providers.sfi_cert.identity import Programme, label_key, programme_for_slug
from app.source_material import SourceDate, SourceMaterial

ARCHIVE_URL = "https://www.sfi.org.tw/Node?id=217"


def native_papers_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/sfi/native-papers-v1.json"


def download_context(url: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    match = re.fullmatch(r"/Download/(?:01|02)/(\d{2})(a?)\.pdf", parsed.path, re.I)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "examweb.sfi.org.tw"
        or parsed.query
        or parsed.fragment
        or match is None
    ):
        raise ValueError(f"Unreviewed SFI download endpoint: {url}")
    return match.group(1), "answer" if match.group(2) else "question"


@dataclass(frozen=True)
class NativePaper:
    id: str
    checksum: str
    category_code: str
    programme_slug: str
    year_roc: int
    round_no: int
    venue: str
    file_type: str
    subjects: tuple[str, ...]
    source_urls: tuple[str, ...]
    native_heading: str
    reviewed_at: str
    inspected_header_pages: tuple[int, ...]
    page_count: int

    @property
    def programme(self) -> Programme:
        programme = programme_for_slug(self.programme_slug)
        if programme is None:
            raise ValueError("Unknown SFI native programme")
        return programme

    @property
    def source_exam_id(self) -> str:
        suffix = f"-{self.venue}" if self.venue else ""
        return f"sfi-cert-{self.programme_slug}-{self.year_roc + 1911}-{self.round_no}{suffix}"

    @property
    def variants(self) -> tuple[tuple[str, str], ...]:
        venue = (("venue-kaohsiung", "高雄考區"),) if self.venue else ()
        return (*self.programme.variants, *venue)

    @property
    def material(self) -> SourceMaterial:
        return SourceMaterial(
            "administered",
            SourceDate("exam_year", self.year_roc + 1911),
            ARCHIVE_URL,
            self.native_heading,
        )


def read_native_papers(path: Path) -> tuple[NativePaper, ...]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        type(document.get("schema_version")) is not int
        or document["schema_version"] != 1
        or document.get("provider_id") != "sfi_cert"
        or document.get("evidence_url") != ARCHIVE_URL
        or not isinstance(document.get("facts"), list)
        or not document["facts"]
    ):
        raise ValueError("Unsupported SFI native paper evidence")
    facts = []
    keys = set()
    ids = set()
    for row in document["facts"]:
        fields: dict[str, Any] = dict(row)
        for name in ("subjects", "source_urls", "inspected_header_pages"):
            fields[name] = tuple(fields[name])
        fact = NativePaper(**fields)
        context = (fact.checksum, fact.category_code, fact.file_type)
        heading = label_key(fact.native_heading)
        native_date = re.search(r"(\d{3})年(?:度)?第(\d+)次", heading[:180])
        if (
            re.fullmatch(r"[0-9a-f]{64}", fact.checksum) is None
            or fact.id != "sfi-native-" + fact.checksum[:20]
            or context in keys
            or fact.id in ids
            or type(fact.year_roc) is not int
            or not 1 <= fact.year_roc <= 999
            or type(fact.round_no) is not int
            or fact.round_no < 1
            or native_date is None
            or tuple(map(int, native_date.groups())) != (fact.year_roc, fact.round_no)
            or not any(label_key(name) in heading[:180] for name in fact.programme.native_names)
            or fact.venue not in {"", "kaohsiung"}
            or ("高雄考區" in heading[:180]) != bool(fact.venue)
            or (fact.file_type == "answer")
            != bool(re.search(r"(?:測驗|試題|選擇題)(?:選擇題|試題)?(?:解答|答案)", heading[:180]))
            or not fact.subjects
            or any(not isinstance(subject, str) or not subject.strip() for subject in fact.subjects)
            or len(set(fact.subjects)) != len(fact.subjects)
            or not fact.source_urls
            or len(set(fact.source_urls)) != len(fact.source_urls)
            or any(
                download_context(url) != (fact.category_code, fact.file_type)
                for url in fact.source_urls
            )
            or type(fact.page_count) is not int
            or fact.page_count < 1
            or not fact.inspected_header_pages
            or fact.inspected_header_pages[0] != 1
            or any(
                type(page) is not int or not 1 <= page <= fact.page_count
                for page in fact.inspected_header_pages
            )
            or tuple(sorted(set(fact.inspected_header_pages))) != fact.inspected_header_pages
        ):
            raise ValueError("Invalid SFI native source context or payload evidence")
        date.fromisoformat(fact.reviewed_at)
        keys.add(context)
        ids.add(fact.id)
        facts.append(fact)
    return tuple(facts)


@lru_cache(maxsize=1)
def native_papers() -> tuple[NativePaper, ...]:
    return read_native_papers(native_papers_path(Path(__file__).resolve().parents[3]))


def reviewed_payload(url: str, data: bytes, programme: Programme) -> NativePaper:
    code, role = download_context(url)
    checksum = hashlib.sha256(data).hexdigest()
    fact = next(
        (
            fact
            for fact in native_papers()
            if (fact.checksum, fact.category_code, fact.file_type, fact.programme_slug)
            == (checksum, code, role, programme.slug)
        ),
        None,
    )
    if not data.startswith(b"%PDF-") or fact is None:
        raise ValueError(f"SFI PDF needs native context review: {url}; SHA256={checksum}")
    return fact


def native_context(
    source_exam_id: str, year_ad: int, category_code: str, checksum: str
) -> NativePaper | None:
    return next(
        (
            fact
            for fact in native_papers()
            if (fact.source_exam_id, fact.year_roc + 1911, fact.category_code, fact.checksum)
            == (source_exam_id, year_ad, category_code, checksum)
        ),
        None,
    )


def validate_native_papers(repo_root: Path, *, verify_mirror: bool = False) -> int:
    """Validate reviewed facts and their current source-record assignments."""
    from app.paths import provider_paths
    from app.state import load_provider_state

    facts = read_native_papers(native_papers_path(repo_root))
    provider = provider_paths(repo_root, "sfi_cert")
    if not provider.data_dir.exists():
        return len(facts)
    _pages, catalog, _failures = load_provider_state(provider)
    for paper in catalog.papers:
        fact = next(
            (
                fact
                for fact in facts
                if (fact.source_exam_id, fact.category_code, fact.file_type, fact.checksum)
                == (paper.source_exam_id, paper.category_code, paper.file_type, paper.checksum)
            ),
            None,
        )
        if (
            fact is None
            or download_context(paper.download_url_source) != (fact.category_code, fact.file_type)
            or paper.year_roc != fact.year_roc
            or paper.category_raw != fact.programme.label
            or paper.subject_name_raw != "、".join(fact.subjects)
            or paper.source_material is None
            or paper.source_material.kind != "administered"
            or paper.source_material.date != fact.material.date
            or paper.source_material.evidence_url != ARCHIVE_URL
        ):
            raise ValueError("SFI current paper differs from its native evidence")
        if verify_mirror:
            path = repo_root / "mirror" / paper.storage_key
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != fact.checksum:
                    raise ValueError("SFI current mirror differs from native PDF bytes")
    return len(facts)


def reviewed_native_revisions(
    entries: list[dict[str, Any]], repo_root: Path
) -> list[dict[str, Any]]:
    """Expose native meaning alongside an immutable earlier wrong assignment."""
    path = native_papers_path(repo_root)
    if not path.exists():
        return []
    facts = read_native_papers(path)
    reviewed = []
    for entry in entries:
        record = entry["source_record"]
        if entry["record_type"] != "paper" or record.get("provider_id") != "sfi_cert":
            continue
        try:
            code, role = download_context(record["download_url_source"])
        except ValueError:
            continue
        fact = next(
            (
                fact
                for fact in facts
                if (fact.checksum, fact.category_code, fact.file_type)
                == (record.get("checksum"), code, role)
            ),
            None,
        )
        if fact is None or (record.get("category_code"), record.get("file_type")) != (code, role):
            continue
        reviewed.append(
            {
                "revision_id": entry["id"],
                "evidence_id": fact.id,
                "disposition": "reviewed_native_paper_context",
                "native_source_exam_id": fact.source_exam_id,
                "native_programme_id": fact.programme.canonical_id,
                "native_programme_label": fact.programme.label,
                "native_round_no": fact.round_no,
                "native_variants": [{"id": key, "label": label} for key, label in fact.variants],
                "native_subjects": list(fact.subjects),
                "native_file_type": fact.file_type,
                "source_material": asdict(fact.material),
            }
        )
    return reviewed
