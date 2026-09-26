from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from app.models import ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata
from app.providers.http import Http

LISTING_URL = "https://www.tcte.edu.tw/index.php?mod=TVETest%2Fdown_exam4y"
USER_AGENT = "Mozilla/5.0 (compatible; tcte-tve-mirror/1.0)"
_YEAR_PAGE_RE = re.compile(r"/EXAM/(?P<roc_year>\d{2,3})_4y/?(?:\?|$)")
_ONCLICK_RE = re.compile(r"location\.href=['\"](?P<href>[^'\"]+)['\"]")
_TCTE_CATEGORY_NAME = "四技二專統一入學測驗"
_COMMON_SUBJECT_SLUGS = {
    "國文科": "chinese",
    "英文科": "english",
    "數學(A)": "math-a",
    "數學(B)": "math-b",
    "數學(C)": "math-c",
}
_HISTORICAL_GROUPS = {
    "工程與管理類工程組": ("09", "工程與管理類工程組"),
    "工程與管理類管理組": ("10", "工程與管理類管理組"),
    "語文類英文組": ("20", "語文類英文組"),
    "語文類日文組": ("21", "語文類日文組"),
    "土木建築": ("07", "土木建築類"),
    "工業設計": ("08", "工業設計類"),
    "海事水產": ("13", "海事水產類"),
    "商業設計": ("15", "商業設計類"),
    "機械": ("01", "機械類"),
    "汽車": ("02", "汽車類"),
    "電機": ("03", "電機類"),
    "電子": ("04", "電子類"),
    "化工": ("05", "化工類"),
    "衛生": ("06", "衛生類"),
    "護理": ("11", "護理類"),
    "食品": ("12", "食品類"),
    "商業": ("14", "商業類"),
    "幼保": ("16", "幼保類"),
    "美容": ("17", "美容類"),
    "家政": ("18", "家政類"),
    "農業": ("19", "農業類"),
    "餐旅": ("22", "餐旅類"),
}
_HISTORICAL_YEAR_RE = re.compile(r"/0?(?P<roc_year>90|91)_4y/")


@dataclass(frozen=True)
class TcteYearPage:
    year_ad: int
    url: str

    @property
    def year_roc(self) -> int:
        return self.year_ad - 1911

    @property
    def code(self) -> str:
        return f"tcte-tve-{self.year_roc}"


@dataclass
class _Cell:
    text_parts: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    link_labels: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return _normalize_text(" ".join(self.text_parts))


class _LinkParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href", "") or ""
        if href:
            self.links.append(urljoin(self.base_url, href))


class _TableParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.rows: list[list[_Cell]] = []
        self._row: list[_Cell] | None = None
        self._cell: _Cell | None = None
        self._link_label_parts: list[str] | None = None
        self._table_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._table_depth += 1
            return
        if tag == "tr" and self._table_depth == 1:
            self._close_row()
            self._row = []
            return
        if tag in {"td", "th"} and self._row is not None and self._table_depth == 1:
            self._close_cell()
            self._cell = _Cell()
            return
        if tag == "a" and self._cell is not None:
            self._finish_link()
            href = dict(attrs).get("href", "") or ""
            if href:
                self._cell.links.append(urljoin(self.base_url, href))
                self._cell.link_labels.append("")
                self._link_label_parts = []
            return
        if tag == "input" and self._cell is not None:
            onclick = dict(attrs).get("onclick", "") or ""
            match = _ONCLICK_RE.search(onclick)
            if match is not None:
                self._cell.links.append(urljoin(self.base_url, match.group("href")))
                self._cell.link_labels.append("")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.text_parts.append(data)
            if self._link_label_parts is not None:
                self._link_label_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            self._table_depth = max(0, self._table_depth - 1)
        elif tag == "a":
            self._finish_link()
        elif tag in {"td", "th"} and self._table_depth == 1:
            self._close_cell()
        elif tag == "tr" and self._table_depth == 1:
            self._close_row()

    def close(self) -> None:
        self._close_row()
        super().close()

    def _finish_link(self) -> None:
        if self._cell is not None and self._link_label_parts is not None:
            self._cell.link_labels[-1] = _normalize_text(" ".join(self._link_label_parts))
        self._link_label_parts = None

    def _close_cell(self) -> None:
        self._finish_link()
        if self._row is not None and self._cell is not None:
            self._row.append(self._cell)
        self._cell = None

    def _close_row(self) -> None:
        self._close_cell()
        if self._row is not None and self._row:
            self.rows.append(self._row)
        self._row = None


