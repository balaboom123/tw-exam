"""Source charset, portable member intake and complete parent-version recovery."""

import hashlib
import io
import stat
import zipfile
from unittest.mock import Mock

import pytest

from app.models import to_plain_data
from app.paths import provider_paths
from app.providers.base import DownloadedFile
from app.providers.taisugar_recruit.archives import (
    member_locator,
    native_member_name,
    parse_member_locator,
    read_pdf_members,
)
from app.providers.taisugar_recruit.provider import TaisugarRecruitProvider
from app.publisher import write_provider_state
from app.source_revisions import load_source_revisions, payload_checksum
from app.storage import MirrorStore
from app.sync import sync_exam_pages
from tests.test_taisugar_source_ownership import client_for

PARENT = "https://www.taisugar.com.tw/upload/UserFiles/News/11543/papers.zip"
PDF = b"%PDF-1.4\nfixture\n%%EOF"


@pytest.fixture(autouse=True)
def fictional_archive_has_no_reviewed_annual_rule(monkeypatch):
    # The fixture event contains synthetic ZIPs, not a reviewed sitting.
    monkeypatch.setattr("app.providers.taisugar_recruit.provider.written_exams", lambda: ())


def archive(items):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        for name, data in items:
            z.writestr(name, data)
    return stream.getvalue()


class Cp950ZipInfo(zipfile.ZipInfo):
    def _encodeFilenameFlags(self):
        return self.filename.encode("cp950"), self.flag_bits & ~0x800


def test_legacy_cp950_names_are_decoded_before_portability_checks():
    name = "試題/會計學.pdf"
    info = Cp950ZipInfo(name)
    info.create_system = 0
    body = archive([(info, PDF)])
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        encoded = z.infolist()[0]
        assert "|" in encoded.filename
        assert native_member_name(encoded) == name
    assert read_pdf_members(body) == {name: PDF}
    assert (
        parse_member_locator(member_locator(PARENT, hashlib.sha256(body).hexdigest(), name))[2]
        == name
    )


def test_utf8_names_and_unicode_path_agreement_are_preserved():
    assert read_pdf_members(archive([("會計學.pdf", PDF)])) == {"會計學.pdf": PDF}
    info = zipfile.ZipInfo("old.pdf")
    info.filename = "different.pdf"
    with pytest.raises(ValueError, match="Conflicting"):
        native_member_name(info)


@pytest.mark.parametrize(
    "name", ["../paper.pdf", "/paper.pdf", "a\\paper.pdf", "CON.pdf", "a|b.pdf"]
)
def test_unsafe_paths_are_rejected(name):
    with pytest.raises(ValueError, match="Unsafe"):
        read_pdf_members(archive([(name, PDF)]))


def test_case_collisions_and_symlinks_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        read_pdf_members(archive([("Paper.pdf", PDF), ("paper.pdf", PDF)]))
    link = zipfile.ZipInfo("link.pdf")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(ValueError, match="Unsafe"):
        read_pdf_members(archive([(link, PDF)]))


def test_unknown_formats_and_crc_corruption_are_not_accepted():
    with pytest.raises(ValueError, match="format"):
        read_pdf_members(archive([("paper.pdf", b"<html>login</html>")]))
    with pytest.raises(ValueError, match="format"):
        read_pdf_members(archive([("nested.zip", archive([("paper.pdf", PDF)]))]))
    body = bytearray(archive([("paper.pdf", PDF)]))
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        info = z.infolist()[0]
        offset = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
    body[offset] ^= 1
    with pytest.raises(zipfile.BadZipFile):
        read_pdf_members(bytes(body))


def test_archive_limits_fail_before_unbounded_expansion(monkeypatch):
    from app.providers.taisugar_recruit import archives

    body = archive([("a.pdf", PDF), ("b.pdf", PDF)])
    monkeypatch.setattr(archives, "MAX_ARCHIVE_MEMBERS", 1)
    with pytest.raises(ValueError, match="limit"):
        read_pdf_members(body)


@pytest.mark.parametrize("change", ["host", "checksum", "path", "extra", "duplicate"])
def test_untrusted_member_locators_fail_before_network_access(change):
    url = member_locator(PARENT, "a" * 64, "paper.pdf")
    if change == "host":
        url = url.replace("www.taisugar.com.tw", "example.test")
    elif change == "checksum":
        url = url.replace("a" * 64, "unverified")
    elif change == "path":
        url = member_locator(PARENT, "a" * 64, "../paper.pdf")
    elif change == "extra":
        url += "&other=value"
    else:
        url += "&member=other.pdf"
    client = client_for("")
    client.http.download = Mock(side_effect=AssertionError("Unexpected network"))
    with pytest.raises(ValueError):
        client.download_file(url)
    client.http.download.assert_not_called()


def test_changed_parent_versions_keep_stable_reference_keys_and_independent_history(tmp_path):
    def provider_for(payload):
        client = client_for(f'<a href="{PARENT}">試題及解答</a>')
        client.http.download = Mock(
            return_value=DownloadedFile(payload, "application/zip", "papers.zip")
        )
        return TaisugarRecruitProvider(client)

    first = archive([("會計學.pdf", PDF)])
    second = archive([("會計學.pdf", PDF + b"\nrevision")])
    store = MirrorStore(tmp_path / "mirror")
    paths = provider_paths(tmp_path, "taisugar_recruit")
    p1 = provider_for(first)
    pages1, cat1, failures = sync_exam_pages(p1, [("taisugar-recruit-111", 2022)], store, [], "")
    assert not failures
    write_provider_state(paths, pages1, cat1, [], [], None)
    p2 = provider_for(second)
    pages2, cat2, failures = sync_exam_pages(
        p2, [("taisugar-recruit-111", 2022)], store, [], "", previous_pages=pages1
    )
    assert not failures and cat1.papers[0].subject_code == cat2.papers[0].subject_code
    assert cat1.papers[0].storage_key == cat2.papers[0].storage_key
    assert cat1.papers[0].download_url_source != cat2.papers[0].download_url_source
    write_provider_state(paths, pages2, cat2, [], [], None)
    history = load_source_revisions(paths)
    assert len(history) == 2 and {r["record_type"] for r in history} == {"paper", "attachment"}
    for row in history:
        blob = tmp_path / "mirror" / row["blob_storage_key"]
        assert payload_checksum(blob) == row["source_record"]["checksum"]
        current = (
            (store.root / cat2.papers[0].storage_key)
            if row["record_type"] == "paper"
            else (store.root / pages2[0].attachments[0].storage_key)
        )
        assert not blob.samefile(current)
    assert p1.client.http.download.call_count == p2.client.http.download.call_count == 1
    old_locator = cat1.papers[0].download_url_source
    with pytest.raises(ValueError, match="version"):
        p2.download_file(old_locator)
    assert to_plain_data(cat2.papers[0])["classification_confidence"] == "review"
