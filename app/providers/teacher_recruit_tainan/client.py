from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from app.models import ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata

BASE_URL = "https://qualify.tn.edu.tw/trexamps/"
LISTING_URL = BASE_URL
USER_AGENT = "Mozilla/5.0 (compatible; teacher-recruit-tainan-mirror/1.0)"
CANONICAL_CATEGORY = "臺南市國小教師甄試"
SUBJECT_CODE = "elementary-prek-special-ed"
SUBJECT_NAME = "國小教師暨學前特教師聯合甄選"


@dataclass(frozen=True)
class TainanAnnouncement:
    title: str
    url: str


@dataclass(frozen=True)
class TainanDownload:
    label: str
    url: str
    file_type: str


def _normalize_text(text: str) -> str:
    return " ".join(unescape(text).split())


def _request_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            quote(parts.path),
            quote(parts.query, safe="=&%"),
            parts.fragment,
        )
    )


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
        self.links.append((self._href, _normalize_text("".join(self._parts))))
        self._in_anchor = False
        self._href = ""
        self._parts = []


def _links(html: str) -> list[tuple[str, str]]:
    parser = _AnchorParser()
    parser.feed(html)
    return parser.links


def parse_available_years(html: str) -> list[int]:
    import re

    years = {int(match) + 1911 for match in re.findall(r"(\d{3})\s*學年度", html)}
    return sorted(years, reverse=True)


def parse_announcement_links(html: str, *, page_url: str = LISTING_URL) -> list[TainanAnnouncement]:
    announcements: list[TainanAnnouncement] = []
    for href, title in _links(html):
        if "view.aspx" not in href.lower():
            continue
        if "試題" not in title and "答案" not in title:
            continue
        announcements.append(TainanAnnouncement(title=title, url=urljoin(page_url, href)))
    return announcements


def _file_type(label: str) -> str:
    if "參考答案" in label:
        return "answer"
    if "答案" in label:
        return "corrected_answer"
    return "question"


def parse_downloads(html: str, *, page_url: str) -> list[TainanDownload]:
    downloads: list[TainanDownload] = []
    for href, label in _links(html):
        url = urljoin(page_url, href)
        parsed = urlparse(url)
        if "/upload/" not in parsed.path.lower() or not parsed.path.lower().endswith(".zip"):
            continue
        if "試題" not in label and "答案" not in label:
            continue
        downloads.append(TainanDownload(label=label, url=url, file_type=_file_type(label)))
    return downloads


class TainanTeacherRecruitClient:
    provider_id = "teacher_recruit_tainan"

    def __init__(self) -> None:
        self._listing_html: str | None = None

    def _fetch_text(self, url: str) -> str:
        request = Request(_request_url(url), headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=60) as response:
            raw: bytes = response.read()
        for encoding in ("utf-8-sig", "utf-8", "big5", "cp950"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", "replace")

    def _listing(self) -> str:
        if self._listing_html is None:
            self._listing_html = self._fetch_text(LISTING_URL)
        return self._listing_html

    def discover_available_years(self) -> list[int]:
        return parse_available_years(self._listing())

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        if year_ad not in self.discover_available_years():
            return []
        year_roc = year_ad - 1911
        return [
            ExamOption(
                code=f"teacher-recruit-tainan-{year_roc}",
                year_ad=year_ad,
                year_roc=year_roc,
                label=f"{year_roc}學年度{CANONICAL_CATEGORY}",
            )
        ]

    def build_discovery_year_url(self, year_ad: int) -> str:
        return LISTING_URL

    def build_discovery_exam_url(self, exam_code: str, year_ad: int) -> str:
        expected_code = f"teacher-recruit-tainan-{year_ad - 1911}"
        if exam_code != expected_code:
            raise ValueError(f"Unexpected Tainan discovery exam code for {year_ad}: {exam_code}")
        return LISTING_URL

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        year_roc = year_ad - 1911
        files: dict[str, str] = {}
        for announcement in parse_announcement_links(self._listing()):
            for download in parse_downloads(
                self._fetch_text(announcement.url), page_url=announcement.url
            ):
                files.setdefault(download.file_type, download.url)
        papers = (
            [
                ParsedPaper(
                    category_raw=CANONICAL_CATEGORY,
                    category_code=str(year_roc),
                    subject_code=SUBJECT_CODE,
                    subject_name_raw=SUBJECT_NAME,
                    files=files,
                )
            ]
            if files
            else []
        )
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=year_roc,
            exam_name_raw=f"{year_roc}學年度{CANONICAL_CATEGORY}",
            attachments=[],
            papers=papers,
            provider_id=self.provider_id,
        )

    def head(self, url: str) -> ResponseMetadata:
        request = Request(_request_url(url), headers={"User-Agent": USER_AGENT}, method="HEAD")
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
        request = Request(_request_url(url), headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=120) as response:
            return DownloadedFile(
                data=response.read(),
                content_type=response.headers.get("Content-Type", "application/octet-stream"),
                file_name=Path(unquote(urlparse(url).path)).name,
            )
