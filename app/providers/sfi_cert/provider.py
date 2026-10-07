from __future__ import annotations

from app.models import ExamOption, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata, SourceProvider
from app.providers.sfi_cert.client import SfiCertClient


class SfiCertProvider(SourceProvider):
    provider_id = "sfi_cert"
    # PHID download slots can serve revised material without changing URLs.
    refresh_files_on_sync = True

    def __init__(self, client: SfiCertClient | None = None) -> None:
        self.client = client or SfiCertClient()

    def discover_available_years(self) -> list[int]:
        return self.client.discover_available_years()

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        return self.client.discover_exams(year_ad)

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        return self.client.fetch_exam_page(exam_code, year_ad)

    def head(self, url: str) -> ResponseMetadata:
        return self.client.head(url)

    def download_file(self, url: str) -> DownloadedFile:
        return self.client.download_file(url)
