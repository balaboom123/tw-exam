from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

from app.models import ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata
from app.providers.http import Http

BASE_URL = "https://www.ceec.edu.tw/"
LISTING_URL = "https://www.ceec.edu.tw/xmfile?xsmsid=0J052427633128416650"
USER_AGENT = "Mozilla/5.0 (compatible; ceec-ast-mirror/1.0)"
AST_NOTICE_URL = "https://www.ceec.edu.tw/xmdoc?xsmsid=0I363338985390931117"
_TOTAL_PAGES_RE = re.compile("共\\s*(\\d+)\\s*頁")
_ENTRY_HEADER_RE = re.compile(
    r"(?P<roc_year>\d{3})-\d{2}-\d{2}\s+(?P<title>\d{3}\s*學年度分科測驗[－-].+)"
)
_ENTRY_DATE_RE = re.compile(r"(?P<roc_year>\d{3})-\d{2}-\d{2}$")
_ENTRY_TITLE_RE = re.compile(r"(?P<title>(?P<roc_year>\d{3})\s*學年度分科測驗[－-].+)$")
_YEAR_BLOCK_RE = re.compile("選擇年度(?P<body>.*?)(?:※本試題為PDF|發佈日期)", re.S)
_YEAR_RE = re.compile(r"\b(\d{2,3})\b")
_CEEC_CATEGORY_NAME = "分科測驗"
_PAGINATION_LABELS = {"第一頁", "上一頁", "下一頁", "最後頁"}
_AST_NOTICE_TITLE_PATTERNS = (
    ("notice", re.compile(r"(?P<roc_year>\d{3})\s*學年度分科測驗試題\s*/\s*答題卷\s*/\s*參考答案")),
    (
        "confirmed",
        re.compile(r"(?P<roc_year>\d{3})\s*學年度分科測驗各考科選擇[（(]填[）)]題答案確定"),
    ),
    ("guidelines", re.compile(r"(?P<roc_year>\d{3})\s*學年度分科測驗各考科非選擇題評分原則")),
)
_SUBJECT_SLUGS = {
    "國文": "chinese",
    "英文": "english",
    "數學甲": "math-a",
    "數學乙": "math-b",
    "數學A": "math-a",
    "數學B": "math-b",
    "歷史": "history",
    "地理": "geography",
    "公民與社會": "civics-society",
    "物理": "physics",
    "化學": "chemistry",
    "生物": "biology",
}


@dataclass(frozen=True)
class CeecAstDownload:
    label: str
    url: str


@dataclass(frozen=True)
class CeecAstEntry:
    source_exam_id: str
    year_ad: int
    title: str
    downloads: list[CeecAstDownload]


@dataclass(frozen=True)
class CeecAstListingPage:
    total_pages: int
    entries: list[CeecAstEntry]


@dataclass(frozen=True)
class CeecAstNotice:
    source_exam_id: str
    year_ad: int
    title: str
    url: str


class CeecAstSourceQualityError(RuntimeError):
    """The official AST notice did not expose the expected subject-file table."""


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
    parts = re.split("[－-]", _normalize_text(title), maxsplit=1)
    return parts[1] if len(parts) == 2 else parts[0]


def _slug_from_subject(subject: str) -> str:
    subject = _normalize_text(subject).replace(" ", "").replace("Ａ", "A").replace("Ｂ", "B")
    mapped = _SUBJECT_SLUGS.get(subject)
    if mapped:
        return mapped
    ascii_slug = re.sub(r"[^0-9A-Za-z]+", "-", subject).strip("-").lower()
    if ascii_slug:
        return ascii_slug
    return "subject-" + subject.encode("utf-8").hex()[:16]


def _slug_from_title(title: str) -> str:
    return _slug_from_subject(_subject_tail(title))


