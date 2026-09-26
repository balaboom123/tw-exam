from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import (
    parse_qs,
    unquote,
    urlencode,
    urljoin,
    urlparse,
    urlsplit,
)
from urllib.request import Request, urlopen

from app.models import ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata

BASE_URL = "https://www.taisugar.com.tw/"
LISTING_URL = "https://www.taisugar.com.tw/chinese/News_Index.aspx?p=3&n=10080"
_LISTING_BASE_URL = "https://www.taisugar.com.tw/chinese/"
USER_AGENT = "Mozilla/5.0 (compatible; taisugar-recruit-mirror/1.0)"
CANONICAL_CATEGORY = "台糖新進工員甄試"

# Matches ROC year in news item title: "114年新進工員甄試試題" -> 114
_YEAR_RE = re.compile(r"(?<!\d)(\d{2,3})\s*(?:年|(?=新進工員))")

MAX_PAGES = 50
MAX_DISCOVERY_EVENTS = 50
_PAGER_SELECT_ID = "MainContent_wucNews_index_ddlPager"
_PAGER_SELECT_NAME = "ctl00$MainContent$wucNews_index$ddlPager"
_PAGER_SUBMIT_NAME = "ctl00$MainContent$wucNews_index$btn前往"
_TOTAL_ROWS_RE = re.compile(
    r'id="MainContent_wucNews_index_lbl11"[^>]*>.*?</span>\s*'
    r'<span\s+class="red"[^>]*>\s*(\d+)\s*</span>',
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class TaisugarNewsItem:
    """A news listing item that links to an exam-paper detail page."""

    title: str
    detail_url: str
    year_roc: int


@dataclass(frozen=True)
class TaisugarDownload:
    """A single official PDF or ZIP download from a detail page."""

    label: str
    url: str


@dataclass(frozen=True)
class TaisugarListingMetadata:
    current_page: int
    total_pages: int
    total_rows: int
    form_fields: dict[str, str]


def _normalize_text(text: str) -> str:
    return " ".join(unescape(text).split())


def _normalize_news_title(text: str) -> str:
    title = _normalize_text(text)
    return title.removeprefix("連結 ").strip()


def _is_worker_paper_title(title: str) -> bool:
    return "新進工員" in title and ("甄試試題" in title or "甄試考題" in title)


class _NewsListingParser(HTMLParser):
    """Parse the Taisugar news listing page.

    Expected structure:
      <div class="wucNews_index">
        <ul>
          <li>
            <a href="News_detail.aspx?p=3&n=10080&s=[ID]" title="[Title]">
              <img ...>
              <h3>[Title]</h3>
              <span class="date">[Date]</span>
              [Summary text]
            </a>
          </li>
          ...
        </ul>
      </div>

    Only new-worker items whose title contains '甄試試題' or '甄試考題' are kept.
    """

    def __init__(self) -> None:
        super().__init__()
        self._in_news_list: bool = False
        self._div_depth_news: int = 0
        self._in_anchor: bool = False
        self._current_href: str = ""
        self._current_title: str = ""
        self._in_h3: bool = False
        self._h3_parts: list[str] = []
        self.rows: list[tuple[str, str]] = []
        self.items: list[TaisugarNewsItem] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        classes = (attrs_dict.get("class") or "").split()

        if tag == "div" and {"n_content", "wucNews_index"}.intersection(classes):
            self._in_news_list = True
            self._div_depth_news = 1
            return

        if self._in_news_list and tag == "div":
            self._div_depth_news += 1
            return

        if not self._in_news_list:
            return

        if tag == "a" and not self._in_anchor:
            href = attrs_dict.get("href") or ""
            title = attrs_dict.get("title") or ""
            if "news_detail.aspx" in href.lower():
                self._in_anchor = True
                self._current_href = href
                self._current_title = _normalize_news_title(title)
                self._h3_parts = []
            return

        if self._in_anchor and tag == "h3":
            self._in_h3 = True
            self._h3_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_h3:
            self._h3_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self._in_news_list:
            self._div_depth_news -= 1
            if self._div_depth_news == 0:
                self._in_news_list = False
            return

        if tag == "h3" and self._in_h3:
            self._in_h3 = False
            return

        if tag == "a" and self._in_anchor:
            self._flush_item()
            self._in_anchor = False
            self._current_href = ""
            self._current_title = ""
            self._h3_parts = []
            return

    def _flush_item(self) -> None:
        # Prefer title attribute; fall back to h3 text
        title = self._current_title
        if not title:
            title = _normalize_news_title("".join(self._h3_parts))
        if not title:
            return
        href = self._current_href
        if not href:
            return
        detail_url = urljoin(_LISTING_BASE_URL, href)
        self.rows.append((title, detail_url))
        if not _is_worker_paper_title(title):
            return
        match = _YEAR_RE.search(title)
        if match is None:
            return
        year_roc = int(match.group(1))
        self.items.append(
            TaisugarNewsItem(
                title=title,
                detail_url=detail_url,
                year_roc=year_roc,
            )
        )


class _ListingFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_form = False
        self._in_pager_select = False
        self.form_fields: dict[str, str] = {}
        self.page_values: list[int] = []
        self.selected_page: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "form" and attrs_dict.get("id") == "newsForm":
            self._in_form = True
            return
        if not self._in_form:
            return
        if tag == "input" and (attrs_dict.get("type") or "").lower() == "hidden":
            name = attrs_dict.get("name") or ""
            if name:
                self.form_fields[name] = attrs_dict.get("value") or ""
            return
        if tag == "select" and attrs_dict.get("id") == _PAGER_SELECT_ID:
            self._in_pager_select = True
            return
        if tag == "option" and self._in_pager_select:
            try:
                page = int(attrs_dict.get("value") or "")
            except ValueError:
                return
            self.page_values.append(page)
            if "selected" in attrs_dict:
                self.selected_page = page

    def handle_endtag(self, tag: str) -> None:
        if tag == "select" and self._in_pager_select:
            self._in_pager_select = False
        elif tag == "form" and self._in_form:
            self._in_form = False


class _NewsDetailParser(HTMLParser):
    """Parse a Taisugar news detail page for PDF and ZIP download links.

    Expected structure (may repeat):
      <p>相關檔案：</p>
      <p>
        <a href="../upload/UserFiles/News/[ID]/[filename].[pdf|zip]"
           title="(另存目標下載檔案)(NMb)">
          [Label](.PDF or .ZIP)
        </a>
      </p>

    We collect any <a> tag whose href ends with '.pdf' or '.zip',
    case-insensitively. The client then validates the exact official host and
    detail-specific upload directory before accepting the links.
    """

    def __init__(self) -> None:
        super().__init__()
        self._in_anchor: bool = False
        self._current_href: str = ""
        self._anchor_parts: list[str] = []
        self.downloads: list[TaisugarDownload] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)

        if tag == "a":
            href = attrs_dict.get("href") or ""
            if Path(urlparse(href).path).suffix.lower() in {".pdf", ".zip"}:
                self._in_anchor = True
                self._current_href = href
                self._anchor_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_anchor:
            self._anchor_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_anchor:
            label = _normalize_text("".join(self._anchor_parts))
            url = urljoin(BASE_URL, self._current_href)
            self.downloads.append(TaisugarDownload(label=label, url=url))
            self._in_anchor = False
            self._anchor_parts = []
            self._current_href = ""


