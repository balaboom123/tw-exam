from __future__ import annotations

import re
import ssl
import time
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from http.client import HTTPResponse
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from app.models import ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata

USER_AGENT = "Mozilla/5.0 (compatible; hce-archive-mirror/1.0)"


@dataclass(frozen=True)
class HceSourcePage:
    url: str
    role: str = "papers"


@dataclass(frozen=True)
class HceYearPage:
    year_ad: int
    url: str
    role: str = "papers"
    additional_pages: tuple[HceSourcePage, ...] = ()

    @property
    def year_roc(self) -> int:
        return self.year_ad - 1911

    @property
    def source_pages(self) -> tuple[HceSourcePage, ...]:
        return (HceSourcePage(self.url, self.role), *self.additional_pages)


def _merge_year_pages(existing: HceYearPage, incoming: HceYearPage) -> HceYearPage:
    if existing.year_ad != incoming.year_ad:
        raise ValueError("Cannot merge different HCE academic years")
    sources: dict[str, HceSourcePage] = {}
    for source in (*existing.source_pages, *incoming.source_pages):
        previous = sources.get(source.url)
        if previous is not None and previous.role != source.role:
            raise ValueError("HCE source page has conflicting material roles")
        sources[source.url] = source
    primary = next((source for source in sources.values() if source.role == "papers"), None)
    if primary is None:
        primary = next(iter(sources.values()))
    return HceYearPage(
        existing.year_ad,
        primary.url,
        primary.role,
        tuple(source for source in sources.values() if source.url != primary.url),
    )


@dataclass(frozen=True)
class HceArchiveConfig:
    provider_id: str
    canonical_slug: str
    listing_url: str
    exam_name: str
    category_code: str
    category_name: str
    subject_slugs: dict[str, str]
    listing_pattern: re.Pattern[str]
    subject_aliases: dict[str, str] = field(default_factory=dict)
    clarification_pattern: re.Pattern[str] | None = None
    combined_pdf_listing: bool = False
    pagination_param: str | None = None
    embedded_pagination_placeholder: str | None = None
    max_listing_pages: int = 1
    historical_year_pages: tuple[HceYearPage, ...] = ()
    crawl_delay_seconds: float = 0.0

    def parse_listing(self, html: str, base_url: str | None = None) -> list[HceYearPage]:
        resolved_base_url = base_url or self.listing_url
        if self.combined_pdf_listing:
            return parse_combined_pdf_listing(html, resolved_base_url, self)
        return parse_article_listing(html, resolved_base_url, self)

    def parse_papers(
        self, html: str, base_url: str, *, source_role: str = "papers"
    ) -> list[ParsedPaper]:
        return parse_subject_file_page(html, base_url, self, source_role=source_role)


@dataclass
class _Link:
    label: str
    url: str


class _LinkParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.links: list[_Link] = []
        self._href = ""
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        self._href = dict(attrs).get("href", "") or ""
        self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._href:
            return
        self.links.append(
            _Link(_normalize_text(" ".join(self._text_parts)), urljoin(self.base_url, self._href))
        )
        self._href = ""
        self._text_parts = []


def _normalize_text(text: str) -> str:
    return " ".join(unescape(text).split())


def _links_from_html(html: str, base_url: str) -> list[_Link]:
    parser = _LinkParser(base_url)
    parser.feed(html)
    parser.close()
    return parser.links


def parse_article_listing(html: str, base_url: str, config: HceArchiveConfig) -> list[HceYearPage]:
    pages: dict[int, HceYearPage] = {}
    for link in _links_from_html(html, base_url):
        match = config.listing_pattern.search(link.label)
        role = "papers"
        if match is None and config.clarification_pattern is not None:
            match = config.clarification_pattern.search(link.label)
            role = "answer_clarification"
        if match is None:
            continue
        year_roc = int(match.group("year"))
        incoming = HceYearPage(year_ad=year_roc + 1911, url=link.url, role=role)
        pages[year_roc] = (
            _merge_year_pages(pages[year_roc], incoming) if year_roc in pages else incoming
        )
    return sorted(pages.values(), key=lambda page: page.year_ad, reverse=True)