def _entry_from_title(roc_year: int, title: str) -> CeecAstEntry:
    normalized_title = _normalize_text(title)
    return CeecAstEntry(
        source_exam_id=f"ceec-ast-{roc_year}-{_slug_from_title(normalized_title)}",
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


def parse_listing_page(html: str) -> CeecAstListingPage:
    normalized_html = unescape(html)
    plain_text = _plain_text_from_html(normalized_html)
    total_pages_match = _TOTAL_PAGES_RE.search(plain_text)
    total_pages = int(total_pages_match.group(1)) if total_pages_match else 1
    parser = _ListingTokenParser()
    parser.feed(normalized_html)
    entries: list[CeecAstEntry] = []
    current_entry: CeecAstEntry | None = None
    pending_roc_year: int | None = None
    for token_type, token_text, token_href in parser.tokens:
        if token_type == "text":
            match = _ENTRY_HEADER_RE.match(token_text)
            if match is not None:
                if current_entry is not None and current_entry.downloads:
                    entries.append(current_entry)
                pending_roc_year = None
                current_entry = _entry_from_title(
                    int(match.group("roc_year")), match.group("title")
                )
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
        current_entry.downloads.append(
            CeecAstDownload(label=token_text, url=urljoin(BASE_URL, token_href))
        )
    if current_entry is not None and current_entry.downloads:
        entries.append(current_entry)
    return CeecAstListingPage(total_pages=total_pages, entries=entries)


@dataclass
class _NoticeCell:
    text: str
    links: list[CeecAstDownload]


class _NoticeTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[_NoticeCell]] = []
        self._table_depth = 0
        self._in_row = False
        self._in_cell = False
        self._cell_text_parts: list[str] = []
        self._cell_links: list[CeecAstDownload] = []
        self._row: list[_NoticeCell] = []
        self._anchor_href = ""
        self._anchor_text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._table_depth += 1
            return
        if not self._table_depth:
            return
        if tag == "tr":
            self._in_row = True
            self._row = []
        elif tag in {"td", "th"} and self._in_row:
            self._in_cell = True
            self._cell_text_parts = []
            self._cell_links = []
        elif tag == "a" and self._in_cell:
            self._anchor_href = dict(attrs).get("href", "") or ""
            self._anchor_text_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_text_parts.append(data)
        if self._anchor_href:
            self._anchor_text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            self._table_depth = max(self._table_depth - 1, 0)
            return
        if not self._table_depth:
            return
        if tag == "a" and self._anchor_href:
            self._cell_links.append(
                CeecAstDownload(
                    label=_normalize_text(" ".join(self._anchor_text_parts)), url=self._anchor_href
                )
            )
            self._anchor_href = ""
            self._anchor_text_parts = []
        elif tag in {"td", "th"} and self._in_cell:
            self._row.append(
                _NoticeCell(
                    text=_normalize_text(" ".join(self._cell_text_parts)),
                    links=list(self._cell_links),
                )
            )
            self._in_cell = False
            self._cell_text_parts = []
            self._cell_links = []
        elif tag == "tr" and self._in_row:
            if self._row:
                self.rows.append(self._row)
            self._in_row = False
            self._row = []


def parse_notice_listing(html: str) -> list[CeecAstNotice]:
    parser = _ListingTokenParser()
    parser.feed(unescape(html))
    notices: list[CeecAstNotice] = []
    seen: set[tuple[int, str]] = set()
    for token_type, token_text, token_href in parser.tokens:
        if token_type != "link":
            continue
        matched = next(
            (
                (kind, match)
                for kind, pattern in _AST_NOTICE_TITLE_PATTERNS
                if (match := pattern.search(token_text))
            ),
            None,
        )
        if matched is None:
            continue
        kind, match = matched
        roc_year = int(match.group("roc_year"))
        key = (roc_year, kind)
        if key in seen:
            continue
        seen.add(key)
        resolved_url = urljoin(BASE_URL, token_href)
        parsed_url = urlparse(resolved_url)
        query = parse_qs(parsed_url.query)
        if parsed_url.path == "/xmdoc" and query.get("sid") and query.get("xsmsid"):
            parameters = (("sid", query["sid"][0]), ("xsmsid", query["xsmsid"][0]))
            resolved_url = urljoin(
                BASE_URL,
                f"xmdoc/cont?{urlencode(parameters)}",
            )
        notices.append(
            CeecAstNotice(
                source_exam_id=f"ceec-ast-{kind}-{roc_year}",
                year_ad=roc_year + 1911,
                title=_normalize_text(token_text),
                url=resolved_url,
            )
        )
    return sorted(notices, key=lambda notice: notice.year_ad, reverse=True)


