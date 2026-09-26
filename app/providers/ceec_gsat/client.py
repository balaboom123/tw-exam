from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from app.models import ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata
from app.providers.http import Http

BASE_URL = "https://www.ceec.edu.tw/"
LISTING_URL = "https://www.ceec.edu.tw/xmfile?xsmsid=0J052424829869345634"
USER_AGENT = "Mozilla/5.0 (compatible; ceec-gsat-mirror/1.0)"
_TOTAL_PAGES_RE = re.compile("\u5171\\s*(\\d+)\\s*\u9801")
_ENTRY_HEADER_RE = re.compile(
    "(?P<roc_year>\\d{2,3})-\\d{2}-\\d{2}\\s+(?P<title>\\d{2,3}\\s*\u5b78\u5e74\u5ea6\u5b78\u79d1\u80fd\u529b\u6e2c\u9a57[\uFF0D-].+)"
)
_ENTRY_DATE_RE = re.compile(r"(?P<roc_year>\d{2,3})-\d{2}-\d{2}$")
_ENTRY_TITLE_RE = re.compile(r"(?P<title>(?P<roc_year>\d{2,3})\s*\u5b78\u5e74\u5ea6\u5b78\u79d1\u80fd\u529b\u6e2c\u9a57[\uFF0D-].+)$")
_YEAR_BLOCK_RE = re.compile(
    "\u9078\u64c7\u5e74\u5ea6(?P<body>.*?)(?:\u203b\u672c\u8a66\u984c\u70baPDF|\u767c\u4f48\u65e5\u671f)",
    re.S,
)
_YEAR_RE = re.compile(r"\b(\d{2,3})\b")
_CEEC_CATEGORY_NAME = "\u5b78\u79d1\u80fd\u529b\u6e2c\u9a57"
_PAGINATION_LABELS = {"\u7b2c\u4e00\u9801", "\u4e0a\u4e00\u9801", "\u4e0b\u4e00\u9801", "\u6700\u5f8c\u9801"}
_SUBJECT_SLUGS = {
    "\u570b\u7d9c": "guozong",
    "\u570b\u5beb": "guoxie",
    "\u82f1\u6587": "english",
    "\u6578\u5b78A": "math-a",
    "\u6578\u5b78B": "math-b",
    "\u793e\u6703": "social",
    "\u81ea\u7136": "science",
}


@dataclass(frozen=True)
class CeecDownload:
    label: str
    url: str


@dataclass(frozen=True)
class CeecEntry:
    source_exam_id: str
    year_ad: int
    title: str
    downloads: list[CeecDownload]


@dataclass(frozen=True)
class CeecListingPage:
    total_pages: int
    entries: list[CeecEntry]


class _ListingTokenParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tokens: list[tuple[str, str, str]] = []
        self._anchor_href = ""
        self._anchor_text_parts: list[str] = []
        self._in_anchor = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        self._in_anchor = True
        self._anchor_href = dict(attrs).get("href", "") or ""
        self._anchor_text_parts = []

    def handle_data(self, data: str) -> None:
        text = _normalize_text(data)
        if not text:
            return
        if self._in_anchor:
            self._anchor_text_parts.append(text)
            return
        self.tokens.append(("text", text, ""))

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._in_anchor:
            return
        label = _normalize_text(" ".join(self._anchor_text_parts))
        if label:
            self.tokens.append(("link", label, self._anchor_href))
        self._anchor_href = ""
        self._anchor_text_parts = []
        self._in_anchor = False


def _normalize_text(text: str) -> str:
    return " ".join(unescape(text).split())


def _plain_text_from_html(html: str) -> str:
    return _normalize_text(re.sub(r"<[^>]+>", " ", html))


def _subject_tail(title: str) -> str:
    parts = re.split("[\uFF0D-]", _normalize_text(title), maxsplit=1)
    return parts[1] if len(parts) == 2 else parts[0]


def _slug_from_title(title: str) -> str:
    subject = _subject_tail(title)
    mapped = _SUBJECT_SLUGS.get(subject)
    if mapped:
        return mapped
    ascii_slug = re.sub(r"[^0-9A-Za-z]+", "-", subject).strip("-").lower()
    if ascii_slug:
        return ascii_slug
    return "subject-" + subject.encode("utf-8").hex()[:16]


def _entry_from_title(roc_year: int, title: str) -> CeecEntry:
    normalized_title = _normalize_text(title)
    return CeecEntry(
        source_exam_id=f"gsat-{roc_year}-{_slug_from_title(normalized_title)}",
        year_ad=roc_year + 1911,
        title=normalized_title,
        downloads=[],
    )


def _available_years_from_text(text: str) -> list[int]:
    block = _YEAR_BLOCK_RE.search(text)
    if block is None:
        return []
    years: list[int] = []
    for match in _YEAR_RE.findall(block.group("body")):
        year_roc = int(match)
        if year_roc >= 80:
            years.append(year_roc + 1911)
    return sorted(dict.fromkeys(years), reverse=True)


