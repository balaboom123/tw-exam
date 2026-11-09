from __future__ import annotations

import hashlib
from pathlib import PurePosixPath

from app.models import ExamOption, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata, SourceProvider
from app.providers.teacher_recruit_kaohsiung.archives import verified_zip_members
from app.providers.teacher_recruit_kaohsiung.client import KaohsiungTeacherRecruitClient
from app.providers.teacher_recruit_kaohsiung.evidence import annual_exams


class KaohsiungTeacherRecruitProvider(SourceProvider):
    provider_id = "teacher_recruit_kaohsiung"
    refresh_files_on_sync = True
    download_attachments_on_sync = True
    max_concurrency = 1

    def __init__(self, client: KaohsiungTeacherRecruitClient | None = None) -> None:
        self.client = client or KaohsiungTeacherRecruitClient()
        self._downloads: dict[str, DownloadedFile] = {}

    def discover_available_years(self) -> list[int]:
        return self.client.discover_available_years()

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        return self.client.discover_exams(year_ad)

    def build_discovery_year_url(self, year_ad: int) -> str:
        return self.client.build_discovery_year_url(year_ad)

    def build_discovery_exam_url(self, exam_code: str, year_ad: int) -> str:
        return self.client.build_discovery_exam_url(exam_code, year_ad)

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        # Reject a mismatched annual event before any source request.
        exam = self.client.known_exam(exam_code, year_ad)
        page = self.client.fetch_exam_page(exam_code, year_ad)
        for archive in exam.archives:
            self.download_file(archive.source_url)
        for file in exam.files:
            self.download_file(file.source_url)
        return page

    def reviewed_file_checksum(self, url: str) -> str:
        downloaded = self._downloads.get(url)
        return hashlib.sha256(downloaded.data).hexdigest() if downloaded is not None else ""

    def head(self, url: str) -> ResponseMetadata:
        downloaded = self.download_file(url)
        return ResponseMetadata(url, 200, len(downloaded.data), downloaded.content_type)

    def download_file(self, url: str) -> DownloadedFile:
        if url in self._downloads:
            return self._downloads[url]
        for exam in annual_exams():
            for archive in exam.archives:
                if url != archive.source_url:
                    continue
                original = self.client.download_file(url)
                payloads = verified_zip_members(archive, original.data)
                self._downloads[url] = original
                for member in archive.members:
                    self._downloads[archive.locator(member)] = DownloadedFile(
                        payloads[member.path], "application/pdf", PurePosixPath(member.path).name
                    )
                return original
            for file in exam.files:
                if file.source_url != url:
                    continue
                if file.archive_id:
                    parent = next(a for a in exam.archives if a.archive_id == file.archive_id)
                    self.download_file(parent.source_url)
                    return self._downloads[url]
                downloaded = self.client.download_file(url)
                if (
                    len(downloaded.data) != file.size
                    or not downloaded.data.startswith(b"%PDF-")
                    or hashlib.sha256(downloaded.data).hexdigest() != file.checksum
                ):
                    raise ValueError(
                        "Kaohsiung native PDF changed; retain previous state for review"
                    )
                self._downloads[url] = downloaded
                return downloaded
        raise ValueError("Unreviewed Kaohsiung native source URL")
