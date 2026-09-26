from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import unquote, urlencode, urljoin, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener

from app.models import ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata

BASE_URL = "https://owinform.wdasec.gov.tw/ExamNet/owInform/"
PAGE_URL = "https://owinform.wdasec.gov.tw/ExamNet/owInform/PastQuestions.aspx"
USER_AGENT = "Mozilla/5.0 (compatible; wdasec-skill-mirror/1.0)"
CANONICAL_CATEGORY = "全國技術士技能檢定"


@dataclass(frozen=True)
class ListingRow:
    year_roc: int
    title: str
    plaid: str
    row_index: int


@dataclass(frozen=True)
class DetailRow:
    trade_code: str
    trade_name: str
    exam_date: str
    level: str
    question_url: str
    practical_url: str


# ---------------------------------------------------------------------------
# HTML parsers
# ---------------------------------------------------------------------------


class _HiddenFieldParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "input":
            return
        attr_dict = dict(attrs)
        if attr_dict.get("type") == "hidden":
            name = attr_dict.get("name") or ""
            if name:
                self.fields[name] = attr_dict.get("value") or ""


class _ListingParser(HTMLParser):
    """Parses the gvData GridView on the category listing page."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[ListingRow] = []
        self._in_gvdata = False
        self._in_row = False
        self._in_header = False
        self._in_cell = False
        self._cell_index = 0
        self._row_index = 0
        self._current_year = ""
        self._current_title = ""
        self._current_plaid = ""
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        if tag == "table" and attr_dict.get("id") == "gvData":
            self._in_gvdata = True
            return
        if not self._in_gvdata:
            return
        if tag == "tr":
            self._in_row = True
            self._cell_index = 0
            self._current_year = ""
            self._current_title = ""
            self._current_plaid = ""
        elif tag == "th":
            self._in_header = True
        elif tag == "td" and self._in_row:
            self._in_cell = True
            self._text_parts = []
        elif tag == "input" and self._in_row:
            name = attr_dict.get("name") or ""
            if name.endswith("$hdfPLAID"):
                self._current_plaid = attr_dict.get("value") or ""

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self._in_gvdata:
            self._in_gvdata = False
            return
        if not self._in_gvdata:
            return
        if tag == "th":
            self._in_header = False
        elif tag == "td" and self._in_cell:
            text = " ".join("".join(self._text_parts).split()).strip()
            if self._cell_index == 0:
                self._current_year = text
            elif self._cell_index == 1:
                self._current_title = text
            self._cell_index += 1
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            if self._current_plaid and self._current_year.isdigit():
                self.rows.append(
                    ListingRow(
                        year_roc=int(self._current_year),
                        title=self._current_title,
                        plaid=self._current_plaid,
                        row_index=self._row_index,
                    )
                )
                self._row_index += 1
            self._in_row = False


class _DetailParser(HTMLParser):
    """Parses the gvFile GridView on the detail page."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[DetailRow] = []
        self._in_gvfile = False
        self._in_row = False
        self._in_header = False
        self._in_cell = False
        self._cell_index = 0
        self._cells: list[str] = []
        self._cell_href = ""
        self._cell_hrefs: list[str] = []
        self._text_parts: list[str] = []
        self._last_trade_code = ""
        self._last_trade_name = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        if tag == "table" and attr_dict.get("id") == "gvFile":
            self._in_gvfile = True
            return
        if not self._in_gvfile:
            return
        if tag == "tr":
            self._in_row = True
            self._cell_index = 0
            self._cells = []
            self._cell_hrefs = []
        elif tag == "th":
            self._in_header = True
        elif tag == "td" and self._in_row:
            self._in_cell = True
            self._text_parts = []
            self._cell_href = ""
        elif tag == "a" and self._in_cell:
            self._cell_href = attr_dict.get("href") or ""

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self._in_gvfile:
            self._in_gvfile = False
            return
        if not self._in_gvfile:
            return
        if tag == "th":
            self._in_header = False
        elif tag == "td" and self._in_cell:
            text = " ".join("".join(self._text_parts).split()).strip()
            self._cells.append(text)
            self._cell_hrefs.append(self._cell_href)
            self._cell_index += 1
            self._in_cell = False
            self._cell_href = ""
        elif tag == "tr" and self._in_row:
            if len(self._cells) >= 6 and not self._in_header:
                trade_code = self._cells[0] or self._last_trade_code
                trade_name = self._cells[1] or self._last_trade_name
                if self._cells[0]:
                    self._last_trade_code = self._cells[0]
                if self._cells[1]:
                    self._last_trade_name = self._cells[1]
                level = self._cells[3]
                q_url = self._cell_hrefs[4] if self._cell_hrefs[4] else ""
                p_url = (
                    self._cell_hrefs[5] if len(self._cell_hrefs) > 5 and self._cell_hrefs[5] else ""
                )
                if level and (q_url or p_url):
                    self.rows.append(
                        DetailRow(
                            trade_code=trade_code,
                            trade_name=trade_name,
                            exam_date=self._cells[2],
                            level=level,
                            question_url=q_url,
                            practical_url=p_url,
                        )
                    )
            self._in_row = False


