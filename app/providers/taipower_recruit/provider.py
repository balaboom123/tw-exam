from __future__ import annotations

import hashlib
from pathlib import Path

from app.manifest import SourceManifest
from app.models import ExamOption, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata, SourceProvider
from app.providers.taipower_recruit.client import TaipowerRecruitClient
from app.providers.taipower_recruit.evidence import native_catalogue, project_native_page
from app.providers.taipower_recruit.manifest import refresh_source_manifest


class TaipowerRecruitProvider(SourceProvider):
    provider_id = "taipower_recruit"
    # A valid retained checksum does not prove that it matches today's source:
    # earlier common-paper copies contained the separate MOEA programme.
    refresh_files_on_sync = True
    download_attachments_on_sync = True

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
        return project_native_page(self.client.fetch_exam_page(exam_code, year_ad))

    def reviewed_file_checksum(self, url: str) -> str:
        resource = native_catalogue().resources_by_url.get(url)
        return resource.checksum if resource is not None else ""

    def head(self, url: str) -> ResponseMetadata:
        return self.client.head(url)

    def download_file(self, url: str) -> DownloadedFile:
        downloaded = self.client.download_file(url)
        resource = native_catalogue().resources_by_url.get(url)
        if resource is not None and (
            len(downloaded.data) != resource.size
            or hashlib.sha256(downloaded.data).hexdigest() != resource.checksum
        ):
            raise ValueError("Taipower native file changed; preserve earlier bytes for review")
        return downloaded

    def refresh_source_manifest(
        self, manifest: SourceManifest, pages: list[SourceExamPage], mirror_root: Path, now: str
    ) -> SourceManifest:
        return refresh_source_manifest(
            manifest, pages, mirror_root, now, listed_source_urls=self.client.listed_source_urls()
        )
