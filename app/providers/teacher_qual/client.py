from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

from app.models import ExamAttachment, ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata
from app.providers.teacher_qual import archive as native_archive
from app.providers.teacher_qual.evidence import (
    extract_member,
    fallback_for,
    member_locator,
    parse_member_locator,
)
from app.source_material import SourceDate, SourceMaterial

BASE_URL = "https://tqa.rcpet.edu.tw/TEA_Exam/"
LISTING_URL = urljoin(BASE_URL, "TEA03.aspx")
USER_AGENT = "Mozilla/5.0 (compatible; teacher-qual-mirror/1.0)"
ALL_SUBJECT_NAME = "全部類科試題及參考答案"
YEAR_FIELD = "ctl00$ContentPlaceHolder1$schyy"
ORDER_FIELD = "ctl00$ContentPlaceHolder1$ddlOrder"
SUBJECT_FIELD = "ctl00$ContentPlaceHolder1$exid"
ORDER_LABELS = {
    "1": "第一次考試",
    "2": "第二次考試",
}


@dataclass(frozen=True)
class TeacherQualDownload:
    label: str
    url: str


@dataclass(frozen=True)
class TeacherQualPaperDownload:
    subject: str
    file_role: str
    file_format: str
    url: str


class _PaperCellParser(HTMLParser):
    """Read one official subject cell without borrowing a sibling's labels."""

    def __init__(self) -> None:
        super().__init__()
        self.downloads: list[TeacherQualPaperDownload] = []
        self._in_cell = False
        self._subject_parts: list[str] = []
        self._role_parts: list[str] = []
        self._subject_complete = False
        self._in_anchor = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "span" and re.fullmatch(
            r"ctl00_ContentPlaceHolder1_frmGrd_DataGrid_ctl\d+_frmGrd_sbid\d+",
            values.get("id") or "",
        ):
            self._in_cell = True
            self._subject_parts = []
            self._role_parts = []
            self._subject_complete = False
        elif self._in_cell and tag == "br":
            self._subject_complete = True
        elif self._in_cell and tag == "a":
            href = values.get("href") or ""
            if "ShowPicOut2.aspx" not in href:
                raise ValueError("Unexpected teacher qualification paper endpoint")
            role_label = "".join(_normalize_text("".join(self._role_parts)).split())
            roles = {"考題": "question", "參考答案": "answer", "範例題": "question_answer"}
            if role_label not in roles:
                raise ValueError(f"Unreviewed teacher qualification file role: {role_label}")
            subject = _normalize_text("".join(self._subject_parts))
            title = values.get("title") or ""
            format_match = re.fullmatch(r"下載(pdf|docx?)檔案", title, re.IGNORECASE)
            if not subject or format_match is None:
                raise ValueError("Teacher qualification paper lacks its subject or format")
            self.downloads.append(
                TeacherQualPaperDownload(
                    subject,
                    roles[role_label],
                    format_match.group(1).lower(),
                    urljoin(BASE_URL, href),
                )
            )
            self._role_parts = []
            self._in_anchor = True

    def handle_data(self, data: str) -> None:
        if self._in_cell and not self._in_anchor:
            (self._role_parts if self._subject_complete else self._subject_parts).append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._in_anchor = False
        if tag == "span":
            self._in_cell = False


def parse_paper_downloads(html: str) -> list[TeacherQualPaperDownload]:
    parser = _PaperCellParser()
    parser.feed(html)
    keys: set[tuple[str, str, str]] = set()
    for download in parser.downloads:
        key = (download.subject, download.file_role, download.file_format)
        if key in keys:
            raise ValueError(f"Duplicate teacher qualification subject/role/format: {key}")
        keys.add(key)
    return parser.downloads


class _SelectionSession:
    """Keep cookies and WebForms tokens together for one selector traversal."""

    def __init__(self, year_roc: int, order_code: str = "") -> None:
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self.year_roc = year_roc
        self.order_code = order_code
        self.html = self._fetch()
        self.select(YEAR_FIELD, f"{year_roc:03d}")
        if order_code:
            self.select(ORDER_FIELD, order_code)

    def _fetch(self, form: dict[str, str] | None = None) -> str:
        request = Request(
            LISTING_URL,
            data=urlencode(form).encode("utf-8") if form is not None else None,
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
        )
        with self.opener.open(request, timeout=60) as response:
            body: bytes = response.read()
            return body.decode("utf-8", "replace")

    def select(self, field: str, value: str) -> str:
        form = {
            **_hidden_fields(self.html),
            "__EVENTTARGET": field,
            "__EVENTARGUMENT": "",
            YEAR_FIELD: f"{self.year_roc:03d}",
            SUBJECT_FIELD: "",
        }
        if self.order_code and field != YEAR_FIELD:
            form[ORDER_FIELD] = self.order_code
        form[field] = value
        self.html = self._fetch(form)
        return self.html