def parse_news_listing(html: str) -> list[TaisugarNewsItem]:
    """Parse the Taisugar news listing page and return worker-paper items."""
    parser = _NewsListingParser()
    parser.feed(html)
    return parser.items


def parse_listing_rows(html: str) -> list[tuple[str, str]]:
    parser = _NewsListingParser()
    parser.feed(html)
    return parser.rows


def parse_listing_metadata(html: str) -> TaisugarListingMetadata:
    parser = _ListingFormParser()
    parser.feed(html)
    if not parser.page_values:
        raise ValueError("Taisugar listing exposes no bounded pager")
    expected_pages = list(range(1, max(parser.page_values) + 1))
    if sorted(parser.page_values) != expected_pages:
        raise ValueError("Taisugar listing exposes a malformed pager")
    if "__VIEWSTATE" not in parser.form_fields or "__EVENTVALIDATION" not in parser.form_fields:
        raise ValueError("Taisugar listing is missing ASP.NET pagination state")
    total_match = _TOTAL_ROWS_RE.search(html)
    if total_match is None:
        raise ValueError("Taisugar listing exposes no total row count")
    current_page = parser.selected_page or min(parser.page_values)
    return TaisugarListingMetadata(
        current_page=current_page,
        total_pages=max(parser.page_values),
        total_rows=int(total_match.group(1)),
        form_fields=parser.form_fields,
    )


