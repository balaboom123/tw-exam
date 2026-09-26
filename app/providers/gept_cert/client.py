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

USER_AGENT = "Mozilla/5.0 (compatible; gept-cert-mirror/1.0)"
CANONICAL_CATEGORY = "GEPT全民英檢官方練習資料"
MATERIALS_YEAR = 2022


@dataclass(frozen=True)
class GeptDownload:
    level_code: str
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


def _year_from_url(url: str) -> int | None:
    match = re.search(r"/(20\d{2})/", url)
    return int(match.group(1)) if match else None


def _exam_code(level_code: str, year_ad: int) -> str:
    return f"gept-cert-{level_code}-{year_ad}"


def parse_intro_downloads(
    html: str, *, base_url: str, level_code: str
) -> tuple[list[GeptDownload], list[str]]:
    parser = _AnchorParser()
    parser.feed(html)
    downloads: list[GeptDownload] = []
    practice_pages: list[str] = []
    for label, href in parser.links:
        if not href:
            continue
        url = _quote_url_for_request(urljoin(base_url, href))
        lower = url.lower()
        if lower.endswith((".pdf", ".zip")):
            downloads.append(
                GeptDownload(
                    level_code=level_code,
                    label=label or Path(urlparse(url).path).name,
                    file_type="question",
                    url=url,
                    year_ad=_year_from_url(url) or MATERIALS_YEAR,
                )
            )
        elif "geptpractice" in lower:
            practice_pages.append(url)
    return downloads, practice_pages


def parse_practice_audio(html: str, *, base_url: str, level_code: str) -> list[GeptDownload]:
    downloads: list[GeptDownload] = []
    seen: set[str] = set()
    for match in re.finditer(r"playAudio\('([^']+\.mp3)'\)", html, re.I):
        url = _quote_url_for_request(urljoin(base_url, match.group(1)))
        if url in seen:
            continue
        seen.add(url)
        label = Path(urlparse(url).path).name
        downloads.append(
            GeptDownload(
                level_code=level_code,
                label=label,
                file_type="listening_audio",
                url=url,
                year_ad=_year_from_url(url) or _year_from_url(base_url) or MATERIALS_YEAR,
            )
        )
    return downloads


class GeptCertClient:
    provider_id = "gept_cert"
    LEVELS = (
        ("elementary", "初級", "https://www.gept.org.tw/Exam_Intro/t01_introduction.asp"),
        ("intermediate", "中級", "https://www.gept.org.tw/Exam_Intro/t02_introduction.asp"),
        ("high-intermediate", "中高級", "https://www.gept.org.tw/Exam_Intro/t03_introduction.asp"),
        ("advanced", "高級", "https://www.gept.org.tw/Exam_Intro/t04_introduction.asp"),
        ("superior", "優級", "https://www.gept.org.tw/Exam_Intro/t05_introduction.asp"),
    )

    def _fetch_text(self, url: str) -> str:
        request = Request(_quote_url_for_request(url), headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=60) as response:
            raw: bytes = response.read()
        for encoding in ("utf-8", "big5", "cp950"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", "replace")

    def _downloads(self) -> list[GeptDownload]:
        all_downloads: list[GeptDownload] = []
        for level_code, _level_name, intro_url in self.LEVELS:
            intro_html = self._fetch_text(intro_url)
            downloads, practice_pages = parse_intro_downloads(
                intro_html, base_url=intro_url, level_code=level_code
            )
            all_downloads.extend(downloads)
            for practice_page in practice_pages:
                all_downloads.extend(
                    parse_practice_audio(
                        self._fetch_text(practice_page),
                        base_url=practice_page,
                        level_code=level_code,
                    )
                )
        return all_downloads

    def discover_available_years(self) -> list[int]:
        years = {download.year_ad for download in self._downloads()}
        return sorted(years or {MATERIALS_YEAR}, reverse=True)

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        downloads = self._downloads()
        level_names = {code: name for code, name, _url in self.LEVELS}
        level_codes = {download.level_code for download in downloads if download.year_ad == year_ad}
        return [
            ExamOption(
                code=_exam_code(level_code, year_ad),
                year_ad=year_ad,
                year_roc=year_ad - 1911,
                label=f"GEPT全民英檢 {level_names.get(level_code, level_code)}",
            )
            for level_code, _name, _url in self.LEVELS
            if level_code in level_codes
        ]

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        level_names = {code: name for code, name, _url in self.LEVELS}
        requested_level = next(
            (code for code, _name, _url in self.LEVELS if exam_code == _exam_code(code, year_ad)),
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
                ),
                category_code=download.level_code,
                subject_code=_slug(download.label, f"download-{index}"),
                subject_name_raw=download.label,
                files={download.file_type: download.url},
            )
            for index, download in enumerate(downloads, start=1)
        ]
        exam_name = (
            f"GEPT全民英檢 {level_names[requested_level]}"
            if requested_level
            else "GEPT全民英檢官方練習資料"
        )
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=year_ad - 1911,
            exam_name_raw=exam_name,
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
