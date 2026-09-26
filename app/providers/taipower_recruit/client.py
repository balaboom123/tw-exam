from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import (
    parse_qs,
    parse_qsl,
    quote,
    unquote,
    urlencode,
    urljoin,
    urlparse,
    urlsplit,
    urlunsplit,
)
from urllib.request import Request, urlopen

from app.models import ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata

BASE_URL = "https://www.taipower.com.tw/"
DOWNLOAD_URL = "https://www.taipower.com.tw/2289/2544/2554/2557/"
LISTING_PATH = "/2289/2544/2554/2557/"
DISCOVERY_PAGE_SIZE = 60
MAX_DISCOVERY_EVENTS = 100
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": BASE_URL,
}
CANONICAL_CATEGORY = "台電新進僱用人員甄試"
_YEAR_RE = re.compile(r"(\d{2,3})\s*年")
_MONTH_RE = re.compile(r"(\d{1,2})\s*月")


@dataclass(frozen=True)
class TaipowerRecruitDownload:
    label: str
    url: str


@dataclass(frozen=True)
class TaipowerRecruitEntry:
    year_roc: int
    year_ad: int
    title: str
    month: int | None
    downloads: list[TaipowerRecruitDownload]


def _normalize_text(text: str) -> str:
    return " ".join(unescape(text).split())


class _HiringPageParser(HTMLParser):
    """Parse the Taipower hiring exam download listing page.

    Structure (as of 2025):
      <ul>
        <li>
          <p class="title">NNN年[MM月]...</p>
          <div class="drawerBox">
            <ul class="fileDownload">
              <li>
                <span class="name">Label</span>
                <ul class="downloadFiles">
                  <li><a download href="/media/...?mediaDL=true">...</a></li>
                </ul>
              </li>
            </ul>
          </div>
        </li>
      </ul>
    """

    def __init__(self) -> None:
        super().__init__()
        self._in_title_p: bool = False
        self._title_parts: list[str] = []
        self._in_name_span: bool = False
        self._name_parts: list[str] = []
        self._current_title: str = ""
        self._current_name: str = ""
        self._current_downloads: list[TaipowerRecruitDownload] = []
        self.entries: list[TaipowerRecruitEntry] = []

    def _flush_entry(self) -> None:
        title = self._current_title
        if not title or not self._current_downloads:
            return
        year_match = _YEAR_RE.search(title)
        if year_match is None:
            return
        year_roc = int(year_match.group(1))
        month: int | None = None
        month_match = _MONTH_RE.search(title[year_match.end() :])
        if month_match is not None:
            month = int(month_match.group(1))
        self.entries.append(
            TaipowerRecruitEntry(
                year_roc=year_roc,
                year_ad=year_roc + 1911,
                title=title,
                month=month,
                downloads=list(self._current_downloads),
            )
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        classes = (attrs_dict.get("class") or "").split()

        if tag == "p" and "title" in classes:
            self._flush_entry()
            self._current_downloads = []
            self._in_title_p = True
            self._title_parts = []
            return

        if tag == "span" and "name" in classes:
            self._in_name_span = True
            self._name_parts = []
            return

        if tag == "a" and "download" in attrs_dict:
            href = attrs_dict.get("href") or ""
            if href:
                label = self._current_name or _normalize_text(
                    unquote(Path(urlparse(href).path).stem)
                )
                url = urljoin(BASE_URL, href)
                self._current_downloads.append(TaipowerRecruitDownload(label=label, url=url))

    def handle_data(self, data: str) -> None:
        if self._in_title_p:
            self._title_parts.append(data)
        elif self._in_name_span:
            self._name_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "p" and self._in_title_p:
            self._current_title = _normalize_text("".join(self._title_parts))
            self._in_title_p = False

        if tag == "span" and self._in_name_span:
            self._current_name = _normalize_text("".join(self._name_parts))
            self._in_name_span = False

    def close(self) -> None:
        super().close()
        self._flush_entry()


_YEAR_TAB_RE = re.compile(
    r'<a\s+href=["\'](/\d+/\d+/\d+/\d+/\?[^"\']+q_attribute=\d+)["\'][^>]*>\s*'
    r"(\d{2,3})\s*年(?:(\d{1,2})\s*月|度)\s*</a>",
    re.IGNORECASE,
)
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)


