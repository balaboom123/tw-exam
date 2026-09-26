from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

from app.models import ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata

BASE_URL = "https://www.tqc.org.tw/TQCNet/"
EXAM_PAPER_URL = urljoin(BASE_URL, "ExamPaper.aspx")
TQC_HOSTS = {"tqc.org.tw", "www.tqc.org.tw"}
USER_AGENT = "Mozilla/5.0 (compatible; tqc-cert-mirror/1.0)"
CANONICAL_CATEGORY = "TQC範例試卷"
MATERIALS_YEAR = 2026


@dataclass(frozen=True)
class TqcExamPaper:
    title: str
    category: str
    published_year: int
    url: str


@dataclass(frozen=True)
class TqcPageRequest:
    event_target: str = ""
    event_argument: str = ""
    url: str = ""


def _normalize_text(text: str) -> str:
    return " ".join(unescape(text).split())


class _TokenParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tokens: list[tuple[str, str, str]] = []
        self._in_anchor = False
        self._href = ""
        self._anchor_parts: list[str] = []
        self.hidden_fields: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "input":
            attr_map = dict(attrs)
            if (attr_map.get("type") or "").lower() == "hidden" and attr_map.get("name"):
                self.hidden_fields[attr_map["name"] or ""] = attr_map.get("value") or ""
            return
        if tag != "a":
            return
        self._in_anchor = True
        self._href = dict(attrs).get("href", "") or ""
        self._anchor_parts = []

    def handle_data(self, data: str) -> None:
        text = _normalize_text(data)
        if not text:
            return
        if self._in_anchor:
            self._anchor_parts.append(text)
        else:
            self.tokens.append(("text", text, ""))

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._in_anchor:
            return
        self.tokens.append(("link", _normalize_text(" ".join(self._anchor_parts)), self._href))
        self._in_anchor = False
        self._href = ""
        self._anchor_parts = []


def _slug(text: str, fallback: str) -> str:
    ascii_slug = re.sub(r"[^0-9A-Za-z]+", "-", text).strip("-").lower()
    if ascii_slug:
        return ascii_slug
    encoded = text.encode("utf-8").hex()[:24]
    return encoded or fallback


def _is_tqc_url(url: str) -> bool:
    parsed = urlparse(url)
    return (parsed.hostname or "").lower() in TQC_HOSTS


_POSTBACK_RE = re.compile(r"__doPostBack\(['\"]([^'\"]+)['\"],\s*['\"]([^'\"]*)['\"]\)")


def parse_page_requests(html: str) -> list[TqcPageRequest]:
    parser = _TokenParser()
    parser.feed(html)
    requests: list[TqcPageRequest] = []
    seen: set[tuple[str, str, str]] = set()
    for token_type, _, token_href in parser.tokens:
        if token_type != "link":
            continue
        match = _POSTBACK_RE.search(token_href)
        if match:
            request = TqcPageRequest(event_target=match.group(1), event_argument=match.group(2))
        elif (
            token_href
            and not token_href.lower().endswith(".pdf")
            and "javascript:" not in token_href.lower()
        ):
            page_url = urljoin(EXAM_PAPER_URL, token_href)
            parsed_page_url = urlparse(page_url)
            if not _is_tqc_url(page_url) or not parsed_page_url.path.lower().endswith(
                "/tqcnet/exampaper.aspx"
            ):
                continue
            request = TqcPageRequest(url=page_url)
        else:
            continue
        key = (request.event_target, request.event_argument, request.url)
        if key not in seen:
            requests.append(request)
            seen.add(key)
    return requests