class _PaginationParser(HTMLParser):
    """Extracts page numbers from a gvData pagination row."""

    def __init__(self) -> None:
        super().__init__()
        self._in_pgr = False
        self.page_count = 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        if tag == "tr" and "pgr" in (attr_dict.get("class") or ""):
            self._in_pgr = True

    def handle_data(self, data: str) -> None:
        if self._in_pgr and data.strip().isdigit():
            page = int(data.strip())
            if page > self.page_count:
                self.page_count = page

    def handle_endtag(self, tag: str) -> None:
        if tag == "tr" and self._in_pgr:
            self._in_pgr = False


# ---------------------------------------------------------------------------
# Public parse functions
# ---------------------------------------------------------------------------


def parse_hidden_fields(html: str) -> dict[str, str]:
    parser = _HiddenFieldParser()
    parser.feed(html)
    return parser.fields


def parse_listing_rows(html: str) -> list[ListingRow]:
    parser = _ListingParser()
    parser.feed(unescape(html))
    return parser.rows


def parse_detail_rows(html: str) -> list[DetailRow]:
    parser = _DetailParser()
    parser.feed(unescape(html))
    return parser.rows


def parse_page_count(html: str) -> int:
    parser = _PaginationParser()
    parser.feed(html)
    return parser.page_count


_LEVEL_KEYS = {"甲級": "class_a", "乙級": "class_b", "丙級": "class_c", "單一級": "single"}


# ---------------------------------------------------------------------------
# ASP.NET session + client
# ---------------------------------------------------------------------------


