import hashlib
import io
import zipfile
from dataclasses import replace
from unittest.mock import patch

import pytest

from app.providers.base import DownloadedFile
from app.providers.teacher_qual.client import TeacherQualClient
from app.providers.teacher_qual.evidence import extract_member, native_fallbacks, parse_member_locator
from app.providers.teacher_qual.provider import TeacherQualProvider
from app.storage import MirrorStore
from app.sync import sync_exam_pages


@pytest.fixture
def fallback_fixture():
    fact = native_fallbacks()[0]
    wrong = b"%PDF-1.7 secondary school paper"
    correct = b"%PDF-1.7 kindergarten paper"
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(fact.member_path, correct)
    original = stream.getvalue()
    fact = replace(
        fact, conflicting_checksum=hashlib.sha256(wrong).hexdigest(),
        archive_checksum=hashlib.sha256(original).hexdigest(),
        member_checksum=hashlib.sha256(correct).hexdigest(),
    )
    html = '''<table><tr><td>102年試題及參考答案</td></tr><tr><td>
    考題及參考答案 全部下載<a href="ShowPicOut2.aspx?ASParam=original">文件下載</a>
    </td></tr></table>'''
    cell = '''<span id="ctl00_ContentPlaceHolder1_frmGrd_DataGrid_ctl02_frmGrd_sbid01">
    教育原理與制度<br>考題<a href="ShowPicOut2.aspx?ASParam=wrong" title="下載pdf檔案">
    </a></span>'''
    client = TeacherQualClient()
    def download(url):
        data = original if url.endswith("ASParam=original") else wrong
        return DownloadedFile(data, "application/octet-stream", "ShowPicOut2.aspx")
    with (
        patch("app.providers.teacher_qual.evidence.native_fallbacks", return_value=(fact,)),
        patch("app.providers.teacher_qual.archive.native_archive_for", return_value=None),
        patch.object(client, "category_listings", return_value=[
            ("10", "幼兒園類科", html + cell), ("99", "全部類科", html),
        ]),
        patch.object(client, "_download_url", side_effect=download) as network,
    ):
        yield client, fact, wrong, correct, original, network


def test_conflict_uses_native_kindergarten_paper_and_preserves_the_bad_reference(fallback_fixture):
    client, fact, wrong, correct, _, _ = fallback_fixture
    page = client.fetch_exam_page("teacher-qual-102", 2013)
    assert len(page.papers) == 1 and len(page.attachments) == 2
    paper = page.papers[0]
    assert paper.category_code == "102-10"
    url = paper.files["question"]
    assert client.download_file(url).data == correct
    assert parse_member_locator(url)[1] == fact
    reference = page.attachments[1]
    assert client.download_file(reference.download_url_source).data == wrong
    assert reference.source_material.review_reason == fact.reason


def test_sync_stores_only_the_correct_bytes_under_the_kindergarten_identity(tmp_path, fallback_fixture):
    client, fact, wrong, correct, _, _ = fallback_fixture
    pages, catalog, failures = sync_exam_pages(
        TeacherQualProvider(client), [("teacher-qual-102", 2013)],
        MirrorStore(tmp_path / "mirror"), [], "", refresh_files=True,
    )
    assert not failures and len(catalog.papers) == 1
    paper = catalog.papers[0]
    assert paper.track_id == "teacher-qualification-kindergarten"
    assert paper.checksum == fact.member_checksum
    assert (tmp_path / "mirror" / paper.storage_key).read_bytes() == correct
    assert (tmp_path / "mirror" / pages[0].attachments[1].storage_key).read_bytes() == wrong


def test_new_conflicting_bytes_stop_before_any_mirror_write(tmp_path, fallback_fixture):
    client, _, _, _, _, _ = fallback_fixture
    with patch.object(client, "_download_url", return_value=DownloadedFile(
        b"%PDF-1.7 unreviewed new content", "application/pdf", "changed.pdf"
    )):
        _, catalog, failures = sync_exam_pages(
            TeacherQualProvider(client), [("teacher-qual-102", 2013)],
            MirrorStore(tmp_path / "mirror"), [], "", refresh_files=True,
        )
    assert not catalog.papers and failures[0].stage == "fetch"
    assert not list((tmp_path / "mirror").rglob("*.pdf"))


def test_changed_original_or_native_member_cannot_supply_the_alternative(fallback_fixture):
    _, fact, _, _, original, _ = fallback_fixture
    with pytest.raises(ValueError, match="original archive changed"):
        extract_member(original + b"changed", fact)
    with pytest.raises(ValueError, match="paper checksum mismatch"):
        extract_member(original, replace(fact, member_checksum="0" * 64))


def test_native_locator_cannot_select_an_unreviewed_member(fallback_fixture):
    client, _, _, _, _, _ = fallback_fixture
    page = client.fetch_exam_page("teacher-qual-102", 2013)
    uri = page.papers[0].files["question"]
    with pytest.raises(ValueError, match="Unreviewed.*native member"):
        parse_member_locator(uri.replace("zip-entry=", "zip-entry=other"))
