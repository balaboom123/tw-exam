"""Archive import is a byte-conservation boundary, not a taxonomy approval."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.models import ExamAttachment, NormalizedCatalog, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata
from app.providers.teacher_recruit_newtaipei import archive_plan
from app.providers.teacher_recruit_newtaipei import provider as provider_module
from app.providers.teacher_recruit_newtaipei.archive_plan import (
    ArchiveMember,
    ReviewedArchive,
    native_archives,
    native_archives_path,
    read_native_archives,
    resolve_member_locator,
)
from app.providers.teacher_recruit_newtaipei.provider import NewTaipeiTeacherRecruitProvider
from scripts import import_newtaipei_archive as importer

PARENT = b"Rar!\x1a\x07\x01\x00reviewed native parent"
PDFS = (b"%PDF-1.7\nquestion\n%%EOF", b"%PDF-1.7\nanswer\n%%EOF")


def fixture_plan() -> ReviewedArchive:
    directory = ArchiveMember("native", "directory", 0, "", "", "", "", 0, ())
    files = tuple(
        ArchiveMember(
            f"native/{role}.pdf",
            "file",
            len(data),
            hashlib.sha256(data).hexdigest(),
            "教育專業科",
            role,
            "新北市立國民中學115學年度教師聯合甄選｜教育專業科",
            1,
            (1,),
        )
        for role, data in zip(("question", "answer"), PDFS, strict=True)
    )
    return ReviewedArchive(
        "teacher-recruit-newtaipei-115-junior",
        115,
        "新北市立國民中學115學年度教師聯合甄選",
        "https://career.ntpc.edu.tw/web-elec-bulletin/open/oauth_data/op_api/download/"
        "c28f85b97a6043149fb89de76d97f3aa",
        hashlib.sha256(PARENT).hexdigest(),
        len(PARENT),
        "2026-10-30",
        "Fixture archive; no recruitment-category approval.",
        (directory, *files),
    )


def entries(plan: ReviewedArchive) -> list[importer.NativeEntry]:
    return [
        importer.NativeEntry(plan.members[0].path, "directory", 0, b""),
        *(
            importer.NativeEntry(m.path, "file", m.size, data)
            for m, data in zip(plan.files, PDFS, strict=True)
        ),
    ]


def write_plan(root: Path, plan: ReviewedArchive) -> None:
    path = native_archives_path(root)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provider_id": "teacher_recruit_newtaipei",
                "archives": [asdict(plan)],
            },
            ensure_ascii=False,
        )
    )


def install_plan(monkeypatch, plan: ReviewedArchive) -> None:
    monkeypatch.setattr(archive_plan, "native_archives", lambda: (plan,))
    monkeypatch.setattr(provider_module, "native_archives", lambda: (plan,))
    monkeypatch.setattr(provider_module, "reviewed_document_checksum", lambda url: "")


def test_reviewed_complete_container_and_focused_visual_scope() -> None:
    (archive,) = native_archives()
    assert archive.checksum == "92b51ab85396fe4addc31218ccd46709519daa713fd9443b5899f41bdbe6da58"
    assert len(archive.members) == 89
    assert len(archive.files) == 58
    assert len({m.checksum for m in archive.files}) == 58
    assert sum(m.page_count for m in archive.files) == 322
    assert sum(len(m.inspected_pages) for m in archive.files) == 88
    references = [m for m in archive.files if m.file_type == "source_document"]
    assert len(references) == 1
    assert references[0].subject_name == "輔導專業知能科"
    assert references[0].page_count == 1
    assert len([m for m in archive.files if m.file_type == "answer"]) == 28


@pytest.mark.parametrize(
    "change",
    [
        lambda p: replace(p, members=p.members[:-1]),
        lambda p: replace(p, members=(*p.members, p.files[0])),
        lambda p: replace(
            p, members=(p.members[0], replace(p.files[0], path="../escape.pdf"), p.files[1])
        ),
        lambda p: replace(
            p, members=(p.members[0], replace(p.files[0], inspected_pages=()), p.files[1])
        ),
        lambda p: replace(
            p, members=(p.members[0], replace(p.files[0], path="missing/question.pdf"), p.files[1])
        ),
        lambda p: replace(p, size=archive_plan.MAX_ARCHIVE_BYTES + 1),
        lambda p: replace(p, source_url=p.source_url.replace("ntpc.edu.tw", "example.org")),
    ],
)
def test_contract_rejects_incomplete_roles_unsafe_paths_and_unreviewed_sources(tmp_path, change):
    write_plan(tmp_path, change(fixture_plan()))
    with pytest.raises(ValueError):
        read_native_archives(native_archives_path(tmp_path))


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "duplicate",
        "case-collision",
        "extra",
        "traversal",
        "symlink",
        "encrypted",
        "special",
        "truncated",
        "late-checksum",
        "directory-data",
    ],
)
def test_complete_preflight_rejects_malformed_entries_before_any_mirror_write(
    tmp_path,
    monkeypatch,
    fault,
):
    plan = fixture_plan()
    write_plan(tmp_path, plan)
    source = tmp_path / "native.rar"
    source.write_bytes(PARENT)
    observed = entries(plan)
    if fault == "missing":
        observed.pop()
    elif fault == "duplicate":
        observed.append(observed[-1])
    elif fault == "case-collision":
        observed.append(replace(observed[-1], path=observed[-1].path.upper()))
    elif fault == "extra":
        observed.append(replace(observed[-1], path="native/extra.pdf"))
    elif fault == "traversal":
        observed[-1] = replace(observed[-1], path="../escape.pdf")
    elif fault == "symlink":
        observed[-1] = replace(observed[-1], linked=True)
    elif fault == "encrypted":
        observed[-1] = replace(observed[-1], encrypted=True)
    elif fault == "special":
        observed[-1] = replace(observed[-1], kind="special")
    elif fault == "truncated":
        observed[-1] = replace(observed[-1], data=observed[-1].data[:-1])
    elif fault == "late-checksum":
        observed[-1] = replace(observed[-1], data=observed[-1].data.replace(b"answer", b"broken"))
    else:
        observed[0] = replace(observed[0], data=b"unexpected directory content")
    monkeypatch.setattr(importer, "read_rar_entries", lambda parent: iter(observed))
    with pytest.raises(ValueError):
        importer.import_archive(tmp_path, source)
    assert not (tmp_path / "mirror").exists()
    assert not (tmp_path / "escape.pdf").exists()


def test_unknown_parent_is_rejected_before_decompressor(tmp_path, monkeypatch):
    write_plan(tmp_path, fixture_plan())
    source = tmp_path / "wrong.rar"
    source.write_bytes(PARENT + b"new revision")
    decoder = Mock(side_effect=AssertionError("must not decompress an unknown parent"))
    monkeypatch.setattr(importer, "read_rar_entries", decoder)
    with pytest.raises(ValueError, match="Unreviewed"):
        importer.import_archive(tmp_path, source)
    decoder.assert_not_called()


def test_import_conserves_native_bytes_is_idempotent_and_checks_existing_targets(
    tmp_path,
    monkeypatch,
):
    plan = fixture_plan()
    write_plan(tmp_path, plan)
    source = tmp_path / "native.rar"
    source.write_bytes(PARENT)
    monkeypatch.setattr(importer, "read_rar_entries", lambda parent: iter(entries(plan)))
    check = importer.import_archive(tmp_path, source, check_only=True)
    assert check["check_only"] is True
    assert not (tmp_path / "mirror").exists()
    first = importer.import_archive(tmp_path, source)
    assert first == importer.import_archive(tmp_path, source)
    assert first["classification_approved"] is False
    for member, expected in zip(plan.files, PDFS, strict=True):
        assert (tmp_path / "mirror" / plan.storage_key(member)).read_bytes() == expected
    conflict = tmp_path / "mirror" / plan.storage_key(plan.files[-1])
    conflict.write_bytes(b"retained conflicting bytes")
    with pytest.raises(ValueError, match="differs"):
        importer.import_archive(tmp_path, source)
    assert conflict.read_bytes() == b"retained conflicting bytes"
    assert source.read_bytes() == PARENT


def test_runtime_uses_verified_cache_and_one_fresh_parent_observation(tmp_path, monkeypatch):
    plan = fixture_plan()
    install_plan(monkeypatch, plan)
    client = Mock()
    client.download_file.return_value = DownloadedFile(
        PARENT, "application/octet-stream", "native.rar"
    )
    metadata = ResponseMetadata(url=plan.source_url, status=200)
    client.head.return_value = metadata
    provider = NewTaipeiTeacherRecruitProvider(client, repo_root=tmp_path)
    for member, data in zip(plan.files, PDFS, strict=True):
        target = tmp_path / "mirror" / plan.storage_key(member)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        locator = plan.locator(member)
        assert resolve_member_locator(locator) == (plan, member)
        assert provider.head(locator) is metadata
        assert provider.download_file(locator).data == data
        assert provider.reviewed_file_checksum(locator) == member.checksum
        assert provider.download_file(locator).data == data
    client.download_file.assert_called_once_with(plan.source_url)
    assert all(call.args == (plan.source_url,) for call in client.head.call_args_list)


@pytest.mark.parametrize("fault", ["missing", "corrupt", "new-parent", "forged-locator"])
def test_runtime_fails_closed_for_missing_or_changed_evidence(tmp_path, monkeypatch, fault):
    plan = fixture_plan()
    install_plan(monkeypatch, plan)
    member = plan.files[0]
    client = Mock()
    client.download_file.return_value = DownloadedFile(
        PARENT + b"changed" if fault == "new-parent" else PARENT,
        "application/octet-stream",
        "native.rar",
    )
    provider = NewTaipeiTeacherRecruitProvider(client, repo_root=tmp_path)
    locator = plan.locator(member)
    target = tmp_path / "mirror" / plan.storage_key(member)
    if fault != "missing":
        target.parent.mkdir(parents=True)
        target.write_bytes(b"%PDF-corrupted" if fault == "corrupt" else PDFS[0])
    if fault == "forged-locator":
        locator += "&member=another.pdf"
    with pytest.raises(ValueError):
        provider.download_file(locator)
    if fault == "forged-locator":
        client.download_file.assert_not_called()


def test_reference_member_survives_pruning_of_its_redundant_import_alias(tmp_path, monkeypatch):
    initial = fixture_plan()
    member = replace(initial.files[-1], file_type="source_document")
    plan = replace(initial, members=(*initial.members[:-1], member))
    install_plan(monkeypatch, plan)
    locator = plan.locator(member)
    key = f"providers/teacher_recruit_newtaipei/115/{plan.source_exam_id}/exam/source_document.pdf"
    attachment = ExamAttachment(
        "未提供參考答案", "source_document", locator, storage_key=key, checksum=member.checksum
    )
    page = SourceExamPage(
        plan.source_exam_id,
        2026,
        115,
        plan.programme_name,
        [attachment],
        [],
        "teacher_recruit_newtaipei",
    )
    monkeypatch.setattr(
        "app.state.load_provider_state", lambda paths: ([page], NormalizedCatalog([], []), [])
    )
    target = tmp_path / "mirror" / key
    target.parent.mkdir(parents=True)
    target.write_bytes(PDFS[-1])
    assert not (tmp_path / "mirror" / plan.storage_key(member)).exists()
    client = Mock()
    client.download_file.return_value = DownloadedFile(PARENT, "application/vnd.rar", "native.rar")
    provider = NewTaipeiTeacherRecruitProvider(client, repo_root=tmp_path)
    assert provider.download_file(locator).data == PDFS[-1]
    attachment.checksum = "0" * 64
    with pytest.raises(ValueError, match="missing"):
        NewTaipeiTeacherRecruitProvider(client, repo_root=tmp_path).download_file(locator)
