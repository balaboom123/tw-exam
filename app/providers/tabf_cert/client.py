from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

from app.models import ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata
from app.providers.http import Http

HISTORY_INDEX_URL = "https://www.tabf.org.tw/LicenseHistoryExam.aspx?PHID=424"
HISTORY_URL_TEMPLATE = "https://www.tabf.org.tw/LicenseHistoryExam.aspx?PHID={phid}"
USER_AGENT = "Mozilla/5.0 (compatible; tabf-cert-mirror/1.0)"

_PHID_RE = re.compile(r"PHID=(\d+)", re.IGNORECASE)
_ROC_YEAR_RE = re.compile(r"(\d{2,3})\s*年")


@dataclass(frozen=True)
class TabfHistoryLink:
    phid: str
    label: str
    year_ad: int
    year_roc: int

    @property
    def code(self) -> str:
        return f"tabf-cert-phid-{self.phid}"


@dataclass(frozen=True)
class TabfPdfLink:
    subject: str
    url: str


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._href = ""
        self._parts: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href") or ""
        if href:
            self._href = unescape(href)
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            label = " ".join(unescape("".join(self._parts)).split())
            self.links.append((self._href, label))
            self._href = ""
            self._parts = []


def _parse_links(html: str) -> list[tuple[str, str]]:
    parser = _AnchorParser()
    parser.feed(html)
    return parser.links


def _year_from_label(label: str, default_year_ad: int) -> tuple[int, int]:
    match = _ROC_YEAR_RE.search(label)
    if match:
        year_roc = int(match.group(1))
        return year_roc + 1911, year_roc
    return default_year_ad, default_year_ad - 1911


def parse_tabf_history_links(html: str, default_year_ad: int) -> list[TabfHistoryLink]:
    links: list[TabfHistoryLink] = []
    seen: set[str] = set()
    for href, label in _parse_links(html):
        match = _PHID_RE.search(href)
        if match is None:
            continue
        phid = match.group(1)
        if phid in seen:
            continue
        seen.add(phid)
        year_ad, year_roc = _year_from_label(label, default_year_ad)
        links.append(
            TabfHistoryLink(
                phid=phid,
                label=label or f"PHID {phid}",
                year_ad=year_ad,
                year_roc=year_roc,
            )
        )
    return links


def parse_tabf_pdf_links(html: str) -> list[TabfPdfLink]:
    pdfs: list[TabfPdfLink] = []
    seen: set[str] = set()
    for href, label in _parse_links(html):
        absolute_url = urljoin(HISTORY_INDEX_URL, href)
        if ".pdf" not in absolute_url.lower():
            continue
        if "ExamHistoryEdit" not in absolute_url:
            continue
        if absolute_url in seen:
            continue
        seen.add(absolute_url)
        subject = label or Path(unquote(urlparse(absolute_url).path)).stem
        pdfs.append(TabfPdfLink(subject=subject, url=absolute_url))
    return pdfs


def classify_tabf_certificate(subjects: list[str]) -> tuple[str, str]:
    joined = " ".join(subjects)
    rules = [
        (("信託",), "trust-business", "信託業業務人員"),
        (("銀行內部控制", "內部控制", "內控"), "bank-internal-control", "銀行內部控制與內部稽核"),
        (("理財規劃",), "financial-planning", "理財規劃人員"),
        (("初階外匯", "外匯"), "fx-junior", "初階外匯人員"),
        (("進階外匯", "高階外匯"), "fx-senior", "進階外匯人員"),
        (("授信", "徵信"), "credit", "授信人員"),
        (("風險管理",), "risk-management", "風險管理基本能力"),
        (("債權", "催收"), "debt-collection", "債權委外催收人員"),
        (("數位金融",), "digital-finance", "數位金融"),
        (("防制洗錢", "洗錢防制", "打擊資恐"), "aml", "防制洗錢與打擊資恐專業人員"),
        (("金融科技", "FinTech"), "fintech", "金融科技力知識檢定"),
        (("資產評價",), "asset-valuation", "資產評價人員"),
    ]
    for keywords, slug, name in rules:
        if any(keyword in joined for keyword in keywords):
            return slug, name
    return "bank-internal-control", subjects[0] if subjects else "金融研訓院證照測驗"


def _file_type_for_subject(subject: str) -> str:
    return "answer" if any(token in subject for token in ("答案", "解答")) else "question"


class TabfCertClient:
    provider_id = "tabf_cert"

    def __init__(self) -> None:
        self.http = Http(self.provider_id, max_attempts=1, user_agent=USER_AGENT)

    def _fetch_text(self, url: str) -> str:
        return self.http.get_text(url, encoding="utf-8")

    def head(self, url: str) -> ResponseMetadata:
        return self.http.head(url)

    def download_file(self, url: str) -> DownloadedFile:
        return self.http.download(
            url, filename_fallback="download.pdf", content_disposition_name=False
        )

    def _history_links(self, default_year_ad: int) -> list[TabfHistoryLink]:
        return parse_tabf_history_links(self._fetch_text(HISTORY_INDEX_URL), default_year_ad)

    def discover_available_years(self) -> list[int]:
        from datetime import datetime

        current_year = datetime.now().year
        return sorted({link.year_ad for link in self._history_links(current_year)}, reverse=True)

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        return [
            ExamOption(
                code=link.code,
                year_ad=link.year_ad,
                year_roc=link.year_roc,
                label=link.label,
            )
            for link in self._history_links(year_ad)
            if link.year_ad == year_ad
        ]

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        match = _PHID_RE.search(exam_code)
        if match is None:
            match = re.search(r"phid-(\d+)", exam_code)
        if match is None:
            raise ValueError(f"TABF PHID not found in exam code: {exam_code}")
        phid = match.group(1)
        html = self._fetch_text(HISTORY_URL_TEMPLATE.format(phid=phid))
        pdfs = parse_tabf_pdf_links(html)
        if not pdfs:
            raise ValueError(f"TABF history page has no PDF links: PHID {phid}")

        label = next(
            (link.label for link in parse_tabf_history_links(html, year_ad) if link.phid == phid),
            "",
        )
        resolved_year_ad, year_roc = _year_from_label(label, year_ad)
        slug, certificate_name = classify_tabf_certificate([pdf.subject for pdf in pdfs])
        source_exam_id = f"tabf-cert-{slug}-{resolved_year_ad}-phid-{phid}"
        papers = [
            ParsedPaper(
                category_raw=certificate_name,
                category_code=phid,
                subject_code=f"{index:02d}",
                subject_name_raw=pdf.subject,
                files={_file_type_for_subject(pdf.subject): pdf.url},
            )
            for index, pdf in enumerate(pdfs, start=1)
        ]
        return SourceExamPage(
            source_exam_id=source_exam_id,
            year_ad=resolved_year_ad,
            year_roc=year_roc,
            exam_name_raw=f"{certificate_name} {label or f'PHID {phid}'}",
            attachments=[],
            papers=papers,
            provider_id=self.provider_id,
        )
