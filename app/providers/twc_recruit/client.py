from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlparse, urlsplit

from app.models import ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata
from app.providers.http import Http

BASE_URL = "https://www.water.gov.tw/"
DOWNLOAD_PAGE_URL = "https://www.water.gov.tw/ch/Subject/Detail/59619?nodeId=715"
USER_AGENT = "Mozilla/5.0 (compatible; twc-recruit-mirror/1.0)"
CANONICAL_CATEGORY = "台水評價職位人員甄試"

# Matches ROC year in anchor text: "114年試題(解答).zip" -> 114
_YEAR_RE = re.compile(r"(\d{2,3})\s*年")

MAX_DISCOVERY_ENTRIES = 100
_DOWNLOAD_HREF_RE = re.compile(r"/ch/ServerFile/Get/")
_DOWNLOAD_ANCHOR_RE = re.compile(
    r"<a\b[^>]*\bhref\s*=\s*[\"'][^\"']*/ch/ServerFile/Get/[^\"']*[\"']",
    re.IGNORECASE,
)
_DOWNLOAD_PATH_RE = re.compile(
    r"^/ch/ServerFile/Get/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TwcRecruitEntry:
    year_roc: int
    year_ad: int
    label: str
    url: str


def _normalize_text(text: str) -> str:
    return " ".join(unescape(text).split())


class _DetailPageParser(HTMLParser):
    """Parse the TWC subject detail page for exam paper ZIP downloads.

    Expected structure (repeating per year):
      <div class="檔案下載">
        <a href="/ch/ServerFile/Get/[UUID]?nodeId=715" title="114年試題(解答).zip">
          114年試題(解答).zip(4.8M)
        </a>
        <a href="/ch/ServerFile/ShowFileSHA256/[UUID]">
          (SHA256驗證)
        </a>
      </div>

    We collect only anchors whose href matches the ServerFile/Get pattern and
    whose text (or title attribute) contains a ROC year. SHA256 verification
    links are automatically excluded by the href pattern check.
    """

    def __init__(self) -> None:
        super().__init__()
        self._in_file_div: bool = False
        self._div_depth_file: int = 0
        # Anchor tracking
        self._in_anchor: bool = False
        self._current_href: str = ""
        self._current_title: str = ""
        self._anchor_parts: list[str] = []
        self.entries: list[TwcRecruitEntry] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        classes = (attrs_dict.get("class") or "").split()

        if tag == "div" and {"main_page_other_download", "檔案下載"}.intersection(classes):
            self._in_file_div = True
            self._div_depth_file = 1
            return

        # Track nested divs inside 檔案下載
        if tag == "div":
            if self._in_file_div:
                self._div_depth_file += 1
            return

        if self._in_file_div and tag == "a":
            href = attrs_dict.get("href") or ""
            title = attrs_dict.get("title") or ""
            if _DOWNLOAD_HREF_RE.search(href):
                self._in_anchor = True
                self._current_href = href
                self._current_title = title
                self._anchor_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_anchor:
            self._anchor_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self._in_file_div:
            self._div_depth_file -= 1
            if self._div_depth_file == 0:
                self._in_file_div = False
            return

        if tag == "a" and self._in_anchor:
            anchor_text = _normalize_text("".join(self._anchor_parts))
            # Prefer title attribute for year extraction; fall back to anchor text
            label_source = self._current_title or anchor_text
            match = _YEAR_RE.search(label_source)
            if match:
                year_roc = int(match.group(1))
                label = label_source
                url = urljoin(BASE_URL, self._current_href)
                self.entries.append(
                    TwcRecruitEntry(
                        year_roc=year_roc,
                        year_ad=year_roc + 1911,
                        label=label,
                        url=url,
                    )
                )
            self._in_anchor = False
            self._anchor_parts = []
            self._current_href = ""
            self._current_title = ""


def parse_employment_detail(html: str) -> list[TwcRecruitEntry]:
    """Parse the TWC subject detail page and return one entry per ZIP bundle."""
    parser = _DetailPageParser()
    parser.feed(html)
    return parser.entries


class TwcRecruitClient:
    provider_id = "twc_recruit"

    def __init__(self) -> None:
        self._cached_entries: list[TwcRecruitEntry] | None = None
        self.http = Http(self.provider_id, max_attempts=1, user_agent=USER_AGENT)

    @staticmethod
    def _validate_download_url(url: str) -> None:
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        if (
            parts.scheme != "https"
            or parts.netloc != "www.water.gov.tw"
            or _DOWNLOAD_PATH_RE.fullmatch(parts.path) is None
            or parts.fragment
            or set(query) != {"nodeId"}
            or query["nodeId"] != ["715"]
        ):
            raise ValueError(f"Unexpected Taiwan Water paper URL: {url}")

    def _fetch_text(self, url: str) -> str:
        return self.http.get_text(url, encoding="utf-8")

    def head(self, url: str) -> ResponseMetadata:
        self._validate_download_url(url)
        return self.http.head(url)

    def download_file(self, url: str) -> DownloadedFile:
        self._validate_download_url(url)
        return self.http.download(url, content_disposition_name=False)

    def _iter_entries(self) -> list[TwcRecruitEntry]:
        if self._cached_entries is not None:
            return self._cached_entries
        html = self._fetch_text(DOWNLOAD_PAGE_URL)
        entries = parse_employment_detail(html)
        candidate_count = len(_DOWNLOAD_ANCHOR_RE.findall(html))
        if len(entries) != candidate_count:
            raise ValueError(
                "Taiwan Water archive candidate/parsed download mismatch: "
                f"{candidate_count} != {len(entries)}"
            )
        if not entries:
            raise ValueError("Taiwan Water archive contains no paper downloads")
        if len(entries) > MAX_DISCOVERY_ENTRIES:
            raise ValueError(
                f"Taiwan Water archive exceeds {MAX_DISCOVERY_ENTRIES} paper downloads"
            )
        years: set[int] = set()
        urls: set[str] = set()
        for entry in entries:
            self._validate_download_url(entry.url)
            if entry.year_roc in years:
                raise ValueError(
                    f"Taiwan Water archive repeats ROC year: {entry.year_roc}"
                )
            if entry.url in urls:
                raise ValueError(f"Taiwan Water archive repeats paper URL: {entry.url}")
            years.add(entry.year_roc)
            urls.add(entry.url)
        self._cached_entries = entries
        return entries

    def build_discovery_year_url(self, year_ad: int) -> str:
        if not any(entry.year_ad == year_ad for entry in self._iter_entries()):
            raise ValueError(f"Unknown Taiwan Water recruitment year: {year_ad}")
        return DOWNLOAD_PAGE_URL

    def build_discovery_exam_url(self, exam_code: str, year_ad: int) -> str:
        if not any(
            f"twc-recruit-{entry.year_roc}" == exam_code
            and entry.year_ad == year_ad
            for entry in self._iter_entries()
        ):
            raise ValueError(
                f"Unknown Taiwan Water recruitment exam: {exam_code} ({year_ad})"
            )
        return DOWNLOAD_PAGE_URL

    def discover_available_years(self) -> list[int]:
        return sorted({entry.year_ad for entry in self._iter_entries()}, reverse=True)

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        return [
            ExamOption(
                code=f"twc-recruit-{entry.year_roc}",
                year_ad=entry.year_ad,
                year_roc=entry.year_roc,
                label=entry.label,
            )
            for entry in self._iter_entries()
            if entry.year_ad == year_ad
        ]

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        entry = next(
            (
                item
                for item in self._iter_entries()
                if f"twc-recruit-{item.year_roc}" == exam_code
                and item.year_ad == year_ad
            ),
            None,
        )
        if entry is None:
            raise ValueError(
                f"No Taiwan Water recruitment event for {exam_code} ({year_ad})"
            )
        paper = ParsedPaper(
            category_raw=CANONICAL_CATEGORY,
            category_code=str(entry.year_roc),
            subject_code=f"twc-recruit-{entry.year_roc}-bundle",
            subject_name_raw=entry.label,
            files={"accessible_bundle": entry.url},
        )
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=entry.year_roc,
            exam_name_raw=entry.label,
            attachments=[],
            papers=[paper],
            provider_id=self.provider_id,
        )
