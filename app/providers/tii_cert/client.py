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

USER_AGENT = "Mozilla/5.0 (compatible; tii-cert-mirror/1.0)"

_YEAR_ROUND_RE = re.compile(r"(\d{2,3})\s*年(?:第\s*(\d+)\s*次)?")
_MESSAGE_DOWNLOAD_FRAGMENT = "/exam/users/message_download/"


@dataclass(frozen=True)
class TiiIntroSource:
    slug: str
    label: str
    url: str


INTRO_SOURCES = [
    TiiIntroSource(
        slug="investment-insurance",
        label="投資型保險商品業務員資格測驗",
        url="https://edu.tii.org.tw/exam/users/exam_intro/1",
    ),
    TiiIntroSource(
        slug="policyholder-service",
        label="人身保險業務員保戶服務檢定考試",
        url="https://edu.tii.org.tw/exam/users/exam_intro/2",
    ),
    TiiIntroSource(
        slug="aml",
        label="防制洗錢與打擊資恐專業人員測驗",
        url="https://edu.tii.org.tw/exam/users/exam_intro/58785",
    ),
    TiiIntroSource(
        slug="sustainability",
        label="永續發展基礎能力測驗",
        url="https://edu.tii.org.tw/exam/users/exam_intro/58786",
    ),
]


@dataclass(frozen=True)
class TiiIntroEntry:
    slug: str
    label: str
    year_ad: int
    year_roc: int
    round_no: int
    files: dict[str, str]

    @property
    def source_exam_id(self) -> str:
        return f"tii-cert-{self.slug}-{self.year_ad}-{self.round_no}"


class _TextAndAnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text_parts: list[str] = []
        self._href = ""
        self._anchor_parts: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href") or ""
        if href:
            self._href = unescape(href)
            self._anchor_parts = []

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)
        if self._href:
            self._anchor_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            label = " ".join(unescape("".join(self._anchor_parts)).split())
            self.links.append((self._href, label))
            self._href = ""
            self._anchor_parts = []


def parse_tii_intro_page(html: str, *, slug: str, label: str) -> TiiIntroEntry | None:
    parser = _TextAndAnchorParser()
    parser.feed(html)
    text = " ".join(unescape(" ".join(parser.text_parts)).split())
    match = _YEAR_ROUND_RE.search(text)
    if match is None:
        return None
    year_roc = int(match.group(1))
    round_no = int(match.group(2) or "1")

    files: dict[str, str] = {}
    for href, anchor_label in parser.links:
        if _MESSAGE_DOWNLOAD_FRAGMENT not in href:
            continue
        if "解答" in anchor_label or "答案" in anchor_label:
            file_type = "answer"
        else:
            file_type = "question"
        files.setdefault(file_type, href)

    if not files:
        return None
    return TiiIntroEntry(
        slug=slug,
        label=label,
        year_ad=year_roc + 1911,
        year_roc=year_roc,
        round_no=round_no,
        files=files,
    )


class TiiCertClient:
    provider_id = "tii_cert"

    def _fetch_text(self, url: str) -> str:
        request = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=60) as response:
            body: bytes = response.read()
            return body.decode("utf-8", "replace")

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
            disposition = response.headers.get("Content-Disposition", "")
            file_name = (
                _filename_from_disposition(disposition)
                or Path(unquote(urlparse(url).path)).name
                or "download.pdf"
            )
            return DownloadedFile(
                data=response.read(),
                content_type=response.headers.get("Content-Type", "application/octet-stream"),
                file_name=file_name,
            )

    def _entries(self) -> list[TiiIntroEntry]:
        entries: list[TiiIntroEntry] = []
        for source in INTRO_SOURCES:
            entry = parse_tii_intro_page(
                self._fetch_text(source.url),
                slug=source.slug,
                label=source.label,
            )
            if entry is not None:
                entries.append(entry)
        return entries

    def discover_available_years(self) -> list[int]:
        return sorted({entry.year_ad for entry in self._entries()}, reverse=True)

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        return [
            ExamOption(
                code=entry.source_exam_id,
                year_ad=entry.year_ad,
                year_roc=entry.year_roc,
                label=f"{entry.year_roc}年第{entry.round_no}次 {entry.label}",
            )
            for entry in self._entries()
            if entry.year_ad == year_ad
        ]

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        entry = next(
            (
                item
                for item in self._entries()
                if item.source_exam_id == exam_code and item.year_ad == year_ad
            ),
            None,
        )
        if entry is None:
            raise ValueError(f"TII exam not found: {exam_code}")

        paper = ParsedPaper(
            category_raw=entry.label,
            category_code=entry.slug,
            subject_code="main",
            subject_name_raw=entry.label,
            files=entry.files,
        )
        return SourceExamPage(
            source_exam_id=entry.source_exam_id,
            year_ad=entry.year_ad,
            year_roc=entry.year_roc,
            exam_name_raw=f"{entry.year_roc}年第{entry.round_no}次 {entry.label}",
            attachments=[],
            papers=[paper],
            provider_id=self.provider_id,
        )


_CONTENT_DISPOSITION_FILENAME_RE = re.compile(
    r"filename\*?=(?:UTF-8''|\"?)?([^\";]+)",
    re.IGNORECASE,
)


def _filename_from_disposition(header: str) -> str | None:
    match = _CONTENT_DISPOSITION_FILENAME_RE.search(header)
    if not match:
        return None
    return unescape(unquote(match.group(1).strip().strip('"')))
