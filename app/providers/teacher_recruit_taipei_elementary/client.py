from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from app.models import ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata

USER_AGENT = "Mozilla/5.0 (compatible; teacher-recruit-taipei-elementary-mirror/1.0)"
CANONICAL_CATEGORY = "臺北市國小教師甄試"
ARTICLE_URLS_BY_YEAR = {
    2025: (
        "https://www.gov.taipei/News_Content.aspx?n=D0042A"
        "87C2F0270A&sms=78D644F2755ACCAA&s=0E5FFDCD602F05C2"
    ),
}
SUBJECT_CODES = {
    "基礎類科知能": "basic-category-knowledge",
    "普通科": "general",
    "英語科": "english",
    "體育科": "physical-education",
    "音樂科": "music",
    "視覺藝術科": "visual-arts",
    "輔導科": "counseling",
    "資訊科技科": "information-technology",
    "閩南語": "taiwanese-minnan",
    "特教科(身障)": "special-education-disability",
    "特教科(資優)": "special-education-gifted",
    "自然科": "science",
}


@dataclass(frozen=True)
class TaipeiElementaryDownload:
    subject_name: str
    subject_code: str
    file_type: str
    url: str


def _request_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            quote(parts.path),
            quote(parts.query, safe="=&%"),
            parts.fragment,
        )
    )


def _decode_base64_query_value(value: str) -> str:
    decoded = unquote(value)
    padding = "=" * (-len(decoded) % 4)
    return base64.b64decode(decoded + padding).decode("utf-8", "replace")


def _decode_download_name(url: str) -> str:
    parsed = urlparse(url)
    values = parse_qs(parsed.query)
    for key in ("n", "u"):
        encoded = values.get(key, [""])[0]
        if not encoded:
            continue
        decoded = _decode_base64_query_value(encoded)
        return Path(decoded).name
    return Path(unquote(parsed.path)).name


def _is_official_download_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc.lower() == "www-ws.gov.taipei" and parsed.path.lower().endswith(
        "/download.ashx"
    )


def _subject_name(file_name: str) -> str:
    value = re.sub(r"\.pdf$", "", file_name, flags=re.IGNORECASE)
    value = re.sub(r"^\d+(?:\.\d+)?", "", value)
    value = re.sub(r"[_\s-]*含答案$", "", value)
    return value.strip(" _-")


def _subject_code(subject_name: str) -> str:
    if subject_name in SUBJECT_CODES:
        return SUBJECT_CODES[subject_name]
    digest = hashlib.sha1(subject_name.encode("utf-8")).hexdigest()[:8]
    return f"subject-{digest}"


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


def parse_downloads(html: str, *, page_url: str) -> list[TaipeiElementaryDownload]:
    parser = _AnchorParser()
    parser.feed(html)
    downloads: list[TaipeiElementaryDownload] = []
    for href, _ in parser.links:
        url = urljoin(page_url, href)
        if not _is_official_download_url(url):
            continue
        file_name = _decode_download_name(url)
        if not file_name.lower().endswith(".pdf") or "含答案" not in file_name:
            continue
        subject_name = _subject_name(file_name)
        downloads.append(
            TaipeiElementaryDownload(
                subject_name=subject_name,
                subject_code=_subject_code(subject_name),
                file_type="question_answer",
                url=url,
            )
        )
    return downloads


class TaipeiElementaryRecruitClient:
    provider_id = "teacher_recruit_taipei_elementary"

    def __init__(
        self,
        article_urls_by_year: dict[int, str] | None = None,
        article_html_by_year: dict[int, str] | None = None,
    ) -> None:
        self.article_urls_by_year = article_urls_by_year or ARTICLE_URLS_BY_YEAR
        self.article_html_by_year = article_html_by_year or {}

    def _fetch_text(self, url: str) -> str:
        request = Request(_request_url(url), headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=60) as response:
            raw: bytes = response.read()
        return raw.decode("utf-8-sig", "replace")

    def _article_html(self, year_ad: int) -> str:
        if year_ad not in self.article_html_by_year:
            self.article_html_by_year[year_ad] = self._fetch_text(
                self.article_urls_by_year[year_ad]
            )
        return self.article_html_by_year[year_ad]

    def discover_available_years(self) -> list[int]:
        return sorted(self.article_urls_by_year, reverse=True)

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        if year_ad not in self.article_urls_by_year:
            return []
        year_roc = year_ad - 1911
        return [
            ExamOption(
                code=f"teacher-recruit-taipei-elementary-{year_roc}",
                year_ad=year_ad,
                year_roc=year_roc,
                label=f"{year_roc}學年度{CANONICAL_CATEGORY}",
            )
        ]

    def build_discovery_year_url(self, year_ad: int) -> str:
        return self.article_urls_by_year[year_ad]

    def build_discovery_exam_url(self, exam_code: str, year_ad: int) -> str:
        expected_code = f"teacher-recruit-taipei-elementary-{year_ad - 1911}"
        if exam_code != expected_code:
            raise ValueError(
                f"Unexpected Taipei elementary discovery exam code for {year_ad}: {exam_code}"
            )
        return self.article_urls_by_year[year_ad]

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        year_roc = year_ad - 1911
        papers_by_subject: dict[tuple[str, str], dict[str, str]] = {}
        page_url = self.article_urls_by_year[year_ad]
        for download in parse_downloads(self._article_html(year_ad), page_url=page_url):
            key = (download.subject_code, download.subject_name)
            papers_by_subject.setdefault(key, {})[download.file_type] = download.url
        papers = [
            ParsedPaper(
                category_raw=CANONICAL_CATEGORY,
                category_code=str(year_roc),
                subject_code=subject_code,
                subject_name_raw=subject_name,
                files=files,
            )
            for (subject_code, subject_name), files in sorted(papers_by_subject.items())
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
            return DownloadedFile(
                data=response.read(),
                content_type=response.headers.get("Content-Type", "application/octet-stream"),
                file_name=_decode_download_name(url),
            )