def _notice_header_indices(rows: list[list[_NoticeCell]]) -> tuple[int, dict[str, int]] | None:
    for row_index, row in enumerate(rows):
        labels = [cell.text for cell in row]
        columns = {
            "subject": next((index for index, label in enumerate(labels) if "科目" in label), None),
            "question": next(
                (index for index, label in enumerate(labels) if "試題" in label), None
            ),
            "answer_sheet": next(
                (index for index, label in enumerate(labels) if "答題卷" in label), None
            ),
            "answer": next(
                (
                    index
                    for index, label in enumerate(labels)
                    if "參考答案" in label or label == "答案"
                ),
                None,
            ),
        }
        if all(index is not None for index in columns.values()):
            return row_index, {name: index for name, index in columns.items() if index is not None}
    return None


def parse_notice_papers(html: str, *, base_url: str, year_ad: int) -> list[ParsedPaper]:
    parser = _NoticeTableParser()
    parser.feed(unescape(html))
    header = _notice_header_indices(parser.rows)
    if header is None:
        raise CeecAstSourceQualityError(
            "CEEC AST notice is missing the 科目/試題/答題卷/參考答案 table"
        )
    header_row_index, columns = header
    papers: list[ParsedPaper] = []
    for row in parser.rows[header_row_index + 1 :]:
        if len(row) <= max(columns.values()):
            continue
        subject = row[columns["subject"]].text
        if not subject:
            continue
        files: dict[str, str] = {}
        for file_type in ("question", "answer_sheet", "answer"):
            for download in row[columns[file_type]].links:
                if not download.url:
                    continue
                resolved_type = (
                    "question_alt" if file_type == "question" and "question" in files else file_type
                )
                if resolved_type in files:
                    continue
                files[resolved_type] = urljoin(base_url, download.url)
        if files:
            papers.append(
                ParsedPaper(
                    category_raw=_CEEC_CATEGORY_NAME,
                    category_code=str(year_ad - 1911),
                    subject_code=_slug_from_subject(subject),
                    subject_name_raw=subject,
                    files=files,
                )
            )
    if not papers:
        raise CeecAstSourceQualityError("CEEC AST notice contains no subject download rows")
    return papers


def parse_guideline_papers(html: str, *, base_url: str, year_ad: int) -> list[ParsedPaper]:
    parser = _NoticeTableParser()
    parser.feed(unescape(html))
    header = next(
        (
            (row_index, subject_index, guideline_index)
            for row_index, row in enumerate(parser.rows)
            for subject_index, subject_cell in enumerate(row)
            for guideline_index, guideline_cell in enumerate(row)
            if "科目" in subject_cell.text and "評分原則" in guideline_cell.text
        ),
        None,
    )
    if header is None:
        raise CeecAstSourceQualityError("CEEC AST notice is missing the 科目/評分原則 table")
    header_row_index, subject_index, guideline_index = header
    papers: list[ParsedPaper] = []
    for row in parser.rows[header_row_index + 1 :]:
        if len(row) <= max(subject_index, guideline_index):
            continue
        subject = row[subject_index].text
        links = row[guideline_index].links
        if not subject or not links:
            continue
        papers.append(
            ParsedPaper(
                category_raw=_CEEC_CATEGORY_NAME,
                category_code=str(year_ad - 1911),
                subject_code=_slug_from_subject(subject),
                subject_name_raw=subject,
                files={"corrected_answer": urljoin(base_url, links[0].url)},
            )
        )
    if not papers:
        raise CeecAstSourceQualityError(
            "CEEC AST notice contains no scoring-principle download rows"
        )
    return papers


