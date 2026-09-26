from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin

from app.models import ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata
from app.providers.http import Http

YEAR_URL = "https://svc.tabf.org.tw/115post02//Paper/Year"
PRIOR_YEAR_URL = "https://svc.tabf.org.tw/114post01//Paper/Year"
YEAR_URLS = (YEAR_URL, PRIOR_YEAR_URL)
USER_AGENT = "Mozilla/5.0 (compatible; post-recruit-mirror/1.0)"
_CATEGORY_NAME = "中華郵政職階人員甄試"
_YEAR_LABEL_RE = re.compile(r"(?P<roc_year>\d{3})年$")
_TITLE_RE = re.compile(r"^(?P<roc_year>\d{3})年-(?P<title>.+)$")


@dataclass(frozen=True)
class PostRecruitYear:
    year_ad: int
    url: str
    listing_url: str

    @property
    def year_roc(self) -> int:
        return self.year_ad - 1911

    @property
    def code(self) -> str:
        return f"post-recruit-{self.year_roc}"


class _TokenParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.tokens: list[tuple[str, str, str]] = []
        self._href = ""
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href", "") or ""
            self._text_parts = []

    def handle_data(self, data: str) -> None:
        text = _normalize_text(data)
        if not text:
            return
        if self._href:
            self._text_parts.append(text)
        else:
            self.tokens.append(("text", text, ""))

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._href:
            return
        label = _normalize_text(" ".join(self._text_parts))
        if label:
            self.tokens.append(("link", label, urljoin(self.base_url, self._href)))
        self._href = ""
        self._text_parts = []


def _normalize_text(text: str) -> str:
    return " ".join(unescape(text).split())


def _clean_title(text: str) -> str:
    title = _TITLE_RE.match(text).group("title") if _TITLE_RE.match(text) else text
    return re.sub(r"\([A-Z]\d+\)", "", title).strip(" -；")


def parse_year_page(html: str, base_url: str) -> list[PostRecruitYear]:
    parser = _TokenParser(base_url)
    parser.feed(html)
    years: list[PostRecruitYear] = []
    seen: set[int] = set()
    for token_type, label, url in parser.tokens:
        if token_type != "link":
            continue
        match = _YEAR_LABEL_RE.match(label)
        if match is None:
            continue
        year_roc = int(match.group("roc_year"))
        if year_roc in seen:
            continue
        seen.add(year_roc)
        years.append(PostRecruitYear(year_ad=year_roc + 1911, url=url, listing_url=base_url))
    return sorted(years, key=lambda year: year.year_ad, reverse=True)


def parse_history_page(html: str, base_url: str) -> list[ParsedPaper]:
    parser = _TokenParser(base_url)
    parser.feed(html)
    papers: list[ParsedPaper] = []
    current_title = ""
    for token_type, text, url in parser.tokens:
        if token_type == "text" and _TITLE_RE.match(text):
            current_title = _clean_title(text)
            continue
        if token_type != "link" or "/_File/Download/" not in url:
            continue
        index = len(papers) + 1
        label = text
        papers.append(
            ParsedPaper(
                category_raw=_CATEGORY_NAME,
                category_code="post-recruit",
                subject_code=f"paper-{index:03d}",
                subject_name_raw=f"{current_title} {label}".strip(),
                files={"question": url},
            )
        )
    return papers


class PostRecruitClient:
    provider_id = "post_recruit"

    def __init__(self) -> None:
        self.http = Http(self.provider_id, max_attempts=1, user_agent=USER_AGENT)

    def _fetch_text(self, url: str) -> str:
        return self.http.get_text(url, encoding="utf-8")

    def head(self, url: str) -> ResponseMetadata:
        return self.http.head(url)

    def download_file(self, url: str) -> DownloadedFile:
        return self.http.download(url, content_disposition_name=False)

    def _years(self) -> list[PostRecruitYear]:
        years_by_ad: dict[int, PostRecruitYear] = {}
        for listing_url in YEAR_URLS:
            for year in parse_year_page(self._fetch_text(listing_url), listing_url):
                years_by_ad.setdefault(year.year_ad, year)
        return sorted(years_by_ad.values(), key=lambda year: year.year_ad, reverse=True)

    def build_discovery_year_url(self, year_ad: int) -> str:
        return next(year.listing_url for year in self._years() if year.year_ad == year_ad)

    def build_discovery_exam_url(self, exam_code: str, year_ad: int) -> str:
        return next(year.url for year in self._years() if year.code == exam_code and year.year_ad == year_ad)

    def discover_available_years(self) -> list[int]:
        return [year.year_ad for year in self._years()]

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        return [
            ExamOption(code=year.code, year_ad=year.year_ad, year_roc=year.year_roc, label=f"{year.year_roc}年{_CATEGORY_NAME}")
            for year in self._years()
            if year.year_ad == year_ad
        ]

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        year = next(item for item in self._years() if item.code == exam_code and item.year_ad == year_ad)
        return SourceExamPage(
            source_exam_id=year.code,
            year_ad=year.year_ad,
            year_roc=year.year_roc,
            exam_name_raw=f"{year.year_roc}年{_CATEGORY_NAME}",
            attachments=[],
            papers=parse_history_page(self._fetch_text(year.url), year.url),
            provider_id=self.provider_id,
        )
