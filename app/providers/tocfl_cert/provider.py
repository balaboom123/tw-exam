from __future__ import annotations

from app.models import ExamOption, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata, SourceProvider
from app.providers.tocfl_cert.client import TocflCertClient


class TocflCertProvider(SourceProvider):
    provider_id = "tocfl_cert"
    # Maintainer decision 2026-11-08: the listening-audio packages stay out of
    # public Release storage, as for Hakka and JLPT.
    public_mirror_backup_allowed = False
    # Rolling-bank URLs can replace their payloads without changing filenames.
    refresh_files_on_sync = True

    def __init__(self, client: TocflCertClient | None = None) -> None:
        self.client = client or TocflCertClient()

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
