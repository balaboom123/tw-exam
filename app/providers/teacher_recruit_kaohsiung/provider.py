from __future__ import annotations

from app.models import ExamOption, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata, SourceProvider
from app.providers.teacher_recruit_kaohsiung.client import KaohsiungTeacherRecruitClient


class KaohsiungTeacherRecruitProvider(SourceProvider):
    provider_id = "teacher_recruit_kaohsiung"

    def __init__(self, client: KaohsiungTeacherRecruitClient | None = None) -> None:
        self.client = client or KaohsiungTeacherRecruitClient()

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
