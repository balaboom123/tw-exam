from __future__ import annotations

from app.models import ExamOption, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata, SourceProvider
from app.providers.taipower_recruit.client import TaipowerRecruitClient


class TaipowerRecruitProvider(SourceProvider):
    provider_id = "taipower_recruit"
    # A valid retained checksum does not prove that it matches today's source:
    # earlier common-paper copies contained the separate MOEA programme.
    refresh_files_on_sync = True

    def __init__(self, client: TaipowerRecruitClient | None = None) -> None:
        self.client = client or TaipowerRecruitClient()

    def discover_available_years(self) -> list[int]:
        return self.client.discover_available_years()

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        return self.client.discover_exams(year_ad)

    def build_discovery_year_url(self, year_ad: int) -> str:
        return self.client.build_discovery_year_url(year_ad)

    def build_discovery_exam_url(self, exam_code: str, year_ad: int) -> str:
        return self.client.build_discovery_exam_url(exam_code, year_ad)

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        return self.client.fetch_exam_page(exam_code, year_ad)

    def head(self, url: str) -> ResponseMetadata:
        return self.client.head(url)

    def download_file(self, url: str) -> DownloadedFile:
        return self.client.download_file(url)
