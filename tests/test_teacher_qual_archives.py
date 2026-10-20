import hashlib
import io
import zipfile
from dataclasses import replace
from unittest.mock import patch

import pytest

from app.providers.base import DownloadedFile
from app.providers.teacher_qual.archive import (
    MemberContext,
    NativeArchive,
    NativeMember,
    extract_native_members,
    member_locator,
    parse_member_locator,
)
from app.providers.teacher_qual.client import TeacherQualClient
from app.providers.teacher_qual.provider import TeacherQualProvider
from app.publication_metadata import derive_public_metadata, publication_paper_subject
from app.storage import MirrorStore
from app.sync import sync_exam_pages


def digest(data):
    return hashlib.sha256(data).hexdigest()


def zip_bytes(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return stream.getvalue()


@pytest.fixture
def native_fixture():
    question = b"%PDF-1.7 native question"
    word = bytes.fromhex("d0cf11e0a1b11ae1") + b"original Word alternative"
    old_answer = b"%PDF-1.7 original answer before change"
    nested = zip_bytes([("試題.pdf", question), ("試題.doc", word), ("答案_更改前.pdf", old_answer)])
    original = zip_bytes([("原始類科.zip", nested), (".DS_Store", b"operating-system metadata")])
    question_context = MemberContext("10", "幼兒園類科", "教育原理與制度", "question")
    answer_context = replace(question_context, file_role="answer")
    members = (
        NativeMember(("原始類科.zip", "試題.pdf"), digest(question), (question_context,), "",
                     "exact_current_SHA256"),
        NativeMember(("原始類科.zip", "試題.doc"), digest(word), (question_context,), "",
                     "paired_reviewed_native_PDF_and_Word_headers"),
        NativeMember(("原始類科.zip", "答案_更改前.pdf"), digest(old_answer), (answer_context,),
                     "來源整包，檔名：更改前", "reviewed_native_shared_special_education_answer_before_change"),
        NativeMember((".DS_Store",), digest(b"operating-system metadata"), (), "",
                     "excluded_operating_system_metadata"),
    )
    fact = NativeArchive("teacher-qual-106", digest(original), members)
    html = '''<table><tr><td>106年試題及參考答案</td></tr><tr><td>
    考題及參考答案 全部下載<a href="ShowPicOut2.aspx?ASParam=original">文件下載</a>
    </td></tr></table>'''
    cell = '''<span id="ctl00_ContentPlaceHolder1_frmGrd_DataGrid_ctl02_frmGrd_sbid01">
    教育原理與制度<br>考題<a href="ShowPicOut2.aspx?ASParam=question" title="下載pdf檔案">
    </a></span>'''
    client = TeacherQualClient()

    def download(url):
        body = original if url.endswith("ASParam=original") else question
        return DownloadedFile(body, "application/octet-stream", "ShowPicOut2.aspx")

    with (
        patch("app.providers.teacher_qual.archive.native_archives", return_value=(fact,)),
        patch.object(client, "category_listings", return_value=[
            ("10", "幼兒園類科", html + cell), ("99", "全部類科", html),
        ]),
        patch.object(client, "_download_url", side_effect=download) as network,
    ):
        yield client, fact, original, word, old_answer, network


def test_sync_conserves_word_and_before_change_answers_with_native_formats(tmp_path, native_fixture):
    client, fact, original, word, answer, network = native_fixture
    pages, catalog, failures = sync_exam_pages(
        TeacherQualProvider(client), [("teacher-qual-106", 2017)],
        MirrorStore(tmp_path / "mirror"), [], "", refresh_files=True,
    )
    assert not failures and len(catalog.papers) == 3
    assert len(pages[0].attachments) == 1
    assert (tmp_path / "mirror" / pages[0].attachments[0].storage_key).read_bytes() == original
    assert {p.checksum for p in catalog.papers} == {
        fact.members[0].checksum, digest(word), digest(answer),
    }
    word_paper = next(p for p in catalog.papers if p.checksum == digest(word))
    assert word_paper.storage_key.endswith(".doc")
    answer_paper = next(p for p in catalog.papers if p.file_type == "answer")
    assert "更改前" in answer_paper.subject_name_raw
    assert answer_paper.source_material.date.basis == "exam_year"
    assert all(p.track_id == "teacher-qualification-kindergarten" for p in catalog.papers)
    assert network.call_count == 2
    for paper in catalog.papers:
        assert digest((tmp_path / "mirror" / paper.storage_key).read_bytes()) == paper.checksum


def test_native_locators_reject_unreviewed_members_and_metadata(native_fixture):
    _, fact, _, _, _, _ = native_fixture
    uri = member_locator("https://tqa.rcpet.edu.tw/TEA_Exam/ShowPicOut2.aspx?ASParam=original",
                         fact, fact.members[1])
    assert parse_member_locator(uri)[1:] == (fact, fact.members[1])
    with pytest.raises(ValueError, match="Unreviewed"):
        parse_member_locator(uri.replace("archive-sha256=", "archive-sha256=0"))
    metadata = member_locator(uri.split("#")[0], fact, fact.members[-1])
    with pytest.raises(ValueError, match="Unreviewed"):
        parse_member_locator(metadata)


def test_subject_summary_shares_the_subject_while_preserving_distinct_file_versions(
    tmp_path, native_fixture
):
    client, _, _, _, _, _ = native_fixture
    _, catalog, failures = sync_exam_pages(
        TeacherQualProvider(client), [("teacher-qual-106", 2017)],
        MirrorStore(tmp_path / "mirror"), [], "", refresh_files=True,
    )
    assert not failures and len(catalog.papers) == 3
    aliases, subjects = derive_public_metadata(
        catalog.papers, bundle_id=catalog.papers[0].bundle_id,
        canonical_name=catalog.papers[0].bundle_name,
    )
    assert subjects == ["教育原理與制度"]
    assert not any("更改前" in label for label in aliases)
    version = next(p for p in catalog.papers if "更改前" in p.subject_name_raw)
    assert publication_paper_subject(version) == "教育原理與制度"
    assert publication_paper_subject(replace(version, checksum="0" * 64)) == version.subject_name_raw


def test_changed_original_stops_before_any_public_paper_is_mirrored(tmp_path, native_fixture):
    client, _, original, _, _, _ = native_fixture
    with patch.object(client, "_download_url", return_value=DownloadedFile(
        original + b"changed", "application/zip", "changed.zip"
    )):
        _, catalog, failures = sync_exam_pages(
            TeacherQualProvider(client), [("teacher-qual-106", 2017)],
            MirrorStore(tmp_path / "mirror"), [], "", refresh_files=True,
        )
    assert not catalog.papers and failures[0].stage == "fetch"
    assert not list((tmp_path / "mirror").rglob("*.pdf"))


def test_all_native_members_are_required_even_when_pdf_reference_coverage_is_complete(native_fixture):
    _, fact, original, _, _, _ = native_fixture
    with pytest.raises(ValueError, match="Unreviewed or changed"):
        extract_native_members(replace(fact, members=(fact.members[0],)), original)
    missing = replace(fact.members[0], path_chain=("absent.pdf",))
    with pytest.raises(ValueError, match="lacks reviewed native members"):
        extract_native_members(replace(fact, members=(*fact.members, missing)), original)
    changed = replace(fact.members[1], checksum="0" * 64)
    with pytest.raises(ValueError, match="Unreviewed or changed"):
        extract_native_members(replace(fact, members=(fact.members[0], changed, *fact.members[2:])),
                               original)


@pytest.mark.parametrize("name", ["../escape.pdf", "/root.pdf", "C:/paper.pdf"])
def test_unsafe_native_paths_are_rejected(name):
    body = b"%PDF-1.7 question"
    data = zip_bytes([(name, body)])
    member = NativeMember((name,), digest(body), (), "", "excluded_operating_system_metadata")
    fact = NativeArchive("teacher-qual-106", digest(data), (member,))
    with pytest.raises(ValueError, match="Unsafe"):
        extract_native_members(fact, data)


@pytest.mark.parametrize("encoding", ["cp950", "utf-8"])
def test_reviewed_legacy_filename_encodings_preserve_chinese_paths(encoding):
    class LegacyInfo(zipfile.ZipInfo):
        def _encodeFilenameFlags(self):
            return self.filename.encode(encoding), self.flag_bits & ~0x800

    body = b"%PDF-1.7 question"
    info = LegacyInfo("教師試題.pdf")
    data = zip_bytes([(info, body)])
    member = NativeMember((info.filename,), digest(body), (), "", "excluded_operating_system_metadata")
    fact = NativeArchive("teacher-qual-106", digest(data), (member,))
    assert extract_native_members(fact, data) == {(info.filename,): body}
