"""Native projection conserves source containers, references and review boundaries."""

import hashlib
import io
import json
import stat
import zipfile
import zlib
from dataclasses import replace
from pathlib import Path

import pytest

from app.models import ParsedPaper, SourceExamPage
from app.normalizer import normalize_papers, renormalize_catalog
from app.paths import provider_paths
from app.providers.base import DownloadedFile
from app.providers.twc_recruit.archive import (
    NativeArchive,
    NativeMember,
    decoded_member_name,
    extract_native_members,
    member_locator,
    native_archive_path,
    native_archives,
    parse_member_locator,
    read_native_archives,
    validate_native_archives,
)
from app.providers.twc_recruit.client import TwcRecruitClient, TwcRecruitEntry
from app.providers.twc_recruit.identity import archive_identities, validate_archive_identities
from app.providers.twc_recruit.provider import TwcRecruitProvider
from app.publisher import write_provider_state
from app.source_material import source_year_roc
from app.source_revisions import load_source_revisions
from app.storage import MirrorStore
from app.sync import sync_exam_pages

ROOT = Path(__file__).resolve().parents[1]
URL = "https://www.water.gov.tw/ch/ServerFile/Get/11111111-1111-1111-1111-111111111111?nodeId=715"
PDF = b"%PDF-1.4\nNative questions and answers\n%%EOF\n"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def make_archive(entries, *, year=115, roles=None, infos=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, payload in entries.items():
            info = infos[name] if infos else zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            archive.writestr(info, payload)
    data = buffer.getvalue()
    members = tuple(
        NativeMember(
            name,
            sha(payload),
            (roles or {}).get(name, "question_answer"),
            "專業科目A：高中(職)物理化學" if name.endswith(".pdf") else "",
            "操作類-乙、化驗類" if name.endswith(".pdf") else "",
            "疑義後" if "疑義後" in name else "",
        )
        for name, payload in entries.items()
    )
    return NativeArchive(year, URL, sha(data), "", members), data


def configured_client(monkeypatch, fact, data):
    monkeypatch.setattr("app.providers.twc_recruit.archive.native_archives", lambda: (fact,))
    monkeypatch.setattr("app.providers.twc_recruit.client.native_archives", lambda: (fact,))
    original = next(row for row in archive_identities() if row.year_roc == fact.year_roc)
    annual = replace(original, checksum=fact.checksum, source_url=fact.source_url)
    monkeypatch.setattr("app.providers.twc_recruit.identity.archive_identities", lambda: (annual,))
    client = TwcRecruitClient()
    client._cached_entries = [
        TwcRecruitEntry(fact.year_roc, fact.year_roc + 1911, "115年試題(解答).zip", URL)
    ]
    requests = []

    def download(url, **kwargs):
        requests.append(url)
        return DownloadedFile(data, "application/zip", "source.zip")

    monkeypatch.setattr(client.http, "download", download)
    return client, requests


def catalogue_document(fact):
    return {
        "schema_version": 1,
        "provider_id": "twc_recruit",
        "archives": [
            {
                "programme_year_roc": fact.year_roc,
                "source_url": fact.source_url,
                "checksum": fact.checksum,
                "reviewed_at": "2026-10-11",
                "review_reason": fact.review_reason,
                "members": [
                    {
                        "native_name": member.name,
                        "checksum": member.checksum,
                        "file_role": member.file_role,
                        "subject_label": member.subject_label,
                        "native_category_header": member.category_header,
                        "revision_notice": member.revision_notice,
                        "category_ids": list(member.category_ids),
                        "paper_slot": member.paper_slot,
                    }
                    for member in fact.members
                ],
                "categories": [
                    {
                        "category_id": category.category_id,
                        "label": category.label,
                        "track_id": category.track_id,
                        "native_categories": list(category.native_categories),
                        "variants": [
                            {"id": variant_id, "label": label}
                            for variant_id, label in category.variants
                        ],
                        "evidence_url": category.evidence_url,
                        "evidence_label": category.evidence_label,
                        "review_reason": category.review_reason,
                    }
                    for category in fact.categories
                ],
            }
        ],
    }


def test_projection_retains_original_when_attachment_downloads_are_disabled(tmp_path, monkeypatch):
    fact, data = make_archive(
        {"原目錄/標準答案.pdf": PDF, "另一目錄/標準答案.pdf": PDF, "原目錄/Thumbs.db": b"desktop"},
        roles={"原目錄/Thumbs.db": "source_artifact"},
    )
    client, requests = configured_client(monkeypatch, fact, data)
    pages, catalog, failures = sync_exam_pages(
        TwcRecruitProvider(client),
        [("twc-recruit-115", 2026)],
        MirrorStore(tmp_path / "mirror"),
        [],
        "",
        download_attachments=False,
    )
    assert not failures
    assert requests == [URL]
    assert len(pages[0].papers) == len(catalog.papers) == 2
    attachment = pages[0].attachments[0]
    assert attachment.file_type == "source_archive"
    assert attachment.checksum == fact.checksum
    assert (tmp_path / "mirror" / attachment.storage_key).read_bytes() == data
    assert len({paper.download_url_source for paper in catalog.papers}) == 2
    paths = [tmp_path / "mirror" / paper.storage_key for paper in catalog.papers]
    assert all(path.read_bytes() == PDF for path in paths)
    assert paths[0].stat().st_ino == paths[1].stat().st_ino
    assert {paper.file_type for paper in catalog.papers} == {"question_answer"}
    assert all(paper.classification_confidence == "review" for paper in catalog.papers)
    assert all(
        "occupational applicability" in paper.classification_reason for paper in catalog.papers
    )
    assert all(paper.stage_id == "stage-1" for paper in catalog.papers)
    assert all(source_year_roc(paper) == 115 for paper in catalog.papers)
    assert renormalize_catalog(catalog, []).papers == catalog.papers


def test_whole_archive_history_survives_native_migration(tmp_path, monkeypatch):
    fact, data = make_archive({"原目錄/題目.pdf": PDF})
    client, _ = configured_client(monkeypatch, fact, data)
    provider = provider_paths(tmp_path, "twc_recruit")
    store = MirrorStore(tmp_path / "mirror")
    original = store.write_bytes("providers/twc_recruit/115/old/question_answer.zip", data)
    before = SourceExamPage(
        "twc-recruit-115",
        2026,
        115,
        "115年試題(解答).zip",
        [],
        [
            ParsedPaper(
                "台水評價職位人員甄試",
                "115",
                "twc-recruit-115-bundle",
                "115年試題(解答).zip",
                {"question_answer": URL},
                mirror_files={
                    "question_answer": {
                        "checksum": fact.checksum,
                        "storage_key": original.storage_key,
                    }
                },
            )
        ],
        provider_id="twc_recruit",
    )
    before_catalog = normalize_papers(
        before.source_exam_id,
        2026,
        before.exam_name_raw,
        before.papers,
        [],
        "",
        {},
        provider_id="twc_recruit",
    )
    write_provider_state(provider, [before], before_catalog, [], [], None)
    pages, catalog, failures = sync_exam_pages(
        TwcRecruitProvider(client),
        [("twc-recruit-115", 2026)],
        store,
        [],
        "",
        previous_pages=[before],
    )
    assert not failures
    write_provider_state(provider, pages, catalog, [], failures, None)
    retained = load_source_revisions(provider)
    assert len(retained) == 1
    assert retained[0]["source_record"]["subject_code"] == "twc-recruit-115-bundle"
    assert (tmp_path / "mirror" / retained[0]["blob_storage_key"]).read_bytes() == data
    assert (tmp_path / "mirror" / original.storage_key).read_bytes() == data
    path = native_archive_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(catalogue_document(fact)))
    assert validate_native_archives(tmp_path, verify_mirror=True) == 1