def parse_news_detail(html: str) -> list[TaisugarDownload]:
    """Parse a Taisugar news detail page and return PDF/ZIP download links."""
    parser = _NewsDetailParser()
    parser.feed(html)
    return parser.downloads


class TaisugarRecruitClient:
    provider_id = "taisugar_recruit"

    def __init__(self) -> None:
        self._cached_items: list[TaisugarNewsItem] | None = None
        self._event_urls: dict[tuple[str, int], str] = {}

    @staticmethod
    def _decode_html(raw: bytes) -> str:
        for encoding in ("utf-8", "big5", "cp950"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", "replace")

    def _fetch_text(self, url: str) -> str:
        request = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=60) as response:
            return self._decode_html(response.read())

    @staticmethod
    def _detail_id(detail_url: str) -> str:
        parts = urlsplit(detail_url)
        query = parse_qs(parts.query)
        if (
            parts.scheme != "https"
            or parts.netloc != "www.taisugar.com.tw"
            or parts.path != "/chinese/News_detail.aspx"
            or parts.fragment
            or set(query) != {"p", "n", "s"}
            or query.get("p") != ["3"]
            or query.get("n") != ["10080"]
            or len(query.get("s", [])) != 1
            or not query["s"][0].isdigit()
        ):
            raise ValueError(f"Unexpected Taisugar paper detail URL: {detail_url}")
        return query["s"][0]

    def _post_listing_page(self, form_fields: dict[str, str], page: int) -> str:
        fields = dict(form_fields)
        fields[_PAGER_SELECT_NAME] = str(page)
        fields[_PAGER_SUBMIT_NAME] = "前往"
        request = Request(
            LISTING_URL,
            data=urlencode(fields).encode(),
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": LISTING_URL,
            },
        )
        with urlopen(request, timeout=60) as response:
            return self._decode_html(response.read())

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

    def _iter_listing_items(self) -> list[TaisugarNewsItem]:
        """Fetch every ASP.NET listing page and return worker-paper items."""
        if self._cached_items is not None:
            return self._cached_items
        items: list[TaisugarNewsItem] = []
        seen_urls: set[str] = set()
        html = self._fetch_text(LISTING_URL)
        metadata = parse_listing_metadata(html)
        if metadata.current_page != 1:
            raise ValueError("Taisugar listing did not open on page 1")
        if metadata.total_pages > MAX_PAGES:
            raise ValueError(f"Taisugar listing exceeds {MAX_PAGES} pages")
        expected_pages = metadata.total_pages
        expected_rows = metadata.total_rows
        form_fields = metadata.form_fields
        for page in range(1, expected_pages + 1):
            if page > 1:
                html = self._post_listing_page(form_fields, page)
                metadata = parse_listing_metadata(html)
                if metadata.current_page != page:
                    raise ValueError(
                        f"Taisugar pager returned page {metadata.current_page}, expected {page}"
                    )
                if metadata.total_pages != expected_pages or metadata.total_rows != expected_rows:
                    raise ValueError("Taisugar pager totals changed during discovery")
                form_fields = metadata.form_fields
            rows = parse_listing_rows(html)
            if not rows:
                raise ValueError(f"Taisugar listing page {page} contains no news rows")
            for _, detail_url in rows:
                self._detail_id(detail_url)
                if detail_url in seen_urls:
                    raise ValueError(f"Taisugar listing repeats detail URL: {detail_url}")
                seen_urls.add(detail_url)
            items.extend(parse_news_listing(html))
        if len(seen_urls) != expected_rows:
            raise ValueError(
                f"Taisugar listing declared {expected_rows} rows but exposed {len(seen_urls)}"
            )
        if not items:
            raise ValueError("Taisugar listing contains no worker-paper events")
        if len(items) > MAX_DISCOVERY_EVENTS:
            raise ValueError(f"Taisugar listing exceeds {MAX_DISCOVERY_EVENTS} paper events")
        event_keys = [(item.year_roc, item.year_roc + 1911) for item in items]
        if len(set(event_keys)) != len(event_keys):
            raise ValueError("Taisugar listing exposes duplicate worker-paper years")
        for item in items:
            code = f"taisugar-recruit-{item.year_roc}"
            self._event_urls[(code, item.year_roc + 1911)] = item.detail_url
        self._cached_items = items
        return items

    def _fetch_downloads(self, detail_url: str) -> list[TaisugarDownload]:
        html = self._fetch_text(detail_url)
        downloads = parse_news_detail(html)
        if not downloads:
            raise ValueError(f"Taisugar paper detail exposes no PDF/ZIP files: {detail_url}")
        detail_id = self._detail_id(detail_url)
        expected_prefix = f"/upload/UserFiles/News/{detail_id}/"
        seen: set[str] = set()
        for download in downloads:
            parts = urlsplit(download.url)
            if (
                parts.scheme != "https"
                or parts.netloc != "www.taisugar.com.tw"
                or f"{Path(parts.path).parent.as_posix()}/" != expected_prefix
                or Path(parts.path).suffix.lower() not in {".pdf", ".zip"}
            ):
                raise ValueError(f"Unexpected Taisugar paper file URL: {download.url}")
            if download.url in seen:
                raise ValueError(f"Taisugar paper detail repeats file URL: {download.url}")
            seen.add(download.url)
        return downloads

    def build_discovery_year_url(self, year_ad: int) -> str:
        self._iter_listing_items()
        if not any(event_year == year_ad for _, event_year in self._event_urls):
            raise ValueError(f"Unknown Taisugar recruitment discovery year: {year_ad}")
        return LISTING_URL

    def build_discovery_exam_url(self, exam_code: str, year_ad: int) -> str:
        self._iter_listing_items()
        try:
            return self._event_urls[(exam_code, year_ad)]
        except KeyError as exc:
            raise ValueError(
                f"Unknown Taisugar recruitment discovery exam: {exam_code} ({year_ad})"
            ) from exc

    def discover_available_years(self) -> list[int]:
        return sorted(
            {item.year_roc + 1911 for item in self._iter_listing_items()},
            reverse=True,
        )

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        return [
            ExamOption(
                code=f"taisugar-recruit-{item.year_roc}",
                year_ad=item.year_roc + 1911,
                year_roc=item.year_roc,
                label=item.title,
            )
            for item in self._iter_listing_items()
            if item.year_roc + 1911 == year_ad
        ]

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        news_item = next(
            (
                item
                for item in self._iter_listing_items()
                if f"taisugar-recruit-{item.year_roc}" == exam_code
                and item.year_roc + 1911 == year_ad
            ),
            None,
        )
        if news_item is None:
            raise ValueError(f"No Taisugar recruitment event for {exam_code} ({year_ad})")
        downloads = self._fetch_downloads(news_item.detail_url)
        papers: list[ParsedPaper] = []
        for index, dl in enumerate(downloads, start=1):
            if "答案" in dl.label and "試題" not in dl.label:
                file_type = "answer"
            elif "解答" in dl.label or ("試題" in dl.label and "答案" in dl.label):
                file_type = "question_answer"
            else:
                file_type = "question"
            papers.append(
                ParsedPaper(
                    category_raw=CANONICAL_CATEGORY,
                    category_code=str(news_item.year_roc),
                    subject_code=f"taisugar-recruit-{news_item.year_roc}-{index:02d}",
                    subject_name_raw=dl.label,
                    files={file_type: dl.url},
                )
            )
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=news_item.year_roc,
            exam_name_raw=news_item.title,
            attachments=[],
            papers=papers,
            provider_id=self.provider_id,
        )
