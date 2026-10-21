from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin

from app.models import ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata
from app.providers.http import Http
from app.providers.sfi_cert.evidence import (
    ARCHIVE_URL,
    NativePaper,
    download_context,
    reviewed_payload,
)
from app.providers.sfi_cert.identity import Programme, programme_for_listing

USER_AGENT = "Mozilla/5.0 (compatible; sfi-cert-mirror/1.0)"


@dataclass(frozen=True)
class ArchiveDownload:
    programme: Programme
    listing_label: str
    category_code: str
    files: dict[str, str]


class _TableRows(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[tuple[str, list[str]]]] = []
        self._cells: list[tuple[str, list[str]]] | None = None
        self._text: list[str] = []
        self._links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._cells = []
        elif self._cells is not None and tag in {"td", "th"}:
            self._text = []
            self._links = []
        elif self._cells is not None and tag == "a":
            self._links.append(dict(attrs).get("href") or "")

    def handle_data(self, data: str) -> None:
        if self._cells is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cells is not None:
            self._cells.append((" ".join("".join(self._text).split()), self._links))
        elif tag == "tr" and self._cells is not None:
            self.rows.append(self._cells)
            self._cells = None


def parse_sfi_archive(html: str) -> list[ArchiveDownload]:
    """Read native table labels and pairs; global headings do not date PDFs."""
    parser = _TableRows()
    parser.feed(html)
    downloads = []
    seen = set()
    for cells in parser.rows:
        urls = [
            urljoin(ARCHIVE_URL, href)
            for _, links in cells
            for href in links
            if "/Download/" in href
        ]
        if not urls:
            continue
        label = cells[0][0]
        programme = programme_for_listing(label)
        if programme is None:
            raise ValueError(f"Unreviewed SFI programme row: {label}")
        pairs: dict[tuple[str, str], dict[str, str]] = {}
        for url in urls:
            code, role = download_context(url)
            folder = url.rsplit("/", 2)[-2]
            files = pairs.setdefault((folder, code), {})
            if role in files and files[role] != url:
                raise ValueError("SFI row has conflicting file-role links")
            files[role] = url
        for (_folder, code), files in pairs.items():
            if set(files) != {"question", "answer"}:
                raise ValueError("SFI source row lacks its question/answer pair")
            key = (programme.slug, files["question"], files["answer"])
            if key not in seen:
                seen.add(key)
                downloads.append(ArchiveDownload(programme, label, code, files))
    if not downloads:
        raise ValueError("SFI archive contains no recognized native table rows")
    return downloads


@dataclass(frozen=True)
class SfiArchiveEntry:
    listing: ArchiveDownload
    question: NativePaper
    answer: NativePaper

    @property
    def source_exam_id(self) -> str:
        return self.question.source_exam_id


class SfiCertClient:
    provider_id = "sfi_cert"

    def __init__(self) -> None:
        self.http = Http(self.provider_id, max_attempts=1, min_interval=0.1, user_agent=USER_AGENT)
        self._responses: dict[str, DownloadedFile] = {}
        self._entries_cache: list[SfiArchiveEntry] | None = None

    def _fetch_text(self, url: str) -> str:
        return self.http.get_text(url, encoding="utf-8")

    def head(self, url: str) -> ResponseMetadata:
        download_context(url)
        return self.http.head(url)

    def download_file(self, url: str) -> DownloadedFile:
        download_context(url)
        if url not in self._responses:
            self._responses[url] = self.http.download(
                url, filename_fallback="download.pdf", content_disposition_name=False
            )
        return self._responses[url]

    def _entries(self) -> list[SfiArchiveEntry]:
        if self._entries_cache is not None:
            return self._entries_cache
        entries = []
        events = set()
        for listing in parse_sfi_archive(self._fetch_text(ARCHIVE_URL)):
            question = reviewed_payload(
                listing.files["question"],
                self.download_file(listing.files["question"]).data,
                listing.programme,
            )
            answer = reviewed_payload(
                listing.files["answer"],
                self.download_file(listing.files["answer"]).data,
                listing.programme,
            )
            if (
                question.source_exam_id != answer.source_exam_id
                or question.subjects != answer.subjects
            ):
                raise ValueError("SFI question and answer native contexts disagree")
            if question.source_exam_id in events:
                raise ValueError("SFI duplicate event needs native form review")
            events.add(question.source_exam_id)
            entries.append(SfiArchiveEntry(listing, question, answer))
        self._entries_cache = entries
        return entries

    def discover_available_years(self) -> list[int]:
        return sorted({entry.question.year_roc + 1911 for entry in self._entries()}, reverse=True)

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        return [
            ExamOption(entry.source_exam_id, year_ad, year_ad - 1911, entry.question.native_heading)
            for entry in self._entries()
            if entry.question.year_roc + 1911 == year_ad
        ]

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        entry = next(
            (
                entry
                for entry in self._entries()
                if entry.source_exam_id == exam_code and entry.question.year_roc + 1911 == year_ad
            ),
            None,
        )
        if entry is None:
            raise ValueError(f"SFI exam not found in the current native listing: {exam_code}")
        fact = entry.question
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=year_ad - 1911,
            exam_name_raw=f"{fact.year_roc}年第{fact.round_no}次 {fact.programme.label}"
            + ("（高雄考區）" if fact.venue else ""),
            attachments=[],
            papers=[
                ParsedPaper(
                    fact.programme.label,
                    fact.category_code,
                    "main",
                    "、".join(fact.subjects),
                    dict(entry.listing.files),
                    source_material=fact.material,
                )
            ],
            provider_id=self.provider_id,
            source_material=fact.material,
        )