def test_changed_source_container_returns_to_outer_archive_review(monkeypatch):
    fact, _ = make_archive({"原目錄/題目.pdf": PDF})
    _, changed = make_archive({"原目錄/題目.pdf": PDF + b"Changed"})
    client, requests = configured_client(monkeypatch, fact, changed)
    page = client.fetch_exam_page("twc-recruit-115", 2026)
    assert not page.attachments
    assert page.papers[0].subject_code == "twc-recruit-115-bundle"
    page.papers[0].mirror_files = {"question_answer": {"checksum": sha(changed)}}
    normalized = normalize_papers(
        page.source_exam_id,
        2026,
        page.exam_name_raw,
        page.papers,
        [],
        "",
        {},
        provider_id="twc_recruit",
    )
    assert source_year_roc(normalized.papers[0]) is None
    assert normalized.papers[0].stage_id == "unknown"
    assert normalized.review_queue
    with pytest.raises(ValueError, match="changed"):
        client.download_file(member_locator(fact, fact.members[0]))
    assert requests == [URL]


def test_damaged_archive_does_not_project_partial_papers(tmp_path, monkeypatch):
    fact, good = make_archive({"good.pdf": PDF, "bad.pdf": PDF + b"Second"})
    offset = good.index(PDF)
    damaged = good[:offset] + b"!" + good[offset + 1 :]
    held = replace(fact, checksum=sha(damaged), review_reason="Native CRC failure", members=())
    client, requests = configured_client(monkeypatch, held, damaged)
    pages, catalog, failures = sync_exam_pages(
        TwcRecruitProvider(client),
        [("twc-recruit-115", 2026)],
        MirrorStore(tmp_path / "mirror"),
        [],
        "",
    )
    assert requests == [URL]
    assert not pages[0].attachments
    assert not pages[0].papers[0].mirror_files
    assert not catalog.papers
    assert len(failures) == 1
    assert failures[0].stage == "download"
    assert "CRC" in failures[0].message


