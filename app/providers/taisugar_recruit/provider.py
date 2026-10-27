from __future__ import annotations

import hashlib
from pathlib import Path

from app.manifest import SourceManifest
from app.models import ExamOption, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata, SourceProvider
from app.providers.taisugar_recruit.annual import written_exams
from app.providers.taisugar_recruit.client import TaisugarRecruitClient
from app.providers.taisugar_recruit.manifest import refresh_source_manifest


class TaisugarRecruitProvider(SourceProvider):
    provider_id = "taisugar_recruit"
    download_attachments_on_sync = True
    # Archive inspection and the retained original must use the same observed
    # version; refresh keeps mutable parent slots and their member locators aligned.
    refresh_files_on_sync = True

    def __init__(self, client: TaisugarRecruitClient | None = None) -> None:
        self.client = client or TaisugarRecruitClient()

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
        retained_urls = {a.download_url_source for a in page.attachments}
        for fact in written_exams():
            if (
                fact.source_exam_id == page.source_exam_id
                and fact.year_roc == page.year_roc
                and fact.exam_name_raw == page.exam_name_raw
                and fact.rule_url not in retained_urls
            ):
                page.attachments.append(fact.rule_attachment())
                retained_urls.add(fact.rule_url)
        return page

    def reviewed_file_checksum(self, url: str) -> str:
        return next((f.rule_checksum for f in written_exams() if f.rule_url == url), "")

    def head(self, url: str) -> ResponseMetadata:
        return self.client.head(url)

    def download_file(self, url: str) -> DownloadedFile:
        downloaded = self.client.download_file(url)
        expected = self.reviewed_file_checksum(url)
        if expected and hashlib.sha256(downloaded.data).hexdigest() != expected:
            raise ValueError("Taiwan Sugar reviewed annual rule checksum changed")
        return downloaded

    def refresh_source_manifest(
        self, manifest: SourceManifest, pages: list[SourceExamPage], mirror_root: Path, now: str
    ) -> SourceManifest:
        return refresh_source_manifest(
            manifest,
            pages,
            mirror_root,
            now,
            listing_rows=self.client.listing_rows,
            listing_page_count=self.client.listing_page_count,
        )
