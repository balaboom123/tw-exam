from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import parse_qs, urljoin, urlsplit

from app.models import ExamAttachment, ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata
from app.providers.http import Http
from app.providers.twc_recruit.archive import (
    extract_native_members,
    member_locator,
    native_archives,
    parse_member_locator,
)

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
      <div class="main_page_other_download">
        <a href="/ch/ServerFile/Get/[UUID]?nodeId=715" title="(檔案下載)">
          115年試題(解答).zip(4.3M)
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
            # Generic accessibility titles are not the source file label.
            label_source = self._current_title
            match = _YEAR_RE.search(label_source)
            if match is None:
                label_source = anchor_text
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


def archive_file_role(label: str) -> str:
    """Use the native archive label, not an anchor's accessibility title."""
    label = _normalize_text(label)
    if "無障礙" in label:
        return "accessible_bundle"
    if "試題" in label and any(word in label for word in ("解答", "答案")):
        return "question_answer"
    raise ValueError(f"Unresolved Taiwan Water archive material role: {label}")


def retained_archive_file_role(
    file_type: str,
    *,
    year_roc: int,
    source_exam_id: str,
    category_code: str,
    subject_code: str,
    label: str,
    source_url: str,
) -> str:
    """Correct the former wrapper role without changing its source locators."""
    if (
        file_type != "accessible_bundle"
        or source_exam_id != f"twc-recruit-{year_roc}"
        or category_code != str(year_roc)
        or subject_code != f"twc-recruit-{year_roc}-bundle"
    ):
        return file_type
    try:
        TwcRecruitClient._validate_download_url(source_url)
        return archive_file_role(label)
    except ValueError:
        # An unrecognized historical context needs its own source review.
        return file_type


class TwcRecruitClient:
    provider_id = "twc_recruit"

    def __init__(self) -> None:
        self._cached_entries: list[TwcRecruitEntry] | None = None
        self._archive_downloads: dict[str, DownloadedFile] = {}
        self._native_payloads: dict[tuple[str, str], dict[str, bytes]] = {}
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
        if urlsplit(url).fragment:
            file = self.download_file(url)
            return ResponseMetadata(
                url=url,
                status=200,
                content_length=len(file.data),
                content_type=file.content_type,
                content_disposition="",
            )
        self._validate_download_url(url)
        return self.http.head(url)

    def _download_archive(self, url: str) -> DownloadedFile:
        self._validate_download_url(url)
        if url not in self._archive_downloads:
            self._archive_downloads[url] = self.http.download(url, content_disposition_name=False)
        return self._archive_downloads[url]

    def download_file(self, url: str) -> DownloadedFile:
        if not urlsplit(url).fragment:
            return self._download_archive(url)
        archive_url, checksum, name = parse_member_locator(url)
        matches = [
            fact
            for fact in native_archives()
            if fact.source_url == archive_url and fact.checksum == checksum
        ]
        if len(matches) != 1 or not any(
            member.name == name and member.file_role != "source_artifact"
            for member in matches[0].members
        ):
            raise ValueError("Unreviewed Taiwan Water native paper locator")
        key = (archive_url, checksum)
        if key not in self._native_payloads:
            self._native_payloads[key] = extract_native_members(
                matches[0], self._download_archive(archive_url).data
            )
        return DownloadedFile(
            self._native_payloads[key][name], "application/pdf", PurePosixPath(name).name
        )

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
                raise ValueError(f"Taiwan Water archive repeats ROC year: {entry.year_roc}")
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
            f"twc-recruit-{entry.year_roc}" == exam_code and entry.year_ad == year_ad
            for entry in self._iter_entries()
        ):
            raise ValueError(f"Unknown Taiwan Water recruitment exam: {exam_code} ({year_ad})")
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
                if f"twc-recruit-{item.year_roc}" == exam_code and item.year_ad == year_ad
            ),
            None,
        )
        if entry is None:
            raise ValueError(f"No Taiwan Water recruitment event for {exam_code} ({year_ad})")
        role = archive_file_role(entry.label)
        candidates = [
            fact
            for fact in native_archives()
            if role == "question_answer"
            and fact.year_roc == entry.year_roc
            and fact.source_url == entry.url
            and fact.members
        ]
        if candidates:
            data = self._download_archive(entry.url).data
            checksum = hashlib.sha256(data).hexdigest()
            archive = next((fact for fact in candidates if fact.checksum == checksum), None)
            if archive is not None:
                self._native_payloads[entry.url, checksum] = extract_native_members(archive, data)
                papers = []
                for member in archive.members:
                    if member.file_role == "source_artifact":
                        continue
                    contexts = [
                        (category.label, category.category_code(entry.year_roc))
                        for category in archive.categories
                        if category.category_id in member.category_ids
                    ]
                    # Older/unreviewed occupational contexts keep their exact
                    # native reference and generic review identity. Shared
                    # reviewed papers receive one reference per occupation;
                    # the locator and cached payload remain the same.
                    for label, category_code in contexts or [
                        (CANONICAL_CATEGORY, str(entry.year_roc))
                    ]:
                        papers.append(
                            ParsedPaper(
                                category_raw=label,
                                category_code=category_code,
                                subject_code=member.subject_code,
                                subject_name_raw=member.subject_label,
                                files={member.file_role: member_locator(archive, member)},
                            )
                        )
                return SourceExamPage(
                    source_exam_id=exam_code,
                    year_ad=year_ad,
                    year_roc=entry.year_roc,
                    exam_name_raw=entry.label,
                    attachments=[ExamAttachment(entry.label, "source_archive", entry.url)],
                    papers=papers,
                    provider_id=self.provider_id,
                )
        # Unknown versions and known damaged containers retain the original
        # acquisition path and explicit review/failure handling.
        paper = ParsedPaper(
            category_raw=CANONICAL_CATEGORY,
            category_code=str(entry.year_roc),
            subject_code=f"twc-recruit-{entry.year_roc}-bundle",
            subject_name_raw=entry.label,
            files={role: entry.url},
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
