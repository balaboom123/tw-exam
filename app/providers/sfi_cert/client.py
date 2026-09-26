from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape

from app.models import ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata
from app.providers.http import Http

ARCHIVE_URL = "https://www.sfi.org.tw/Node?id=217"
USER_AGENT = "Mozilla/5.0 (compatible; sfi-cert-mirror/1.0)"

_ROUND_RE = re.compile(r"(\d{2,3})\s*年度第\s*(\d+)\s*次")
_DOWNLOAD_RE = re.compile(
    r"https?://examweb\.sfi\.org\.tw/Download/(\d{2})/(\d{2})(a?)\.pdf",
    re.IGNORECASE,
)

_CERTIFICATIONS = {
    "01": ("securities-analyst", "證券投資分析人員"),
    "02": ("senior-securities-dealer", "證券商高級業務員"),
    "03": ("securities-dealer", "證券商業務員"),
    "06": ("futures-dealer", "期貨商業務員"),
    "40": ("sitca", "投信投顧業務員"),
    "59": ("corporate-internal-control", "企業內部控制"),
    "81": ("aml", "防制洗錢與打擊資恐專業人員測驗"),
    "82": ("sustainability", "永續發展基礎能力測驗"),
}


@dataclass(frozen=True)
class SfiArchiveEntry:
    slug: str
    title: str
    paper_code: str
    year_ad: int
    year_roc: int
    round_no: int
    files: dict[str, str]

    @property
    def source_exam_id(self) -> str:
        return f"sfi-cert-{self.slug}-{self.year_ad}-{self.round_no}"


def _normalize_text(text: str) -> str:
    return " ".join(unescape(text).split())


def parse_sfi_archive(html: str) -> list[SfiArchiveEntry]:
    """Parse SFI's latest written-test archive page.

    The page publishes two round folders: ``Download/01`` for the latest round
    and ``Download/02`` for the previous round.  Individual files use the exam
    code plus optional ``a`` suffix for answers, for example ``03.pdf`` and
    ``03a.pdf`` for securities dealer question/answer files.
    """
    text = _normalize_text(re.sub(r"<[^>]+>", " ", html))
    round_matches = list(_ROUND_RE.finditer(text))
    round_by_folder: dict[str, tuple[int, int]] = {}
    for index, match in enumerate(round_matches[:2], start=1):
        year_roc = int(match.group(1))
        round_by_folder[f"{index:02d}"] = (year_roc, int(match.group(2)))

    grouped: dict[tuple[str, str], dict[str, str]] = {}
    for match in _DOWNLOAD_RE.finditer(html):
        folder, paper_code, answer_suffix = match.groups()
        if folder not in round_by_folder or paper_code not in _CERTIFICATIONS:
            continue
        file_type = "answer" if answer_suffix else "question"
        grouped.setdefault((folder, paper_code), {})[file_type] = match.group(0)

    entries: list[SfiArchiveEntry] = []
    for (folder, paper_code), files in sorted(grouped.items()):
        if "question" not in files:
            continue
        year_roc, round_no = round_by_folder[folder]
        slug, title = _CERTIFICATIONS[paper_code]
        entries.append(
            SfiArchiveEntry(
                slug=slug,
                title=title,
                paper_code=paper_code,
                year_ad=year_roc + 1911,
                year_roc=year_roc,
                round_no=round_no,
                files=dict(sorted(files.items())),
            )
        )
    return entries


class SfiCertClient:
    provider_id = "sfi_cert"

    def __init__(self) -> None:
        self.http = Http(self.provider_id, max_attempts=1, user_agent=USER_AGENT)

    def _fetch_text(self, url: str) -> str:
        return self.http.get_text(url, encoding="utf-8")

    def head(self, url: str) -> ResponseMetadata:
        return self.http.head(url)

    def download_file(self, url: str) -> DownloadedFile:
        return self.http.download(url, filename_fallback="download.pdf", content_disposition_name=False)

    def _entries(self) -> list[SfiArchiveEntry]:
        return parse_sfi_archive(self._fetch_text(ARCHIVE_URL))

    def discover_available_years(self) -> list[int]:
        return sorted({entry.year_ad for entry in self._entries()}, reverse=True)

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        entries = [entry for entry in self._entries() if entry.year_ad == year_ad]
        return [
            ExamOption(
                code=entry.source_exam_id,
                year_ad=entry.year_ad,
                year_roc=entry.year_roc,
                label=f"{entry.year_roc}年度第{entry.round_no}次 {entry.title}",
            )
            for entry in entries
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
            raise ValueError(f"SFI exam not found: {exam_code}")

        paper = ParsedPaper(
            category_raw=entry.title,
            category_code=entry.paper_code,
            subject_code="main",
            subject_name_raw=entry.title,
            files=entry.files,
        )
        return SourceExamPage(
            source_exam_id=entry.source_exam_id,
            year_ad=entry.year_ad,
            year_roc=entry.year_roc,
            exam_name_raw=f"{entry.year_roc}年度第{entry.round_no}次 {entry.title}",
            attachments=[],
            papers=[paper],
            provider_id=self.provider_id,
        )
