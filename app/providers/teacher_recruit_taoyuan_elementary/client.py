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

ANSWER_URL = "https://elementary.tyc.edu.tw/web/answer.aspx?openExternalBrowser=1"
USER_AGENT = "Mozilla/5.0 (compatible; teacher-recruit-taoyuan-elementary-mirror/1.0)"
CANONICAL_CATEGORY = "桃園市國小教師甄試"
SKIP_TOKENS = ("疑義", "釋疑", "成績", "錄取", "試場", "分配", "簡章")


@dataclass(frozen=True)
class TaoyuanPaper:
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


def _subject_code(subject_name: str) -> str:
    digest = hashlib.sha1(subject_name.encode("utf-8")).hexdigest()[:8]
    return f"subject-{digest}"


def _file_type(label: str) -> str | None:
    if any(token in label for token in SKIP_TOKENS):
        return None
    if "正確答案" in label:
        return "corrected_answer"
    if "建議答案" in label:
        return "answer"
    if "試題" in label:
        return "question"
    return None


def _subject_name(label: str) -> str:
    value = re.sub(r"\.pdf$", "", label.strip(), flags=re.IGNORECASE)
    value = re.sub(r"^\d{3}桃", "", value)
    value = re.sub(r"[-_](?:試題|建議答案|正確答案)$", "", value)
    return value.strip()


def parse_answer_page(page_url: str, html: str) -> list[TaoyuanPaper]:
    parser = _AnchorParser()
    parser.feed(html)
    by_subject: dict[str, dict[str, str]] = {}
    for href, label in parser.links:
        file_type = _file_type(label)
        if file_type is None or "download_file.aspx" not in href:
            continue
        subject_name = _subject_name(label)
        by_subject.setdefault(subject_name, {}).setdefault(file_type, urljoin(page_url, href))
    return [
        TaoyuanPaper(
            subject_name=subject_name, subject_code=_subject_code(subject_name), downloads=downloads
        )
        for subject_name, downloads in sorted(by_subject.items())
    ]


def _filename_from_content_disposition(value: str) -> str:
    match = re.search(r"filename\*?\s*=\s*(?:UTF-8'')?\"?([^\";]+)", value, flags=re.IGNORECASE)
    return unquote_plus(match.group(1).strip()) if match else ""


class TaoyuanElementaryRecruitClient:
    provider_id = "teacher_recruit_taoyuan_elementary"

    def __init__(self, answer_html: str | None = None) -> None:
        self.answer_html = answer_html

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

    def _answer_html(self) -> str:
        if self.answer_html is None:
            self.answer_html = self._fetch_text(ANSWER_URL)
        return self.answer_html

    def discover_available_years(self) -> list[int]:
        years = {int(match) + 1911 for match in re.findall(r"(\d{3})桃", self._answer_html())}
        return sorted(years, reverse=True)

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        if year_ad not in self.discover_available_years():
            return []
        year_roc = year_ad - 1911
        return [
            ExamOption(
                code=f"teacher-recruit-taoyuan-elementary-{year_roc}",
                year_ad=year_ad,
                year_roc=year_roc,
                label=f"{year_roc}學年度{CANONICAL_CATEGORY}",
            )
        ]

    def build_discovery_year_url(self, year_ad: int) -> str:
        return ANSWER_URL

    def build_discovery_exam_url(self, exam_code: str, year_ad: int) -> str:
        expected_code = f"teacher-recruit-taoyuan-elementary-{year_ad - 1911}"
        if exam_code != expected_code:
            raise ValueError(f"Unexpected Taoyuan discovery exam code for {year_ad}: {exam_code}")
        return ANSWER_URL

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        year_roc = year_ad - 1911
        papers = [
            ParsedPaper(
                category_raw=CANONICAL_CATEGORY,
                category_code=str(year_roc),
                subject_code=paper.subject_code,
                subject_name_raw=paper.subject_name,
                files=paper.downloads,
            )
            for paper in parse_answer_page(ANSWER_URL, self._answer_html())
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