def parse_listing_page(html: str) -> CeecListingPage:
    normalized_html = unescape(html)
    plain_text = _plain_text_from_html(normalized_html)
    total_pages_match = _TOTAL_PAGES_RE.search(plain_text)
    total_pages = int(total_pages_match.group(1)) if total_pages_match else 1
    parser = _ListingTokenParser()
    parser.feed(normalized_html)
    entries: list[CeecEntry] = []
    current_entry: CeecEntry | None = None
    pending_roc_year: int | None = None
    for token_type, token_text, token_href in parser.tokens:
        if token_type == "text":
            match = _ENTRY_HEADER_RE.match(token_text)
            if match is not None:
                if current_entry is not None and current_entry.downloads:
                    entries.append(current_entry)
                pending_roc_year = None
                current_entry = _entry_from_title(int(match.group("roc_year")), match.group("title"))
                continue
            date_match = _ENTRY_DATE_RE.match(token_text)
            if date_match is not None:
                if current_entry is not None and current_entry.downloads:
                    entries.append(current_entry)
                    current_entry = None
                pending_roc_year = int(date_match.group("roc_year"))
                continue
            title_match = _ENTRY_TITLE_RE.match(token_text)
            if pending_roc_year is not None and title_match is not None:
                current_entry = _entry_from_title(pending_roc_year, title_match.group("title"))
                pending_roc_year = None
                continue
            pending_roc_year = None
            continue
        if current_entry is None:
            pending_roc_year = None
            continue
        if token_text in _PAGINATION_LABELS or token_text.isdigit():
            if current_entry.downloads:
                entries.append(current_entry)
                current_entry = None
            continue
        current_entry.downloads.append(CeecDownload(label=token_text, url=urljoin(BASE_URL, token_href)))
    if current_entry is not None and current_entry.downloads:
        entries.append(current_entry)
    return CeecListingPage(total_pages=total_pages, entries=entries)


class CeecGsatClient:
    provider_id = "ceec_gsat"

    def __init__(self) -> None:
        self._entries_cache: tuple[CeecEntry, ...] | None = None
        self.http = Http(self.provider_id, max_attempts=1, user_agent=USER_AGENT)

    def _fetch_text(self, url: str) -> str:
        return self.http.get_text(url, encoding="utf-8")

    def head(self, url: str) -> ResponseMetadata:
        return self.http.head(url)

    def download_file(self, url: str) -> DownloadedFile:
        return self.http.download(url, content_disposition_name=False)

    def _iter_entries(self) -> list[CeecEntry]:
        if self._entries_cache is not None:
            return list(self._entries_cache)
        first_page_html = self._fetch_text(LISTING_URL)
        first_page = parse_listing_page(first_page_html)
        entries = list(first_page.entries)
        for page_number in range(2, first_page.total_pages + 1):
            page = parse_listing_page(self._fetch_text(f"{LISTING_URL}&page={page_number}"))
            entries.extend(page.entries)

        deduped: list[CeecEntry] = []
        seen: set[tuple[int, str]] = set()
        for entry in entries:
            key = (entry.year_ad, entry.source_exam_id)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(entry)
        self._entries_cache = tuple(deduped)
        return list(self._entries_cache)

    def discover_available_years(self) -> list[int]:
        first_page_html = self._fetch_text(LISTING_URL)
        years = _available_years_from_text(_plain_text_from_html(first_page_html))
        if years:
            return years
        return sorted({entry.year_ad for entry in parse_listing_page(first_page_html).entries}, reverse=True)

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        return [
            ExamOption(code=entry.source_exam_id, year_ad=entry.year_ad, year_roc=entry.year_ad - 1911, label=entry.title)
            for entry in self._iter_entries()
            if entry.year_ad == year_ad
        ]

    def build_discovery_year_url(self, year_ad: int) -> str:
        if any(entry.year_ad == year_ad for entry in self._iter_entries()):
            return LISTING_URL
        raise ValueError(f"Unknown CEEC GSAT discovery year: {year_ad}")

    def build_discovery_exam_url(self, exam_code: str, year_ad: int) -> str:
        if any(entry.source_exam_id == exam_code and entry.year_ad == year_ad for entry in self._iter_entries()):
            return LISTING_URL
        raise ValueError(f"Unknown CEEC GSAT discovery exam: {exam_code} ({year_ad})")

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        entry = next(item for item in self._iter_entries() if item.source_exam_id == exam_code and item.year_ad == year_ad)
        subject_tail = _subject_tail(entry.title)
        subject_slug = _slug_from_title(entry.title)
        papers: list[ParsedPaper] = []
        question_seen = 0
        for index, download in enumerate(entry.downloads, start=1):
            if download.label == "\u8a66\u984c\u5167\u5bb9":
                question_seen += 1
                file_type = "question" if question_seen == 1 else "question_alt"
            elif download.label == "\u7b54\u984c\u5377":
                file_type = "answer_sheet"
            elif "\u8a55\u5206\u539f\u5247" in download.label or "\u53c3\u8003\u7b54\u6848" in download.label:
                file_type = "corrected_answer"
            elif "\u7b54\u6848" in download.label:
                file_type = "answer"
            else:
                file_type = "corrected_answer"
            papers.append(
                ParsedPaper(
                    category_raw=_CEEC_CATEGORY_NAME,
                    category_code=str(year_ad - 1911),
                    subject_code=f"{subject_slug}-{index:02d}",
                    subject_name_raw=f"{subject_tail} {download.label}",
                    files={file_type: download.url},
                )
            )
        return SourceExamPage(
            source_exam_id=entry.source_exam_id,
            year_ad=year_ad,
            year_roc=year_ad - 1911,
            exam_name_raw=entry.title,
            attachments=[],
            papers=papers,
            provider_id=self.provider_id,
        )
