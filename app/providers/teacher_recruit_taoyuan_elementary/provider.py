from __future__ import annotations

from app.models import ExamOption, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata, SourceProvider
from app.providers.teacher_recruit_taoyuan_elementary.client import TaoyuanElementaryRecruitClient


class TaoyuanElementaryRecruitProvider(SourceProvider):
    provider_id = "teacher_recruit_taoyuan_elementary"

    def __init__(self, client: TaoyuanElementaryRecruitClient | None = None) -> None:
        self.client = client or TaoyuanElementaryRecruitClient()

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
