from __future__ import annotations

import hashlib
import re

from app.models import ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata
from app.providers.http import Http
from app.providers.taipei_downloads import PaperDownload as TaipeiJuniorDownload
from app.providers.taipei_downloads import download_name as _decode_download_name
from app.providers.taipei_downloads import group_paper_files, official_pdf_downloads
from app.providers.taipei_downloads import request_url as _request_url
from app.providers.teacher_recruit_taipei_junior.evidence import (
    CANONICAL_CATEGORY,
    annual_exams,
)

USER_AGENT = "Mozilla/5.0 (compatible; teacher-recruit-taipei-junior-mirror/1.0)"
ARTICLE_URLS_BY_YEAR = {exam.year_roc + 1911: exam.paper_article_url for exam in annual_exams()}
SUBJECT_CODES = {
    "國文": "guo-wen",
    "英文": "ying-wen",
    "數學": "shu-xue",
    "理化": "li-hua",
    "生物": "sheng-wu",
    "地理": "di-li",
    "體育": "ti-yu",
    "健康教育": "jian-kang-jiao-yu",
    "公民與社會": "gong-min-yu-she-hui",
    "本土語(閩南語)": "ben-tu-yu-min-nan-yu",
}


def _subject_name(file_name: str) -> str:
    value = re.sub(r"\.pdf$", "", file_name, flags=re.IGNORECASE)
    value = re.sub(r"(試題|答案)(?:\s*[-－]?\s*完稿)?$", "", value)
    return value.strip()


def _subject_code(subject_name: str) -> str:
    if subject_name in SUBJECT_CODES:
        return SUBJECT_CODES[subject_name]
    digest = hashlib.sha1(subject_name.encode("utf-8")).hexdigest()[:8]
    return f"subject-{digest}"


def parse_downloads(html: str, *, page_url: str) -> list[TaipeiJuniorDownload]:
    downloads: list[TaipeiJuniorDownload] = []
    for file_name, url in official_pdf_downloads(html, page_url=page_url):
        if "疑義" in file_name or ("試題" not in file_name and "答案" not in file_name):
            continue
        subject_name = _subject_name(file_name)
        downloads.append(
            TaipeiJuniorDownload(
                subject_name=subject_name,
                subject_code=_subject_code(subject_name),
                file_type="answer" if "答案" in file_name else "question",
                url=url,
            )
        )
    return downloads


class TaipeiJuniorRecruitClient:
    provider_id = "teacher_recruit_taipei_junior"

    def __init__(
        self,
        article_urls_by_year: dict[int, str] | None = None,
        article_html_by_year: dict[int, str] | None = None,
    ) -> None:
        self.article_urls_by_year = article_urls_by_year or ARTICLE_URLS_BY_YEAR
        self.article_html_by_year = article_html_by_year or {}
        self.http = Http(self.provider_id, max_attempts=1, user_agent=USER_AGENT)

    def _fetch_text(self, url: str) -> str:
        return self.http.get_text(_request_url(url), encoding="utf-8-sig")

    def _article_html(self, year_ad: int) -> str:
        if year_ad not in self.article_html_by_year:
            self.article_html_by_year[year_ad] = self._fetch_text(
                self.article_urls_by_year[year_ad]
            )
        return self.article_html_by_year[year_ad]

    def pdf_downloads(self, page_url: str) -> list[tuple[str, str]]:
        return list(official_pdf_downloads(self._fetch_text(page_url), page_url=page_url))

    def discover_available_years(self) -> list[int]:
        return sorted(self.article_urls_by_year, reverse=True)

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        if year_ad not in self.article_urls_by_year:
            return []
        year_roc = year_ad - 1911
        return [
            ExamOption(
                code=f"teacher-recruit-taipei-junior-{year_roc}",
                year_ad=year_ad,
                year_roc=year_roc,
                label=f"{year_roc}學年度{CANONICAL_CATEGORY}",
            )
        ]

    def build_discovery_year_url(self, year_ad: int) -> str:
        return self.article_urls_by_year[year_ad]

    def build_discovery_exam_url(self, exam_code: str, year_ad: int) -> str:
        expected_code = f"teacher-recruit-taipei-junior-{year_ad - 1911}"
        if exam_code != expected_code:
            raise ValueError(
                f"Unexpected Taipei junior discovery exam code for {year_ad}: {exam_code}"
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
        return DownloadedFile(
            data=download.data,
            content_type=download.content_type,
            file_name=_decode_download_name(url),
        )