def test_changed_member_locator_cannot_inherit_parent_material(monkeypatch):
    fact, data = make_archive({"疑義後.pdf": PDF})
    client, _ = configured_client(monkeypatch, fact, data)
    page = client.fetch_exam_page("twc-recruit-115", 2026)
    member = fact.members[0]
    page.papers[0].files = {
        member.file_role: member_locator(fact, member).replace("11111111-", "22222222-", 1)
    }
    page.papers[0].mirror_files = {member.file_role: {"checksum": member.checksum}}
    catalog = normalize_papers(
        page.source_exam_id,
        2026,
        page.exam_name_raw,
        page.papers,
        [],
        "",
        {},
        provider_id="twc_recruit",
    )
    assert source_year_roc(catalog.papers[0]) is None
    assert catalog.papers[0].stage_id == "unknown"
    assert catalog.papers[0].classification_confidence == "review"


def test_same_native_pdf_in_reviewed_archive_versions_uses_its_own_parent_date(monkeypatch):
    original, data = make_archive({"paper.pdf": PDF})
    buffer = io.BytesIO(data)
    with zipfile.ZipFile(buffer, "a") as archive:
        archive.comment = b"Reviewed container replacement"
    replacement_data = buffer.getvalue()
    replacement = replace(original, checksum=sha(replacement_data))
    annual = next(row for row in archive_identities() if row.year_roc == 115)
    client, _ = configured_client(monkeypatch, replacement, replacement_data)
    older_annual = replace(
        annual,
        source_url=URL,
        checksum=original.checksum,
        written_test_date=None,
        review_reason="Original sitting date requires review",
    )
    newer_annual = replace(annual, source_url=URL, checksum=replacement.checksum)
    monkeypatch.setattr(
        "app.providers.twc_recruit.identity.archive_identities",
        lambda: (older_annual, newer_annual),
    )
    monkeypatch.setattr(
        "app.providers.twc_recruit.archive.native_archives", lambda: (original, replacement)
    )
    monkeypatch.setattr(
        "app.providers.twc_recruit.client.native_archives", lambda: (original, replacement)
    )
    page = client.fetch_exam_page("twc-recruit-115", 2026)
    member = replacement.members[0]
    page.papers[0].mirror_files = {member.file_role: {"checksum": member.checksum}}
    catalog = normalize_papers(
        page.source_exam_id,
        2026,
        page.exam_name_raw,
        page.papers,
        [],
        "",
        {},
        provider_id="twc_recruit",
    )
    assert original.members[0].subject_code == member.subject_code
    assert member_locator(original, original.members[0]) != member_locator(replacement, member)
    assert catalog.papers[0].stage_id == "stage-1"
    assert source_year_roc(catalog.papers[0]) == 115


class Cp950Info(zipfile.ZipInfo):
    def _encodeFilenameFlags(self):
        return self.filename.encode("cp950"), self.flag_bits & ~0x800