def parse_hiring_page(html: str) -> list[TaipowerRecruitEntry]:
    """Parse the Taipower hiring exam download listing page."""
    parser = _HiringPageParser()
    parser.feed(html)
    parser.close()
    return parser.entries


def parse_year_tabs(html: str) -> list[tuple[int, int | None, str]]:
    """Extract (year_roc, month, relative_url) from official event tabs."""
    results: list[tuple[int, int | None, str]] = []
    for m in _YEAR_TAB_RE.finditer(html):
        href = unescape(m.group(1))
        year_roc = int(m.group(2))
        month = int(m.group(3)) if m.group(3) is not None else None
        results.append((year_roc, month, href))
    return results


def _full_event_listing_url(relative_url: str) -> str:
    parts = urlsplit(urljoin(BASE_URL, relative_url))
    if parts.netloc != urlsplit(BASE_URL).netloc or parts.path != LISTING_PATH:
        raise ValueError(f"Unexpected Taipower archive event-tab URL: {relative_url}")
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in {"Page", "PageSize"}
    ]
    query[:0] = [("Page", "1"), ("PageSize", str(DISCOVERY_PAGE_SIZE))]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def parse_listing_page_numbers(html: str, listing_url: str) -> set[int]:
    """Return pagination links for the same official listing route."""
    expected = urlsplit(listing_url)
    pages: set[int] = set()
    for raw_href in _HREF_RE.findall(html):
        candidate = urlsplit(urljoin(listing_url, unescape(raw_href)))
        if candidate.netloc != expected.netloc or candidate.path != expected.path:
            continue
        for raw_page in parse_qs(candidate.query).get("Page", []):
            try:
                pages.add(int(raw_page))
            except ValueError:
                continue
    return pages


def _exam_code(entry: TaipowerRecruitEntry) -> str:
    """Build the canonical exam code for an entry."""
    if entry.month is not None:
        return f"taipower-recruit-{entry.year_roc}-{entry.month}"
    return f"taipower-recruit-{entry.year_roc}"


def _exam_label(entry: TaipowerRecruitEntry) -> str:
    if entry.month is not None:
        return f"{entry.year_roc}年{entry.month}月台電新進僱用人員甄試"
    return f"{entry.year_roc}年度台電新進僱用人員甄試"


def _quote_url_for_request(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            quote(unquote(parts.path), safe="/%"),
            quote(unquote(parts.query), safe="=&%"),
            quote(unquote(parts.fragment), safe="%"),
        )
    )


