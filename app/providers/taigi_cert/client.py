from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from urllib.parse import quote, unquote, urljoin, urlparse, urlsplit, urlunsplit

from app.models import ExamAttachment, ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata
from app.providers.http import Http
from app.providers.taigi_cert.evidence import (
    CAPTURE_PARTITION_YEAR,
    EXCLUDED_LEARNING_RESOURCES,
    RESOURCE_URL,
    current_resources,
    normalized_label,
    validate_native_payload,
    validate_resource_url,
)

DOWNLOAD_URL = RESOURCE_URL
USER_AGENT = "Mozilla/5.0 (compatible; taigi-cert-mirror/1.0)"
CANONICAL_CATEGORY = "臺灣台語語言能力認證官方試題範例"
# Compatibility export for the retained acquisition partition, never an exam date.
MATERIALS_YEAR = CAPTURE_PARTITION_YEAR


@dataclass(frozen=True)
class TaigiDownload:
    form_code: str
    label: str
    file_type: str
    url: str


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._in_anchor = False
        self._href = ""
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        self._in_anchor = True
        self._href = dict(attrs).get("href", "") or ""
        self._parts = []

    def handle_data(self, data: str) -> None:
        if self._in_anchor:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._in_anchor:
            return
        label = " ".join(unescape("".join(self._parts)).split())
        self.links.append((label, self._href))
        self._in_anchor = False
        self._href = ""
        self._parts = []


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


def parse_downloads(html: str, *, base_url: str = DOWNLOAD_URL) -> list[TaigiDownload]:
    parser = _AnchorParser()
    parser.feed(html)
    downloads: list[TaigiDownload] = []
    known = {resource.source_url: resource for resource in current_resources()}
    seen: dict[str, str] = {}
    for label, href in parser.links:
        url = _quote_url_for_request(urljoin(base_url, href))
        parsed = urlparse(url)
        if not parsed.path.startswith("/tmt/src/upload/file/"):
            continue
        validate_resource_url(url)
        if url in EXCLUDED_LEARNING_RESOURCES:
            if normalized_label(label) != normalized_label(EXCLUDED_LEARNING_RESOURCES[url]):
                raise ValueError("Taigi excluded learning-resource context changed")
            continue
        resource = known.get(url)
        if resource is None or normalized_label(label) != normalized_label(resource.source_label):
            raise ValueError(f"Taigi resource label/URL requires native review: {url}")
        if url in seen:
            if normalized_label(seen[url]) != normalized_label(label):
                raise ValueError("Conflicting Taigi resource labels")
            continue
        seen[url] = label
        downloads.append(
            TaigiDownload(
                form_code=resource.form_code,
                label=resource.source_label,
                file_type=resource.file_type,
                url=url,
            )
        )
    return downloads


class TaigiCertClient:
    provider_id = "taigi_cert"

    def __init__(self) -> None:
        self.http = Http(self.provider_id, max_attempts=1, user_agent=USER_AGENT)
        self._listing: list[TaigiDownload] | None = None
        self._payloads: dict[str, DownloadedFile] = {}

    def _fetch_text(self, url: str) -> str:
        return self.http.get_text(_quote_url_for_request(url), encoding="utf-8")

    def _downloads(self) -> list[TaigiDownload]:
        if self._listing is None:
            links = parse_downloads(self._fetch_text(DOWNLOAD_URL), base_url=DOWNLOAD_URL)
            if {link.url for link in links} != {r.source_url for r in current_resources()}:
                raise ValueError("Taigi current resource membership requires native review")
            self._listing = links
        return self._listing

    def discover_available_years(self) -> list[int]:
        self._downloads()
        return [MATERIALS_YEAR]

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        if year_ad != MATERIALS_YEAR:
            return []
        forms = {download.form_code for download in self._downloads()}
        return [
            ExamOption(
                code=f"taigi-cert-{form_code}-samples",
                year_ad=year_ad,
                year_roc=year_ad - 1911,
                label=f"臺灣台語語言能力認證 {form_code.upper()}卷範例資源",
            )
            for form_code in ("a", "b", "c")
            if form_code in forms
        ]

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        requested_form = next(
            (
                form
                for form in ("a", "b", "c")
                if exam_code
                in {f"taigi-cert-{form}-samples", f"taigi-cert-{form}-{MATERIALS_YEAR}"}
            ),
            None,
        )
        if requested_form is None or year_ad != MATERIALS_YEAR:
            raise ValueError("Taigi requested resource group lacks a reviewed form/partition")
        downloads = [
            download for download in self._downloads() if download.form_code == requested_form
        ]
        known = {resource.source_url: resource for resource in current_resources()}
        papers = []
        attachments = []
        for download in downloads:
            resource = known[download.url]
            if resource.file_type == "source_archive":
                attachments.append(
                    ExamAttachment(
                        title=resource.source_label,
                        file_type=resource.file_type,
                        download_url_source=resource.source_url,
                        source_material=resource.material,
                    )
                )
                continue
            papers.append(
                ParsedPaper(
                    category_raw=f"{CANONICAL_CATEGORY}_{download.form_code.upper()}卷",
                    category_code=download.form_code,
                    subject_code=resource.subject_code,
                    subject_name_raw=download.label,
                    files={download.file_type: download.url},
                    source_material=resource.material,
                )
            )
        return SourceExamPage(
            source_exam_id=f"taigi-cert-{requested_form}-samples",
            year_ad=year_ad,
            year_roc=year_ad - 1911,
            exam_name_raw=f"臺灣台語語言能力認證 {requested_form.upper()}卷範例資源",
            attachments=attachments,
            papers=papers,
            provider_id=self.provider_id,
        )

    def head(self, url: str) -> ResponseMetadata:
        validate_resource_url(url)
        if not any(r.source_url == url for r in current_resources()):
            raise ValueError("Unreviewed Taigi resource cannot be requested")
        return self.http.head(_quote_url_for_request(url))

    def download_file(self, url: str) -> DownloadedFile:
        validate_resource_url(url)
        resource = next((r for r in current_resources() if r.source_url == url), None)
        if resource is None:
            raise ValueError("Unreviewed Taigi resource cannot be acquired automatically")
        if url not in self._payloads:
            payload = self.http.download(
                _quote_url_for_request(url), content_disposition_name=False
            )
            validate_native_payload(resource, payload.data)
            self._payloads[url] = payload
        return self._payloads[url]