class _HistoricalCellParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.cells: list[_Cell] = []
        self._stack: list[_Cell] = []
        self._link_label_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "td":
            self._finish_link()
            self._stack.append(_Cell())
            return
        if tag == "a" and self._stack:
            self._finish_link()
            href = dict(attrs).get("href", "") or ""
            if href:
                self._stack[-1].links.append(urljoin(self.base_url, href))
                self._stack[-1].link_labels.append("")
                self._link_label_parts = []

    def handle_data(self, data: str) -> None:
        if self._stack:
            self._stack[-1].text_parts.append(data)
            if self._link_label_parts is not None:
                self._link_label_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._finish_link()
        elif tag == "td" and self._stack:
            self._finish_link()
            self.cells.append(self._stack.pop())

    def close(self) -> None:
        self._finish_link()
        while self._stack:
            self.cells.append(self._stack.pop())
        super().close()

    def _finish_link(self) -> None:
        if self._stack and self._link_label_parts is not None:
            self._stack[-1].link_labels[-1] = _normalize_text(" ".join(self._link_label_parts))
        self._link_label_parts = None


def _normalize_text(text: str) -> str:
    return " ".join(unescape(text).split())


def _plain_text_from_html(html: str) -> str:
    return _normalize_text(re.sub(r"<[^>]+>", " ", html))


def _category_code(group: str) -> str:
    if group == "共同科目":
        return "common"
    match = re.match(r"(?P<code>\d{2})", group)
    if match is not None:
        return match.group("code")
    return "group-" + group.encode("utf-8").hex()[:8]


def _subject_slug(subject: str) -> str:
    if subject in _COMMON_SUBJECT_SLUGS:
        return _COMMON_SUBJECT_SLUGS[subject]
    if "一" in subject:
        return "professional-1"
    if "二" in subject:
        return "professional-2"
    ascii_slug = re.sub(r"[^0-9A-Za-z]+", "-", subject).strip("-").lower()
    if ascii_slug:
        return ascii_slug
    return "subject-" + subject.encode("utf-8").hex()[:12]


def parse_listing_page(html: str) -> list[TcteYearPage]:
    parser = _LinkParser(LISTING_URL)
    parser.feed(html)
    pages: list[TcteYearPage] = []
    seen: set[int] = set()
    for url in parser.links:
        match = _YEAR_PAGE_RE.search(url)
        if match is None:
            continue
        year_roc = int(match.group("roc_year"))
        if year_roc in seen:
            continue
        seen.add(year_roc)
        pages.append(TcteYearPage(year_ad=year_roc + 1911, url=url.split("?")[0].rstrip("/")))
    return sorted(pages, key=lambda page: page.year_ad, reverse=True)


_MATH_VARIANT_RE = re.compile(r"數學\s*[（(]\s*([ABC])\s*[）)]")


def _math_variant(label: str) -> str:
    match = _MATH_VARIANT_RE.search(_normalize_text(label))
    return f"數學({match.group(1).upper()})" if match else ""


def _cell_link_label(cell: _Cell, index: int) -> str:
    return cell.link_labels[index] if index < len(cell.link_labels) else ""


