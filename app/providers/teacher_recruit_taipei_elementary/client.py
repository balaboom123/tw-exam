from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qs, urlsplit

from app.models import ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata
from app.providers.http import Http, links
from app.providers.taipei_downloads import PaperDownload as TaipeiElementaryDownload
from app.providers.taipei_downloads import download_name as _decode_download_name
from app.providers.taipei_downloads import group_paper_files, official_pdf_downloads
from app.providers.taipei_downloads import request_url as _request_url
from app.providers.teacher_recruit_taipei_elementary.evidence import annual_exams

USER_AGENT = "Mozilla/5.0 (compatible; teacher-recruit-taipei-elementary-mirror/1.0)"
CANONICAL_CATEGORY = "臺北市國小教師甄試"
ARTICLE_URLS_BY_YEAR = {exam.year_roc + 1911: exam.paper_article_url for exam in annual_exams()}
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
    "閩南語科": "taiwanese-minnan",
    "特教科(身障)": "special-education-disability",
    "特教科(資優)": "special-education-gifted",
    "自然科": "science",
}


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


def parse_downloads(html: str, *, page_url: str) -> list[TaipeiElementaryDownload]:
    downloads: list[TaipeiElementaryDownload] = []
    for file_name, url in official_pdf_downloads(html, page_url=page_url):
        if "含答案" not in file_name:
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
        self.http = Http(self.provider_id, max_attempts=1, user_agent=USER_AGENT)
        self.notice_html_by_url: dict[str, str] = {}

    def _fetch_text(self, url: str) -> str:
        return self.http.get_text(_request_url(url), encoding="utf-8-sig")

    def _article_html(self, year_ad: int) -> str:
        if year_ad not in self.article_html_by_year:
            self.article_html_by_year[year_ad] = self._fetch_text(
                self.article_urls_by_year[year_ad]
            )
        return self.article_html_by_year[year_ad]

    def pdf_downloads(self, page_url: str) -> list[tuple[str, str]]:
        year = next((y for y, url in self.article_urls_by_year.items() if url == page_url), None)
        if year is not None:
            html = self._article_html(year)
        else:
            if page_url not in self.notice_html_by_url:
                self.notice_html_by_url[page_url] = self._fetch_text(page_url)
            html = self.notice_html_by_url[page_url]
        downloads = list(official_pdf_downloads(html, page_url=page_url))
        if page_url == "https://primary.tp.edu.tw/Home/Index_Ann":
            for _title, url in links(html, page_url):
                parts = urlsplit(url)
                query = parse_qs(parts.query)
                name = query.get("file", [""])[0]
                if (
                    parts.scheme == "https"
                    and parts.netloc == "primary.tp.edu.tw"
                    and parts.path == "/Home/AnnDownload"
                    and name.endswith(".pdf")
                    and "類科" in name
                    and "錄取名額" in name
                ):
                    downloads.append((name, url))
        return list(dict.fromkeys(downloads))

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
        page_url = self.build_discovery_exam_url(exam_code, year_ad)
        papers_by_subject = group_paper_files(
            parse_downloads(self._article_html(year_ad), page_url=page_url)
        )
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
        return self.http.head(_request_url(url))

    def download_file(self, url: str) -> DownloadedFile:
        download = self.http.download(_request_url(url), content_disposition_name=False)
        name = (
            parse_qs(urlsplit(url).query).get("file", [""])[0]
            if urlsplit(url).netloc == "primary.tp.edu.tw"
            else _decode_download_name(url)
        )
        return DownloadedFile(
            data=download.data,
            content_type=download.content_type,
            file_name=name,
        )
