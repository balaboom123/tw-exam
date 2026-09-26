from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from app.models import ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata

DOWNLOAD_URL = "https://ttg.moe.edu.tw/tmt/view.php?page=resource"
USER_AGENT = "Mozilla/5.0 (compatible; taigi-cert-mirror/1.0)"
CANONICAL_CATEGORY = "臺灣台語語言能力認證官方試題範例"
MATERIALS_YEAR = 2026


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


def _slug(text: str, fallback: str) -> str:
    ascii_slug = re.sub(r"[^0-9A-Za-z]+", "-", text).strip("-").lower()
    if ascii_slug:
        return ascii_slug
    encoded = text.encode("utf-8").hex()[:24]
    return encoded or fallback


def _subject_code(url: str, label: str, fallback: str) -> str:
    stem = Path(unquote(urlparse(url).path)).stem
    label_slug = _slug(label, fallback)
    return f"{stem}-{label_slug}" if stem and stem != label_slug else label_slug


def _form_code(label: str) -> str:
    if label.startswith("A卷"):
        return "a"
    if label.startswith("B卷"):
        return "b"
    if label.startswith("C卷"):
        return "c"
    return "general"


def _exam_code(form_code: str, year_ad: int) -> str:
    return f"taigi-cert-{form_code}-{year_ad}"


def parse_downloads(html: str, *, base_url: str = DOWNLOAD_URL) -> list[TaigiDownload]:
    parser = _AnchorParser()
    parser.feed(html)
    downloads: list[TaigiDownload] = []
    seen: set[str] = set()
    for label, href in parser.links:
        url = _quote_url_for_request(urljoin(base_url, href))
        parsed = urlparse(url)
        lower_path = parsed.path.lower()
        if parsed.netloc != "ttg.moe.edu.tw" or not lower_path.startswith("/tmt/src/upload/file/"):
            continue
        if not lower_path.endswith((".pdf", ".mp3", ".zip")) or url in seen:
            continue
        seen.add(url)
        file_type = "listening_audio" if lower_path.endswith(".mp3") else "question"
        display_label = label or Path(unquote(parsed.path)).name
        downloads.append(
            TaigiDownload(
                form_code=_form_code(display_label),
                label=display_label,
                file_type=file_type,
                url=url,
            )
        )
    return downloads


class TaigiCertClient:
    provider_id = "taigi_cert"

    def _fetch_text(self, url: str) -> str:
        request = Request(_quote_url_for_request(url), headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=60) as response:
            body: bytes = response.read()
            return body.decode("utf-8", "replace")

    def _downloads(self) -> list[TaigiDownload]:
        return parse_downloads(self._fetch_text(DOWNLOAD_URL), base_url=DOWNLOAD_URL)

    def discover_available_years(self) -> list[int]:
        return [MATERIALS_YEAR]

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        if year_ad != MATERIALS_YEAR:
            return []
        forms = {
            download.form_code for download in self._downloads() if download.form_code != "general"
        }
        return [
            ExamOption(
                code=_exam_code(form_code, year_ad),
                year_ad=year_ad,
                year_roc=year_ad - 1911,
                label=f"臺灣台語語言能力認證 {form_code.upper()}卷",
            )
            for form_code in ("a", "b", "c")
            if form_code in forms
        ]

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        requested_form = next(
            (form for form in ("a", "b", "c") if exam_code == _exam_code(form, year_ad)), None
        )
        downloads = [
            download
            for download in self._downloads()
            if requested_form is None or download.form_code == requested_form
        ]
        papers = [
            ParsedPaper(
                category_raw=f"{CANONICAL_CATEGORY}_{download.form_code.upper()}卷",
                category_code=download.form_code,
                subject_code=_subject_code(download.url, download.label, f"download-{index}"),
                subject_name_raw=download.label,
                files={download.file_type: download.url},
            )
            for index, download in enumerate(downloads, start=1)
        ]
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=year_ad - 1911,
            exam_name_raw=CANONICAL_CATEGORY,
            attachments=[],
            papers=papers,
            provider_id=self.provider_id,
        )

    def head(self, url: str) -> ResponseMetadata:
        request = Request(
            _quote_url_for_request(url), headers={"User-Agent": USER_AGENT}, method="HEAD"
        )
        with urlopen(request, timeout=60) as response:
            content_length = response.headers.get("Content-Length")
            return ResponseMetadata(
                url=url,
                status=response.status,
                content_length=int(content_length) if content_length else None,
                content_type=response.headers.get("Content-Type", ""),
                content_disposition=response.headers.get("Content-Disposition", ""),
                cache_control=response.headers.get("Cache-Control", ""),
            )

    def download_file(self, url: str) -> DownloadedFile:
        request = Request(_quote_url_for_request(url), headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=120) as response:
            return DownloadedFile(
                data=response.read(),
                content_type=response.headers.get("Content-Type", "application/octet-stream"),
                file_name=Path(unquote(urlparse(url).path)).name,
            )
