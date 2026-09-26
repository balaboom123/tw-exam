from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, unquote_plus, urljoin, urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from app.models import ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata

ELEMENTARY_URL = "https://exam.kh.edu.tw/teaexam/index.jsp?cnt=board/board.jsp&now_page=2"
SPECIAL_URL = "https://exam.kh.edu.tw/special/index.jsp"
USER_AGENT = "Mozilla/5.0 (compatible; teacher-recruit-kaohsiung-mirror/1.0)"
CANONICAL_CATEGORY = "高雄市教師甄試"
SKIP_TOKENS = ("試教", "複試", "試場", "名單", "缺額", "簡章", "附件", "配置圖", "申請")


@dataclass(frozen=True)
class KaohsiungPaper:
    subject_name: str
    subject_code: str
    downloads: dict[str, str]


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href", "") or ""
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(unescape("".join(self._parts)).split())))
            self._href = ""
            self._parts = []


def _request_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            quote(parts.path, safe="/%"),
            quote(parts.query, safe="=&%"),
            parts.fragment,
        )
    )


def _links(html: str) -> list[tuple[str, str]]:
    parser = _AnchorParser()
    parser.feed(html)
    return parser.links


def _decoded_filename(url: str, label: str) -> str:
    path_name = Path(unquote(urlparse(url).path)).name
    return label.strip() or path_name


def _subject_code(subject_name: str) -> str:
    if subject_name == "國小教師聯合甄選":
        return "elementary"
    digest = hashlib.sha1(subject_name.encode("utf-8")).hexdigest()[:8]
    return f"subject-{digest}"


def parse_elementary_page(page_url: str, html: str) -> KaohsiungPaper | None:
    downloads: dict[str, str] = {}
    seen: set[str] = set()
    type_by_name = {
        "試題.zip": "question",
        "答案.zip": "answer",
        "正確答案.zip": "corrected_answer",
    }
    for href, label in _links(html):
        url = urljoin(page_url, href)
        file_name = _decoded_filename(url, label)
        if file_name in seen:
            continue
        seen.add(file_name)
        file_type = type_by_name.get(file_name)
        if file_type:
            downloads[file_type] = url
    if not downloads:
        return None
    return KaohsiungPaper(
        subject_name="國小教師聯合甄選", subject_code="elementary", downloads=downloads
    )


def _special_file_type_and_subject(file_name: str) -> tuple[str, str] | None:
    if any(token in file_name for token in SKIP_TOKENS):
        return None
    stem = re.sub(r"\.pdf$", "", file_name, flags=re.IGNORECASE)
    if stem.endswith("試題教育局"):
        return "question", stem.removesuffix("試題教育局")
    if stem.endswith("參考答案教育局"):
        return "answer", stem.removesuffix("參考答案教育局")
    if stem.startswith("正確答案-"):
        return "corrected_answer", stem.removeprefix("正確答案-")
    return None


def parse_special_page(page_url: str, html: str) -> list[KaohsiungPaper]:
    by_subject: dict[str, dict[str, str]] = {}
    seen: set[str] = set()
    for href, label in _links(html):
        url = urljoin(page_url, href)
        file_name = _decoded_filename(url, label)
        if file_name in seen:
            continue
        seen.add(file_name)
        parsed = _special_file_type_and_subject(file_name)
        if parsed is None:
            continue
        file_type, subject_name = parsed
        by_subject.setdefault(subject_name, {})[file_type] = url
    return [
        KaohsiungPaper(
            subject_name=subject_name, subject_code=_subject_code(subject_name), downloads=downloads
        )
        for subject_name, downloads in sorted(by_subject.items())
    ]


def _filename_from_content_disposition(value: str) -> str:
    match = re.search(r"filename\*?\s*=\s*(?:UTF-8'')?\"?([^\";]+)", value, flags=re.IGNORECASE)
    return unquote_plus(match.group(1).strip()) if match else ""


class KaohsiungTeacherRecruitClient:
    provider_id = "teacher_recruit_kaohsiung"

    def __init__(self, elementary_html: str | None = None, special_html: str | None = None) -> None:
        self.elementary_html = elementary_html
        self.special_html = special_html

    def _fetch_text(self, url: str) -> str:
        request = Request(_request_url(url), headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=60) as response:
            raw: bytes = response.read()
        for encoding in ("utf-8-sig", "utf-8", "big5", "cp950"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", "replace")

    def _elementary_html(self) -> str:
        if self.elementary_html is None:
            self.elementary_html = self._fetch_text(ELEMENTARY_URL)
        return self.elementary_html

    def _special_html(self) -> str:
        if self.special_html is None:
            self.special_html = self._fetch_text(SPECIAL_URL)
        return self.special_html

    def discover_available_years(self) -> list[int]:
        # Probe both source boards before syncing so a source outage preserves
        # the last known-good provider state instead of replacing it with failures.
        self._elementary_html()
        self._special_html()
        return [2026]

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        if year_ad != 2026:
            return []
        return [
            ExamOption(
                "teacher-recruit-kaohsiung-115-elementary", 2026, 115, "115學年度高雄市國小教師甄試"
            ),
            ExamOption(
                "teacher-recruit-kaohsiung-115-special",
                2026,
                115,
                "115學年度高雄市特殊教育教師甄試",
            ),
        ]

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        year_roc = year_ad - 1911
        scope = "special" if exam_code.endswith("-special") else "elementary"
        parsed_papers = (
            parse_special_page(SPECIAL_URL, self._special_html()) if scope == "special" else []
        )
        if scope == "elementary":
            elementary = parse_elementary_page(ELEMENTARY_URL, self._elementary_html())
            parsed_papers = [elementary] if elementary is not None else []
        papers = [
            ParsedPaper(
                category_raw=CANONICAL_CATEGORY,
                category_code=scope,
                subject_code=paper.subject_code,
                subject_name_raw=paper.subject_name,
                files=paper.downloads,
            )
            for paper in parsed_papers
        ]
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=year_roc,
            exam_name_raw=f"{year_roc}學年度{CANONICAL_CATEGORY}",
            attachments=[],
            papers=papers,
            provider_id=self.provider_id,
        )

    def head(self, url: str) -> ResponseMetadata:
        request = Request(_request_url(url), headers={"User-Agent": USER_AGENT}, method="HEAD")
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
        request = Request(_request_url(url), headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=120) as response:
            content_disposition = response.headers.get("Content-Disposition", "")
            return DownloadedFile(
                data=response.read(),
                content_type=response.headers.get("Content-Type", "application/octet-stream"),
                file_name=_filename_from_content_disposition(content_disposition)
                or Path(unquote(urlparse(url).path)).name,
            )