def _paper_links(
    question_cell: _Cell, answer_cell: _Cell, base_subject: str
) -> list[tuple[str, str, str | None]]:
    """Pair old anchor-based paper links with answer links.

    The 2002–2005 pages use anchors instead of the newer JavaScript inputs.
    Some years publish one shared mathematics question with three variant
    answer links, while 2005 publishes three matching question/answer links.
    """
    question_links = list(enumerate(question_cell.links))
    answer_links = list(enumerate(answer_cell.links))
    if not question_links:
        return []

    question_variants = {
        _math_variant(_cell_link_label(question_cell, index)): index
        for index, _url in question_links
        if _math_variant(_cell_link_label(question_cell, index))
    }
    answer_variants = {
        _math_variant(_cell_link_label(answer_cell, index)): index
        for index, _url in answer_links
        if _math_variant(_cell_link_label(answer_cell, index))
    }
    variants = list(dict.fromkeys([*question_variants, *answer_variants]))
    if not variants:
        question_url = question_links[0][1]
        answer_url = answer_links[0][1] if answer_links else None
        return [(base_subject, question_url, answer_url)]

    paired: list[tuple[str, str, str | None]] = []
    for variant in variants:
        question_index = question_variants.get(variant, question_links[0][0])
        answer_index = answer_variants.get(variant)
        answer_url = answer_links[answer_index][1] if answer_index is not None else None
        paired.append((variant, question_cell.links[question_index], answer_url))
    return paired


def _historical_group(text: str) -> tuple[str, str] | None:
    for marker, value in _HISTORICAL_GROUPS.items():
        if marker in text:
            return value
    return None


def _historical_professional_part(text: str) -> int | None:
    if "專業科目一" in text or "專一" in text:
        return 1
    if "專業科目二" in text or "專二" in text:
        return 2
    return None


def _historical_question_files(urls: list[str]) -> dict[str, str]:
    if len(urls) == 1:
        return {"question": urls[0]}
    return {f"question_page_{index:02d}": url for index, url in enumerate(urls, start=1)}


def _historical_common_subject(text: str) -> tuple[str, str] | None:
    if "國文" in text:
        return "chinese", "國文科"
    if "英文" in text:
        return "english", "英文科"
    if "數學" in text:
        return "math", "數學科"
    return None


def parse_historical_year_page(html: str, base_url: str, year_roc: int) -> list[ParsedPaper]:
    parser = _HistoricalCellParser(base_url)
    parser.feed(html)
    parser.close()
    grouped: dict[tuple[str, str, str], list[str]] = {}
    answer_url = ""

    for cell in parser.cells:
        cell_text = cell.text
        for index, url in enumerate(cell.links):
            label = _cell_link_label(cell, index)
            lower_path = urlparse(url).path.lower()
            if year_roc == 90 and lower_path.endswith("/answer.htm"):
                answer_url = url
                continue
            if year_roc == 91 and lower_path.endswith("answer-new.pdf"):
                answer_url = url
                continue
            if not lower_path.endswith((".pdf", ".jpg", ".jpeg", ".png")):
                continue

            common = _historical_common_subject(label) if "共同科" in cell_text else None
            if common is None and "共同科" in cell_text:
                common = _historical_common_subject(cell_text)
            if common is not None:
                subject_code, subject_name = common
                key = ("common", subject_code, f"共同科目 {subject_name}")
                grouped.setdefault(key, []).append(url)
                continue

            group = _historical_group(cell_text)
            part = _historical_professional_part(label) or _historical_professional_part(cell_text)
            if group is None or part is None:
                continue
            category_code, group_name = group
            subject_code = f"professional-{part}"
            numeral = "一" if part == 1 else "二"
            subject_name = f"{category_code}{group_name} 專業科目({numeral})"
            grouped.setdefault((category_code, subject_code, subject_name), []).append(url)

    papers = [
        ParsedPaper(
            category_raw=_TCTE_CATEGORY_NAME,
            category_code=category_code,
            subject_code=subject_code,
            subject_name_raw=subject_name,
            files=_historical_question_files(urls),
        )
        for (category_code, subject_code, subject_name), urls in grouped.items()
    ]
    if answer_url:
        papers.append(
            ParsedPaper(
                category_raw=_TCTE_CATEGORY_NAME,
                category_code="common",
                subject_code="all",
                subject_name_raw="各科標準答案",
                files={"answer_table" if year_roc == 90 else "all_answers": answer_url},
            )
        )
    return papers


