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

DOWNLOAD_URL = "https://www.jlpt.jp/e/samples/sampleindex.html"
USER_AGENT = "Mozilla/5.0 (compatible; jlpt-cert-mirror/1.0)"
CANONICAL_CATEGORY = "JLPT Japanese-Language Proficiency Test"


@dataclass(frozen=True)
class JlptDownload:
    year_ad: int
    level_code: str
    part_code: str
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
        if tag == "a":
            self._in_anchor = True
            self._href = dict(attrs).get("href", "") or ""
            self._parts = []
        elif self._in_anchor and tag == "img":
            self._parts.append(dict(attrs).get("alt", "") or "")

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


def _workbook_sections(html: str) -> list[tuple[int, str]]:
    markers = [
        (match.start(), int(match.group(1))) for match in re.finditer(r"book(20\d{2})\.gif", html)
    ]
    if not markers:
        return [(0, html)]
    sections: list[tuple[int, str]] = []
    for index, (start, year_ad) in enumerate(markers):
        end = markers[index + 1][0] if index + 1 < len(markers) else len(html)
        sections.append((year_ad, html[start:end]))
    return sections


def _file_parts(url: str) -> tuple[str, str] | None:
    stem = Path(unquote(urlparse(url).path)).stem.lower()
    match = re.match(r"(n[1-5])(.+)", stem)
    if not match:
        return None
    return match.group(1), match.group(2)


def _label(level_code: str, part_code: str) -> str:
    labels = {
        "v": "vocabulary question",
        "g": "grammar question",
        "r": "reading question",
        "l": "listening question",
        "sheet": "sample answer sheet",
        "answer": "answers",
        "script": "listening script",
    }
    return f"{level_code.upper()} {labels.get(part_code, part_code.upper())}"


def _file_type(part_code: str, url: str) -> str:
    if url.lower().endswith(".mp3"):
        return "listening_audio"
    if part_code == "answer":
        return "answer"
    if part_code == "sheet":
        return "answer_sheet"
    if part_code == "script":
        return "question_alt"
    return "question"


def _exam_code(year_ad: int) -> str:
    return f"jlpt-cert-practice-{year_ad}"


def parse_downloads(html: str, *, base_url: str = DOWNLOAD_URL) -> list[JlptDownload]:
    downloads: list[JlptDownload] = []
    seen: set[str] = set()
    for year_ad, section in _workbook_sections(html):
        parser = _AnchorParser()
        parser.feed(section)
        for _label_text, href in parser.links:
            url = _quote_url_for_request(urljoin(base_url, href))
            parsed = urlparse(url)
            if parsed.netloc != "www.jlpt.jp" or not parsed.path.startswith("/samples/"):
                continue
            if not parsed.path.lower().endswith((".pdf", ".mp3")) or url in seen:
                continue
            parts = _file_parts(url)
            if parts is None:
                continue
            seen.add(url)
            level_code, part_code = parts
            downloads.append(
                JlptDownload(
                    year_ad=year_ad,
                    level_code=level_code,
                    part_code=part_code,
                    label=_label(level_code, part_code),
                    file_type=_file_type(part_code, url),
                    url=url,
                )
            )
    return downloads


class JlptCertClient:
    provider_id = "jlpt_cert"

    def _fetch_text(self, url: str) -> str:
        request = Request(_quote_url_for_request(url), headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=60) as response:
            body: bytes = response.read()
            return body.decode("utf-8", "replace")

    def _downloads(self) -> list[JlptDownload]:
        return parse_downloads(self._fetch_text(DOWNLOAD_URL), base_url=DOWNLOAD_URL)

    def discover_available_years(self) -> list[int]:
        return sorted({download.year_ad for download in self._downloads()}, reverse=True)

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        if not any(download.year_ad == year_ad for download in self._downloads()):
            return []
        return [
            ExamOption(
                code=_exam_code(year_ad),
                year_ad=year_ad,
                year_roc=year_ad - 1911,
                label=f"JLPT Official Practice Workbook {year_ad}",
            )
        ]

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        downloads = [download for download in self._downloads() if download.year_ad == year_ad]
        papers = [
            ParsedPaper(
                category_raw=f"{CANONICAL_CATEGORY}_{download.level_code.upper()}",
                category_code=download.level_code,
                subject_code=f"{download.level_code}-{download.part_code}",
                subject_name_raw=download.label,
                files={download.file_type: download.url},
            )
            for download in downloads
        ]
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=year_ad - 1911,
            exam_name_raw=f"{CANONICAL_CATEGORY} Official Practice Workbook {year_ad}",
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
