from __future__ import annotations

import hashlib

from app.models import ExamOption, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata, SourceProvider
from app.providers.post_recruit.client import PostRecruitClient
from app.providers.post_recruit.evidence import project_native_page


class PostRecruitProvider(SourceProvider):
    provider_id = "post_recruit"
    max_concurrency = 1
    refresh_files_on_sync = True

    def __init__(self, client: PostRecruitClient | None = None) -> None:
        self.client = client or PostRecruitClient()
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
        return project_native_page(page, checksums)

    def reviewed_file_checksum(self, url: str) -> str:
        downloaded = self._downloads.get(url)
        return hashlib.sha256(downloaded.data).hexdigest() if downloaded is not None else ""

    def head(self, url: str) -> ResponseMetadata:
        return self.client.head(url)

    def download_file(self, url: str) -> DownloadedFile:
        # One native paper can serve many jobs. The command's fresh source
        # observation also supplies every shared mirror request.
        if url not in self._downloads:
            self._downloads[url] = self.client.download_file(url)
        return self._downloads[url]