def parse_listing_page_urls(html: str, base_url: str, config: HceArchiveConfig) -> list[str]:
    """Return official archive-page links that retain the configured listing context.

    Some admission sites publish only a small number of announcements on the
    first page. Following every same-site link would turn a bounded archive
    crawl into a site crawl, so pagination is intentionally opt-in per
    provider configuration.
    """
    if config.embedded_pagination_placeholder:
        prefix_match = re.search(r"urlPrefix\s*:\s*['\"](?P<url>[^'\"]+)['\"]", html)
        total_match = re.search(r"totalPage\s*:\s*(?P<total>\d+)", html)
        if prefix_match is None or total_match is None:
            raise ValueError("Missing embedded listing pagination metadata")
        placeholder = config.embedded_pagination_placeholder
        template = unescape(prefix_match.group("url"))
        total_pages = int(total_match.group("total"))
        if template.count(placeholder) != 1:
            raise ValueError("Invalid embedded listing pagination template")
        if total_pages > config.max_listing_pages:
            raise ValueError(
                f"Official listing pagination exceeds configured "
                f"bound: {total_pages} > {config.max_listing_pages}"
            )
        base = urlparse(base_url)
        urls = []
        for page_number in range(2, total_pages + 1):
            url = urljoin(base_url, template.replace(placeholder, str(page_number)))
            candidate = urlparse(url)
            if (candidate.scheme, candidate.netloc) != (base.scheme, base.netloc):
                raise ValueError("Embedded listing pagination leaves the official host")
            urls.append(url)
        return urls

    if not config.pagination_param:
        return []

    base = urlparse(base_url)
    base_query = parse_qs(base.query, keep_blank_values=True)
    base_query.pop(config.pagination_param, None)
    urls = []
    seen: set[str] = set()
    for link in _links_from_html(html, base_url):
        candidate = urlparse(link.url)
        if (candidate.scheme, candidate.netloc, candidate.path) != (
            base.scheme,
            base.netloc,
            base.path,
        ):
            continue
        candidate_query = parse_qs(candidate.query, keep_blank_values=True)
        page_values = candidate_query.pop(config.pagination_param, [])
        if not page_values or not all(value.isdigit() and int(value) >= 0 for value in page_values):
            continue
        candidate_url = urlunsplit(
            (candidate.scheme, candidate.netloc, candidate.path, candidate.query, "")
        )
        if candidate_query != base_query or candidate_url in seen:
            continue
        seen.add(candidate_url)
        urls.append(candidate_url)
    return urls


def parse_combined_pdf_listing(
    html: str, base_url: str, config: HceArchiveConfig
) -> list[HceYearPage]:
    pages: list[HceYearPage] = []
    seen: set[int] = set()
    for link in _links_from_html(html, base_url):
        if not link.url.lower().endswith(".pdf"):
            continue
        match = config.listing_pattern.search(link.label) or config.listing_pattern.search(link.url)
        if match is None:
            continue
        year_roc = int(match.group("year"))
        if year_roc < 80 or year_roc > 199:
            continue
        if year_roc in seen:
            continue
        seen.add(year_roc)
        pages.append(HceYearPage(year_ad=year_roc + 1911, url=link.url))
    return sorted(pages, key=lambda page: page.year_ad, reverse=True)


def _candidate_file_url(link: _Link) -> bool:
    value = (link.url + " " + link.label).lower()
    return ".pdf" in value or "action=downloadfile" in value


def _asset_kind(label: str) -> tuple[str, str, str] | None:
    if "各科" in label and "答案" in label:
        return "all_answers", "all", "各科參考答案"
    if "答案" in label:
        return "answer", "", ""
    if "試題" in label:
        return "question", "", ""
    return None


