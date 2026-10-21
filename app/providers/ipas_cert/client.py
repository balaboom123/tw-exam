from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urlparse, urlsplit, urlunsplit

from app.models import ExamAttachment, ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata
from app.providers.http import Http
from app.providers.ipas_cert.evidence import (
    NativeDocument,
    reviewed_payload,
    validate_source_url,
    validated_payload_format,
)
from app.providers.ipas_cert.identity import (
    GRADE_LABELS,
    PROGRAMMES,
    RESOURCE_STORAGE_YEAR,
    ROUTE_LABELS,
    Programme,
)
from app.source_material import material_label

USER_AGENT = "Mozilla/5.0 (compatible; ipas-cert-mirror/1.0)"
CURRENT_BASE_URL = "https://ipd.nat.gov.tw/ipas/"
IPAS_HOSTS = ("www.ipas.org.tw", "ipd.nat.gov.tw")
IPAS_SECTIONS = ("news", "exam-info", "learning-resources", "downloads")


@dataclass(frozen=True)
class IpasDownload:
    cert_code: str
    label: str
    url: str


def _quote_url_for_request(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            quote(unquote(parts.path), safe="/:%"),
            quote(unquote(parts.query), safe="=&:%"),
            quote(unquote(parts.fragment), safe=""),
        )
    )


def _normalize_ipas_url(url: str) -> str:
    ref = url.strip().replace("&amp;", "&")
    if ref.startswith("http"):
        parts = urlsplit(ref)
        if parts.scheme != "https" or parts.netloc not in IPAS_HOSTS:
            raise ValueError(f"Unexpected iPAS document source owner: {ref}")
        path = parts.path
    else:
        path = ref if ref.startswith("/") else f"/{ref}"
        parts = urlsplit(CURRENT_BASE_URL)
    if path.startswith("/api/"):
        path = f"/ipas{path}"
    elif not path.startswith("/ipas/"):
        path = f"/ipas{path}"
    return _quote_url_for_request(urlunsplit(("https", "ipd.nat.gov.tw", path, parts.query, "")))


def parse_certification_codes(html: str) -> list[str]:
    codes = set(re.findall(r"(?:/ipas)?/certification/([A-Z0-9]+)/news", html))
    return sorted(codes)


_DOCUMENT_REF_RE = re.compile(
    r"(?<![A-Za-z0-9._:/-])(?:https?://[^/\"'<>\\\s]+)?"
    r"(?:/ipas)?/api/proxy/uploads/[^\"'<>\\]+",
    re.IGNORECASE,
)
SITE_MEDIA_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"})


class _ProgrammeLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.names: dict[str, str] = {}
        self._code = ""
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href") or ""
        parsed = urlsplit(href)
        match = re.fullmatch(r"(?:/ipas)?/certification/([A-Z0-9]+)/news", parsed.path)
        self._code = ""
        self._text = []
        if match is None:
            return
        if parsed.netloc and (parsed.scheme != "https" or parsed.netloc not in IPAS_HOSTS):
            raise ValueError("iPAS programme link has an unexpected source owner")
        self._code = match.group(1)

    def handle_data(self, data: str) -> None:
        if self._code:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._code:
            label = "".join("".join(self._text).split())
            previous = self.names.setdefault(self._code, label)
            if previous != label:
                raise ValueError("Conflicting iPAS programme labels")
            self._code = ""


def parse_programme_listing(html: str) -> dict[str, str]:
    parser = _ProgrammeLinks()
    parser.feed(html)
    return parser.names


def parse_document_downloads(html: str, *, cert_code: str = "") -> list[IpasDownload]:
    downloads: list[IpasDownload] = []
    seen: set[str] = set()
    for ref in _DOCUMENT_REF_RE.findall(html):
        if Path(urlsplit(ref).path).suffix.lower() in SITE_MEDIA_EXTENSIONS:
            continue
        url = _normalize_ipas_url(ref)
        if url in seen:
            continue
        seen.add(url)
        label = Path(unquote(urlparse(url).path)).name
        downloads.append(IpasDownload(cert_code=cert_code, label=label, url=url))
    return downloads


def parse_pdf_downloads(html: str, *, cert_code: str = "") -> list[IpasDownload]:
    """Compatibility view for existing PDF-only source inspection consumers."""
    return [
        row
        for row in parse_document_downloads(html, cert_code=cert_code)
        if Path(urlsplit(row.url).path).suffix.lower() == ".pdf"
    ]


