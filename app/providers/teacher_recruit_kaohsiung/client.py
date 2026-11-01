from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import (
    parse_qs,
    quote,
    unquote,
    unquote_plus,
    urlencode,
    urlparse,
    urlsplit,
    urlunsplit,
)

from app.models import ExamOption, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata
from app.providers.http import Http, links
from app.providers.teacher_recruit_kaohsiung.archives import attachment_notice
from app.providers.teacher_recruit_kaohsiung.evidence import AnnualExam, NativeNotice, annual_exams

USER_AGENT = "Mozilla/5.0 (compatible; teacher-recruit-kaohsiung-mirror/1.0)"
_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


def _text(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


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


def _filename_from_content_disposition(value: str) -> str:
    match = re.search(r"filename\*?\s*=\s*(?:UTF-8'')?\"?([^\";]+)", value, flags=re.IGNORECASE)
    return unquote_plus(match.group(1).strip()) if match else ""


def search_url(exam: AnnualExam, page: int = 0) -> str:
    department, keyword = (
        ("P0", "教師聯合甄選") if exam.scope == "elementary" else ("T0", "教師甄選")
    )
    return "https://bulletin.kh.edu.tw/_RSS_ajax.php?" + urlencode(
        {
            "Target": "B",
            "Y": exam.year_roc + 1911,
            "M": "",
            "sort": "2",
            "keyword": keyword,
            "department": department,
            "page": page,
        }
    )


def _owns_notice(exam: AnnualExam, title: str) -> bool:
    text = _text(title)
    if exam.scope == "special":
        return f"高雄市{exam.year_roc}學年度" in text and "特殊教育班教師甄選" in text
    # One native title uses CJK radical glyphs in 國⺠⼩. Department and
    # city/year/programme context establish ownership without a filename alias.
    return (
        text.startswith((f"公告本市{exam.year_roc}年度", f"公告高雄市{exam.year_roc}年度"))
        and "教師聯合甄選" in text
    )


class _NoticeText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[tuple[str, str]] = []
        self.sections: dict[str, list[str]] = {"BHead": [], "BSubject": []}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _VOID_TAGS:
            self.stack.append((tag, dict(attrs).get("id") or ""))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        for _tag, region in self.stack:
            if region in self.sections:
                self.sections[region].append(data)


def validate_notice_html(notice: NativeNotice, html: str) -> None:
    parser = _NoticeText()
    parser.feed(html)
    head = _text("".join(parser.sections["BHead"]))
    title = _text("".join(parser.sections["BSubject"]))
    published = re.search(r"發布日期:(\d+)年(\d+)月(\d+)日", head)
    if published is None:
        raise ValueError("Kaohsiung native notice lacks its publication date")
    year, month, day = map(int, published.groups())
    if (
        f"公告編號:{notice.notice_id}" not in head
        or title != _text(notice.title)
        or date(year + 1911, month, day).isoformat() != notice.publication_date
    ):
        raise ValueError("Kaohsiung native notice identity, title or date changed")
    observed: dict[str, str] = {}
    for label, url in links(html, notice.url):
        if "attachment.php" not in url:
            continue
        url = url.strip()
        if attachment_notice(url) != notice.notice_id:
            raise ValueError("Kaohsiung native notice links a competing attachment owner")
        name = _text(label)
        if url in observed and observed[url] != name:
            raise ValueError("Kaohsiung native attachment has competing labels")
        observed[url] = name
    expected = {a.url: _text(a.label) for a in notice.attachments}
    if observed != expected:
        raise ValueError("Kaohsiung native notice attachment population changed; review required")


class KaohsiungTeacherRecruitClient:
    provider_id = "teacher_recruit_kaohsiung"

    def __init__(self, html_pages: Mapping[str, str] | None = None) -> None:
        self._pages = dict(html_pages or {})
        self.http = Http(self.provider_id, max_attempts=1, user_agent=USER_AGENT)

    def _fetch_text(self, url: str) -> str:
        return self.http.get_text(_request_url(url)).removeprefix("\ufeff")

    def _html(self, url: str) -> str:
        if url not in self._pages:
            self._pages[url] = self._fetch_text(url)
        return self._pages[url]

    def _validate_listing(self, exam: AnnualExam) -> None:
        observed = {}
        all_ids = set()
        for page in range(20):
            url = search_url(exam, page)
            html = self._html(url)
            count = re.search(r"共\s*(\d+)\s*筆資料", html)
            if count is None:
                raise ValueError("Kaohsiung official search lacks its complete population count")
            total = int(count.group(1))
            page_ids = []
            for title, href in links(html, url):
                if "view.php?sn=" not in href:
                    continue
                parts = urlsplit(href.strip())
                query = parse_qs(parts.query)
                if (
                    parts.netloc != "bulletin.kh.edu.tw"
                    or parts.path != "/view.php"
                    or set(query) != {"sn"}
                    or len(query["sn"]) != 1
                ):
                    raise ValueError("Invalid Kaohsiung official search notice")
                notice_id = query["sn"][0]
                if re.fullmatch(r"B[0-9]{9}", notice_id) is None or notice_id in all_ids:
                    raise ValueError("Duplicate or invalid Kaohsiung search notice")
                all_ids.add(notice_id)
                page_ids.append(notice_id)
                if _owns_notice(exam, title):
                    observed[notice_id] = _text(title)
            if len(all_ids) == total:
                break
            if not page_ids or len(all_ids) > total:
                raise ValueError("Kaohsiung official search pagination is incomplete")
        else:
            raise ValueError("Kaohsiung official search exceeds its reviewed pagination budget")
        expected = {n.notice_id: _text(n.title) for n in exam.notices}
        if observed != expected:
            raise ValueError(
                "Kaohsiung annual programme notice population changed; review required"
            )

    def discover_available_years(self) -> list[int]:
        for exam in annual_exams():
            self._validate_listing(exam)
        return sorted({exam.year_roc + 1911 for exam in annual_exams()})

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        exams = [e for e in annual_exams() if e.year_roc + 1911 == year_ad]
        for exam in exams:
            self._validate_listing(exam)
        return [ExamOption(e.source_exam_id, year_ad, e.year_roc, e.programme_name) for e in exams]

    def build_discovery_year_url(self, year_ad: int) -> str:
        exam = next((e for e in annual_exams() if e.year_roc + 1911 == year_ad), None)
        if exam is None:
            raise ValueError("Unreviewed Kaohsiung annual source scope")
        return search_url(exam)

    def build_discovery_exam_url(self, exam_code: str, year_ad: int) -> str:
        exam = self.known_exam(exam_code, year_ad)
        file = next(f for f in exam.files if f.file_type == "question")
        return next(n.url for n in exam.notices if n.notice_id == file.notice_id)

    @staticmethod
    def known_exam(exam_code: str, year_ad: int) -> AnnualExam:
        exam = next(
            (
                e
                for e in annual_exams()
                if e.source_exam_id == exam_code and e.year_roc + 1911 == year_ad
            ),
            None,
        )
        if exam is None:
            raise ValueError("Unreviewed Kaohsiung native event or source year")
        return exam

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        exam = self.known_exam(exam_code, year_ad)
        self._validate_listing(exam)
        for notice in exam.notices:
            validate_notice_html(notice, self._html(notice.url))
        return exam.source_page()

    def head(self, url: str) -> ResponseMetadata:
        return self.http.head(_request_url(url))

    def download_file(self, url: str) -> DownloadedFile:
        data, headers, _status = self.http._request(_request_url(url), timeout=120)
        file_name = (
            _filename_from_content_disposition(headers.get("Content-Disposition", ""))
            or Path(unquote(urlparse(url).path)).name
        )
        return DownloadedFile(
            data, headers.get("Content-Type", "application/octet-stream"), file_name
        )