class WdasecSkillClient:
    provider_id = "wdasec_skill"

    def __init__(self) -> None:
        self._cookie_jar = CookieJar()
        self._opener = build_opener(HTTPCookieProcessor(self._cookie_jar))
        self._hidden_fields: dict[str, str] = {}
        self._listing_rows_cache: tuple[ListingRow, ...] | None = None

    def _get(self, url: str) -> str:
        request = Request(url, headers={"User-Agent": USER_AGENT})
        with self._opener.open(request, timeout=60) as response:
            body: bytes = response.read()
            html = body.decode("utf-8")
        self._hidden_fields = parse_hidden_fields(html)
        return html

    def _post(self, extra_fields: dict[str, str]) -> str:
        payload = dict(self._hidden_fields)
        payload.setdefault("__EVENTTARGET", "")
        payload.setdefault("__EVENTARGUMENT", "")
        payload.update(extra_fields)
        data = urlencode(payload).encode("utf-8")
        request = Request(
            PAGE_URL,
            data=data,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": PAGE_URL,
                "Origin": "https://owinform.wdasec.gov.tw",
            },
        )
        with self._opener.open(request, timeout=60) as response:
            body: bytes = response.read()
            html = body.decode("utf-8")
        self._hidden_fields = parse_hidden_fields(html)
        return html

    def head(self, url: str) -> ResponseMetadata:
        request = Request(url, headers={"User-Agent": USER_AGENT}, method="HEAD")
        with self._opener.open(request, timeout=60) as response:
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
        with self._opener.open(request, timeout=120) as response:
            return DownloadedFile(
                data=response.read(),
                content_type=response.headers.get("Content-Type", "application/octet-stream"),
                file_name=Path(unquote(urlparse(url).path)).name,
            )

    def fetch_category_listing(self, *, category: str = "btnSelectA") -> str:
        self._get(PAGE_URL)
        return self._post({category: "全國技能檢定各梯次試題及答案"})

    def fetch_listing_page(self, page_number: int) -> str:
        return self._post(
            {
                "__EVENTTARGET": "gvData",
                "__EVENTARGUMENT": f"Page${page_number}",
            }
        )

    def fetch_detail(self, row_index: int) -> str:
        return self._post(
            {
                "__EVENTTARGET": "gvData",
                "__EVENTARGUMENT": f"order${row_index}",
            }
        )

    def discover_all_listing_rows(self) -> list[ListingRow]:
        if self._listing_rows_cache is not None:
            return list(self._listing_rows_cache)
        html = self.fetch_category_listing()
        all_rows = parse_listing_rows(html)
        total_pages = parse_page_count(html)
        for page in range(2, total_pages + 1):
            html = self.fetch_listing_page(page)
            all_rows.extend(parse_listing_rows(html))
        self._listing_rows_cache = tuple(all_rows)
        return list(self._listing_rows_cache)

    def discover_available_years(self) -> list[int]:
        rows = self.discover_all_listing_rows()
        return sorted({row.year_roc + 1911 for row in rows}, reverse=True)

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        year_roc = year_ad - 1911
        rows = self.discover_all_listing_rows()
        return [
            ExamOption(
                code=row.plaid,
                year_ad=year_ad,
                year_roc=year_roc,
                label=row.title,
            )
            for row in rows
            if row.year_roc == year_roc
        ]

    def build_discovery_year_url(self, year_ad: int) -> str:
        if any(row.year_roc + 1911 == year_ad for row in self.discover_all_listing_rows()):
            return PAGE_URL
        raise ValueError(f"Unknown WDASEC discovery year: {year_ad}")

    def build_discovery_exam_url(self, exam_code: str, year_ad: int) -> str:
        if any(
            row.plaid == exam_code and row.year_roc + 1911 == year_ad
            for row in self.discover_all_listing_rows()
        ):
            return f"{PAGE_URL}?{urlencode({'yserno': exam_code})}"
        raise ValueError(f"Unknown WDASEC discovery exam: {exam_code} ({year_ad})")

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        target_row = next(
            (
                row
                for row in self.discover_all_listing_rows()
                if row.plaid == exam_code and row.year_roc + 1911 == year_ad
            ),
            None,
        )

        if target_row is None:
            raise ValueError(f"Session not found: {exam_code}")

        detail_html = self._get(self.build_discovery_exam_url(exam_code, year_ad))
        detail_rows = parse_detail_rows(detail_html)

        year_roc = target_row.year_roc
        papers: list[ParsedPaper] = []
        for row in detail_rows:
            level_key = _LEVEL_KEYS.get(row.level, row.level)
            subject_base = f"{row.trade_code}-{level_key}"
            if row.question_url:
                full_url = urljoin(BASE_URL, row.question_url.split("?")[0])
                papers.append(
                    ParsedPaper(
                        category_raw=CANONICAL_CATEGORY,
                        category_code=row.trade_code,
                        subject_code=f"{subject_base}-question",
                        subject_name_raw=f"{row.trade_name} {row.level} 學科",
                        files={"question": full_url},
                    )
                )
            if row.practical_url:
                full_url = urljoin(BASE_URL, row.practical_url.split("?")[0])
                papers.append(
                    ParsedPaper(
                        category_raw=CANONICAL_CATEGORY,
                        category_code=row.trade_code,
                        subject_code=f"{subject_base}-practical",
                        subject_name_raw=f"{row.trade_name} {row.level} 術科",
                        files={"question": full_url},
                    )
                )

        return SourceExamPage(
            provider_id="wdasec_skill",
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=year_roc,
            exam_name_raw=target_row.title,
            attachments=[],
            papers=papers,
        )