def _normalize_text(text: str) -> str:
    return " ".join(unescape(text).split())


class _TokenParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tokens: list[tuple[str, str, str]] = []
        self._in_anchor = False
        self._href = ""
        self._anchor_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        self._in_anchor = True
        self._href = dict(attrs).get("href", "") or ""
        self._anchor_parts = []

    def handle_data(self, data: str) -> None:
        text = _normalize_text(data)
        if not text:
            return
        if self._in_anchor:
            self._anchor_parts.append(text)
        else:
            self.tokens.append(("text", text, ""))

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._in_anchor:
            return
        self.tokens.append(("link", _normalize_text(" ".join(self._anchor_parts)), self._href))
        self._in_anchor = False
        self._href = ""
        self._anchor_parts = []


class _SelectParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.selects: dict[str, list[tuple[str, str]]] = {}
        self._select_name = ""
        self._option_value: str | None = None
        self._option_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "select":
            self._select_name = attrs_dict.get("name") or attrs_dict.get("id") or ""
            return
        if tag == "option" and self._select_name:
            self._option_value = attrs_dict.get("value", "") or ""
            self._option_parts = []

    def handle_data(self, data: str) -> None:
        if self._option_value is not None:
            self._option_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "option" and self._select_name and self._option_value is not None:
            label = _normalize_text("".join(self._option_parts))
            self.selects.setdefault(self._select_name, []).append((self._option_value, label))
            self._option_value = None
            self._option_parts = []
            return
        if tag == "select":
            self._select_name = ""


def _select_options(html: str, name: str) -> list[tuple[str, str]]:
    parser = _SelectParser()
    parser.feed(html)
    return [(value, label) for value, label in parser.selects.get(name, []) if value]


def parse_available_years(html: str) -> list[int]:
    years: list[int] = []
    for value, _label in _select_options(html, YEAR_FIELD):
        if value.isdigit():
            years.append(int(value) + 1911)
    return sorted(dict.fromkeys(years), reverse=True)


def parse_order_options(html: str) -> list[tuple[str, str]]:
    return _select_options(html, ORDER_FIELD)


def parse_subject_options(html: str) -> list[tuple[str, str]]:
    return _select_options(html, SUBJECT_FIELD)


def _is_year_section_heading(token_text: str, *, year_roc: int) -> bool:
    return token_text in {
        f"{year_roc}年試題及參考答案",
        f"{year_roc:03d}年試題及參考答案",
        f"{year_roc}年僅有範例題",
        f"{year_roc:03d}年僅有範例題",
    }


def parse_downloads(html: str, *, year_roc: int) -> list[TeacherQualDownload]:
    parser = _TokenParser()
    parser.feed(html)
    downloads: list[TeacherQualDownload] = []
    context: list[str] = []
    in_year_section = False
    for token_type, token_text, token_href in parser.tokens:
        if token_type == "text":
            if _is_year_section_heading(token_text, year_roc=year_roc):
                in_year_section = True
                context = [token_text]
                continue
            if token_text == "樣卷":
                in_year_section = False
                context = []
                continue
            if in_year_section:
                context.append(token_text)
                context = context[-4:]
            continue
        if not in_year_section or "ShowPicOut2.aspx" not in token_href:
            continue
        label = _normalize_text(" ".join(context))
        if not label:
            label = ALL_SUBJECT_NAME
        downloads.append(TeacherQualDownload(label=label, url=urljoin(BASE_URL, token_href)))
    return downloads


def _hidden_fields(html: str) -> dict[str, str]:
    return dict(
        re.findall(
            r'<input type="hidden" name="([^"]+)" id="[^"]*" value="([^"]*)"',
            html,
        )
    )


def listing_material(html: str, year_roc: int) -> SourceMaterial:
    parser = _TokenParser()
    parser.feed(html)
    headings = {
        text
        for kind, text, _ in parser.tokens
        if kind == "text" and _is_year_section_heading(text, year_roc=year_roc)
    }
    if len(headings) != 1:
        raise ValueError("Teacher qualification listing lacks one matching material heading")
    heading = headings.pop()
    sample = "僅有範例題" in heading
    return SourceMaterial(
        "sample" if sample else "administered",
        SourceDate("undated", None) if sample else SourceDate("exam_year", year_roc + 1911),
        LISTING_URL,
        heading,
    )


