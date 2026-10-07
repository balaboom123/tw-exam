import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from app.history_audit import build_history_coverage_audit, history_audit_exit_code
from app.models import ExamAttachment, NormalizedCatalog, NormalizedPaper, ParsedPaper, SourceExamPage
from app.paths import provider_paths
from app.providers.base import DownloadedFile
from app.providers.sfi_cert.provider import SfiCertProvider
from app.providers.tabf_cert.provider import TabfCertProvider
from app.publisher import write_provider_state
from app.source_revisions import (
    audit_source_revisions,
    load_source_revisions,
    retain_superseded_sources,
    revision_blob_key,
    revision_journal_path,
)
from app.storage import MirrorStore
from app.sync import sync_exam_pages


ORIGINAL = b"%PDF-1.7 original source paper"
REVISED = b"%PDF-1.7 corrected source paper"
KEY = "providers/moex/115/115030/101/0101/question.pdf"


def paper(data=ORIGINAL, **changes):
    return replace(
        NormalizedPaper(
            provider_id="moex", canonical_id="nurse", canonical_name="Nurse",
            year_roc=115, exam_name_raw="115年專技高考護理師",
            category_raw="護理師", subject_name_raw="基礎醫學",
            paper_code="101-0101-question", file_type="question",
            download_url_source="https://example.test/question.pdf",
            category_code="101", source_exam_id="115030", subject_code="0101",
            storage_key=KEY, checksum=hashlib.sha256(data).hexdigest(),
        ),
        **changes,
    )


def write_state(root, papers, pages=()):
    write_provider_state(
        provider_paths(root, "moex"), list(pages), NormalizedCatalog(papers, []), [], [], None,
    )


class MutableClient:
    provider_id = "moex"
    max_concurrency = 4

    def __init__(self, data, *, duplicate=False):
        self.data = data
        self.downloads = []
        self.duplicate = duplicate

    def fetch_exam_page(self, exam_code, year_ad):
        parsed = ParsedPaper(
            category_raw="護理師", category_code="101", subject_code="0101",
            subject_name_raw="基礎醫學", files={"question": "https://example.test/question.pdf"},
        )
        return SourceExamPage(
            provider_id="moex", source_exam_id=exam_code, year_ad=year_ad,
            year_roc=year_ad - 1911, exam_name_raw="115年專技高考護理師",
            attachments=[], papers=[parsed, replace(parsed)] if self.duplicate else [parsed],
        )

    def download_file(self, url):
        self.downloads.append(url)
        return DownloadedFile(self.data, "application/pdf", "question.pdf")


def sync(client, store, **options):
    return sync_exam_pages(client, [("115030", 2026)], store, [], "", **options)


def test_refreshed_same_url_retains_previous_bytes_and_source_reference(tmp_path):
    store = MirrorStore(tmp_path / "mirror")
    pages, catalog, failures = sync(MutableClient(ORIGINAL), store)
    assert failures == []
    write_state(tmp_path, catalog.papers, pages)
    original_paper = catalog.papers[0]
    client = MutableClient(REVISED, duplicate=True)
    pages, catalog, failures = sync(client, store, refresh_files=True)
    assert failures == []
    assert client.downloads == [original_paper.download_url_source]
    write_state(tmp_path, catalog.papers, pages)

    provider = provider_paths(tmp_path, "moex")
    entries = load_source_revisions(provider)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["reason"] == "payload_replaced"
    assert entry["source_record"]["checksum"] == original_paper.checksum
    assert entry["source_record"]["category_raw"] == original_paper.category_raw
    assert entry["source_record"]["file_type"] == "question"
    assert entry["source_record"]["download_url_source"] == original_paper.download_url_source
    assert "download_url_bundle" not in entry["source_record"]
    assert (store.root / entry["blob_storage_key"]).read_bytes() == ORIGINAL
    assert (store.root / catalog.papers[0].storage_key).read_bytes() == REVISED
    journal_bytes = revision_journal_path(provider).read_bytes()
    write_state(tmp_path, catalog.papers, pages)
    assert revision_journal_path(provider).read_bytes() == journal_bytes
    assert audit_source_revisions(provider, verify_mirror=True)["verified_payload_count"] == 1


