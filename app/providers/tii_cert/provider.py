from __future__ import annotations

from app.models import ExamOption, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata, SourceProvider
from app.providers.tii_cert.client import TiiCertClient


class TiiCertProvider(SourceProvider):
    provider_id = "tii_cert"
    # The reviewed redistribution hold covers the same source bytes in both
    # site bundles and public mirror releases. Retain private recovery inputs.
    public_mirror_backup_allowed = False
    # The source can correct a PDF at the same URL, and earlier adapter/cache
    # generations stored a brochure in the first section's question locator.
    refresh_files_on_sync = True
    # Extracted historical papers depend on the original package's provenance.
    download_attachments_on_sync = True

    def __init__(self, client: TiiCertClient | None = None) -> None:
        self.client = client or TiiCertClient()

    def discover_available_years(self) -> list[int]:
        return self.client.discover_available_years()

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        return self.client.discover_exams(year_ad)

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        return self.client.fetch_exam_page(exam_code, year_ad)

    def build_discovery_year_url(self, year_ad: int) -> str:
        return self.client.build_discovery_year_url(year_ad)

    def build_discovery_exam_url(self, exam_code: str, year_ad: int) -> str:
        return self.client.build_discovery_exam_url(exam_code, year_ad)

    def head(self, url: str) -> ResponseMetadata:
        return self.client.head(url)

    def download_file(self, url: str) -> DownloadedFile:
        return self.client.download_file(url)
