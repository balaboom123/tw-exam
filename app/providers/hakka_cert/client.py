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

DOWNLOAD_URL = "https://elearning.hakka.gov.tw/hakka/download-files"
USER_AGENT = "Mozilla/5.0 (compatible; hakka-cert-mirror/1.0)"
CANONICAL_CATEGORY = "客語能力認證官方教材及試題"
MATERIALS_YEAR = 2026
LEVEL_CATEGORIES = (
    ("basic-elementary", "基礎級暨初級", f"{DOWNLOAD_URL}?c=2"),
    ("intermediate-high-intermediate", "中級暨中高級", f"{DOWNLOAD_URL}?c=3"),
    ("advanced", "高級", f"{DOWNLOAD_URL}?c=5"),
)
SUPPORTED_DOWNLOAD_SUFFIXES = (
    ".pdf",
    ".zip",
    ".rar",
    ".mp3",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ods",
)
EXAM_ASSET_LABEL_TOKENS = ("題庫", "試題", "答案")
AUDIO_SUFFIXES = (".zip", ".rar", ".mp3")


@dataclass(frozen=True)
class HakkaDownload:
    level_code: str
    category_code: str
    label: str
    file_type: str
    url: str
    year_ad: int


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


def _dialect_code(label: str) -> str:
    for token, code in (
        ("四縣", "sixian"),
        ("海陸", "hailu"),
        ("大埔", "dapu"),
        ("饒平", "raoping"),
        ("詔安", "zhaoan"),
    ):
        if token in label:
            return code
    return "general"


def _year_from_label(label: str) -> int:
    match = re.search(r"(\d{3})\s*年度", label)
    return int(match.group(1)) + 1911 if match else MATERIALS_YEAR


def _file_type_for_download(path_lower: str, label: str) -> str:
    if "答案" in label:
        return "answer"
    if path_lower.endswith(AUDIO_SUFFIXES) or any(
        token in label for token in ("音檔", "聽力", "聽測")
    ):
        return "listening_audio"
    return "question"


def _is_exam_asset(label: str) -> bool:
    return any(token in label for token in EXAM_ASSET_LABEL_TOKENS)


def _exam_code(level_code: str, year_ad: int) -> str:
    return f"hakka-cert-{level_code}-{year_ad}"


def parse_page_urls(html: str, *, base_url: str = DOWNLOAD_URL) -> list[str]:
    parser = _AnchorParser()
    parser.feed(html)
    urls: list[str] = []
    for _label, href in parser.links:
        url = _quote_url_for_request(urljoin(base_url, href))
        if "page=" in url and url.startswith(DOWNLOAD_URL) and url not in urls:
            urls.append(url)
    return urls


def parse_downloads(
    html: str, *, base_url: str = DOWNLOAD_URL, level_code: str = "materials"
) -> list[HakkaDownload]:
    parser = _AnchorParser()
    parser.feed(html)
    downloads: list[HakkaDownload] = []
    seen: set[str] = set()
    for label, href in parser.links:
        url = _quote_url_for_request(urljoin(base_url, href))
        parsed = urlparse(url)
        if parsed.netloc != "elearning.hakka.gov.tw" or not parsed.path.startswith(
            "/hakka/files/downloads/"
        ):
            continue
        path_lower = parsed.path.lower()
        if not path_lower.endswith(SUPPORTED_DOWNLOAD_SUFFIXES) or url in seen:
            continue
        display_label = label or Path(unquote(parsed.path)).name
        if not _is_exam_asset(display_label):
            continue
        seen.add(url)
        file_type = _file_type_for_download(path_lower, display_label)
        downloads.append(
            HakkaDownload(
                level_code=level_code,
                category_code=_dialect_code(display_label),
                label=display_label,
                file_type=file_type,
                url=url,
                year_ad=_year_from_label(display_label),
            )
        )
    return downloads


class HakkaCertClient:
    provider_id = "hakka_cert"

    def __init__(self) -> None:
        self._downloads_cache: tuple[HakkaDownload, ...] | None = None

    def _fetch_text(self, url: str) -> str:
        request = Request(_quote_url_for_request(url), headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=60) as response:
            body: bytes = response.read()
            return body.decode("utf-8", "replace")

    def _downloads(self) -> list[HakkaDownload]:
        if self._downloads_cache is not None:
            return list(self._downloads_cache)

        downloads: list[HakkaDownload] = []
        seen_download_urls: set[str] = set()
        for level_code, _level_name, start_url in LEVEL_CATEGORIES:
            seen_pages: set[str] = set()
            pending = [start_url]
            while pending:
                page_url = pending.pop(0)
                if page_url in seen_pages:
                    continue
                seen_pages.add(page_url)
                html = self._fetch_text(page_url)
                for download in parse_downloads(html, base_url=page_url, level_code=level_code):
                    if download.url in seen_download_urls:
                        continue
                    seen_download_urls.add(download.url)
                    downloads.append(download)
                pending.extend(
                    url
                    for url in parse_page_urls(html, base_url=page_url)
                    if url not in seen_pages and url not in pending
                )
        self._downloads_cache = tuple(downloads)
        return list(self._downloads_cache)

    def discover_available_years(self) -> list[int]:
        years = {download.year_ad for download in self._downloads()}
        return sorted(years or {MATERIALS_YEAR}, reverse=True)

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        downloads = self._downloads()
        level_names = {code: name for code, name, _url in LEVEL_CATEGORIES}
        level_codes = {download.level_code for download in downloads if download.year_ad == year_ad}
        return [
            ExamOption(
                code=_exam_code(level_code, year_ad),
                year_ad=year_ad,
                year_roc=year_ad - 1911,
                label=f"客語能力認證 {level_names.get(level_code, level_code)}",
            )
            for level_code, _name, _url in LEVEL_CATEGORIES
            if level_code in level_codes
        ]

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        level_names = {code: name for code, name, _url in LEVEL_CATEGORIES}
        requested_level = next(
            (
                code
                for code, _name, _url in LEVEL_CATEGORIES
                if exam_code == _exam_code(code, year_ad)
            ),
            None,
        )
        downloads = [
            download
            for download in self._downloads()
            if download.year_ad == year_ad
            and (requested_level is None or download.level_code == requested_level)
        ]
        papers = [
            ParsedPaper(
                category_raw=(
                    f"{CANONICAL_CATEGORY}_"
                    f"{level_names.get(download.level_code, download.level_code)}"
                    f"_{download.category_code}"
                ),
                category_code=download.category_code,
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
            exam_name_raw=f"{CANONICAL_CATEGORY} {level_names[requested_level]}"
            if requested_level
            else CANONICAL_CATEGORY,
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
