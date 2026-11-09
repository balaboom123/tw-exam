from __future__ import annotations

from app.models import ExamOption, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata, SourceProvider
from app.providers.sfi_cert.client import SfiCertClient


class SfiCertProvider(SourceProvider):
    provider_id = "sfi_cert"
    # The source times out from GitHub-hosted runners but serves Taiwan (2026-11-09),
    # so it syncs from the maintainer's machine instead of a schedule.
    hosted_runner_reachable = False
    # Seasonal slots can serve another examination without changing URLs.
    refresh_files_on_sync = True
    max_concurrency = 1

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
