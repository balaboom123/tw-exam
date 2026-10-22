from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlsplit

from app.models import ExamOption, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata, SourceProvider
from app.providers.hce_archive import HCE_CONFIGS, HceArchiveClient
from app.providers.hce_nsysu.evidence import project_native_page, resolve_selection_locator
from app.providers.hce_nsysu.selection_plan import read_compiled_selection, selection_plan_path


class HceNsysuProvider(SourceProvider):
    provider_id = "hce_nsysu"
    max_concurrency = 1
    download_attachments_on_sync = True

    def __init__(
        self, client: HceArchiveClient | None = None, *, repo_root: Path | None = None
    ) -> None:
        self.client = client or HceArchiveClient(HCE_CONFIGS[self.provider_id])
        self.repo_root = repo_root or Path(__file__).resolve().parents[3]
        self._native_downloads: dict[str, DownloadedFile] = {}
        self._native_heads: dict[str, ResponseMetadata] = {}
        self._selected_downloads: dict[str, DownloadedFile] = {}

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
        if len(page.papers) != 1 or set(page.papers[0].files) != {"question_answer"}:
            raise ValueError("NSYSU discovery did not return its native annual document")
        source_url = page.papers[0].files["question_answer"]
        original = self._native_download(source_url)
        return project_native_page(page, parent_checksum=hashlib.sha256(original.data).hexdigest())

    def reviewed_file_checksum(self, url: str) -> str:
        context = resolve_selection_locator(url)
        if context is not None:
            return context[2].checksum
        original = self._native_downloads.get(url)
        return hashlib.sha256(original.data).hexdigest() if original is not None else ""

    def head(self, url: str) -> ResponseMetadata:
        if urlsplit(url).fragment:
            context = resolve_selection_locator(url)
            if context is None:
                raise ValueError("Unknown NSYSU native PDF selection locator")
            url = context[1].source_url
        if url not in self._native_heads:
            self._native_heads[url] = self.client.head(url)
        # This is the actual parent-source HTTP observation. A compiled
        # subject selection is not a separate official HTTP download.
        return self._native_heads[url]

    def _native_download(self, url: str) -> DownloadedFile:
        if url not in self._native_downloads:
            self._native_downloads[url] = self.client.download_file(url)
        return self._native_downloads[url]

    def download_file(self, url: str) -> DownloadedFile:
        if not urlsplit(url).fragment:
            return self._native_download(url)
        context = resolve_selection_locator(url)
        if context is None:
            raise ValueError("Unknown NSYSU native PDF selection locator")
        if url not in self._selected_downloads:
            _annual, document, selection = context
            original = self._native_download(document.source_url)
            plan = read_compiled_selection(
                selection_plan_path(self.repo_root, selection.checksum),
                parent_checksum=document.checksum,
                parent_bytes=document.size,
                output_checksum=selection.checksum,
                output_bytes=selection.size,
            )
            data = plan.apply(original.data)
            self._selected_downloads[url] = DownloadedFile(
                data, "application/pdf", f"{selection.subject_code}_{selection.file_type}.pdf"
            )
        return self._selected_downloads[url]
