from __future__ import annotations

import hashlib

from app.models import ExamOption, SourceExamPage
from app.providers.base import (
    DownloadedFile,
    ResponseMetadata,
    SourceProvider,
    UnreviewedSourceFile,
)
from app.providers.teacher_recruit_taipei_elementary.client import TaipeiElementaryRecruitClient
from app.providers.teacher_recruit_taipei_elementary.evidence import (
    annual_exams,
    project_native_page,
)


class TaipeiElementaryRecruitProvider(SourceProvider):
    provider_id = "teacher_recruit_taipei_elementary"
    refresh_files_on_sync = True
    download_attachments_on_sync = True
    max_concurrency = 1

    def __init__(self, client: TaipeiElementaryRecruitClient | None = None) -> None:
        self.client = client or TaipeiElementaryRecruitClient()
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
                "Elementary recruitment requires annual native programme review",
                url=f"{self.build_discovery_year_url(year_ad)}#{exam_code}",
            )
        # Compare the whole relevant PDF population, not just links whose names
        # happen to match the current booklet parser. Historical unavailable
        # documents are evidence dispositions and never successful downloads.
        notices = {f.notice_url for f in exam.resources}
        for notice in sorted(notices):
            expected = {f.source_url for f in exam.resources if f.notice_url == notice}
            observed = {url for _name, url in self.client.pdf_downloads(notice)}
            if observed != expected:
                raise UnreviewedSourceFile(
                    "Elementary native notice changed; retain previous state for review"
                )
        checksums = {
            url: hashlib.sha256(self.download_file(url).data).hexdigest()
            for paper in page.papers
            for url in paper.files.values()
        }
        for resource in exam.resources:
            checksums[resource.source_url] = hashlib.sha256(
                self.download_file(resource.source_url).data
            ).hexdigest()
        projected = project_native_page(page, checksums)
        projected.attachments = exam.attachments()
        return projected

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
                raise UnreviewedSourceFile("Unreviewed elementary native download", url=url)
            downloaded = self.client.download_file(url)
            if hashlib.sha256(downloaded.data).hexdigest() != expected:
                raise UnreviewedSourceFile(
                    "Elementary native checksum changed; retain previous state", url=url
                )
            self._downloads[url] = downloaded
        return self._downloads[url]
