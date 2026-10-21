"""Exact, reviewed iPAS document contexts, separate from download filenames."""

from __future__ import annotations

import hashlib
import io
import json
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from app.providers.ipas_cert.identity import (
    GRADE_LABELS,
    RESOURCE_STORAGE_YEAR,
    ROUTE_LABELS,
    Programme,
    programme_for_code,
)
from app.source_material import SourceDate, SourceMaterial

HOME_URL = "https://ipd.nat.gov.tw/ipas/"


def native_documents_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/ipas/native-documents-v1.json"


def validate_source_url(url: str) -> None:
    parsed = urlsplit(url)
    prefix = (
        "/ipas/api/proxy/uploads/" if parsed.netloc == "ipd.nat.gov.tw" else "/api/proxy/uploads/"
    )
    if (
        parsed.scheme != "https"
        or parsed.netloc not in {"ipd.nat.gov.tw", "www.ipas.org.tw"}
        or not parsed.path.startswith(prefix)
        or Path(parsed.path).suffix.lower() not in {".pdf", ".odt", ".ods"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"Unreviewed iPAS document endpoint: {url}")


def validated_payload_format(url: str, data: bytes) -> str:
    """Keep native PDFs and OpenDocument references distinct from generic ZIPs."""
    extension = Path(urlsplit(url).path).suffix.lower()
    if extension == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise ValueError(f"iPAS document is not a native PDF: {url}")
        return "pdf"
    mimetypes = {
        ".odt": b"application/vnd.oasis.opendocument.text",
        ".ods": b"application/vnd.oasis.opendocument.spreadsheet",
    }
    if extension not in mimetypes:
        raise ValueError(f"Unreviewed iPAS native format: {url}")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if (
                archive.getinfo("mimetype").file_size > 128
                or archive.read("mimetype") != mimetypes[extension]
            ):
                raise ValueError("iPAS OpenDocument mimetype does not match its native extension")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ValueError("iPAS source is not a valid native OpenDocument container") from exc
    return extension.removeprefix(".")


@dataclass(frozen=True)
class SourceReference:
    programme_code: str
    url: str


@dataclass(frozen=True)
class NativeDocument:
    id: str
    checksum: str
    payload_format: str
    sources: tuple[SourceReference, ...]
    kind: str
    file_type: str
    grade: str
    session: str
    subjects: tuple[str, ...]
    routes: tuple[str, ...]
    date: SourceDate
    edition_label: str
    document_type: str
    label: str
    native_heading: str
    native_material_label: str
    review_basis: str
    reviewed_at: str
    inspected_pages: tuple[int, ...]
    page_count: int | None
    notes: str

    @property
    def is_paper(self) -> bool:
        return self.kind != "reference"

    @property
    def storage_year(self) -> int:
        return self.date.year_ad if self.is_paper and self.date.year_ad else RESOURCE_STORAGE_YEAR

    @property
    def subject_label(self) -> str:
        return "、".join(self.subjects)

    @property
    def subject_code(self) -> str:
        return hashlib.sha256(self.subject_label.encode("utf-8")).hexdigest()[:24]

    def event_code(self, programme_code: str) -> str:
        if not self.is_paper:
            raise ValueError("A reference document does not define an examination event")
        prefix = f"ipas-cert-{programme_code.lower()}-{self.grade}"
        if self.kind == "administered":
            suffix = f"-{self.session}" if self.session else ""
            return f"{prefix}-{self.storage_year}{suffix}"
        edition = str(self.date.year_ad) if self.date.year_ad else "undated"
        return f"{prefix}-{self.kind}-{edition}"

    def category_code(self, programme_code: str, route: str = "") -> str:
        suffix = f"-{route}" if route else ""
        return f"{programme_code.lower()}-{self.grade}{suffix}"

    def material(self, programme_code: str, url: str) -> SourceMaterial:
        if SourceReference(programme_code, url) not in self.sources:
            raise ValueError("iPAS material requires its reviewed programme and source URL")
        reason = self.notes if self.date.basis == "unknown" else ""
        return SourceMaterial(self.kind, self.date, url, self.label, reason)


@dataclass(frozen=True)
class PaperContext:
    document: NativeDocument
    programme: Programme
    route: str

    @property
    def variants(self) -> tuple[tuple[str, str], ...]:
        pairs = []
        if self.route:
            pairs.append((f"qualification-{self.route}", ROUTE_LABELS[self.route]))
        if self.document.edition_label:
            token = re.sub(r"[^0-9a-z]+", "-", self.document.edition_label.lower()).strip("-")
            pairs.append((f"content-edition-{token}", self.document.edition_label))
        return tuple(pairs)


def read_native_documents(path: Path) -> tuple[NativeDocument, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        type(payload.get("schema_version")) is not int
        or payload["schema_version"] != 1
        or payload.get("provider_id") != "ipas_cert"
        or payload.get("evidence_url") != HOME_URL
        or not isinstance(payload.get("facts"), list)
        or not payload["facts"]
    ):
        raise ValueError("Unsupported iPAS native document evidence")
    documents = []
    ids = set()
    anchors = set()
    for row in payload["facts"]:
        fields: dict[str, Any] = dict(row)
        fields["sources"] = tuple(SourceReference(**s) for s in fields["sources"])
        fields["date"] = SourceDate.from_data(fields["date"])
        for name in ("subjects", "routes", "inspected_pages"):
            fields[name] = tuple(fields[name])
        document = NativeDocument(**fields)
        if (
            re.fullmatch(r"[0-9a-f]{64}", document.checksum) is None
            or document.id != "ipas-native-" + document.checksum[:20]
            or document.id in ids
            or not document.sources
            or document.payload_format not in {"pdf", "odt", "ods"}
            or len(set(document.sources)) != len(document.sources)
            or (
                document.payload_format == "pdf"
                and (
                    type(document.page_count) is not int
                    or document.page_count < 1
                    or not document.inspected_pages
                    or any(
                        type(p) is not int or not 1 <= p <= document.page_count
                        for p in document.inspected_pages
                    )
                )
            )
            or (
                document.payload_format != "pdf"
                and (
                    document.page_count is not None
                    or document.inspected_pages
                    or document.review_basis != "native_office_content"
                )
            )
            or not document.native_heading
            or not document.native_material_label
            or document.kind not in {"administered", "sample", "mock", "reference"}
            or document.review_basis
            not in {
                "native_text",
                "listing_and_native_structure",
                "native_text_and_visual_solutions",
                "native_office_content",
            }
            or re.fullmatch(r"(?:[1-9][0-9]*)?", document.session) is None
        ):
            raise ValueError(f"Invalid iPAS native document: {document.id}")
        date.fromisoformat(document.reviewed_at)
        ids.add(document.id)
        codes = {s.programme_code for s in document.sources}
        if document.is_paper:
            if (
                len(codes) != 1
                or document.payload_format != "pdf"
                or document.grade not in GRADE_LABELS
                or not document.subjects
                or document.file_type not in {"question", "question_answer"}
                or (document.kind == "administered") != (document.date.basis == "exam_year")
                or (
                    document.kind != "administered"
                    and document.date.basis not in {"undated", "edition_year"}
                )
                or (document.kind != "administered" and bool(document.session))
                or any(route not in ROUTE_LABELS for route in document.routes)
                or len(set(document.routes)) != len(document.routes)
            ):
                raise ValueError(f"Invalid iPAS native paper context: {document.id}")
            if document.routes and (codes != {"AIAP"} or document.grade != "intermediate"):
                raise ValueError(
                    "iPAS qualification routes require native AIAP intermediate evidence"
                )
            heading = "".join(unicodedata.normalize("NFKC", document.native_heading).split())
            if document.review_basis == "listing_and_native_structure":
                heading = "".join(unicodedata.normalize("NFKC", document.label).split())
                if codes != {"PMAE"} or not document.notes:
                    raise ValueError("Headerless iPAS paper requires its scoped native review")
            programme = programme_for_code(next(iter(codes)))
            if programme.label not in heading or GRADE_LABELS[document.grade] not in heading:
                raise ValueError("iPAS native programme or grade contradicts its reviewed heading")
            if document.kind == "administered":
                year = re.search(r"(\d{3})年", heading)
                sitting = re.search(r"第([一二三四1-9])次", heading)
                session = sitting.group(1) if sitting else ""
                session = {"一": "1", "二": "2", "三": "3", "四": "4"}.get(session, session)
                if (
                    year is None
                    or int(year.group(1)) + 1911 != document.date.year_ad
                    or session != document.session
                ):
                    raise ValueError("iPAS native event year or sitting contradicts its heading")
            elif not re.search(r"樣題|示範|模擬", document.native_material_label):
                raise ValueError("iPAS sample or mock requires its native material label")
        elif (
            document.grade
            or document.session
            or document.subjects
            or document.routes
            or document.file_type != "source_asset"
        ):
            raise ValueError("iPAS reference documents cannot claim paper identities")
        for source in document.sources:
            programme_for_code(source.programme_code)
            validate_source_url(source.url)
            if Path(urlsplit(source.url).path).suffix.lower() != "." + document.payload_format:
                raise ValueError("iPAS source extension contradicts its reviewed native format")
            anchor = (source.programme_code, source.url, document.checksum)
            if anchor in anchors:
                raise ValueError("Duplicate iPAS native source anchor")
            anchors.add(anchor)
            document.material(source.programme_code, source.url)
        documents.append(document)
    return tuple(documents)


@lru_cache(maxsize=1)
def native_documents() -> tuple[NativeDocument, ...]:
    return read_native_documents(native_documents_path(Path(__file__).resolve().parents[3]))


def reviewed_payload(programme_code: str, url: str, checksum: str) -> NativeDocument:
    validate_source_url(url)
    reference = SourceReference(programme_code, url)
    matches = [d for d in native_documents() if d.checksum == checksum and reference in d.sources]
    if len(matches) != 1:
        raise ValueError(
            f"iPAS document requires native review: {programme_code} {url} SHA256={checksum}"
        )
    return matches[0]


@lru_cache(maxsize=1)
def _paper_contexts() -> dict[tuple[str, int, str, str], PaperContext]:
    contexts = {}
    for document in native_documents():
        if not document.is_paper:
            continue
        code = document.sources[0].programme_code
        for route in document.routes or ("",):
            key = (
                document.event_code(code),
                document.storage_year,
                document.category_code(code, route),
                document.checksum,
            )
            if key in contexts:
                raise ValueError("Ambiguous iPAS native paper projection")
            contexts[key] = PaperContext(document, programme_for_code(code), route)
    return contexts


def native_context(
    source_exam_id: str, year_ad: int, category_code: str, checksum: str
) -> PaperContext | None:
    return _paper_contexts().get((source_exam_id, year_ad, category_code, checksum))


def _source_index(
    documents: tuple[NativeDocument, ...],
) -> dict[tuple[str, str, str], NativeDocument]:
    return {
        (source.programme_code, source.url, document.checksum): document
        for document in documents
        for source in document.sources
    }


def _record_document(
    record: dict[str, Any], index: dict[tuple[str, str, str], NativeDocument]
) -> tuple[str, NativeDocument] | None:
    if record.get("provider_id") != "ipas_cert":
        return None
    if record.get("source_exam_id") == "ipas-cert-downloads":
        # The first retained acquisition used one generic event for every
        # programme. Its native source URL/checksum still anchors the evidence.
        code = record.get("category_code", "").upper()
    else:
        match = re.match(r"ipas-cert-([a-z0-9]+)-", record.get("source_exam_id", ""))
        if match is None:
            return None
        code = match.group(1).upper()
    document = index.get((code, record.get("download_url_source", ""), record.get("checksum", "")))
    return (code, document) if document is not None else None


def reviewed_native_revisions(
    entries: list[dict[str, Any]], *, repo_root: Path
) -> list[dict[str, Any]]:
    """Explain old roles/events without rewriting original source evidence."""
    index = _source_index(read_native_documents(native_documents_path(repo_root)))
    reviewed = []
    for entry in entries:
        context = _record_document(entry["source_record"], index)
        if context is None:
            continue
        code, document = context
        reviewed.append(
            {
                "revision_id": entry["id"],
                "native_document_id": document.id,
                "programme_code": code,
                "material_kind": document.kind,
                "file_type": document.file_type,
                "grade": document.grade if document.is_paper else None,
                "native_event": document.event_code(code) if document.is_paper else None,
                "material_date": {"basis": document.date.basis, "year_ad": document.date.year_ad},
                "original_source_record_unchanged": True,
            }
        )
    return reviewed


def validate_native_documents(repo_root: Path, *, verify_mirror: bool = False) -> dict[str, int]:
    """Reconcile every current projection and earlier source anchor with its fact."""
    from app.models import to_plain_data
    from app.paths import provider_paths
    from app.source_revisions import load_source_revisions
    from app.state import load_provider_state

    documents = read_native_documents(native_documents_path(repo_root))
    index = _source_index(documents)
    provider = provider_paths(repo_root, "ipas_cert")
    pages, catalog, _failures = load_provider_state(provider)
    seen = set()
    projections = set()
    verified_paths = set()

    def check_payload(record: dict[str, Any]) -> tuple[str, NativeDocument]:
        context = _record_document(record, index)
        if context is None:
            raise ValueError("iPAS source reference lacks matching native document evidence")
        code, document = context
        seen.add((code, record["download_url_source"], record["checksum"]))
        key = record.get("storage_key", "")
        parts = PurePosixPath(key).parts
        path = repo_root / "mirror" / key
        if (
            parts[:2] != ("providers", "ipas_cert")
            or ".." in parts
            or "\\" in key
            or path.suffix.lower() != "." + document.payload_format
            or not path.resolve().is_relative_to(provider.mirror_dir.resolve())
        ):
            raise ValueError("iPAS source locator escapes its provider owner")
        if verify_mirror and record["storage_key"] not in verified_paths:
            if (
                not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != document.checksum
            ):
                raise ValueError("iPAS native payload is missing or changed")
            verified_paths.add(record["storage_key"])
        return code, document

    for paper in catalog.papers:
        record = to_plain_data(paper)
        code, document = check_payload(record)
        categories = {document.category_code(code, route) for route in document.routes or ("",)}
        if (
            not document.is_paper
            or paper.source_exam_id != document.event_code(code)
            or paper.year_roc + 1911 != document.storage_year
            or paper.category_code not in categories
            or paper.subject_code != document.subject_code
            or paper.subject_name_raw != document.subject_label
            or paper.file_type != document.file_type
            or paper.canonical_id != programme_for_code(code).canonical_id
            or paper.source_material != document.material(code, paper.download_url_source)
        ):
            raise ValueError("iPAS normalized paper contradicts its native context")
        projection = (document.id, paper.category_code)
        if projection in projections:
            raise ValueError("Duplicate iPAS native paper projection")
        projections.add(projection)
    attachments = 0
    attachment_dates_unresolved = 0
    for page in pages:
        for attachment in page.attachments:
            record = to_plain_data(attachment)
            record.update(provider_id=page.provider_id, source_exam_id=page.source_exam_id)
            code, document = check_payload(record)
            token = hashlib.sha256(attachment.download_url_source.encode("utf-8")).hexdigest()[:24]
            if (
                document.is_paper
                or page.source_exam_id != f"ipas-cert-{code.lower()}-resources"
                or page.year_ad != RESOURCE_STORAGE_YEAR
                or attachment.file_type != f"source_asset_{token}"
                or attachment.title != document.label
                or attachment.source_material
                != document.material(code, attachment.download_url_source)
            ):
                raise ValueError("iPAS reference attachment contradicts its reviewed source role")
            attachments += 1
            attachment_dates_unresolved += document.date.basis == "unknown"
    revisions = load_source_revisions(provider)
    for entry in revisions:
        record = dict(entry["source_record"])
        record["storage_key"] = entry["blob_storage_key"] or ""
        check_payload(record)
    if seen != index.keys():
        raise ValueError("iPAS native source anchors lack current or conserved historical coverage")
    return {
        "native_documents": len(documents),
        "source_anchors": len(seen),
        "paper_projections": len(projections),
        "reference_attachments": attachments,
        "reference_attachment_dates_unresolved": attachment_dates_unresolved,
        "retired_source_references": len(revisions),
        "verified_payload_paths": len(verified_paths),
    }
