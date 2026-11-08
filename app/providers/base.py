from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.models import ExamOption, ReviewItem, SourceExamPage


@dataclass
class DownloadedFile:
    data: bytes
    content_type: str
    file_name: str


@dataclass
class ResponseMetadata:
    url: str
    status: int
    content_length: int | None = None
    content_type: str = ""
    content_disposition: str = ""
    cache_control: str = ""


UNREVIEWED_SOURCE_FILE = "unreviewed-source-file:"
CORRUPT_SOURCE_PAYLOAD = "corrupt-source-payload:"


class UnreviewedSourceFile(ValueError):
    """An upstream file has no reviewed record yet.

    Sync holds back only this file and lists it on the provider's review queue;
    the rest of the provider keeps syncing. An adapter that skips such a file
    while building pages appends the review item to ``held_files``.
    """

    def __init__(self, message: str, *, url: str) -> None:
        super().__init__(message)
        self.url = url

    @property
    def signature(self) -> str:
        return f"{UNREVIEWED_SOURCE_FILE}{self.url}"

    def review_item(
        self, provider_id: str, *, event_id: str = "", year_roc: int = 0, category: str = ""
    ) -> ReviewItem:
        return ReviewItem(
            raw_category=category,
            normalized_candidate="",
            source_exam_id=event_id,
            year_roc=year_roc,
            provider_id=provider_id,
            classification_signature=self.signature,
            reason=str(self),
        )


class CorruptSourcePayload(UnreviewedSourceFile):
    """The source serves these exact bytes corrupt.

    The checksum in the review item also stops provider state from retaining
    an earlier paper with the same bytes.
    """

    def __init__(self, message: str, *, url: str, checksum: str) -> None:
        super().__init__(message, url=url)
        self.checksum = checksum

    @property
    def signature(self) -> str:
        return f"{CORRUPT_SOURCE_PAYLOAD}{self.checksum}"


@runtime_checkable
class SourceProvider(Protocol):
    provider_id: str

    def discover_available_years(self) -> list[int]: ...
    def discover_exams(self, year_ad: int) -> list[ExamOption]: ...
    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage: ...
    def head(self, url: str) -> ResponseMetadata: ...
    def download_file(self, url: str) -> DownloadedFile: ...
