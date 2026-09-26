from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import (
    parse_qs,
    quote,
    unquote,
    unquote_plus,
    urljoin,
    urlparse,
    urlsplit,
    urlunsplit,
)
from urllib.request import Request, urlopen

from app.models import ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata

BASE_URL = "https://qa115-tse-cl.twrecruit.com.tw"
USER_AGENT = "Mozilla/5.0 (compatible; teacher-recruit-central-alliance-mirror/1.0)"
CANONICAL_CATEGORY = "中區策略聯盟教師甄試"
LEVELS = {
    "kindergarten": ("幼兒園", "A"),
    "elementary": ("國小", "B"),
    "junior": ("國中", "C"),
}


@dataclass(frozen=True)
class CentralAlliancePaper:
    level_code: str
    level_name: str
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


def _subject_code(level_code: str, subject_name: str) -> str:
    digest = hashlib.sha1(subject_name.encode("utf-8")).hexdigest()[:8]
    return f"{level_code}-{digest}"


def _subject_from_filename(file_name: str) -> str:
    value = Path(unquote(file_name)).name
    value = re.sub(r"\.pdf$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^115中策_", "", value)
    value = re.sub(r"_試題$", "", value)
    value = re.sub(r"試題$", "", value)
    value = re.sub(r"答案$", "", value)
    return value.strip(" _-")


def _file_type_from_url(url: str) -> str | None:
    value = parse_qs(urlparse(url).query).get("type", [""])[0]
    return {
        "question": "question",
        "referenceanswer": "answer",
        "finalanswer": "corrected_answer",
    }.get(value)


def parse_subject_page(
    page_url: str, level_code: str, level_name: str, html: str
) -> list[CentralAlliancePaper]:
    by_subject: dict[str, dict[str, str]] = {}
    for href, label in _links(html):
        url = urljoin(page_url, href)
        file_type = _file_type_from_url(url)
        if file_type not in {"question", "answer"}:
            continue
        subject_name = _subject_from_filename(label)
        by_subject.setdefault(subject_name, {})[file_type] = url
    return [
        CentralAlliancePaper(
            level_code=level_code,
            level_name=level_name,
            subject_name=subject_name,
            subject_code=_subject_code(level_code, subject_name),
            downloads=downloads,
        )
        for subject_name, downloads in sorted(by_subject.items())
    ]


def parse_final_answer_page(page_url: str, html: str) -> dict[str, str]:
    answers: dict[str, str] = {}
    for href, label in _links(html):
        url = urljoin(page_url, href)
        if _file_type_from_url(url) != "corrected_answer":
            continue
        answers[_subject_from_filename(label)] = url
    return answers


def _filename_from_content_disposition(value: str) -> str:
    match = re.search(r"filename\*?\s*=\s*(?:UTF-8'')?\"?([^\";]+)", value, flags=re.IGNORECASE)
    return unquote_plus(match.group(1).strip()) if match else ""


class CentralAllianceRecruitClient:
    provider_id = "teacher_recruit_central_alliance"

    def __init__(
        self,
        subject_html_by_level: dict[str, str] | None = None,
        final_html_by_level: dict[str, str] | None = None,
    ) -> None:
        self.subject_html_by_level = subject_html_by_level or {}
        self.final_html_by_level = final_html_by_level or {}

    def _subject_url(self, level_code: str) -> str:
        return f"{BASE_URL}/Subject/news.php?cate={LEVELS[level_code][1]}"

    def _final_url(self, level_code: str) -> str:
        return f"{BASE_URL}/Ans2/news.php?cate={LEVELS[level_code][1]}"

    def _fetch_text(self, url: str) -> str:
        request = Request(_request_url(url), headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=60) as response:
            raw: bytes = response.read()
        return raw.decode("utf-8-sig", "replace")

    def _subject_html(self, level_code: str) -> str:
        if level_code not in self.subject_html_by_level:
            self.subject_html_by_level[level_code] = self._fetch_text(self._subject_url(level_code))
        return self.subject_html_by_level[level_code]

    def _final_html(self, level_code: str) -> str:
        if level_code not in self.final_html_by_level:
            self.final_html_by_level[level_code] = self._fetch_text(self._final_url(level_code))
        return self.final_html_by_level[level_code]

    def discover_available_years(self) -> list[int]:
        return [2026]

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        if year_ad != 2026:
            return []
        return [
            ExamOption(
                code=f"teacher-recruit-central-alliance-115-{level_code}",
                year_ad=2026,
                year_roc=115,
                label=f"115學年度{CANONICAL_CATEGORY}_{level_name}",
            )
            for level_code, (level_name, _cate) in LEVELS.items()
        ]

    def build_discovery_year_url(self, year_ad: int) -> str:
        if year_ad != 2026:
            raise ValueError(f"Unexpected Central Alliance discovery year: {year_ad}")
        return f"{BASE_URL}/Subject/news.php"

    def build_discovery_exam_url(self, exam_code: str, year_ad: int) -> str:
        expected_prefix = f"teacher-recruit-central-alliance-{year_ad - 1911}-"
        if year_ad != 2026 or not exam_code.startswith(expected_prefix):
            raise ValueError(
                f"Unexpected Central Alliance discovery exam for {year_ad}: {exam_code}"
            )
        level_code = exam_code.removeprefix(expected_prefix)
        if level_code not in LEVELS:
            raise ValueError(f"Unexpected Central Alliance discovery level: {level_code}")
        return self._subject_url(level_code)

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        year_roc = year_ad - 1911
        level_code = exam_code.rsplit("-", 1)[-1]
        level_name = LEVELS[level_code][0]
        papers = parse_subject_page(
            self._subject_url(level_code), level_code, level_name, self._subject_html(level_code)
        )
        final_answers = parse_final_answer_page(
            self._final_url(level_code), self._final_html(level_code)
        )
        parsed = []
        for paper in papers:
            files = dict(paper.downloads)
            if paper.subject_name in final_answers:
                files["corrected_answer"] = final_answers[paper.subject_name]
            parsed.append(
                ParsedPaper(
                    category_raw=CANONICAL_CATEGORY,
                    category_code=level_code,
                    subject_code=paper.subject_code,
                    subject_name_raw=f"{paper.level_name}-{paper.subject_name}",
                    files=files,
                )
            )
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=year_roc,
            exam_name_raw=f"{year_roc}學年度{CANONICAL_CATEGORY}",
            attachments=[],
            papers=parsed,
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