def test_retired_role_and_url_and_attachment_keep_recovery_evidence(tmp_path):
    store = MirrorStore(tmp_path / "mirror")
    store.write_bytes(KEY, ORIGINAL)
    old = paper()
    attachment = ExamAttachment(
        "全部答案", "all_answers", "https://example.test/all.pdf",
        storage_key=KEY, checksum=old.checksum,
    )
    page = SourceExamPage("115030", 2026, 115, old.exam_name_raw, [attachment], [], "moex")
    write_state(tmp_path, [old], [page])
    corrected = replace(old, file_type="corrected_answer", download_url_source="https://example.test/corrected.pdf")
    write_state(tmp_path, [corrected], [replace(page, attachments=[])])
    entries = load_source_revisions(provider_paths(tmp_path, "moex"))
    assert {entry["record_type"] for entry in entries} == {"paper", "attachment"}
    assert {entry["reason"] for entry in entries} == {"source_reference_retired"}
    assert {entry["source_record"]["file_type"] for entry in entries} == {"question", "all_answers"}
    assert len({entry["blob_storage_key"] for entry in entries}) == 1


def test_extension_cleanup_preserves_old_revision_before_unlink(tmp_path):
    store = MirrorStore(tmp_path / "mirror")
    store.write_bytes(KEY, ORIGINAL)
    store.write_bytes(KEY.removesuffix(".pdf") + ".zip", b"new archive")
    store.delete_matching_except(KEY.removesuffix(".pdf"), KEY.removesuffix(".pdf") + ".zip")
    assert not (store.root / KEY).exists()
    old = paper()
    provider = provider_paths(tmp_path, "moex")
    assert retain_superseded_sources(provider, [], [old], [], []) == 1
    entry = load_source_revisions(provider)[0]
    assert (store.root / entry["blob_storage_key"]).read_bytes() == ORIGINAL


@pytest.mark.parametrize("scoped", [True, False])
def test_retirement_copies_existing_provider_payload_without_hard_link(tmp_path, scoped):
    old = paper(storage_key=KEY if scoped else KEY.removeprefix("providers/moex/"))
    source = tmp_path / "mirror" / KEY
    source.parent.mkdir(parents=True)
    source.write_bytes(ORIGINAL)
    provider = provider_paths(tmp_path, "moex")
    retain_superseded_sources(provider, [], [old], [], [])
    blob = tmp_path / "mirror" / load_source_revisions(provider)[0]["blob_storage_key"]
    assert not blob.samefile(source)
    source.write_bytes(REVISED)
    assert blob.read_bytes() == ORIGINAL


def test_prune_and_dedupe_protect_immutable_revisions(tmp_path):
    store = MirrorStore(tmp_path / "mirror")
    store.write_bytes(KEY, ORIGINAL)
    store.write_bytes(KEY, REVISED, overwrite=True)
    blob_key = revision_blob_key("moex", paper().checksum, ".pdf")
    same_old = store.write_bytes("providers/moex/115/other/question.pdf", ORIGINAL)
    another_old = store.write_bytes("providers/moex/115/shared/question.pdf", ORIGINAL)
    blob = store.root / blob_key
    assert not blob.samefile(same_old.path)
    assert same_old.path.samefile(another_old.path)
    store.deduplicate_existing(apply=True)
    assert not blob.samefile(same_old.path)
    same_old.path.write_bytes(b"accidental in-place modification")
    assert blob.read_bytes() == ORIGINAL
    result = store.prune_unreferenced_provider("moex", [], NormalizedCatalog([paper(REVISED)], []), apply=True)
    assert result.removed_files == 2
    assert blob.read_bytes() == ORIGINAL
    with pytest.raises(ValueError, match="immutable"):
        store.write_bytes(blob_key, REVISED, overwrite=True)
    assert blob.read_bytes() == ORIGINAL


def test_missing_verified_previous_bytes_abort_before_provider_state_changes(tmp_path):
    write_state(tmp_path, [paper()])
    provider = provider_paths(tmp_path, "moex")
    before = {path: path.read_bytes() for path in provider.data_dir.rglob("*.json")}
    with pytest.raises(ValueError, match="restore the checksum-verified original"):
        write_state(tmp_path, [paper(REVISED)])
    assert {path: path.read_bytes() for path in provider.data_dir.rglob("*.json")} == before
    assert not revision_journal_path(provider).exists()


def test_metadata_only_revision_never_claims_verified_bytes(tmp_path):
    old = paper(checksum="", storage_key="")
    provider = provider_paths(tmp_path, "moex")
    retain_superseded_sources(provider, [], [old], [], [])
    audit = audit_source_revisions(provider, verify_mirror=True)
    assert audit["revision_count"] == 1
    assert audit["unverified_source_reference_count"] == 1
    assert audit["verified_payload_count"] == 0
    assert load_source_revisions(provider)[0]["blob_storage_key"] is None


