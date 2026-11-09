from __future__ import annotations

import hashlib
import unicodedata

from app.models import ExamOption, SourceExamPage
from app.providers.base import (
    DownloadedFile,
    ResponseMetadata,
    SourceProvider,
    UnreviewedSourceFile,
)
from app.providers.teacher_recruit_taipei_junior.client import TaipeiJuniorRecruitClient
from app.providers.teacher_recruit_taipei_junior.evidence import (
    AnnualExam,
    annual_exams,
    project_native_page,
)


class TaipeiJuniorRecruitProvider(SourceProvider):
    provider_id = "teacher_recruit_taipei_junior"
    refresh_files_on_sync = True
    download_attachments_on_sync = True
    max_concurrency = 1

    def __init__(self, client: TaipeiJuniorRecruitClient | None = None) -> None:
        self.client = client or TaipeiJuniorRecruitClient()
        self._downloads: dict[str, DownloadedFile] = {}

    def discover_available_years(self) -> list[int]:
        return self.client.discover_available_years()

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        return self.client.discover_exams(year_ad)

    def build_discovery_year_url(self, year_ad: int) -> str:
        return self.client.build_discovery_year_url(year_ad)

    def build_discovery_exam_url(self, exam_code: str, year_ad: int) -> str:
        return self.client.build_discovery_exam_url(exam_code, year_ad)

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        page = self.client.fetch_exam_page(exam_code, year_ad)
        exam = next((e for e in annual_exams() if e.source_exam_id == exam_code), None)
        if exam is None:
            raise UnreviewedSourceFile(
                "Taipei junior annual recruitment requires native programme review",
                url=f"{self.build_discovery_year_url(year_ad)}#{exam_code}",
            )
        self._validate_document_notices(exam)
        checksums = {
            url: hashlib.sha256(self.download_file(url).data).hexdigest()
            for paper in page.papers
            for url in paper.files.values()
        }
        for file in exam.resources:
            checksums[file.source_url] = hashlib.sha256(
                self.download_file(file.source_url).data
            ).hexdigest()
        projected = project_native_page(page, checksums)
        projected.attachments = exam.attachments()
        return projected

    def _validate_document_notices(self, exam: AnnualExam) -> None:
        rule_urls = {d.source_url for d in exam.documents if d.document_id == "rule"}
        review_urls = {f.source_url for f in exam.files if f.file_type == "corrected_answer"}
        review_urls.update(d.source_url for d in exam.documents if d.document_id == "vacancy")
        for notice, expected in (
            (exam.rule_notice_url, rule_urls),
            (exam.review_notice_url, review_urls),
        ):
            observed = set()
            for name, url in self.client.pdf_downloads(notice):
                name = "".join(unicodedata.normalize("NFKC", name).split())
                if notice == exam.review_notice_url and (
                    "初試錄取與備取名單" in name or "複試場地公告" in name
                ):
                    continue
                observed.add(url)
            if observed != expected:
                raise UnreviewedSourceFile(
                    "Taipei junior rule or answer-review notice changed; review required"
                )

    def reviewed_file_checksum(self, url: str) -> str:
        downloaded = self._downloads.get(url)
        return hashlib.sha256(downloaded.data).hexdigest() if downloaded is not None else ""

    def head(self, url: str) -> ResponseMetadata:
        return self.client.head(url)

    def download_file(self, url: str) -> DownloadedFile:
        if url not in self._downloads:
            expected = next(
                (f.checksum for e in annual_exams() for f in e.resources if f.source_url == url),
                "",
            )
            if not expected:
                raise UnreviewedSourceFile("Unreviewed Taipei junior native download", url=url)
            downloaded = self.client.download_file(url)
            if hashlib.sha256(downloaded.data).hexdigest() != expected:
                raise UnreviewedSourceFile(
                    "Taipei junior native source checksum changed; retain previous state", url=url
                )
            self._downloads[url] = downloaded
        return self._downloads[url]
