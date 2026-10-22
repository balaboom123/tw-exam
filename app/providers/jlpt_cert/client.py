from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urljoin, urlparse, urlsplit, urlunsplit

from app.models import ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata
from app.providers.http import Http
from app.source_material import SourceDate, SourceMaterial

DOWNLOAD_URL = "https://www.jlpt.jp/e/samples/sampleindex.html"
USER_AGENT = "Mozilla/5.0 (compatible; jlpt-cert-mirror/1.0)"
CANONICAL_CATEGORY = "JLPT Japanese-Language Proficiency Test"
PDF_PARTS = {
    "v": ("vocabulary question", "question"),
    "g": ("grammar question", "question"),
    "r": ("reading question", "question"),
    "l": ("listening question", "question"),
    "sheet": ("sample answer sheet", "answer_sheet"),
    "answer": ("answers", "answer"),
    "script": ("listening script", "listening_transcript"),
}


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
        self.links: list[tuple[str, str, str | None]] = []
        self._in_anchor = False
        self._href = ""
        self._parts: list[str] = []
        self._row_levels: set[str] = set()
        self._in_header = False
        self._header_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row_levels = set()
        elif tag == "th":
            self._in_header = True
            self._header_parts = []
        elif tag == "a":
            self._in_anchor = True
            self._href = dict(attrs).get("href", "") or ""
            self._parts = []
        elif self._in_anchor and tag == "img":
            self._parts.append(dict(attrs).get("alt", "") or "")

    def handle_data(self, data: str) -> None:
        if self._in_anchor:
            self._parts.append(data)
        if self._in_header:
            self._header_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "tr":
            self._row_levels = set()
        if tag == "th" and self._in_header:
            heading = "".join(unescape("".join(self._header_parts)).split()).lower()
            if re.fullmatch(r"n[1-5]", heading):
                self._row_levels.add(heading)
            self._in_header = False
        if tag != "a" or not self._in_anchor:
            return
        label = " ".join(unescape("".join(self._parts)).split())
        row_level = next(iter(self._row_levels)) if len(self._row_levels) == 1 else None
        self.links.append((label, self._href, row_level))
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
    if not markers or len({year for _start, year in markers}) != len(markers):
        raise ValueError("JLPT workbook edition headings are missing or duplicated")
    sections: list[tuple[int, str]] = []
    for index, (start, year_ad) in enumerate(markers):
        end = markers[index + 1][0] if index + 1 < len(markers) else len(html)
        sections.append((year_ad, html[start:end]))
    return sections


def _file_parts(url: str) -> tuple[str, str] | None:
    stem = Path(unquote(urlparse(url).path)).stem.lower()
    match = re.fullmatch(r"(n[1-5])(v|g|r|l|sheet|answer|script|q[1-5])", stem)
    if not match:
        return None
    level, part = match.groups()
    extension = Path(unquote(urlparse(url).path)).suffix.lower()
    if extension != (".pdf" if part in PDF_PARTS else ".mp3"):
        return None
    return level, part


def _label(level_code: str, part_code: str) -> str:
    label = PDF_PARTS[part_code][0] if part_code in PDF_PARTS else part_code.upper()
    return f"{level_code.upper()} {label}"


def _file_type(part_code: str) -> str:
    if part_code not in PDF_PARTS:
        return "listening_audio"
    return PDF_PARTS[part_code][1]


def _validate_resource_url(url: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    path = unquote(parsed.path)
    parts = _file_parts(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.jlpt.jp"
        or not path.startswith("/samples/")
        or ".." in PurePosixPath(path).parts
        or "\\" in path
        or parsed.fragment
        or parts is None
    ):
        raise ValueError(f"JLPT workbook file requires native endpoint/part review: {url}")
    return parts


def _exam_code(year_ad: int) -> str:
    return f"jlpt-cert-practice-{year_ad}"


def parse_downloads(html: str, *, base_url: str = DOWNLOAD_URL) -> list[JlptDownload]:
    downloads: list[JlptDownload] = []
    seen: set[tuple[int, str]] = set()
    part_urls: dict[tuple[int, str, str], str] = {}
    for year_ad, section in _workbook_sections(html):
        parser = _AnchorParser()
        parser.feed(section)
        for _label_text, href, row_level in parser.links:
            url = _quote_url_for_request(urljoin(base_url, href))
            parsed = urlparse(url)
            if not parsed.path.lower().endswith((".pdf", ".mp3")):
                continue
            level_code, part_code = _validate_resource_url(url)
            if row_level != level_code:
                raise ValueError("JLPT workbook table level disagrees with its file")
            part = (year_ad, level_code, part_code)
            if part in part_urls and part_urls[part] != url:
                raise ValueError("JLPT workbook has conflicting URLs for one native part")
            part_urls[part] = url
            if (year_ad, url) in seen:
                continue
            seen.add((year_ad, url))
            downloads.append(
                JlptDownload(
                    year_ad=year_ad,
                    level_code=level_code,
                    part_code=part_code,
                    label=_label(level_code, part_code),
                    file_type=_file_type(part_code),
                    url=url,
                )
            )
    return downloads


class JlptCertClient:
    provider_id = "jlpt_cert"

    def __init__(self) -> None:
        self.http = Http(self.provider_id, max_attempts=1, user_agent=USER_AGENT)
        self._downloads_cache: tuple[JlptDownload, ...] | None = None

    def _fetch_text(self, url: str) -> str:
        return self.http.get_text(_quote_url_for_request(url), encoding="utf-8")

    def _downloads(self) -> list[JlptDownload]:
        if self._downloads_cache is None:
            self._downloads_cache = tuple(
                parse_downloads(self._fetch_text(DOWNLOAD_URL), base_url=DOWNLOAD_URL)
            )
        return list(self._downloads_cache)

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
        if not downloads or exam_code != _exam_code(year_ad):
            raise ValueError("Requested JLPT workbook edition is not in the official listing")
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
            source_material=SourceMaterial(
                kind="practice_collection",
                date=SourceDate("edition_year", year_ad),
                evidence_url=DOWNLOAD_URL,
                evidence_label=f"JLPT Official Practice Workbook (published {year_ad})",
            ),
        )

    def head(self, url: str) -> ResponseMetadata:
        _validate_resource_url(url)
        return self.http.head(_quote_url_for_request(url))

    def download_file(self, url: str) -> DownloadedFile:
        _validate_resource_url(url)
        return self.http.download(_quote_url_for_request(url), content_disposition_name=False)
