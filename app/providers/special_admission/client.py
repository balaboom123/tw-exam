from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin

from app.models import ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata
from app.providers.http import Http

QUESTION_URL = "https://cis.ncu.edu.tw/EnableSys/admissionInfo/examInfo/question"
USER_AGENT = "Mozilla/5.0 (compatible; special-admission-mirror/1.0)"
_CATEGORY_NAME = "身心障礙學生升學大專校院甄試"
_SUBJECT_SLUGS = {
    "國文": "chinese",
    "英文": "english",
    "數學A": "math-a",
    "數學B": "math-b",
    "歷史": "history",
    "地理": "geography",
    "物理": "physics",
    "化學": "chemistry",
    "生物": "biology",
}


@dataclass
class _Cell:
    text_parts: list[str] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)

    @property
    def text(self) -> str:
        return _normalize_text(" ".join(self.text_parts))


class _TableParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.rows: list[list[_Cell]] = []
        self._row: list[_Cell] | None = None
        self._cell: _Cell | None = None
        self._href = ""
        self._anchor_text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._close_row()
            self._row = []
            return
        if tag in {"td", "th"} and self._row is not None:
            self._close_cell()
            self._cell = _Cell()
            return
        if tag == "a" and self._cell is not None:
            self._href = dict(attrs).get("href", "") or ""
            self._anchor_text_parts = []

    def handle_data(self, data: str) -> None:
        if self._cell is None:
            return
        self._cell.text_parts.append(data)
        if self._href:
            self._anchor_text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._cell is not None and self._href:
            label = _normalize_text(" ".join(self._anchor_text_parts))
            self._cell.links.append((label, urljoin(self.base_url, self._href)))
            self._href = ""
            self._anchor_text_parts = []
        elif tag in {"td", "th"}:
            self._close_cell()
        elif tag == "tr":
            self._close_row()

    def close(self) -> None:
        self._close_row()
        super().close()

    def _close_cell(self) -> None:
        if self._row is not None and self._cell is not None:
            self._row.append(self._cell)
        self._cell = None

    def _close_row(self) -> None:
        self._close_cell()
        if self._row is not None and self._row:
            self.rows.append(self._row)
        self._row = None


def _normalize_text(text: str) -> str:
    return " ".join(unescape(text).split())


def _question_url(year_roc: int) -> str:
    return f"{QUESTION_URL}?{urlencode({'year': str(year_roc)})}"


def _normalized_subject(subject: str) -> str:
    return subject.replace("數學(A)", "數學A").replace("數學(B)", "數學B").strip()


def parse_available_years(html: str) -> list[int]:
    years = [int(year) + 1911 for year in re.findall(r'<option\s+value="(?P<year>\d{3})"', html)]
    return sorted(dict.fromkeys(years), reverse=True)


def parse_question_page(html: str, base_url: str) -> list[ParsedPaper]:
    parser = _TableParser(base_url)
    parser.feed(html)
    parser.close()
    papers: list[ParsedPaper] = []
    for row in parser.rows:
        cells = [cell for cell in row if cell.text or cell.links]
        if len(cells) < 5:
            continue
        year_roc, school_track, group, subject = (cells[index].text for index in range(4))
        subject = _normalized_subject(subject)
        if not year_roc.isdigit() or school_track != "大學組" or group != "共同" or subject not in _SUBJECT_SLUGS:
            continue
        files: dict[str, str] = {}
        for label, url in cells[4].links:
            if "答案" in label:
                files["answer"] = url
            elif "試題" in label:
                files["question"] = url
        if "question" not in files:
            continue
        papers.append(
            ParsedPaper(
                category_raw=_CATEGORY_NAME,
                category_code="university-common",
                subject_code=_SUBJECT_SLUGS[subject],
                subject_name_raw=subject,
                files=files,
            )
        )
    return papers


class SpecialAdmissionClient:
    provider_id = "special_admission"

    def __init__(self) -> None:
        self._available_years_cache: tuple[int, ...] | None = None
        self.http = Http(self.provider_id, max_attempts=1, user_agent=USER_AGENT)

    def _fetch_text(self, url: str) -> str:
        return self.http.get_text(url, encoding="utf-8")

    def head(self, url: str) -> ResponseMetadata:
        return self.http.head(url)

    def download_file(self, url: str) -> DownloadedFile:
        return self.http.download(url, content_disposition_name=False)

    def _available_years(self) -> list[int]:
        if self._available_years_cache is None:
            self._available_years_cache = tuple(parse_available_years(self._fetch_text(QUESTION_URL)))
        return list(self._available_years_cache)

    def build_discovery_year_url(self, year_ad: int) -> str:
        if year_ad not in self._available_years():
            raise ValueError(f"Unknown special-admission year: {year_ad}")
        return _question_url(year_ad - 1911)

    def build_discovery_exam_url(self, exam_code: str, year_ad: int) -> str:
        expected_code = f"special-admission-{year_ad - 1911}"
        if exam_code != expected_code or year_ad not in self._available_years():
            raise ValueError(f"Unknown special-admission exam: {exam_code} ({year_ad})")
        return _question_url(year_ad - 1911)

    def discover_available_years(self) -> list[int]:
        return self._available_years()

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        year_roc = year_ad - 1911
        if year_ad not in self._available_years():
            return []
        return [
            ExamOption(
                code=f"special-admission-{year_roc}",
                year_ad=year_ad,
                year_roc=year_roc,
                label=f"{year_roc}學年度{_CATEGORY_NAME}",
            )
        ]

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        year_roc = year_ad - 1911
        page_url = self.build_discovery_exam_url(exam_code, year_ad)
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=year_roc,
            exam_name_raw=f"{year_roc}學年度{_CATEGORY_NAME}",
            attachments=[],
            papers=parse_question_page(self._fetch_text(page_url), page_url),
            provider_id=self.provider_id,
        )
