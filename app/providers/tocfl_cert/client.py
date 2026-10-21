from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from app.models import ExamAttachment, ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata
from app.providers.http import Http
from app.providers.tocfl_cert.evidence import (
    BANK_URL,
    CAPTURE_PARTITION_YEAR,
    DOWNLOAD_URL,
    NativeResource,
    current_resources,
    native_catalog,
    normalized_label,
    validate_native_payload,
    validate_resource_url,
)

MOCK_TEST_URL = BANK_URL
USER_AGENT = "Mozilla/5.0 (compatible; tocfl-cert-mirror/1.0)"
CANONICAL_CATEGORY = "TOCFL華語文能力測驗官方參考資料"
MATERIALS_YEAR = CAPTURE_PARTITION_YEAR


@dataclass(frozen=True)
class TocflDownload:
    resource: NativeResource

    @property
    def label(self) -> str:
        return self.resource.display_label

    @property
    def url(self) -> str:
        return self.resource.source_url

    @property
    def file_type(self) -> str:
        return self.resource.file_type


class _ContextParser(HTMLParser):
    """Keep each download with its native band, skill and unit heading."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str, str, str, str]] = []
        self._divs: list[tuple[str, list[str]]] = []
        self._anchor: tuple[str, list[str]] | None = None
        self.band = self.skill = self.unit = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "div":
            css = values.get("class") or ""
            self._divs.append((css, []))
            for marker, band in (("NoB", "novice"), ("BandA", "a"), ("BandB", "b"), ("BandC", "c")):
                if marker in css:
                    if self.band != band:
                        self.skill = self.unit = ""
                    self.band = band
                    break
        if tag == "a":
            self._anchor = (values.get("href") or "", [])

    def handle_data(self, data: str) -> None:
        if self._divs:
            self._divs[-1][1].append(data)
        if self._anchor is not None:
            self._anchor[1].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self._divs:
            css, parts = self._divs.pop()
            text = " ".join(unescape("".join(parts)).split())
            if css.startswith(("section-title-", "section-title2-")):
                self.skill, self.unit = text, ""
            elif css.startswith("subsection-title-"):
                self.unit = text
        if tag == "a" and self._anchor is not None:
            href, parts = self._anchor
            self.links.append(
                (" ".join(unescape("".join(parts)).split()), href, self.band, self.skill, self.unit)
            )
            self._anchor = None


def _request_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            quote(unquote(parts.path), safe="/"),
            parts.query,
            parts.fragment,
        )
    )


def parse_downloads(html: str, *, base_url: str = DOWNLOAD_URL) -> list[TocflDownload]:
    if base_url not in {DOWNLOAD_URL, MOCK_TEST_URL}:
        raise ValueError("Unreviewed TOCFL listing endpoint")
    parser = _ContextParser()
    parser.feed(html)
    known = {r.source_url: r for r in current_resources()}
    downloads = []
    seen: set[str] = set()
    for label, href, band, skill, unit in parser.links:
        url = _request_url(urljoin(base_url, href))
        parts = urlsplit(url)
        path = unquote(parts.path)
        if "/tocfl/assets/files/" not in path and not path.lower().endswith(
            (".pdf", ".zip", ".rar", ".xls", ".xlsx")
        ):
            continue
        validate_resource_url(url)
        resource = known.get(url)
        if resource is None or normalized_label(label) != normalized_label(resource.source_label):
            raise ValueError(f"TOCFL download label/URL requires native review: {url}")
        expected_listing = DOWNLOAD_URL if resource.collection == "reference" else MOCK_TEST_URL
        if base_url != expected_listing:
            raise ValueError("TOCFL reference and question-bank source contexts are mixed")
        if resource.collection == "question_bank" and (
            band != resource.band_code
            or normalized_label(skill) != normalized_label(resource.skill or "")
            or normalized_label(unit) != normalized_label(resource.unit_label or "")
        ):
            raise ValueError("TOCFL band/skill/unit heading changed")
        if resource.collection == "bank_instructions" and band != resource.band_code:
            raise ValueError("TOCFL instruction band changed")
        if url in seen:
            raise ValueError("Duplicate TOCFL native download anchor")
        seen.add(url)
        downloads.append(TocflDownload(resource))
    return downloads


class TocflCertClient:
    provider_id = "tocfl_cert"

    def __init__(self) -> None:
        self.http = Http(self.provider_id, max_attempts=1, user_agent=USER_AGENT)
        self._listing: list[TocflDownload] | None = None
        self._payloads: dict[str, DownloadedFile] = {}

    def _fetch_text(self, url: str) -> str:
        return self.http.get_text(url, encoding="utf-8")

    def _downloads(self) -> list[TocflDownload]:
        if self._listing is None:
            downloads = [
                download
                for url in (DOWNLOAD_URL, MOCK_TEST_URL)
                for download in parse_downloads(self._fetch_text(url), base_url=url)
            ]
            if {d.url for d in downloads} != {r.source_url for r in current_resources()}:
                raise ValueError(
                    "TOCFL resource membership changed; review before replacing retained state"
                )
            self._listing = downloads
        return self._listing

    def discover_available_years(self) -> list[int]:
        self._downloads()
        return [MATERIALS_YEAR]

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        if year_ad != MATERIALS_YEAR:
            return []
        resources = [d.resource for d in self._downloads()]
        labels = {
            **{
                f"tocfl-cert-{b.code}-question-bank": f"華語文能力測驗 {b.source_label}題庫"
                for b in native_catalog().bands
            },
            "tocfl-cert-reference": "華語文能力測驗參考詞表",
            "cccc-cert-reference": "兒童華語文能力測驗參考詞表",
        }
        return [
            ExamOption(code=code, year_ad=year_ad, year_roc=year_ad - 1911, label=label)
            for code, label in labels.items()
            if any(r.source_exam_id == code for r in resources)
        ]

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        if year_ad != MATERIALS_YEAR or exam_code not in {
            resource.source_exam_id for resource in current_resources()
        }:
            raise ValueError("TOCFL resource group/partition lacks reviewed context")
        options = {e.code: e for e in self.discover_exams(year_ad)}
        if exam_code not in options or year_ad != MATERIALS_YEAR:
            raise ValueError("TOCFL resource group/partition lacks reviewed context")
        resources = [
            d.resource for d in self._downloads() if d.resource.source_exam_id == exam_code
        ]
        papers = []
        attachments = []
        for resource in resources:
            if resource.collection != "question_bank":
                attachments.append(
                    ExamAttachment(
                        title=resource.display_label,
                        file_type=resource.file_type,
                        download_url_source=resource.source_url,
                        source_material=resource.material,
                    )
                )
                continue
            band = next(b for b in native_catalog().bands if b.code == resource.band_code)
            for script in resource.script_variants:
                script_label = next(s.label for s in native_catalog().scripts if s.code == script)
                papers.append(
                    ParsedPaper(
                        category_raw=f"{CANONICAL_CATEGORY}_{band.source_label}_{script_label}",
                        category_code=resource.category_code(script),
                        subject_code=resource.subject_code,
                        subject_name_raw=resource.display_label,
                        files={resource.file_type: resource.source_url},
                        source_material=resource.material,
                    )
                )
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=year_ad - 1911,
            exam_name_raw=options[exam_code].label,
            attachments=attachments,
            papers=papers,
            provider_id=self.provider_id,
        )

    def _resource(self, url: str) -> NativeResource:
        validate_resource_url(url)
        resource = next((r for r in current_resources() if r.source_url == url), None)
        if resource is None:
            raise ValueError("Unreviewed TOCFL resource cannot be requested")
        return resource

    def head(self, url: str) -> ResponseMetadata:
        self._resource(url)
        return self.http.head(_request_url(url))

    def download_file(self, url: str) -> DownloadedFile:
        resource = self._resource(url)
        if url not in self._payloads:
            payload = self.http.download(_request_url(url), content_disposition_name=False)
            validate_native_payload(resource, payload.data)
            self._payloads[url] = payload
        return self._payloads[url]