class CeecAstClient:
    provider_id = "ceec_ast"

    def __init__(self) -> None:
        self._entries_cache: tuple[CeecAstEntry, ...] | None = None
        self._notices_cache: tuple[CeecAstNotice, ...] | None = None
        self._http = Http(self.provider_id, user_agent=USER_AGENT)

    def _fetch_text(self, url: str) -> str:
        return self._http.get_text(url, encoding="utf-8")

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

    def _iter_entries(self) -> list[CeecAstEntry]:
        if self._entries_cache is not None:
            return list(self._entries_cache)
        first_page_html = self._fetch_text(LISTING_URL)
        first_page = parse_listing_page(first_page_html)
        entries = list(first_page.entries)
        for page_number in range(2, first_page.total_pages + 1):
            page = parse_listing_page(self._fetch_text(f"{LISTING_URL}&page={page_number}"))
            entries.extend(page.entries)

        deduped: list[CeecAstEntry] = []
        seen: set[tuple[int, str]] = set()
        for entry in entries:
            key = (entry.year_ad, entry.source_exam_id)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(entry)
        self._entries_cache = tuple(deduped)
        return list(self._entries_cache)

    def _iter_notices(self) -> list[CeecAstNotice]:
        if self._notices_cache is None:
            self._notices_cache = tuple(parse_notice_listing(self._fetch_text(AST_NOTICE_URL)))
        return list(self._notices_cache)

    def discover_available_years(self) -> list[int]:
        years = {entry.year_ad for entry in self._iter_entries()}
        years.update(notice.year_ad for notice in self._iter_notices())
        return sorted(years, reverse=True)

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        notices = [notice for notice in self._iter_notices() if notice.year_ad == year_ad]
        if notices:
            return [
                ExamOption(
                    code=notice.source_exam_id,
                    year_ad=notice.year_ad,
                    year_roc=notice.year_ad - 1911,
                    label=notice.title,
                )
                for notice in notices
            ]
        return [
            ExamOption(
                code=entry.source_exam_id,
                year_ad=entry.year_ad,
                year_roc=entry.year_ad - 1911,
                label=entry.title,
            )
            for entry in self._iter_entries()
            if entry.year_ad == year_ad
        ]

    def build_discovery_year_url(self, year_ad: int) -> str:
        if any(notice.year_ad == year_ad for notice in self._iter_notices()):
            return AST_NOTICE_URL
        if any(entry.year_ad == year_ad for entry in self._iter_entries()):
            return LISTING_URL
        raise ValueError(f"Unknown CEEC AST discovery year: {year_ad}")

    def build_discovery_exam_url(self, exam_code: str, year_ad: int) -> str:
        notice = next(
            (
                item
                for item in self._iter_notices()
                if item.source_exam_id == exam_code and item.year_ad == year_ad
            ),
            None,
        )
        if notice is not None:
            return notice.url
        if any(
            item.source_exam_id == exam_code and item.year_ad == year_ad
            for item in self._iter_entries()
        ):
            return LISTING_URL
        raise ValueError(f"Unknown CEEC AST discovery exam: {exam_code} ({year_ad})")

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        notice = next(
            (
                item
                for item in self._iter_notices()
                if item.source_exam_id == exam_code and item.year_ad == year_ad
            ),
            None,
        )
        if notice is not None:
            html = self._fetch_text(notice.url)
            papers: list[ParsedPaper] = (
                parse_guideline_papers(html, base_url=notice.url, year_ad=notice.year_ad)
                if notice.source_exam_id.startswith("ceec-ast-guidelines-")
                else parse_notice_papers(html, base_url=notice.url, year_ad=notice.year_ad)
            )
            return SourceExamPage(
                source_exam_id=notice.source_exam_id,
                year_ad=notice.year_ad,
                year_roc=notice.year_ad - 1911,
                exam_name_raw=notice.title,
                attachments=[],
                papers=papers,
                provider_id=self.provider_id,
            )

        entry = next(
            item
            for item in self._iter_entries()
            if item.source_exam_id == exam_code and item.year_ad == year_ad
        )
        subject_tail = _subject_tail(entry.title)
        subject_slug = _slug_from_title(entry.title)
        papers = []
        question_seen = 0
        for index, download in enumerate(entry.downloads, start=1):
            if download.label == "試題內容":
                question_seen += 1
                file_type = "question" if question_seen == 1 else "question_alt"
            elif download.label == "答題卷":
                file_type = "answer_sheet"
            elif "評分原則" in download.label:
                file_type = "corrected_answer"
            elif "答案" in download.label:
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