def parse_exam_papers(html: str) -> list[TqcExamPaper]:
    parser = _TokenParser()
    parser.feed(html)
    entries: list[TqcExamPaper] = []
    text_window: list[str] = []
    for token_type, token_text, token_href in parser.tokens:
        if token_type == "text":
            text_window.append(token_text)
            text_window = text_window[-4:]
            continue
        paper_url = urljoin(EXAM_PAPER_URL, token_href)
        parsed_paper_url = urlparse(paper_url)
        paper_path = parsed_paper_url.path.lower()
        if (
            not _is_tqc_url(paper_url)
            or "/user/example/" not in paper_path
            or not paper_path.endswith(".pdf")
        ):
            continue
        if len(text_window) < 3:
            continue
        title, category, published = text_window[-3], text_window[-2], text_window[-1]
        year_match = re.match(r"(\d{4})/", published)
        entries.append(
            TqcExamPaper(
                title=title,
                category=category,
                published_year=int(year_match.group(1)) if year_match else 0,
                url=paper_url,
            )
        )
    return entries


class TqcCertClient:
    provider_id = "tqc_cert"

    def __init__(self) -> None:
        self._cached_entries: list[TqcExamPaper] | None = None

    def _fetch_text(self, url: str, form: dict[str, str] | None = None) -> str:
        data = urlencode(form).encode("utf-8") if form is not None else None
        headers = {"User-Agent": USER_AGENT}
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = Request(url, data=data, headers=headers)
        with urlopen(request, timeout=60) as response:
            raw: bytes = response.read()
        for encoding in ("utf-8", "big5", "cp950"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", "replace")

    def _fetch_page_request(self, first_html: str, page_request: TqcPageRequest) -> str:
        if page_request.url:
            return self._fetch_text(page_request.url)
        parser = _TokenParser()
        parser.feed(first_html)
        form = dict(parser.hidden_fields)
        form["__EVENTTARGET"] = page_request.event_target
        form["__EVENTARGUMENT"] = page_request.event_argument
        return self._fetch_text(EXAM_PAPER_URL, form=form)

    def _entries(self) -> list[TqcExamPaper]:
        if self._cached_entries is None:
            first_html = self._fetch_text(EXAM_PAPER_URL)
            pages = [first_html]
            pages.extend(
                self._fetch_page_request(first_html, request)
                for request in parse_page_requests(first_html)
            )
            entries_by_url: dict[str, TqcExamPaper] = {}
            for html in pages:
                for entry in parse_exam_papers(html):
                    entries_by_url.setdefault(entry.url, entry)
            self._cached_entries = list(entries_by_url.values())
        return self._cached_entries

    def _entry_year(self, entry: TqcExamPaper) -> int:
        return entry.published_year or MATERIALS_YEAR

    def discover_available_years(self) -> list[int]:
        years = {self._entry_year(entry) for entry in self._entries()}
        return sorted(years, reverse=True)

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        if year_ad not in self.discover_available_years():
            return []
        return [
            ExamOption(
                code=f"tqc-cert-samples-{year_ad}",
                year_ad=year_ad,
                year_roc=year_ad - 1911,
                label=f"{year_ad} TQC範例試卷",
            )
        ]

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        entries = [entry for entry in self._entries() if self._entry_year(entry) == year_ad]
        papers = [
            ParsedPaper(
                category_raw=f"{CANONICAL_CATEGORY}_{entry.category}",
                category_code=_slug(entry.category, f"category-{index}"),
                subject_code=_slug(entry.title, f"sample-{index}"),
                subject_name_raw=entry.title,
                files={"question": entry.url},
            )
            for index, entry in enumerate(entries, start=1)
        ]
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=year_ad - 1911,
            exam_name_raw="TQC官方範例試卷",
            attachments=[],
            papers=papers,
            provider_id=self.provider_id,
        )

    def head(self, url: str) -> ResponseMetadata:
        request = Request(url, headers={"User-Agent": USER_AGENT}, method="HEAD")
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
        request = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=120) as response:
            return DownloadedFile(
                data=response.read(),
                content_type=response.headers.get("Content-Type", "application/octet-stream"),
                file_name=Path(unquote(urlparse(url).path)).name,
            )
