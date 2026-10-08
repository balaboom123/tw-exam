from __future__ import annotations

import io
import re
import ssl
import zipfile
from dataclasses import dataclass
from datetime import date
from html import unescape
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit, urlunsplit

from app.models import ExamAttachment, ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata
from app.providers.http import Http
from app.source_material import SourceDate, SourceMaterial

USER_AGENT = "Mozilla/5.0 (compatible; tii-cert-mirror/1.0)"
DOWNLOAD_CENTER_URL = "https://edu.tii.org.tw/home/mpage/downloadfiles"
INTERMEDIATE_CA_PATH = Path(__file__).with_name("twca-secure-ssl-2023g3.pem")
_DATE_RE = re.compile(r"^(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_SECTION_RE = re.compile(r"第([一二三四五六七八九十1-9])節")

# Retained identifiers remain stable where dated listings and question headers
# establish their correspondence. New events use the official date, never an
# ordinal guessed from list order.
_LEGACY_EVENT_IDS = {
    ("aml", date(2025, 6, 7)): "tii-cert-aml-2025-2",
    ("investment-insurance", date(2026, 1, 11)): "tii-cert-investment-insurance-2026-1",
    ("sustainability", date(2026, 6, 6)): "tii-cert-sustainability-2026-3",
}


@dataclass(frozen=True)
class TiiListingSource:
    slug: str
    label: str
    url: str


PAPER_SOURCES = (
    TiiListingSource(
        "investment-insurance",
        "投資型保險商品業務員資格測驗",
        "https://edu.tii.org.tw/exam/users/exam_message/1",
    ),
    TiiListingSource(
        "policyholder-service",
        "保險業保戶服務認證測驗",
        "https://edu.tii.org.tw/exam/users/exam_message/2",
    ),
    TiiListingSource(
        "aml",
        "防制洗錢與打擊資恐專業人員測驗",
        "https://edu.tii.org.tw/exam/users/exam_message/58785",
    ),
    TiiListingSource(
        "sustainability",
        "永續發展基礎能力測驗",
        "https://edu.tii.org.tw/exam/users/exam_message/58786",
    ),
)


class _PaperListingParser(HTMLParser):
    """Only links in official 歷屆試題 rows are paper candidates."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._cell = -1
        self._heading: list[str] = []
        self._row_links: list[tuple[str, str]] = []
        self._href = ""
        self._label: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._cell = -1
            self._heading = []
            self._row_links = []
        elif tag == "td":
            self._cell += 1
        elif tag == "a" and self._cell > 0:
            self._href = dict(attrs).get("href") or ""
            self._label = []

    def handle_data(self, data: str) -> None:
        if self._cell == 0:
            self._heading.append(data)
        if self._href:
            self._label.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            self._row_links.append((self._href, " ".join("".join(self._label).split())))
            self._href = ""
        elif tag == "tr":
            if "".join(self._heading).strip().startswith("歷屆試題"):
                self.links.extend(self._row_links)
            self._cell = -1


@dataclass(frozen=True)
class TiiPaperLink:
    exam_date: date
    subject_code: str
    subject_label: str
    file_type: str
    url: str
    label: str


@dataclass(frozen=True)
class TiiExamEntry:
    source: TiiListingSource
    exam_date: date
    links: tuple[TiiPaperLink, ...]
    archive_url: str = ""

    @property
    def source_exam_id(self) -> str:
        return _LEGACY_EVENT_IDS.get(
            (self.source.slug, self.exam_date),
            f"tii-cert-{self.source.slug}-{self.exam_date.isoformat()}",
        )

    @property
    def label(self) -> str:
        day = self.exam_date
        return f"{day.year - 1911}年{day.month}月{day.day}日 {self.source.label}"


def parse_tii_paper_link(label: str, url: str) -> TiiPaperLink | None:
    label = " ".join(unescape(label).split())
    if any(word in label for word in ("簡章", "申請", "日程", "教材")):
        return None
    if "解答" in label or "答案" in label:
        role = "corrected_answer" if re.search(r"修正|更新|更正", label) else "answer"
    elif "試題" in label:
        role = "question"
    else:
        return None
    match = _DATE_RE.match(label)
    if match is None:
        raise ValueError(f"TII paper has no official examination date: {label}")
    day = date(int(match[1]) + 1911, int(match[2]), int(match[3]))
    section = _SECTION_RE.search(label)
    subject_code, subject_label = "main", ""
    if section is not None:
        number = section[1]
        number = str("一二三四五六七八九十".index(number) + 1) if not number.isdigit() else number
        subject_code = "main" if number == "1" else f"section-{number}"
        subject_label = section[0]
    elif "上午" in label or "下午" in label:
        subject_code = "morning" if "上午" in label else "afternoon"
        subject_label = "上午" if subject_code == "morning" else "下午"
    return TiiPaperLink(day, subject_code, subject_label, role, url, label)


def parse_tii_message_page(html: str, source: TiiListingSource) -> list[TiiExamEntry]:
    parser = _PaperListingParser()
    parser.feed(html)
    links: list[TiiPaperLink] = []
    for href, label in parser.links:
        url = urljoin(source.url, href)
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.netloc != "edu.tii.org.tw"
            or not re.fullmatch(r"/exam/users/message_download/\d+", parts.path)
        ):
            continue
        paper = parse_tii_paper_link(label, url)
        if paper is not None:
            links.append(paper)
    return _group_entries(source, links)


def _group_entries(
    source: TiiListingSource, links: list[TiiPaperLink], archive_url: str = ""
) -> list[TiiExamEntry]:
    grouped: dict[date, list[TiiPaperLink]] = {}
    for link in links:
        same_event = grouped.setdefault(link.exam_date, [])
        existing = next(
            (
                paper
                for paper in same_event
                if (paper.subject_code, paper.file_type) == (link.subject_code, link.file_type)
            ),
            None,
        )
        if existing is not None and existing.url != link.url:
            raise ValueError(f"TII event has conflicting paper sources: {link.label}")
        if existing is None:
            same_event.append(link)
    return [
        TiiExamEntry(source, day, tuple(papers), archive_url)
        for day, papers in sorted(grouped.items())
    ]


def _build_ssl_context() -> ssl.SSLContext:
    # The source omits this public intermediate. Keep system roots, full-chain
    # validation and hostname checking; do not trust the intermediate as a root.
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=str(INTERMEDIATE_CA_PATH))
    context.verify_flags &= ~ssl.VERIFY_X509_PARTIAL_CHAIN
    return context


class TiiCertClient:
    provider_id = "tii_cert"

    def __init__(self) -> None:
        self.http = Http(self.provider_id, ssl_context=_build_ssl_context(), user_agent=USER_AGENT)
        self._entries_cache: tuple[TiiExamEntry, ...] | None = None
        self._archives: dict[str, dict[str, bytes]] = {}
        self._archive_payloads: dict[str, bytes] = {}

    def _fetch_text(self, url: str) -> str:
        return self.http.get_text(url, encoding="utf-8")

    def _archive(self, url: str) -> dict[str, bytes]:
        if url not in self._archives:
            raw, _headers, _status = self.http._request(url, timeout=120)
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                members = archive.infolist()
                names = [item.filename.casefold() for item in members]
                if len(names) != len(set(names)):
                    raise ValueError("TII history archive has duplicate paths")
                for item in members:
                    path = PurePosixPath(item.filename)
                    if (
                        path.is_absolute()
                        or ".." in path.parts
                        or "\\" in item.filename
                        or ":" in item.filename
                    ):
                        raise ValueError("TII history archive has an unsafe path")
                self._archives[url] = {
                    item.filename: archive.read(item) for item in members if not item.is_dir()
                }
            self._archive_payloads[url] = raw
        return self._archives[url]

    def head(self, url: str) -> ResponseMetadata:
        if urlsplit(url).fragment:
            file = self.download_file(url)
            return ResponseMetadata(
                url=url,
                status=200,
                content_length=len(file.data),
                content_type=file.content_type,
                content_disposition=f'attachment; filename="{file.file_name}"',
            )
        return self.http.head(url)

    def download_file(self, url: str) -> DownloadedFile:
        parts = urlsplit(url)
        if parts.fragment:
            query = parse_qs(parts.fragment)
            if set(query) != {"zip-entry"} or len(query["zip-entry"]) != 1:
                raise ValueError("Invalid TII archive member locator")
            archive_url = urlunsplit(parts._replace(fragment=""))
            name = query["zip-entry"][0]
            return DownloadedFile(
                data=self._archive(archive_url)[name],
                content_type="application/pdf",
                file_name=PurePosixPath(name).name,
            )
        if url in self._archive_payloads:
            return DownloadedFile(
                data=self._archive_payloads[url],
                content_type="application/zip",
                file_name="保發中心-防制洗錢測驗歷屆試題.zip",
            )
        data, headers, _status = self.http._request(url, timeout=120)
        disposition = headers.get("Content-Disposition", "")
        match = re.search(r"filename\*?=(?:UTF-8''|\"?)?([^\";]+)", disposition, re.IGNORECASE)
        name = (
            unescape(unquote(match[1].strip().strip('"')))
            if match
            else Path(unquote(parts.path)).name
        )
        # This source sends UTF-8 filename bytes in Latin-1 HTTP header text.
        try:
            name = name.encode("latin-1").decode("utf-8")
        except (UnicodeError, ValueError):
            pass
        return DownloadedFile(
            data=data,
            content_type=headers.get("Content-Type", "application/octet-stream"),
            file_name=name or "download.pdf",
        )

    def _historical_entries(self, source: TiiListingSource) -> list[TiiExamEntry]:
        html = self._fetch_text(DOWNLOAD_CENTER_URL)
        matching = [
            block
            for block in re.findall(r"<dl\b[^>]*>(.*?)</dl>", html, re.DOTALL)
            if "檔案名稱：保發中心-防制洗錢測驗歷屆試題" in block
        ]
        if len(matching) != 1:
            raise ValueError("TII AML history download is missing or ambiguous")
        hrefs = re.findall(r"<a\b[^>]*href=[\"\']([^\"\']+)", matching[0])
        if len(hrefs) != 1:
            raise ValueError("TII AML history has no unique download URL")
        url = urljoin(DOWNLOAD_CENTER_URL, unescape(hrefs[0]))
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc != "edu.tii.org.tw":
            raise ValueError("TII history download is outside the official source")
        # The source emits an absolute /mpage/../download.php URL. urllib does
        # not remove dot segments in absolute hrefs; the server serves HTML for
        # that path. Resolve its native path without changing the signed query.
        resolved_path = urlsplit(urljoin("https://edu.tii.org.tw/", parts.path)).path
        url = urlunsplit(parts._replace(path=resolved_path))
        links = []
        for name in self._archive(url):
            if not name.lower().endswith(".pdf"):
                continue
            locator = f"{url}#zip-entry={quote(name, safe='')}"
            paper = parse_tii_paper_link(PurePosixPath(name).name, locator)
            if paper is None:
                raise ValueError(f"TII history PDF has no paper role: {name}")
            links.append(paper)
        if not links:
            raise ValueError("TII history archive contains no dated papers")
        return _group_entries(source, links, url)

    def _entries(self) -> tuple[TiiExamEntry, ...]:
        if self._entries_cache is None:
            entries: list[TiiExamEntry] = []
            for source in PAPER_SOURCES:
                html = self._fetch_text(source.url)
                entries.extend(parse_tii_message_page(html, source))
                if source.slug == "aml" and "防制洗錢測驗歷屆試題" in html:
                    entries.extend(self._historical_entries(source))
            ids = [entry.source_exam_id for entry in entries]
            if len(ids) != len(set(ids)):
                raise ValueError(
                    "TII current and history listings overlap; reconcile their evidence"
                )
            self._entries_cache = tuple(entries)
        return self._entries_cache

    def discover_available_years(self) -> list[int]:
        return sorted({entry.exam_date.year for entry in self._entries()}, reverse=True)

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        return [
            ExamOption(
                code=entry.source_exam_id,
                year_ad=year_ad,
                year_roc=year_ad - 1911,
                label=entry.label,
            )
            for entry in self._entries()
            if entry.exam_date.year == year_ad
        ]

    def build_discovery_year_url(self, year_ad: int) -> str:
        if year_ad not in self.discover_available_years():
            raise ValueError(f"Unknown TII discovery year: {year_ad}")
        return DOWNLOAD_CENTER_URL

    def build_discovery_exam_url(self, exam_code: str, year_ad: int) -> str:
        for entry in self._entries():
            if entry.source_exam_id == exam_code and entry.exam_date.year == year_ad:
                return DOWNLOAD_CENTER_URL if entry.archive_url else entry.source.url
        raise ValueError(f"Unknown TII discovery exam: {exam_code} ({year_ad})")

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        entry = next(
            (
                item
                for item in self._entries()
                if item.source_exam_id == exam_code and item.exam_date.year == year_ad
            ),
            None,
        )
        if entry is None:
            raise ValueError(f"TII exam not found: {exam_code}")
        papers = []
        for link in entry.links:
            material = SourceMaterial(
                "administered", SourceDate("exam_year", year_ad), link.url, link.label
            )
            papers.append(
                ParsedPaper(
                    category_raw=entry.source.label,
                    category_code=entry.source.slug,
                    subject_code=link.subject_code,
                    subject_name_raw=" ".join(
                        filter(None, (entry.source.label, link.subject_label))
                    ),
                    files={link.file_type: link.url},
                    source_material=material,
                )
            )
        attachments = (
            [
                ExamAttachment(
                    file_type="source_archive",
                    title="保發中心-防制洗錢測驗歷屆試題",
                    download_url_source=entry.archive_url,
                )
            ]
            if entry.archive_url
            else []
        )
        return SourceExamPage(
            source_exam_id=entry.source_exam_id,
            year_ad=year_ad,
            year_roc=year_ad - 1911,
            exam_name_raw=entry.label,
            attachments=attachments,
            papers=papers,
            provider_id=self.provider_id,
        )