def test_generated_revision_journal_satisfies_versioned_contract(tmp_path):
    provider = provider_paths(tmp_path, "moex")
    MirrorStore(tmp_path / "mirror").write_bytes(KEY, ORIGINAL)
    retain_superseded_sources(provider, [], [paper()], [], [])
    schema = Path(__file__).resolve().parents[1] / "schemas/provider-source-revisions-v1.schema.json"
    validator = Draft202012Validator(json.loads(schema.read_text()), format_checker=FormatChecker())
    validator.validate(json.loads(revision_journal_path(provider).read_text()))


@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_strict_history_audit_detects_lost_revision_bytes(tmp_path, damage):
    store = MirrorStore(tmp_path / "mirror")
    store.write_bytes(KEY, ORIGINAL)
    write_state(tmp_path, [paper()])
    store.write_bytes(KEY, REVISED, overwrite=True)
    write_state(tmp_path, [paper(REVISED)])
    provider = provider_paths(tmp_path, "moex")
    entry = load_source_revisions(provider)[0]
    blob = store.root / entry["blob_storage_key"]
    if damage == "missing":
        blob.unlink()
    else:
        blob.write_bytes(b"damaged recovery bytes")
    report = build_history_coverage_audit(tmp_path, provider_ids=["moex"], check_mirror=True)
    assert report["summary"]["source_revision_download_gap"] == 1
    assert history_audit_exit_code(report, strict=True) == 1
    metadata = audit_source_revisions(provider, verify_mirror=False)
    assert metadata["mirror_checked"] is False
    assert metadata["verified_payload_count"] == 0


@pytest.mark.parametrize("mutation", ["version", "owner", "id", "blob", "checksum_type", "timestamp"])
def test_revision_reader_rejects_corrupted_contracts(tmp_path, mutation):
    provider = provider_paths(tmp_path, "moex")
    MirrorStore(tmp_path / "mirror").write_bytes(KEY, ORIGINAL)
    retain_superseded_sources(provider, [], [paper()], [], [])
    path = revision_journal_path(provider)
    document = json.loads(path.read_text())
    entry = document["revisions"][0]
    if mutation == "version":
        document["schema_version"] = 9
    elif mutation == "owner":
        entry["source_record"]["provider_id"] = "ceec_gsat"
    elif mutation == "id":
        entry["id"] = "0" * 64
    elif mutation == "blob":
        entry["blob_storage_key"] = KEY
    elif mutation == "checksum_type":
        entry["source_record"]["checksum"] = []
    else:
        entry["retained_at"] = "2026-10-07"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        load_source_revisions(provider)


def test_invalid_refresh_keeps_original_file_and_reports_failure(tmp_path):
    store = MirrorStore(tmp_path / "mirror")
    store.write_bytes(KEY, ORIGINAL)
    pages, catalog, failures = sync(MutableClient(b"<html>upstream error</html>"), store, refresh_files=True)
    assert len(failures) == 1
    assert failures[0].stage == "download"
    assert (store.root / KEY).read_bytes() == ORIGINAL
    assert not (store.root / "providers/moex/recovery").exists()


def test_unchanged_refresh_preserves_shared_storage_and_creates_no_revision(tmp_path):
    store = MirrorStore(tmp_path / "mirror")
    current = store.write_bytes(KEY, ORIGINAL)
    shared = store.write_bytes("providers/moex/115/other/question.pdf", ORIGINAL)
    assert current.path.samefile(shared.path)
    inode = current.path.stat().st_ino
    pages, catalog, failures = sync(MutableClient(ORIGINAL), store, refresh_files=True)
    assert failures == []
    assert current.path.stat().st_ino == inode
    assert current.path.samefile(shared.path)
    assert not (store.root / "providers/moex/recovery").exists()
    write_state(tmp_path, catalog.papers, pages)
    write_state(tmp_path, catalog.papers, pages)
    assert load_source_revisions(provider_paths(tmp_path, "moex")) == []


def test_default_reuse_and_mutable_provider_refresh_policy(tmp_path):
    store = MirrorStore(tmp_path / "mirror")
    store.write_bytes(KEY, ORIGINAL)
    client = MutableClient(REVISED)
    _pages, catalog, failures = sync(client, store)
    assert failures == []
    assert client.downloads == []
    assert catalog.papers[0].checksum == paper().checksum
    client.refresh_files_on_sync = True
    _pages, catalog, failures = sync(client, store)
    assert failures == []
    assert catalog.papers[0].checksum == paper(REVISED).checksum
    assert SfiCertProvider.refresh_files_on_sync is True
    assert TabfCertProvider.refresh_files_on_sync is True
