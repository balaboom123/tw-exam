from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlsplit

from app.models import ExamOption, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata, SourceProvider
from app.providers.teacher_recruit_newtaipei.archive_plan import (
    member_storage_keys,
    native_archives,
    resolve_member_locator,
    verify_member,
    verify_parent,
)
from app.providers.teacher_recruit_newtaipei.client import NewTaipeiTeacherRecruitClient
from app.providers.teacher_recruit_newtaipei.evidence import (
    annual_exams,
    project_native_page,
    reviewed_document_checksum,
)


class NewTaipeiTeacherRecruitProvider(SourceProvider):
    provider_id = "teacher_recruit_newtaipei"
    # Answer keys and their review notices can change at a stable official URL.
    refresh_files_on_sync = True
    max_concurrency = 1
    download_attachments_on_sync = True

    def __init__(
        self, client: NewTaipeiTeacherRecruitClient | None = None, *, repo_root: Path | None = None
    ) -> None:
        self.client = client or NewTaipeiTeacherRecruitClient()
        self.repo_root = repo_root or Path(__file__).resolve().parents[3]
        self._downloads: dict[str, DownloadedFile] = {}
        self._heads: dict[str, ResponseMetadata] = {}

    def discover_available_years(self) -> list[int]:
        return self.client.discover_available_years()

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        return self.client.discover_exams(year_ad)

    def build_discovery_year_url(self, year_ad: int) -> str:
        return self.client.build_discovery_year_url(year_ad)

    def build_discovery_exam_url(self, exam_code: str, year_ad: int) -> str:
        return self.client.build_discovery_exam_url(exam_code, year_ad)

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        exam = next((e for e in annual_exams() if e.source_exam_id == exam_code), None)
        if exam is None:
            raise ValueError("New Taipei annual programme requires native rule and paper review")
        page = self.client.fetch_reviewed_exam_page(exam_code, year_ad, exam.acquisition_event())
        checksums = {
            url: hashlib.sha256(self.download_file(url).data).hexdigest()
            for paper in page.papers
            for url in paper.files.values()
        }
        # Applicability depends on these rules and allocation documents too.
        # Verify the entire reviewed evidence before replacing an event.
        for document in exam.documents:
            self.download_file(document.source_url)
        projected = project_native_page(page, checksums)
        for attachment in projected.attachments:
            self.download_file(attachment.download_url_source)
        return projected

    def head(self, url: str) -> ResponseMetadata:
        if urlsplit(url).fragment:
            context = resolve_member_locator(url)
            if context is None:
                raise ValueError("Unknown New Taipei native RAR member locator")
            # A member locator is provenance, not a distinct HTTP download.
            url = context[0].source_url
        if url not in self._heads:
            self._heads[url] = self.client.head(url)
        return self._heads[url]

    def reviewed_file_checksum(self, url: str) -> str:
        context = resolve_member_locator(url)
        if context is not None:
            return context[1].checksum
        downloaded = self._downloads.get(url)
        return hashlib.sha256(downloaded.data).hexdigest() if downloaded is not None else ""

    def reviewed_source_archive_checksum(self, url: str) -> str:
        return next((a.checksum for a in native_archives() if a.source_url == url), "")

    def download_file(self, url: str) -> DownloadedFile:
        if url in self._downloads:
            return self._downloads[url]
        if not urlsplit(url).fragment:
            downloaded = self.client.download_file(url)
            archive = next((a for a in native_archives() if a.source_url == url), None)
            if archive is not None:
                verify_parent(archive, downloaded.data)
            expected = reviewed_document_checksum(url)
            if expected and hashlib.sha256(downloaded.data).hexdigest() != expected:
                raise ValueError("New Taipei native rule, paper or answer-review document changed")
        else:
            context = resolve_member_locator(url)
            if context is None:
                raise ValueError("Unknown New Taipei native RAR member locator")
            archive, member = context
            self.download_file(archive.source_url)
            target = next(
                (
                    self.repo_root / "mirror" / key
                    for key in member_storage_keys(self.repo_root, archive, member)
                    if (self.repo_root / "mirror" / key).is_file()
                ),
                None,
            )
            if target is None:
                raise ValueError(
                    "New Taipei native member mirror is missing; restore the provider snapshot "
                    "or run scripts/import_newtaipei_archive.py with the reviewed original RAR"
                )
            with target.open("rb") as handle:
                data = handle.read(member.size + 1)
            verify_member(member, data)
            downloaded = DownloadedFile(data, "application/pdf", Path(member.path).name)
        self._downloads[url] = downloaded
        return downloaded