class IpasCertClient:
    provider_id = "ipas_cert"

    def __init__(self) -> None:
        self.http = Http(self.provider_id, max_attempts=2, user_agent=USER_AGENT)
        self._payloads: dict[str, DownloadedFile] = {}
        self._pages: dict[str, SourceExamPage] | None = None

    HOME_URL = CURRENT_BASE_URL

    def _fetch_text(self, url: str) -> str:
        return self.http.get_text(_quote_url_for_request(url), encoding="utf-8")

    def _downloads(self, cert_code: str) -> list[IpasDownload]:
        downloads: list[IpasDownload] = []
        seen: set[str] = set()
        for section in IPAS_SECTIONS:
            html = self._fetch_text(f"{CURRENT_BASE_URL}certification/{cert_code}/{section}")
            for download in parse_document_downloads(html, cert_code=cert_code):
                if download.url in seen:
                    continue
                seen.add(download.url)
                downloads.append(download)
        return downloads

    def _paper_page(self, programme: Programme, document: NativeDocument) -> SourceExamPage:
        code = document.event_code(programme.code)
        label = f"iPAS {programme.label}｜{GRADE_LABELS[document.grade]}"
        if document.kind == "administered":
            sitting = f"第{document.session}次" if document.session else ""
            label = f"{document.storage_year - 1911}年{sitting}{label}"
        else:
            label += f"｜{material_label(document.kind)}"
        return SourceExamPage(
            code,
            document.storage_year,
            document.storage_year - 1911,
            label,
            [],
            [],
            self.provider_id,
        )

    def _load_snapshot(self) -> dict[str, SourceExamPage]:
        if self._pages is not None:
            return self._pages
        listing = parse_programme_listing(self._fetch_text(self.HOME_URL))
        advertised = set(listing)
        expected = {programme.code for programme in PROGRAMMES}
        if advertised != expected:
            raise ValueError(
                "iPAS native programme membership changed: "
                f"missing={sorted(expected - advertised)}, "
                f"unreviewed={sorted(advertised - expected)}"
            )
        for programme in PROGRAMMES:
            if listing[programme.code] != "".join(programme.label.split()):
                raise ValueError(f"iPAS native programme label changed: {programme.code}")
        pages: dict[str, SourceExamPage] = {}
        for programme in PROGRAMMES:
            downloads = self._downloads(programme.code)
            if not downloads:
                raise ValueError(
                    f"iPAS programme has no recognized source documents: {programme.code}"
                )
            resource_code = f"ipas-cert-{programme.code.lower()}-resources"
            resources = SourceExamPage(
                resource_code,
                RESOURCE_STORAGE_YEAR,
                RESOURCE_STORAGE_YEAR - 1911,
                f"iPAS {programme.label}｜來源參考文件",
                [],
                [],
                self.provider_id,
            )
            for download in downloads:
                payload = self.download_file(download.url)
                checksum = hashlib.sha256(payload.data).hexdigest()
                document = reviewed_payload(programme.code, download.url, checksum)
                material = document.material(programme.code, download.url)
                if not document.is_paper:
                    token = hashlib.sha256(download.url.encode("utf-8")).hexdigest()[:24]
                    resources.attachments.append(
                        ExamAttachment(
                            document.label,
                            f"source_asset_{token}",
                            download.url,
                            source_material=material,
                        )
                    )
                    continue
                event = document.event_code(programme.code)
                page = pages.setdefault(event, self._paper_page(programme, document))
                for route in document.routes or ("",):
                    category = f"iPAS {programme.label}｜{GRADE_LABELS[document.grade]}"
                    if route:
                        category += f"｜{ROUTE_LABELS[route]}"
                    page.papers.append(
                        ParsedPaper(
                            category_raw=category,
                            category_code=document.category_code(programme.code, route),
                            subject_code=document.subject_code,
                            subject_name_raw=document.subject_label,
                            files={document.file_type: download.url},
                            source_material=material,
                        )
                    )
            if resources.attachments:
                pages[resource_code] = resources
        # Publish the client snapshot only after every listing and payload agrees.
        self._pages = pages
        return pages

    def discover_available_years(self) -> list[int]:
        return sorted({page.year_ad for page in self._load_snapshot().values()})

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        return [
            ExamOption(page.source_exam_id, year_ad, year_ad - 1911, page.exam_name_raw)
            for page in self._load_snapshot().values()
            if page.year_ad == year_ad
        ]

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        page = self._load_snapshot().get(exam_code)
        if page is None or page.year_ad != year_ad:
            raise ValueError("iPAS event/year is absent from the reviewed native snapshot")
        return page

    def head(self, url: str) -> ResponseMetadata:
        return self.http.head(_quote_url_for_request(url))

    def download_file(self, url: str) -> DownloadedFile:
        validate_source_url(url)
        if url not in self._payloads:
            payload = self.http.download(
                _quote_url_for_request(url), content_disposition_name=False
            )
            validated_payload_format(url, payload.data)
            self._payloads[url] = payload
        return self._payloads[url]
