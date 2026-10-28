"""Native reference and dictation formats use validated cache reuse."""

import io
import zipfile
from copy import deepcopy

import pytest

from app.models import ExamAttachment, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile
from app.storage import MirrorStore
from app.sync import DOC_SIGNATURE, sync_exam_pages


def reference_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("詞表/reference.txt", "原始詞表")
    return buffer.getvalue()


@pytest.mark.parametrize("placement", ["paper", "attachment"])
@pytest.mark.parametrize(
    "role,extension,body",
    [
        ("dictation_audio", ".mp3", b"ID3 native dictation fixture"),
        ("reference", ".pdf", b"%PDF-1.7 native reference fixture"),
        ("reference", ".xls", DOC_SIGNATURE + b"native workbook fixture"),
        ("reference", ".zip", reference_zip()),
    ],
)
def test_reviewed_native_formats_reuse_only_verified_bytes(
    tmp_path, placement, role, extension, body
):
    url = "https://example.test/original" + extension
    source = SourceExamPage(
        "native-resource",
        2026,
        115,
        "Native material",
        [ExamAttachment("Original reference", role, url)] if placement == "attachment" else [],
        [ParsedPaper("Resource", "resource", "original", "Original", {role: url})]
        if placement == "paper"
        else [],
        provider_id="gept_cert",
    )

    class Client:
        provider_id = "gept_cert"

        def fetch_exam_page(self, code, year):
            return deepcopy(source)

        def download_file(self, location):
            assert location == url
            return DownloadedFile(body, "application/octet-stream", "original" + extension)

    client = Client()
    store = MirrorStore(tmp_path / "mirror")
    codes = [("native-resource", 2026)]
    previous, old_catalog, failures = sync_exam_pages(client, codes, store, [], "")
    assert not failures
    client.download_file = lambda url: pytest.fail("Valid native cache triggered acquisition")
    pages, catalog, failures = sync_exam_pages(
        client,
        codes,
        store,
        [],
        "",
        download_attachments=False,
        previous_pages=previous,
    )
    assert not failures and pages == previous and catalog == old_catalog


@pytest.mark.parametrize("role,extension", [("dictation_audio", ".mp3"), ("reference", ".pdf")])
def test_native_role_does_not_accept_arbitrary_non_html_bytes(tmp_path, role, extension):
    class Client:
        provider_id = "gept_cert"

        def fetch_exam_page(self, code, year):
            return SourceExamPage(
                code,
                year,
                year - 1911,
                "Native reference",
                [
                    ExamAttachment(
                        "Native original", role, "https://example.test/original" + extension
                    )
                ],
                [],
                provider_id=self.provider_id,
            )

        def download_file(self, url):
            return DownloadedFile(
                b"Unrecognized body", "application/octet-stream", "original" + extension
            )

    pages, _, failures = sync_exam_pages(
        Client(), [("native-resource", 2026)], MirrorStore(tmp_path), [], ""
    )
    assert len(failures) == 1 and "does not match expected" in failures[0].message
    assert not pages[0].attachments[0].checksum
