"""CPC Corporation (中油) recruitment exam provider — client and HTML parser.

The accepted source is CPC's doctoral exam-paper archive:
  - PhD exam papers:   https://www.cpc.com.tw/News_Content.aspx?n=32&s=826

The neighboring hiring page at ``s=824`` contains recruitment brochures, not
exam papers. It is retained as scope evidence but is not provider input.

Both pages use an ASP.NET CMS that renders the inner content inside a div
(``ContentPlaceHolder1_contentText`` or ``mcnTextContent``).  Anchors point
to the download handler::

    https://ws.cpc.com.tw/Download.ashx?u=<b64-path>&n=<b64-filename>

The ``u`` and ``n`` query-string parameters are base64-encoded — they are
preserved verbatim; this module never decodes or re-encodes them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from app.models import ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata

BASE_URL = "https://www.cpc.com.tw/"
DOWNLOAD_BASE_URL = "https://ws.cpc.com.tw/"
PHD_PAGE_URL = "https://www.cpc.com.tw/News_Content.aspx?n=32&s=826"
HIRING_PAGE_URL = "https://www.cpc.com.tw/News_Content.aspx?n=32&s=824"
USER_AGENT = "Mozilla/5.0 (compatible; cpc-recruit-mirror/1.0)"
CANONICAL_CATEGORY = "中油新進博士級人員甄試"

_YEAR_RE = re.compile(r"(\d{2,3})\s*年")


def _normalize_text(text: str) -> str:
    return " ".join(unescape(text).split())


@dataclass(frozen=True)
class CpcRecruitEntry:
    """A single PDF download entry from one of the CPC exam pages."""

    year_roc: int
    year_ad: int
    label: str
    url: str
    source: str  # "phd" | "hiring"


class _ContentPageParser(HTMLParser):
    """Parse a CPC ``News_Content.aspx`` page and collect Download.ashx links.

    Captures all ``<a>`` anchors whose ``href`` contains ``Download.ashx``,
    regardless of their position in the page structure.
    """

    _DOWNLOAD_FRAGMENT = "Download.ashx"

    def __init__(self) -> None:
        super().__init__()
        self._in_anchor: bool = False
        self._current_href: str = ""
        self._anchor_parts: list[str] = []
        self.links: list[tuple[str, str]] = []  # (href, label)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            attrs_dict = dict(attrs)
            href = attrs_dict.get("href") or ""
            if self._DOWNLOAD_FRAGMENT in href:
                self._in_anchor = True
                self._current_href = unescape(href)
                self._anchor_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_anchor:
            self._anchor_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_anchor:
            label = _normalize_text("".join(self._anchor_parts))
            href = self._current_href
            if label and href:
                self.links.append((href, label))
            self._in_anchor = False
            self._anchor_parts = []
            self._current_href = ""


def _decode_url_filename(href: str) -> str:
    """Decode the base64-encoded filename from a Download.ashx URL's ``n`` parameter."""
    from base64 import b64decode

    parsed = urlparse(href)
    from urllib.parse import parse_qs

    qs = parse_qs(parsed.query)
    n_param = qs.get("n", [""])[0]
    if not n_param:
        return ""
    try:
        raw = b64decode(unquote(n_param))
        for enc in ("utf-8", "cp950", "big5"):
            try:
                return raw.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return raw.decode("utf-8", "replace")
    except Exception:
        return ""


def parse_employment_page(html: str, source: str = "phd") -> list[CpcRecruitEntry]:
    """Parse a CPC employment news-content page and return download entries.

    Extracts year from the link label or, as fallback, from the base64-decoded
    filename in the Download.ashx URL.
    """
    parser = _ContentPageParser()
    parser.feed(html)

    entries: list[CpcRecruitEntry] = []
    for href, label in parser.links:
        match = _YEAR_RE.search(label)
        effective_label = label
        if match is None:
            decoded = _decode_url_filename(href)
            match = _YEAR_RE.search(decoded)
            if decoded:
                effective_label = decoded
        if match is None:
            continue
        year_roc = int(match.group(1))
        if year_roc < 80 or year_roc > 200:
            continue
        entries.append(
            CpcRecruitEntry(
                year_roc=year_roc,
                year_ad=year_roc + 1911,
                label=effective_label,
                url=href,
                source=source,
            )
        )
    return entries


