"""A native RAR review is required independently of download observation."""

import hashlib
from pathlib import Path
from urllib.error import URLError

import pytest

from app.models import ExamAttachment, SourceExamPage
from app.providers.base import DownloadedFile, UnreviewedSourceFile
from app.storage import MirrorStore
from app.sync import (
    _download_validated,
    _is_valid_stored_file,
    _validated_extension,
    sync_exam_pages,
)

NATIVE = b"Rar!\x1a\x07\x01\x00reviewed native fixture"
CHECKSUM = hashlib.sha256(NATIVE).hexdigest()
URL = "https://example.test/original.rar"


class ReviewedProvider:
    provider_id = "tii_cert"
    max_concurrency = 1
    download_attachments_on_sync = True

    def __init__(self):
        self.data = NATIVE
        self.failure = False

    def reviewed_source_archive_checksum(self, url):
        return CHECKSUM if url == URL else ""

    def fetch_exam_page(self, code, year):
        return SourceExamPage(
            code,
            year,
            year - 1911,
            "Native original",
            [ExamAttachment("Native original", "source_archive", URL)],
            [],
            self.provider_id,
        )

    def download_file(self, url):
        if self.failure:
            raise URLError("temporarily unavailable")
        return DownloadedFile(self.data, "application/vnd.rar", "original.rar")


def test_rar_requires_an_independent_review_and_matching_native_bytes(tmp_path):
    assert (
        _validated_extension(
            "source_archive",
            NATIVE,
            "application/octet-stream",
            "download",
            reviewed_archive_checksum=CHECKSUM,
        )
        == ".rar"
    )
    for pin in ("", "0" * 64, "not a checksum"):
        with pytest.raises(RuntimeError, match="independently reviewed"):
            _validated_extension(
                "source_archive",
                NATIVE,
                "application/vnd.rar",
                "original.rar",
                reviewed_archive_checksum=pin,
            )
    for body in (b"<html>failed</html>", b"%PDF-native", NATIVE + b"new revision"):
        with pytest.raises(RuntimeError):
            _validated_extension(
                "source_archive",
                body,
                "application/vnd.rar",
                "original.rar",
                reviewed_archive_checksum=CHECKSUM,
            )
    target = tmp_path / "native.rar"
    target.write_bytes(NATIVE)
    assert not _is_valid_stored_file(target, "source_archive")
    assert _is_valid_stored_file(target, "source_archive", reviewed_archive_checksum=CHECKSUM)
    target.write_bytes(NATIVE + b"changed")
    assert not _is_valid_stored_file(target, "source_archive", reviewed_archive_checksum=CHECKSUM)


def test_download_observation_is_not_a_native_archive_review():
    class ObservedProvider:
        def download_file(self, url):
            return DownloadedFile(NATIVE, "application/vnd.rar", "original.rar")

        def reviewed_file_checksum(self, url):
            return CHECKSUM

    # An unreviewed RAR is held for review rather than failing the sync.
    with pytest.raises(UnreviewedSourceFile, match="independently reviewed"):
        _download_validated(ObservedProvider(), "source_archive", URL)
    assert _download_validated(ReviewedProvider(), "source_archive", URL) == (NATIVE, ".rar")


def test_sync_conserves_exact_rar_and_keeps_verified_previous_bytes_on_transport_failure(
    tmp_path,
    monkeypatch,
):
    provider = ReviewedProvider()
    store = MirrorStore(tmp_path)

    def sync(previous=()):
        return sync_exam_pages(
            client=provider,
            exam_codes=[("event", 2026)],
            mirror_store=store,
            alias_rules=[],
            mirror_base_url="",
            download_attachments=True,
            refresh_files=True,
            previous_pages=list(previous),
        )

    pages, _catalog, failures = sync()
    assert not failures
    original = pages[0].attachments[0]
    target = tmp_path / original.storage_key
    assert target.suffix == ".rar" and target.read_bytes() == NATIVE
    provider.failure = True
    monkeypatch.setattr("app.sync.time.sleep", lambda delay: None)
    refreshed, _catalog, failures = sync(pages)
    assert failures
    assert refreshed[0].attachments[0].checksum == original.checksum == CHECKSUM
    assert target.read_bytes() == NATIVE
    provider.failure = False
    provider.data += b"changed upstream revision"
    _pages, catalog, failures = sync(pages)
    assert not failures
    assert [item.classification_signature for item in catalog.review_queue] == [
        f"unreviewed-source-file:{URL}"
    ]
    assert target.read_bytes() == NATIVE
    assert not list(Path(tmp_path).rglob("*.zip"))