def _subject_for(label: str, config: HceArchiveConfig) -> tuple[str, str] | None:
    subject_names = {subject: subject for subject in config.subject_slugs}
    subject_names.update(config.subject_aliases)
    for source_label in sorted(subject_names, key=len, reverse=True):
        if source_label in label:
            subject = subject_names[source_label]
            return config.subject_slugs[subject], subject
    return None


def parse_subject_file_page(
    html: str, base_url: str, config: HceArchiveConfig, *, source_role: str = "papers"
) -> list[ParsedPaper]:
    if source_role not in {"papers", "answer_clarification"}:
        raise ValueError("Unsupported HCE source-page role")
    grouped: dict[str, ParsedPaper] = {}
    for link in _links_from_html(html, base_url):
        if not _candidate_file_url(link):
            continue
        kind = _asset_kind(link.label)
        subject = _subject_for(link.label, config)
        if source_role == "answer_clarification":
            if not any(marker in link.label for marker in ("釋疑", "疑義")) or "申請" in link.label:
                continue
            file_type = "answer_clarification"
            subject_code, subject_name = subject or ("all", "各科答案釋疑結果")
        elif kind is None and subject is None:
            continue
        elif kind is None:
            # Some historical CMU pages label a question PDF only as
            # "112國文.pdf" rather than including the word "試題".
            file_type = "question"
            assert subject is not None
            subject_code, subject_name = subject
        else:
            file_type, subject_code, subject_name = kind
            if subject_code != "all":
                if subject is None:
                    # A single untitled reference-answer PDF applies to all
                    # subjects; keep it rather than dropping source evidence.
                    if file_type != "answer":
                        continue
                    file_type = "all_answers"
                    subject_code, subject_name = "all", "各科參考答案"
                else:
                    subject_code, subject_name = subject
        paper = grouped.setdefault(
            subject_code,
            ParsedPaper(
                category_raw=config.category_name,
                category_code=config.category_code,
                subject_code=subject_code,
                subject_name_raw=subject_name,
                files={},
            ),
        )
        if file_type in paper.files and paper.files[file_type] != link.url:
            raise ValueError("HCE source page has conflicting URLs for one subject and file role")
        paper.files[file_type] = link.url
    if source_role == "answer_clarification" and not grouped:
        raise ValueError("HCE clarification page has no recognized native result PDF")
    return list(grouped.values())


def _combined_pdf_paper(config: HceArchiveConfig, url: str) -> ParsedPaper:
    return ParsedPaper(
        category_raw=config.category_name,
        category_code=config.category_code,
        subject_code="all",
        subject_name_raw="各科試題與參考答案",
        files={"question_answer": url},
    )


def _ssl_context_for(url: str) -> ssl.SSLContext | None:
    host = urlparse(url).hostname or ""
    if host == "adm21.cmu.edu.tw":
        return ssl.create_default_context()
    return None


def _request_url(url: str) -> str:
    parts = urlsplit(url)
    # NTHU's native article routes require the literal comma before the category.
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            quote(parts.path, safe="/%,"),
            quote(parts.query, safe="=&%"),
            parts.fragment,
        )
    )


