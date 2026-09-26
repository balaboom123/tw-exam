from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

from app.models import ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata
from app.providers.http import Http

DOWNLOAD_URL = "https://tocfl.edu.tw/tocfl/index.php/exam/download"
MOCK_TEST_URL = (
    "https://tocfl.edu.tw/tocfl/index.php/exam/test/page/1?pressBtn=%28%E9%A1%8C%E5%BA%AB%29"
)
USER_AGENT = "Mozilla/5.0 (compatible; tocfl-cert-mirror/1.0)"
CANONICAL_CATEGORY = "TOCFL華語文能力測驗官方參考資料"
MATERIALS_YEAR = 2026


@dataclass(frozen=True)
class TocflDownload:
    label: str
    url: str
    year_ad: int
    file_type: str = "question"


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


def _slug(text: str, fallback: str) -> str:
    ascii_slug = re.sub(r"[^0-9A-Za-z]+", "-", text).strip("-").lower()
    if ascii_slug:
        return ascii_slug
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] if text else fallback


def _year_from_text(text: str) -> int | None:
    match = re.search(r"(20\d{2})", text)
    return int(match.group(1)) if match else None


def _file_type_for(label: str) -> str:
    if "音檔" in label:
        return "listening_audio"
    if "答案" in label:
        return "answer"
    if "聽力腳本" in label:
        return "question_alt"
    return "question"


def _display_label(label: str, url: str) -> str:
    cleaned = label.strip().strip("[]")
    file_stem = Path(unquote(urlparse(url).path)).stem.replace("_", " ")
    if label.startswith("[") and cleaned:
        return f"{file_stem} {cleaned}"
    return label or Path(urlparse(url).path).name


def parse_downloads(html: str, *, base_url: str = DOWNLOAD_URL) -> list[TocflDownload]:
    parser = _AnchorParser()
    parser.feed(html)
    downloads: list[TocflDownload] = []
    seen: set[str] = set()
    for label, href in parser.links:
        url = urljoin(base_url, href)
        if not url.lower().endswith((".pdf", ".zip", ".rar", ".xls", ".xlsx")) or url in seen:
            continue
        seen.add(url)
        display_label = _display_label(label, url)
        downloads.append(
            TocflDownload(
                label=display_label,
                url=url,
                year_ad=_year_from_text(url) or _year_from_text(display_label) or MATERIALS_YEAR,
                file_type=_file_type_for(label),
            )
        )
    return downloads


class TocflCertClient:
    provider_id = "tocfl_cert"

    def __init__(self) -> None:
        self.http = Http(self.provider_id, max_attempts=1, user_agent=USER_AGENT)

    def _fetch_text(self, url: str) -> str:
        return self.http.get_text(url, encoding="utf-8")

    def _downloads(self) -> list[TocflDownload]:
        downloads: list[TocflDownload] = []
        seen: set[str] = set()
        for url in (DOWNLOAD_URL, MOCK_TEST_URL):
            for download in parse_downloads(self._fetch_text(url), base_url=url):
                if download.url in seen:
                    continue
                seen.add(download.url)
                downloads.append(download)
        return downloads

    def discover_available_years(self) -> list[int]:
        years = {download.year_ad for download in self._downloads()}
        return sorted(years or {MATERIALS_YEAR}, reverse=True)

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        if not any(download.year_ad == year_ad for download in self._downloads()):
            return []
        return [
            ExamOption(
                code=f"tocfl-cert-{year_ad}",
                year_ad=year_ad,
                year_roc=year_ad - 1911,
                label=f"TOCFL官方參考資料 {year_ad}",
            )
        ]

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        downloads = self._downloads()
        if exam_code != "tocfl-cert-materials":
            downloads = [download for download in downloads if download.year_ad == year_ad]
        papers = [
            ParsedPaper(
                category_raw=CANONICAL_CATEGORY,
                category_code="tocfl-reference",
                subject_code=_slug(download.label, f"download-{index}"),
                subject_name_raw=download.label,
                files={download.file_type: download.url},
            )
            for index, download in enumerate(downloads, start=1)
        ]
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=year_ad - 1911,
            exam_name_raw="TOCFL華語文能力測驗官方參考資料",
            attachments=[],
            papers=papers,
            provider_id=self.provider_id,
        )

    def head(self, url: str) -> ResponseMetadata:
        return self.http.head(url)

    def download_file(self, url: str) -> DownloadedFile:
        return self.http.download(url, content_disposition_name=False)