class CpcRecruitClient:
    """HTTP client and data-aggregator for the CPC recruitment exam provider."""

    provider_id = "cpc_recruit"

    def _fetch_text(self, url: str) -> str:
        request = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=60) as response:
            body: bytes = response.read()
            # CPC may serve Big5/CP950; detect from meta charset or fall back
            content_type: str = response.headers.get("Content-Type", "")
            return _decode_html_bytes(body, content_type)

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
            content_disposition = response.headers.get("Content-Disposition", "")
            file_name = (
                _filename_from_disposition(content_disposition)
                or Path(unquote(urlparse(url).path)).name
                or "download.pdf"
            )
            return DownloadedFile(
                data=response.read(),
                content_type=response.headers.get("Content-Type", "application/octet-stream"),
                file_name=file_name,
            )

    def _iter_entries(self) -> list[CpcRecruitEntry]:
        """Fetch the accepted doctoral exam-paper archive."""
        phd_html = self._fetch_text(PHD_PAGE_URL)
        entries = parse_employment_page(phd_html, source="phd")
        return [entry for entry in entries if "博士" in entry.label and "試題" in entry.label]

    def build_discovery_year_url(self, year_ad: int) -> str:
        return PHD_PAGE_URL

    def build_discovery_exam_url(self, exam_code: str, year_ad: int) -> str:
        return PHD_PAGE_URL

    def discover_available_years(self) -> list[int]:
        return sorted(
            {entry.year_ad for entry in self._iter_entries()},
            reverse=True,
        )

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        # The archive exposes one doctoral paper package per represented year.
        seen: set[int] = set()
        options: list[ExamOption] = []
        for entry in self._iter_entries():
            if entry.year_ad != year_ad:
                continue
            if entry.year_roc in seen:
                continue
            seen.add(entry.year_roc)
            options.append(
                ExamOption(
                    code=f"cpc-recruit-{entry.year_roc}",
                    year_ad=entry.year_ad,
                    year_roc=entry.year_roc,
                    label=f"{entry.year_roc}年中油公司新進博士級人員甄試",
                )
            )
        return options

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        year_roc = year_ad - 1911
        entries = [e for e in self._iter_entries() if e.year_roc == year_roc]
        if not entries:
            return SourceExamPage(
                source_exam_id=exam_code,
                year_ad=year_ad,
                year_roc=year_roc,
                exam_name_raw=exam_code,
                attachments=[],
                papers=[],
                provider_id=self.provider_id,
            )

        papers: list[ParsedPaper] = []
        for index, entry in enumerate(entries, start=1):
            # Determine file type from label heuristics
            label_lower = entry.label
            if any(kw in label_lower for kw in ("答案", "解答", "答題")):
                file_type = "answer"
            else:
                file_type = "question"

            subject_prefix = "phd" if entry.source == "phd" else "hire"
            papers.append(
                ParsedPaper(
                    category_raw=CANONICAL_CATEGORY,
                    category_code=str(year_roc),
                    subject_code=f"{subject_prefix}-{index:02d}",
                    subject_name_raw=entry.label,
                    files={file_type: entry.url},
                )
            )

        exam_name_raw = f"{year_roc}年中油公司新進博士級人員甄試"
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=year_roc,
            exam_name_raw=exam_name_raw,
            attachments=[],
            papers=papers,
            provider_id=self.provider_id,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HEADER_CHARSET_RE = re.compile(r"charset=['\"]?\s*([a-zA-Z0-9_-]+)", re.IGNORECASE)
_HTML_CHARSET_RE = re.compile(rb"<meta[^>]+charset=['\"]?\s*([a-zA-Z0-9_-]+)", re.IGNORECASE)
_CONTENT_DISPOSITION_FILENAME_RE = re.compile(
    r'filename\*?=(?:UTF-8\'\'|")?([^";]+)', re.IGNORECASE
)


def _charset_from_content_type(content_type: str) -> str | None:
    match = _HEADER_CHARSET_RE.search(content_type)
    return match.group(1) if match else None


def _charset_from_html_bytes(body: bytes) -> str | None:
    match = _HTML_CHARSET_RE.search(body)
    return match.group(1).decode("ascii", "ignore") if match else None


def _decode_html_bytes(body: bytes, content_type: str) -> str:
    encodings = [
        _charset_from_content_type(content_type),
        _charset_from_html_bytes(body),
        "cp950",
        "big5",
        "utf-8",
    ]
    seen: set[str] = set()
    for enc in encodings:
        if not enc:
            continue
        key = enc.lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            return body.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", "replace")


def _filename_from_disposition(header: str) -> str | None:
    match = _CONTENT_DISPOSITION_FILENAME_RE.search(header)
    if match:
        return unescape(unquote(match.group(1).strip().strip('"')))
    return None
