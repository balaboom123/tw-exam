from __future__ import annotations

import hashlib

from app.models import ExamOption, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata, SourceProvider
from app.providers.teacher_recruit_taoyuan_elementary.client import TaoyuanElementaryRecruitClient
from app.providers.teacher_recruit_taoyuan_elementary.evidence import (
    annual_exams,
    project_native_page,
)


class TaoyuanElementaryRecruitProvider(SourceProvider):
    provider_id = "teacher_recruit_taoyuan_elementary"
    refresh_files_on_sync = True
    download_attachments_on_sync = True
    max_concurrency = 1

    def __init__(self, client: TaoyuanElementaryRecruitClient | None = None) -> None:
        self.client = client or TaoyuanElementaryRecruitClient()
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
        checksums = {
            url: hashlib.sha256(self.download_file(url).data).hexdigest()
            for paper in page.papers
            for url in paper.files.values()
        }
        projected = project_native_page(page, checksums)
        exam = next(e for e in annual_exams() if e.source_exam_id == page.source_exam_id)
        # A paper's applicability also depends on the complete annual rule.
        # Verify it now so failed acquisition leaves the previous event intact.
        self.download_file(exam.rule_url)
        projected.attachments = [exam.rule_attachment()]
        return projected

    def reviewed_file_checksum(self, url: str) -> str:
        downloaded = self._downloads.get(url)
        return hashlib.sha256(downloaded.data).hexdigest() if downloaded is not None else ""

    def head(self, url: str) -> ResponseMetadata:
        return self.client.head(url)

    def download_file(self, url: str) -> DownloadedFile:
        if url not in self._downloads:
            downloaded = self.client.download_file(url)
            expected = next((e.rule_checksum for e in annual_exams() if e.rule_url == url), "")
            if expected and hashlib.sha256(downloaded.data).hexdigest() != expected:
                raise ValueError("Taoyuan reviewed annual rule checksum changed")
            # The same observation supplies all categories sharing an original.
            self._downloads[url] = downloaded
        return self._downloads[url]
