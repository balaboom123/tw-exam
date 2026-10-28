"""Failed network refreshes retain exact earlier bytes without hiding failures."""

from copy import deepcopy
from dataclasses import asdict
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


def test_skipping_attachment_acquisition_keeps_verified_locators_without_requests(retained):
    from app.paths import provider_paths
    from app.publisher import write_provider_state
    from app.source_revisions import load_source_revisions

    client, store, previous, old = retained
    provider = provider_paths(store.root.parent, client.provider_id)
    write_provider_state(provider, previous, old, [], [], None)
    client.offline = True
    pages, catalog, failures = sync_exam_pages(
        client,
        [("gept-cert-elementary-2022", 2022)],
        store,
        [],
        "",
        download_attachments=False,
        previous_pages=previous,
    )
    assert not failures
    assert pages == previous
    assert catalog == old
    write_provider_state(provider, pages, catalog, [], failures, None)
    assert load_source_revisions(provider) == []


@pytest.mark.parametrize("change", ["caption", "material", "url", "role", "provider", "year"])
def test_skipped_attachment_cannot_borrow_a_different_source_context(retained, change):
    client, store, previous, _ = retained
    discovered = client.fetch_exam_page("gept-cert-elementary-2022", 2022)
    attachment = discovered.attachments[0]
    if change == "caption":
        attachment.title = "A different resource"
    elif change == "material":
        attachment.source_material = SourceMaterial(
            "sample", SourceDate("undated", None), "https://example.test/source.html", "Sample"
        )
    elif change == "url":
        attachment.download_url_source = "https://example.test/other.html"
    elif change == "role":
        attachment.file_type = "instructions"
    elif change == "provider":
        discovered.provider_id = "other_owner"
    else:
        discovered.year_ad += 1
        discovered.year_roc += 1
    discovered.papers = []
    client.fetch_exam_page = lambda code, year: deepcopy(discovered)
    pages, _, failures = sync_exam_pages(
        client,
        [(discovered.source_exam_id, discovered.year_ad)],
        store,
        [],
        "",
        download_attachments=False,
        previous_pages=previous,
    )
    assert not failures
    assert not pages[0].attachments[0].storage_key
    assert not pages[0].attachments[0].checksum


@pytest.mark.parametrize("invalid", ["missing", "changed", "wrong_format"])
def test_skipped_attachment_reports_unverifiable_retained_bytes(retained, invalid):
    import hashlib

    client, store, previous, _ = retained
    attachment = previous[0].attachments[0]
    path = store.root / attachment.storage_key
    if invalid == "missing":
        path.unlink()
    elif invalid == "changed":
        path.write_bytes(b"<!doctype html><html>Unrelated resource</html>")
    else:
        path.write_bytes(b"Not an HTML resource")
        attachment.checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    pages, _, failures = sync_exam_pages(
        client,
        [("gept-cert-elementary-2022", 2022)],
        store,
        [],
        "",
        download_attachments=False,
        previous_pages=previous,
    )
    assert len(failures) == 1
    assert failures[0].url == attachment.download_url_source
    assert "Retained attachment could not be verified" in failures[0].message
    assert not pages[0].attachments[0].storage_key


def test_skipped_same_role_attachments_keep_independent_url_locators(tmp_path):
    class CollidingAttachments(RefreshClient):
        def fetch_exam_page(self, code, year):
            page = super().fetch_exam_page(code, year)
            page.attachments.append(
                ExamAttachment("Other reference", "source_page", "https://example.test/other.html")
            )
            return page

        def download_file(self, url):
            if url.endswith(".html"):
                return DownloadedFile(
                    b"<!doctype html><html>" + url.encode() + b"</html>",
                    "text/html",
                    "source.html",
                )
            return super().download_file(url)

    client = CollidingAttachments()
    store = MirrorStore(tmp_path / "mirror")
    codes = [("gept-cert-elementary-2022", 2022)]
    previous, _, failures = sync_exam_pages(client, codes, store, [], "")
    assert not failures
    assert len({a.storage_key for a in previous[0].attachments}) == 2
    client.download_file = lambda url: pytest.fail("Skipped acquisition made a request")
    pages, _, failures = sync_exam_pages(
        client, codes, store, [], "", download_attachments=False, previous_pages=previous
    )
    assert not failures
    assert [asdict(a) for a in pages[0].attachments] == [asdict(a) for a in previous[0].attachments]


@pytest.mark.parametrize("invalid", ["absolute", "traversal", "other_owner", "symlink"])
def test_skipped_attachment_rejects_locators_outside_its_owner(retained, tmp_path, invalid):
    client, store, previous, _ = retained
    attachment = previous[0].attachments[0]
    original = store.root / attachment.storage_key
    outside = tmp_path / "outside.html"
    outside.write_bytes(original.read_bytes())
    if invalid == "absolute":
        attachment.storage_key = str(outside)
    elif invalid == "traversal":
        attachment.storage_key = "../outside.html"
    elif invalid == "other_owner":
        attachment.storage_key = "providers/other_owner/retained.html"
        other = store.root / attachment.storage_key
        other.parent.mkdir(parents=True)
        other.write_bytes(outside.read_bytes())
    else:
        original.unlink()
        original.symlink_to(outside)
    pages, _, failures = sync_exam_pages(
        client,
        [("gept-cert-elementary-2022", 2022)],
        store,
        [],
        "",
        download_attachments=False,
        previous_pages=previous,
    )
    assert len(failures) == 1
    assert not pages[0].attachments[0].storage_key