class TeacherQualClient:
    provider_id = "teacher_qual"

    def __init__(self) -> None:
        self._downloads: dict[str, DownloadedFile] = {}
        self._native_payloads: dict[str, dict[tuple[str, ...], bytes]] = {}

    def _fetch_text(self, url: str) -> str:
        request = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=60) as response:
            raw: bytes = response.read()
        for encoding in ("utf-8-sig", "utf-16", "utf-8", "big5", "cp950"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", "replace")

    def _year_selection_html(self, year_roc: int) -> str:
        return _SelectionSession(year_roc).html

    def category_listings(self, year_roc: int, order_code: str = "") -> list[tuple[str, str, str]]:
        session = _SelectionSession(year_roc, order_code)
        categories = parse_subject_options(session.html)
        if len({value for value, _ in categories}) != len(categories):
            raise ValueError("Teacher qualification selector repeats a category value")
        return [(value, label, session.select(SUBJECT_FIELD, value)) for value, label in categories]

    def discover_available_years(self) -> list[int]:
        return parse_available_years(self._fetch_text(LISTING_URL))

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        if year_ad not in self.discover_available_years():
            return []
        year_roc = year_ad - 1911
        order_options = parse_order_options(self._year_selection_html(year_roc))
        if order_options:
            return [
                ExamOption(
                    code=f"teacher-qual-{year_roc}-{order_code}",
                    year_ad=year_ad,
                    year_roc=year_roc,
                    label=f"{year_roc}年教師資格考試{order_label}歷屆試題及參考答案",
                )
                for order_code, order_label in order_options
            ]
        return [
            ExamOption(
                code=f"teacher-qual-{year_roc}",
                year_ad=year_ad,
                year_roc=year_roc,
                label=f"{year_roc}年教師資格考試歷屆試題及參考答案",
            )
        ]

    def build_discovery_year_url(self, year_ad: int) -> str:
        return LISTING_URL

    def build_discovery_exam_url(self, exam_code: str, year_ad: int) -> str:
        year_roc = year_ad - 1911
        match = re.fullmatch(r"teacher-qual-(\d+)(?:-(1|2))?", exam_code)
        if match is None or int(match.group(1)) != year_roc:
            raise ValueError(
                f"Unexpected teacher qualification discovery exam for {year_ad}: {exam_code}"
            )
        order_code = match.group(2)
        if (year_ad == 2019 and order_code not in ORDER_LABELS) or (
            year_ad != 2019 and order_code is not None
        ):
            raise ValueError(
                f"Unexpected teacher qualification discovery order for {year_ad}: {exam_code}"
            )
        return LISTING_URL

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        self.build_discovery_exam_url(exam_code, year_ad)
        year_roc = year_ad - 1911
        match = re.fullmatch(r"teacher-qual-\d+(?:-(\d+))?", exam_code)
        order_code = match.group(1) if match else ""
        order_label = ORDER_LABELS.get(order_code, "")
        listings = self.category_listings(year_roc, order_code)
        combined = [html for code, _label, html in listings if code == "99"]
        if len(combined) != 1:
            raise ValueError("Teacher qualification selector lacks its original combined archive")
        material = listing_material(combined[0], year_roc)
        originals = parse_downloads(combined[0], year_roc=year_roc)
        if len(originals) != 1:
            raise ValueError("Teacher qualification listing lacks one combined source archive")
        papers: list[ParsedPaper] = []
        conflicting_references = []
        for code, label, html in listings:
            if code == "99":
                continue
            downloads = parse_paper_downloads(html)
            if downloads and listing_material(html, year_roc) != material:
                raise ValueError("Teacher qualification categories disagree on material nature")
            subjects: dict[tuple[str, str], ParsedPaper] = {}
            for download in downloads:
                source_url = download.url
                fallback = fallback_for(year_roc, code, download.subject, download.file_role)
                if fallback is not None:
                    observed = self.download_file(source_url)
                    checksum = hashlib.sha256(observed.data).hexdigest()
                    if checksum == fallback.conflicting_checksum:
                        source_url = member_locator(originals[0].url, fallback)
                        # Verify the alternative before any paper reaches the mirror.
                        self.download_file(source_url)
                        conflicting_references.append(
                            ExamAttachment(
                                fallback.evidence_label,
                                download.file_role,
                                download.url,
                                source_material=SourceMaterial(
                                    material.kind,
                                    SourceDate("exam_year", fallback.conflicting_year_roc + 1911),
                                    fallback.evidence_url,
                                    fallback.evidence_label,
                                    fallback.reason,
                                ),
                            )
                        )
                    elif checksum != fallback.member_checksum:
                        raise ValueError(
                            "Teacher qualification conflicting source changed; review required"
                        )
                key = (download.subject, download.file_format)
                if key not in subjects:
                    digest = hashlib.sha256("|".join(key).encode("utf-8")).hexdigest()[:16]
                    subjects[key] = ParsedPaper(
                        category_raw=label,
                        category_code=f"{year_roc}-{code}",
                        subject_code=f"subject-{digest}",
                        subject_name_raw=download.subject,
                        files={},
                        source_material=material,
                    )
                subjects[key].files[download.file_role] = source_url
            papers.extend(subjects.values())
        if not papers:
            raise ValueError("Teacher qualification category pages contain no paper downloads")
        self._append_native_papers(exam_code, originals[0].url, papers, material)
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=year_roc,
            exam_name_raw=f"{year_roc}年教師資格考試{order_label}歷屆試題及參考答案",
            attachments=[
                ExamAttachment(
                    originals[0].label, "source_archive", originals[0].url, source_material=material
                ),
                *conflicting_references,
            ],
            papers=papers,
            provider_id=self.provider_id,
            source_material=material,
        )

    def _append_native_papers(
        self, exam_code: str, source_url: str, papers: list[ParsedPaper], material: SourceMaterial
    ) -> None:
        archive = native_archive.native_archive_for(exam_code)
        if archive is None:
            return
        self._archive_payloads(source_url, archive)
        observed = {
            (
                paper.category_code,
                paper.subject_name_raw,
                role,
                hashlib.sha256(self.download_file(url).data).hexdigest(),
            )
            for paper in papers
            for role, url in paper.files.items()
        }
        year = exam_code.split("-")[2]
        for member in archive.members:
            for context in member.contexts:
                code = f"{year}-{context.selector_value}"
                key = (code, context.subject, context.file_role, member.checksum)
                if key in observed:
                    continue
                papers.append(
                    ParsedPaper(
                        category_raw=context.category_label,
                        category_code=code,
                        subject_code=member.subject_code,
                        subject_name_raw=member.subject_label(context),
                        files={
                            context.file_role: native_archive.member_locator(
                                source_url, archive, member
                            )
                        },
                        source_material=material,
                    )
                )
                observed.add(key)

    def _archive_payloads(
        self, source_url: str, archive: native_archive.NativeArchive
    ) -> dict[tuple[str, ...], bytes]:
        if archive.checksum not in self._native_payloads:
            self._native_payloads[archive.checksum] = native_archive.extract_native_members(
                archive, self.download_file(source_url).data
            )
        return self._native_payloads[archive.checksum]

    def head(self, url: str) -> ResponseMetadata:
        if urlparse(url).fragment:
            file = self.download_file(url)
            return ResponseMetadata(url, 200, len(file.data), file.content_type)
        request = Request(url, headers={"User-Agent": USER_AGENT}, method="HEAD")
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
        if urlparse(url).fragment:
            if "member-chain" in parse_qs(urlparse(url).fragment):
                source_url, archive, member = native_archive.parse_member_locator(url)
                payload = self._archive_payloads(source_url, archive)[member.path_chain]
                suffix = Path(member.path_chain[-1]).suffix.lower()
                types = {
                    ".pdf": "application/pdf",
                    ".doc": "application/msword",
                    ".docx": (
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    ),
                }
                return DownloadedFile(payload, types[suffix], Path(member.path_chain[-1]).name)
            archive_url, fact = parse_member_locator(url)
            return DownloadedFile(
                extract_member(self.download_file(archive_url).data, fact),
                "application/pdf",
                Path(fact.member_path).name,
            )
        if url not in self._downloads:
            self._downloads[url] = self._download_url(url)
        return self._downloads[url]

    def _download_url(self, url: str) -> DownloadedFile:
        request = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=120) as response:
            return DownloadedFile(
                data=response.read(),
                content_type=response.headers.get("Content-Type", "application/octet-stream"),
                file_name=Path(unquote(urlparse(url).path)).name,
            )