def _fallback_file_name(url: str, content_disposition: str) -> str:
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?(?P<name>[^";]+)', content_disposition)
    if match is not None:
        return unquote(match.group("name"))
    name = Path(unquote(urlparse(url).path)).name
    return name if "." in name else "download.pdf"


class HceArchiveClient:
    def __init__(self, config: HceArchiveConfig) -> None:
        self.config = config
        self.provider_id = config.provider_id
        self._year_pages_cache: tuple[HceYearPage, ...] | None = None
        self._last_request_at: float | None = None

    def _wait_for_request_slot(self) -> None:
        delay = self.config.crawl_delay_seconds
        now = time.monotonic()
        if self._last_request_at is not None:
            remaining = self._last_request_at + delay - now
            if remaining > 0:
                time.sleep(remaining)
                now += remaining
        self._last_request_at = now

    def _open(self, url: str, *, method: str = "GET", timeout: int = 60) -> HTTPResponse:
        self._wait_for_request_slot()
        request = Request(_request_url(url), headers={"User-Agent": USER_AGENT}, method=method)
        context = _ssl_context_for(url)
        if context is None:
            return cast(HTTPResponse, urlopen(request, timeout=timeout))
        return cast(HTTPResponse, urlopen(request, timeout=timeout, context=context))

    def _fetch_text(self, url: str) -> str:
        with self._open(url) as response:
            return response.read().decode("utf-8", "replace")

    def _year_pages(self) -> list[HceYearPage]:
        if self._year_pages_cache is not None:
            return list(self._year_pages_cache)

        pages_by_year = {page.year_ad: page for page in self.config.historical_year_pages}
        pending = [self.config.listing_url]
        seen: set[str] = set()

        while pending and len(seen) < max(self.config.max_listing_pages, 1):
            listing_url = pending.pop(0)
            if listing_url in seen:
                continue
            seen.add(listing_url)
            html = self._fetch_text(listing_url)
            for page in self.config.parse_listing(html, listing_url):
                previous = pages_by_year.get(page.year_ad)
                pages_by_year[page.year_ad] = (
                    _merge_year_pages(previous, page) if previous is not None else page
                )
            for page_url in parse_listing_page_urls(html, listing_url, self.config):
                if page_url not in seen and page_url not in pending:
                    pending.append(page_url)

        self._year_pages_cache = tuple(
            sorted(pages_by_year.values(), key=lambda page: page.year_ad, reverse=True)
        )
        return list(self._year_pages_cache)

    def discover_available_years(self) -> list[int]:
        return [page.year_ad for page in self._year_pages()]

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        return [
            ExamOption(
                code=f"{self.config.canonical_slug}-{page.year_roc}",
                year_ad=page.year_ad,
                year_roc=page.year_roc,
                label=f"{page.year_roc}學年度{self.config.exam_name}",
            )
            for page in self._year_pages()
            if page.year_ad == year_ad
        ]

    def build_discovery_year_url(self, year_ad: int) -> str:
        try:
            return next(page.url for page in self._year_pages() if page.year_ad == year_ad)
        except StopIteration as exc:
            raise ValueError(f"Unknown {self.provider_id} discovery year: {year_ad}") from exc

    def build_discovery_exam_url(self, exam_code: str, year_ad: int) -> str:
        expected_code = f"{self.config.canonical_slug}-{year_ad - 1911}"
        if exam_code != expected_code:
            raise ValueError(f"Unknown {self.provider_id} discovery exam: {exam_code} ({year_ad})")
        return self.build_discovery_year_url(year_ad)

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        year_page = next(
            page
            for page in self._year_pages()
            if page.year_ad == year_ad
            and exam_code == f"{self.config.canonical_slug}-{page.year_roc}"
        )
        if self.config.combined_pdf_listing:
            papers = [_combined_pdf_paper(self.config, year_page.url)]
        else:
            papers = []
            seen: set[tuple[str, str, str, str]] = set()
            for source in year_page.source_pages:
                parsed = self.config.parse_papers(
                    self._fetch_text(source.url), source.url, source_role=source.role
                )
                if not parsed:
                    raise ValueError(
                        "HCE source page has no recognized native papers; source review required"
                    )
                for paper in parsed:
                    files = {}
                    for role, url in paper.files.items():
                        key = (paper.category_code, paper.subject_code, role, url)
                        if key not in seen:
                            seen.add(key)
                            files[role] = url
                    if files:
                        paper.files = files
                        papers.append(paper)
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=year_ad - 1911,
            exam_name_raw=f"{year_ad - 1911}學年度{self.config.exam_name}",
            attachments=[],
            papers=papers,
            provider_id=self.provider_id,
        )

    def head(self, url: str) -> ResponseMetadata:
        with self._open(url, method="HEAD") as response:
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
        with self._open(url, timeout=120) as response:
            content_disposition = response.headers.get("Content-Disposition", "")
            return DownloadedFile(
                data=response.read(),
                content_type=response.headers.get("Content-Type", "application/octet-stream"),
                file_name=_fallback_file_name(url, content_disposition),
            )


HCE_CONFIGS = {
    "hce_cmu": HceArchiveConfig(
        provider_id="hce_cmu",
        canonical_slug="hce-cmu",
        listing_url="https://adm21.cmu.edu.tw/?q=zh-hant/news_spbcm",
        exam_name="中國醫藥大學學士後中醫學系",
        category_code="post-bacc-chinese-medicine",
        category_name="中國醫藥大學學士後中醫學系",
        subject_slugs={
            "國文": "chinese",
            "化學": "chemistry",
            "英文": "english",
            "生物學": "biology",
        },
        subject_aliases={"生物": "生物學"},
        listing_pattern=re.compile(
            r"(?P<year>\d{3})(?:學年度)?學士後中醫學系.*試題(?:及參考答案|參考答案|$)"
        ),
        clarification_pattern=re.compile(
            r"(?P<year>\d{3})(?:學年度)?學士後中醫學系.*試題答案釋疑結果"
        ),
        pagination_param="page",
        max_listing_pages=8,
        crawl_delay_seconds=10.0,
    ),
    "hce_tcu": HceArchiveConfig(
        provider_id="hce_tcu",
        canonical_slug="hce-tcu",
        listing_url="https://admissions.tcu.edu.tw/?cat=23",
        exam_name="慈濟大學學士後中醫學系",
        category_code="post-bacc-chinese-medicine",
        category_name="慈濟大學學士後中醫學系",
        subject_slugs={
            "國文": "chinese",
            "化學": "chemistry",
            "英文": "english",
            "生物學": "biology",
        },
        listing_pattern=re.compile(r"(?P<year>\d{3})學年度學士後中醫學系.*試題及參考答案"),
        clarification_pattern=re.compile(r"(?P<year>\d{3})學年度學士後中醫學系.*試題答案釋疑結果"),
    ),
    "hce_nsysu": HceArchiveConfig(
        provider_id="hce_nsysu",
        canonical_slug="hce-nsysu",
        listing_url="https://lis.nsysu.edu.tw/p/412-1001-23442.php?Lang=zh-tw",
        exam_name="國立中山大學學士後醫學系",
        category_code="post-bacc-medicine",
        category_name="國立中山大學學士後醫學系",
        subject_slugs={},
        listing_pattern=re.compile(r"(?P<year>\d{3})(?:年|_|\.pdf)"),
        combined_pdf_listing=True,
        crawl_delay_seconds=1.0,
    ),
    "hce_nthu": HceArchiveConfig(
        provider_id="hce_nthu",
        canonical_slug="hce-nthu",
        listing_url="https://adms.site.nthu.edu.tw/p/403-1207-6125-1.php?Lang=zh-tw",
        exam_name="國立清華大學學士後醫學系",
        category_code="post-bacc-medicine",
        category_name="國立清華大學學士後醫學系",
        subject_slugs={
            "英文": "english",
            "生物與生化": "biology-biochemistry",
            "化學與物理": "chemistry-physics",
            "資訊科學": "computer-science",
            "進階物理與線性代數": "advanced-physics-linear-algebra",
        },
        listing_pattern=re.compile(r"(?P<year>\d{3})學年度學士後醫學系.*各科試題及參考答案"),
        clarification_pattern=re.compile(
            r"(?P<year>\d{3})學年度學士後醫學系.*(?:試題|答案).*釋疑結果"
        ),
        embedded_pagination_placeholder="PAGE",
        max_listing_pages=8,
    ),
}