class TaipowerRecruitClient:
    provider_id = "taipower_recruit"

    def __init__(self) -> None:
        self._cached_entries: list[TaipowerRecruitEntry] | None = None
        self._event_urls: dict[tuple[str, int], str] = {}

    def _fetch_text(self, url: str) -> str:
        request = Request(_quote_url_for_request(url), headers=REQUEST_HEADERS)
        with urlopen(request, timeout=60) as response:
            raw: bytes = response.read()
            for encoding in ("utf-8", "big5", "cp950"):
                try:
                    return raw.decode(encoding)
                except (UnicodeDecodeError, LookupError):
                    continue
            return raw.decode("utf-8", "replace")

    def head(self, url: str) -> ResponseMetadata:
        request = Request(_quote_url_for_request(url), headers=REQUEST_HEADERS, method="HEAD")
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
        request = Request(_quote_url_for_request(url), headers=REQUEST_HEADERS)
        with urlopen(request, timeout=120) as response:
            return DownloadedFile(
                data=response.read(),
                content_type=response.headers.get("Content-Type", "application/octet-stream"),
                file_name=Path(unquote(urlparse(url).path)).name,
            )

    def _iter_entries(self) -> list[TaipowerRecruitEntry]:
        if self._cached_entries is not None:
            return self._cached_entries
        main_html = self._fetch_text(DOWNLOAD_URL)
        event_tabs = parse_year_tabs(main_html)
        if not event_tabs:
            raise ValueError("Taipower archive exposes no official event tabs")
        if len(event_tabs) > MAX_DISCOVERY_EVENTS:
            raise ValueError(f"Taipower archive exceeds {MAX_DISCOVERY_EVENTS} discovery events")
        event_keys = [(year_roc, month) for year_roc, month, _ in event_tabs]
        if len(set(event_keys)) != len(event_keys):
            raise ValueError("Taipower archive exposes duplicate event tabs")

        entries: list[TaipowerRecruitEntry] = []
        seen_download_urls: set[str] = set()
        for year_roc, month, rel_url in event_tabs:
            page_url = _full_event_listing_url(rel_url)
            page_html = self._fetch_text(page_url)
            page_entries = parse_hiring_page(page_html)
            if not page_entries:
                raise ValueError(
                    f"Taipower archive event {year_roc}/{month} contains no paper entries"
                )
            wrong_events = {
                (entry.year_roc, entry.month)
                for entry in page_entries
                if (entry.year_roc, entry.month) != (year_roc, month)
            }
            if wrong_events:
                labels = sorted(
                    f"{year}/{event_month or '-'}" for year, event_month in wrong_events
                )
                raise ValueError(
                    f"Taipower archive event {year_roc}/{month} contains "
                    f"cross-event entries: {labels}"
                )
            remaining_pages = {
                page for page in parse_listing_page_numbers(page_html, page_url) if page > 1
            }
            if remaining_pages:
                raise ValueError(
                    f"Taipower archive event {year_roc}/{month} still paginates at "
                    f"PageSize={DISCOVERY_PAGE_SIZE}: {sorted(remaining_pages)}"
                )
            for entry in page_entries:
                for download in entry.downloads:
                    if download.url in seen_download_urls:
                        raise ValueError(f"Taipower archive repeats download URL: {download.url}")
                    seen_download_urls.add(download.url)
            entries.extend(page_entries)
            code = _exam_code(page_entries[0])
            self._event_urls[(code, year_roc + 1911)] = page_url
        self._cached_entries = entries
        return entries

    def build_discovery_year_url(self, year_ad: int) -> str:
        self._iter_entries()
        if not any(event_year == year_ad for _, event_year in self._event_urls):
            raise ValueError(f"Unknown Taipower recruitment discovery year: {year_ad}")
        return DOWNLOAD_URL

    def build_discovery_exam_url(self, exam_code: str, year_ad: int) -> str:
        self._iter_entries()
        try:
            return self._event_urls[(exam_code, year_ad)]
        except KeyError as exc:
            raise ValueError(
                f"Unknown Taipower recruitment discovery exam: {exam_code} ({year_ad})"
            ) from exc

    def discover_available_years(self) -> list[int]:
        return sorted({entry.year_ad for entry in self._iter_entries()}, reverse=True)

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        seen: set[str] = set()
        result: list[ExamOption] = []
        for entry in self._iter_entries():
            if entry.year_ad != year_ad:
                continue
            code = _exam_code(entry)
            if code in seen:
                continue
            seen.add(code)
            result.append(
                ExamOption(
                    code=code,
                    year_ad=entry.year_ad,
                    year_roc=entry.year_roc,
                    label=_exam_label(entry),
                )
            )
        return result

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        matching = [
            item
            for item in self._iter_entries()
            if _exam_code(item) == exam_code and item.year_ad == year_ad
        ]
        if not matching:
            raise ValueError(f"No entries found for {exam_code} year {year_ad}")
        first = matching[0]
        papers: list[ParsedPaper] = []
        for file_index, download in enumerate(
            (dl for entry in matching for dl in entry.downloads), start=1
        ):
            if "答案" in download.label or "解答" in download.label:
                file_type = "answer"
            else:
                file_type = "question"
            papers.append(
                ParsedPaper(
                    category_raw=CANONICAL_CATEGORY,
                    category_code=str(first.year_roc),
                    subject_code=f"hiring-{file_index:02d}",
                    subject_name_raw=download.label,
                    files={file_type: download.url},
                )
            )
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=first.year_roc,
            exam_name_raw=_exam_label(first),
            attachments=[],
            papers=papers,
            provider_id=self.provider_id,
        )
