"""Failed network refreshes retain exact earlier bytes without hiding failures."""

from urllib.error import HTTPError

import pytest

from app.models import ExamAttachment, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile
from app.source_material import SourceDate, SourceMaterial
from app.storage import MirrorStore
from app.sync import sync_exam_pages


class RefreshClient:
    provider_id = "gept_cert"

    def __init__(self):
        self.offline = False
        self.invalid_pdf = False
        self.caption = "Native scoring examples"
        self.kind = "reference"
        self.pdf_url = "https://example.test/examples.pdf"
        self.role = "scoring_examples"

    def fetch_exam_page(self, code, year):
        material = SourceMaterial(
            self.kind,
            SourceDate("undated", None),
            "https://example.test/source.html",
            "Official resource",
        )
        return SourceExamPage(
            code,
            year,
            year - 1911,
            "GEPT全民英檢 初級",
            [
                ExamAttachment(
                    self.caption,
                    "source_page",
                    "https://example.test/source.html",
                    source_material=material,
                )
            ],
            [
                ParsedPaper(
                    "GEPT初級",
                    "elementary",
                    "resource",
                    self.caption,
                    {self.role: self.pdf_url},
                    source_material=material,
                )
            ],
            provider_id=self.provider_id,
        )

    def download_file(self, url):
        if self.offline:
            raise HTTPError(url, 404, "Source removed", None, None)
        if url.endswith(".html") or self.invalid_pdf:
            return DownloadedFile(
                b"<!doctype html><html>Official source</html>", "text/html", "source.html"
            )
        return DownloadedFile(b"%PDF-1.7 original resource", "application/pdf", "examples.pdf")


@pytest.fixture
def retained(tmp_path):
    client = RefreshClient()
    store = MirrorStore(tmp_path / "mirror")
    pages, catalog, failures = sync_exam_pages(
        client, [("gept-cert-elementary-2022", 2022)], store, [], ""
    )
    assert not failures and len(catalog.papers) == 1
    return client, store, pages, catalog


def test_failed_network_refresh_keeps_verified_paper_and_attachment_metadata(retained):
    client, store, previous, old = retained
    client.offline = True
    pages, catalog, failures = sync_exam_pages(
        client,
        [("gept-cert-elementary-2022", 2022)],
        store,
        [],
        "",
        refresh_files=True,
        previous_pages=previous,
    )
    assert len(failures) == 2 and all(f.stage == "download" for f in failures)
    assert all("404" in f.message for f in failures)
    assert pages[0].papers[0].mirror_files == previous[0].papers[0].mirror_files
    assert pages[0].attachments[0].checksum == previous[0].attachments[0].checksum
    assert pages[0].attachments[0].storage_key == previous[0].attachments[0].storage_key
    assert len(catalog.papers) == 1 and catalog.papers[0].checksum == old.papers[0].checksum


@pytest.mark.parametrize("change", ["caption", "material", "url", "role"])
def test_changed_reference_context_cannot_borrow_the_old_file_after_network_failure(
    retained, change
):
    client, store, previous, _ = retained
    client.offline = True
    if change == "caption":
        client.caption = "New source context"
    elif change == "material":
        client.kind = "sample"
    elif change == "url":
        client.pdf_url = "https://example.test/replacement.pdf"
    else:
        client.role = "listening_transcript"
    pages, catalog, failures = sync_exam_pages(
        client,
        [("gept-cert-elementary-2022", 2022)],
        store,
        [],
        "",
        refresh_files=True,
        previous_pages=previous,
    )
    assert failures and not catalog.papers
    assert not pages[0].papers[0].mirror_files


def test_changed_cached_bytes_cannot_be_retained_after_failed_refresh(retained):
    client, store, previous, old = retained
    (store.root / old.papers[0].storage_key).write_bytes(b"%PDF-1.7 unrelated content")
    client.offline = True
    pages, catalog, failures = sync_exam_pages(
        client,
        [("gept-cert-elementary-2022", 2022)],
        store,
        [],
        "",
        refresh_files=True,
        previous_pages=previous,
    )
    assert len(failures) == 2 and not catalog.papers
    assert not pages[0].papers[0].mirror_files
    assert pages[0].attachments[0].checksum == previous[0].attachments[0].checksum


def test_invalid_new_content_is_not_treated_as_a_network_failure(retained):
    client, store, previous, old = retained
    original = (store.root / old.papers[0].storage_key).read_bytes()
    client.invalid_pdf = True
    pages, catalog, failures = sync_exam_pages(
        client,
        [("gept-cert-elementary-2022", 2022)],
        store,
        [],
        "",
        refresh_files=True,
        previous_pages=previous,
    )
    assert len(failures) == 1 and "HTML placeholder" in failures[0].message
    assert not catalog.papers and not pages[0].papers[0].mirror_files
    assert (store.root / old.papers[0].storage_key).read_bytes() == original