def test_native_decoder_honors_cp950_and_verified_unicode_paths():
    legacy = "原目錄/更正題目.pdf"
    info = Cp950Info(legacy)
    fact, data = make_archive({legacy: PDF}, infos={legacy: info})
    assert extract_native_members(fact, data) == {legacy: PDF}
    raw = legacy.encode("cp950")
    unicode_name = "原目錄/疑義後題目.pdf"
    field = b"\x01" + zlib.crc32(raw).to_bytes(4, "little") + unicode_name.encode("utf-8")
    info.extra = b"\x75\x70" + len(field).to_bytes(2, "little") + field
    fact, data = make_archive({legacy: PDF}, infos={legacy: info})
    fact = replace(fact, members=(replace(fact.members[0], name=unicode_name),))
    assert extract_native_members(fact, data) == {unicode_name: PDF}
    info.extra = (
        b"\x75\x70" + len(field).to_bytes(2, "little") + field[:1] + b"\x00" * 4 + field[5:]
    )
    fact, data = make_archive({legacy: PDF}, infos={legacy: info})
    with pytest.raises(ValueError, match="Unicode path"):
        extract_native_members(fact, data)


@pytest.mark.parametrize(
    "name", ["../paper.pdf", "/paper.pdf", "folder\\paper.pdf", "CON.pdf", "folder/paper.pdf."]
)
def test_native_decoder_rejects_unsafe_paths(name):
    fact, data = make_archive({name: PDF})
    with pytest.raises(ValueError, match="Unsafe"):
        extract_native_members(fact, data)


def test_native_decoder_rejects_nulls_links_and_casefold_collisions():
    info = zipfile.ZipInfo("paper.pdf\x00hidden")
    with pytest.raises(ValueError, match="null"):
        decoded_member_name(info)
    info = zipfile.ZipInfo("paper.pdf")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    fact, data = make_archive({"paper.pdf": PDF}, infos={"paper.pdf": info})
    with pytest.raises(ValueError, match="linked"):
        extract_native_members(fact, data)
    fact, data = make_archive({"Paper.pdf": PDF, "paper.pdf": PDF})
    with pytest.raises(ValueError, match="duplicate"):
        extract_native_members(fact, data)


def test_inventory_and_role_validation_are_conservative(tmp_path):
    fact, data = make_archive({"paper.pdf": PDF, "unreviewed.pdf": PDF})
    with pytest.raises(ValueError, match="Unreviewed"):
        extract_native_members(replace(fact, members=fact.members[:1]), data)
    fact, data = make_archive({"paper.pdf": PDF})
    with pytest.raises(ValueError, match="coverage"):
        extract_native_members(
            replace(fact, members=(*fact.members, replace(fact.members[0], name="missing.pdf"))),
            data,
        )
    candidate = catalogue_document(fact)
    candidate["archives"][0]["members"][0]["file_role"] = "corrected_answer"
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(candidate))
    with pytest.raises(ValueError, match="file role"):
        read_native_archives(path)


@pytest.mark.parametrize(
    "fragment",
    [
        "zip-entry=paper.pdf",
        "archive-sha256=bad&zip-entry=paper.pdf",
        "archive-sha256=" + "a" * 64 + "&zip-entry=../paper.pdf",
        "archive-sha256=" + "a" * 64 + "&zip-entry=paper.pdf&zip-entry=other.pdf",
    ],
)
def test_member_locator_requires_unambiguous_parent_and_full_path(fragment):
    with pytest.raises(ValueError):
        parse_member_locator(URL + "#" + fragment)


@pytest.mark.repo_data
def test_reviewed_native_catalog_covers_retained_parent_and_paper_anchors():
    facts = native_archives()
    # Repository CI has no mirror tree. The release audit separately verifies
    # complete local containers and child bytes; this gate verifies references.
    assert len(facts) == validate_native_archives(ROOT)
    assert validate_archive_identities(ROOT) == len(archive_identities())
    held = [fact for fact in facts if fact.review_reason]
    assert [fact.year_roc for fact in held] == [107]
    assert not held[0].members
    assert (
        sum(member.file_role != "source_artifact" for fact in facts for member in fact.members)
        == 201
    )
    assert sum(bool(member.revision_notice) for fact in facts for member in fact.members) == 9