def parse_year_page(html: str, base_url: str) -> list[ParsedPaper]:
    historical_match = _HISTORICAL_YEAR_RE.search(base_url)
    if historical_match is not None:
        return parse_historical_year_page(html, base_url, int(historical_match.group("roc_year")))
    parser = _TableParser(base_url)
    parser.feed(html)
    parser.close()
    papers: list[ParsedPaper] = []
    current_group = ""
    for row in parser.rows:
        cells = [cell for cell in row if cell.text or cell.links]
        if len(cells) < 3:
            continue
        first_text = cells[0].text
        has_group = first_text == "共同科目" or re.match(r"^\d{2}", first_text) is not None
        if has_group and len(cells) >= 4:
            current_group = first_text
            subject, question_cell, answer_cell = cells[1], cells[2], cells[3]
        else:
            subject, question_cell, answer_cell = cells[0], cells[1], cells[2]
        if not current_group or not question_cell.links:
            continue
        base_subject = subject.text
        code = _category_code(current_group)
        for subject_name, question_url, answer_url in _paper_links(
            question_cell, answer_cell, base_subject
        ):
            files = {"question": question_url}
            if answer_url:
                files["answer"] = answer_url
            papers.append(
                ParsedPaper(
                    category_raw=_TCTE_CATEGORY_NAME,
                    category_code=code,
                    subject_code=_subject_slug(subject_name),
                    subject_name_raw=f"{current_group} {subject_name}",
                    files=files,
                )
            )
    return papers


class TcteTveClient:
    provider_id = "tcte_tve"

    def __init__(self) -> None:
        self._year_pages_cache: tuple[TcteYearPage, ...] | None = None
        self.http = Http(self.provider_id, max_attempts=1, user_agent=USER_AGENT)

    def _fetch_text(self, url: str) -> str:
        return self.http.get_text(url, encoding="utf-8")

    def head(self, url: str) -> ResponseMetadata:
        return self.http.head(url)

    def download_file(self, url: str) -> DownloadedFile:
        return self.http.download(url, content_disposition_name=False)

    def _year_pages(self) -> list[TcteYearPage]:
        if self._year_pages_cache is None:
            self._year_pages_cache = tuple(parse_listing_page(self._fetch_text(LISTING_URL)))
        return list(self._year_pages_cache)

    def build_discovery_year_url(self, year_ad: int) -> str:
        if not any(page.year_ad == year_ad for page in self._year_pages()):
            raise ValueError(f"Unknown TCTE TVE year: {year_ad}")
        return LISTING_URL

    def build_discovery_exam_url(self, exam_code: str, year_ad: int) -> str:
        try:
            page = next(
                page
                for page in self._year_pages()
                if page.code == exam_code and page.year_ad == year_ad
            )
        except StopIteration as exc:
            raise ValueError(f"Unknown TCTE TVE exam: {exam_code} ({year_ad})") from exc
        return page.url + "/"

    def discover_available_years(self) -> list[int]:
        return [page.year_ad for page in self._year_pages()]

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        return [
            ExamOption(
                code=page.code,
                year_ad=page.year_ad,
                year_roc=page.year_roc,
                label=f"{page.year_roc}學年度四技二專統一入學測驗",
            )
            for page in self._year_pages()
            if page.year_ad == year_ad
        ]

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        year_page = next(
            page
            for page in self._year_pages()
            if page.code == exam_code and page.year_ad == year_ad
        )
        exam_name = f"{year_page.year_roc}學年度四技二專統一入學測驗"
        return SourceExamPage(
            source_exam_id=year_page.code,
            year_ad=year_ad,
            year_roc=year_page.year_roc,
            exam_name_raw=exam_name,
            attachments=[],
            papers=parse_year_page(self._fetch_text(year_page.url + "/"), year_page.url + "/"),
            provider_id=self.provider_id,
        )
