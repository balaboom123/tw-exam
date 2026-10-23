import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from app.history_audit import build_history_coverage_audit, history_audit_exit_code
from app.models import ExamAttachment, NormalizedCatalog, NormalizedPaper, ParsedPaper, SourceExamPage
from app.paths import provider_paths
from app.providers.base import DownloadedFile
from app.providers.sfi_cert.provider import SfiCertProvider
from app.providers.tabf_cert.provider import TabfCertProvider
from app.providers.tii_cert.provider import TiiCertProvider
from app.publisher import write_provider_state
from app.source_revisions import (
    audit_source_revisions,
    load_source_revisions,
    retain_superseded_sources,
    revision_blob_key,
    revision_journal_path,
    validate_revision_conservation,
)
from app.source_material import SourceDate, SourceMaterial
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
    result = store.prune_unreferenced_provider(
        "moex", [], NormalizedCatalog([paper(REVISED)], []), apply=True, duplicates_only=False
    )
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


def test_corrected_locator_recovers_from_recorded_old_cache_path(tmp_path):
    store = MirrorStore(tmp_path / "mirror")
    old = paper()
    store.write_bytes(KEY, ORIGINAL)
    write_state(tmp_path, [old])
    corrected = replace(old, source_exam_id="corrected-event", storage_key=KEY.replace("115030", "corrected-event"))
    write_state(tmp_path, [corrected])
    provider = provider_paths(tmp_path, "moex")
    journal_before = revision_journal_path(provider).read_bytes()
    blob = store.root / load_source_revisions(provider)[0]["blob_storage_key"]
    target = store.root / corrected.storage_key
    target.unlink()
    blob.unlink()

    # The old snapshot predates both the corrected path and the revision blob.
    write_state(tmp_path, [corrected])
    assert target.read_bytes() == blob.read_bytes() == ORIGINAL
    assert not target.samefile(blob)
    assert not target.samefile(store.root / KEY)
    assert revision_journal_path(provider).read_bytes() == journal_before


@pytest.mark.parametrize("survivor", ["current", "blob"])
def test_recovery_reuses_exact_recorded_payload_with_independent_copies(tmp_path, survivor):
    store = MirrorStore(tmp_path / "mirror")
    store.write_bytes(KEY, ORIGINAL)
    old = paper()
    corrected = replace(old, source_exam_id="corrected-event", storage_key=KEY.replace("115030", "corrected-event"))
    write_state(tmp_path, [old])
    write_state(tmp_path, [corrected])
    provider = provider_paths(tmp_path, "moex")
    journal_before = revision_journal_path(provider).read_bytes()
    blob = store.root / load_source_revisions(provider)[0]["blob_storage_key"]
    target = store.root / corrected.storage_key
    (store.root / KEY).unlink()
    (blob if survivor == "current" else target).unlink()

    write_state(tmp_path, [corrected])
    assert target.read_bytes() == blob.read_bytes() == ORIGINAL
    assert not target.samefile(blob)
    target.write_bytes(REVISED)
    assert blob.read_bytes() == ORIGINAL
    assert revision_journal_path(provider).read_bytes() == journal_before


@pytest.mark.parametrize("unusable", ["wrong_checksum", "unrecorded", "other_provider", "symlink"])
def test_recovery_refuses_unproven_or_foreign_payload_candidates(tmp_path, unusable):
    store = MirrorStore(tmp_path / "mirror")
    store.write_bytes(KEY, ORIGINAL)
    old = paper()
    write_state(tmp_path, [old])
    provider = provider_paths(tmp_path, "moex")
    before = {path: path.read_bytes() for path in provider.data_dir.rglob("*.json")}
    original = store.root / KEY
    original.unlink()
    if unusable == "wrong_checksum":
        original.write_bytes(REVISED)
    else:
        alternative = store.root / ("providers/other/paper.pdf" if unusable == "other_provider" else "providers/moex/unrecorded.pdf")
        alternative.parent.mkdir(parents=True, exist_ok=True)
        alternative.write_bytes(ORIGINAL)
        if unusable == "symlink":
            original.symlink_to(alternative)
    with pytest.raises(ValueError, match="restore the checksum-verified original"):
        write_state(tmp_path, [paper(REVISED)])
    assert {path: path.read_bytes() for path in provider.data_dir.rglob("*.json")} == before


def test_recovery_preserves_existing_unknown_current_cache_bytes(tmp_path):
    store = MirrorStore(tmp_path / "mirror")
    store.write_bytes(KEY, ORIGINAL)
    old = paper()
    write_state(tmp_path, [old])
    corrected = replace(old, source_exam_id="corrected-event", storage_key=KEY.replace("115030", "corrected-event"))
    target = store.root / corrected.storage_key
    target.parent.mkdir(parents=True)
    target.write_bytes(REVISED)
    write_state(tmp_path, [corrected])
    assert target.read_bytes() == REVISED
    entry = load_source_revisions(provider_paths(tmp_path, "moex"))[0]
    assert (store.root / entry["blob_storage_key"]).read_bytes() == ORIGINAL


def test_generated_revision_journal_satisfies_versioned_contract(tmp_path):
    provider = provider_paths(tmp_path, "moex")
    MirrorStore(tmp_path / "mirror").write_bytes(KEY, ORIGINAL)
    retain_superseded_sources(provider, [], [paper()], [], [])
    validator = revision_validator(2)
    validator.validate(json.loads(revision_journal_path(provider).read_text()))


def revision_validator(version):
    folder = Path(__file__).resolve().parents[1] / "schemas"
    schemas = [
        json.loads((folder / f"provider-source-revisions-v{v}.schema.json").read_text())
        for v in (1, 2)
    ]
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas
    )
    return Draft202012Validator(
        schemas[version - 1], registry=registry, format_checker=FormatChecker()
    )


@pytest.mark.parametrize("field", ["exam_name_raw", "category_raw", "subject_name_raw"])
def test_metadata_only_refresh_keeps_exact_earlier_source_context(tmp_path, field):
    store = MirrorStore(tmp_path / "mirror")
    store.write_bytes(KEY, ORIGINAL)
    old = paper()
    changed = replace(old, **{field: getattr(old, field) + "（官方修訂）"})
    write_state(tmp_path, [old])
    write_state(tmp_path, [changed])
    provider = provider_paths(tmp_path, "moex")
    (entry,) = load_source_revisions(provider)
    assert entry["reason"] == "source_context_changed"
    assert entry["source_record"][field] == getattr(old, field)
    assert entry["source_record"]["checksum"] == changed.checksum
    blob = store.root / entry["blob_storage_key"]
    assert blob.read_bytes() == ORIGINAL
    assert blob.stat().st_ino != (store.root / KEY).stat().st_ino
    revision_validator(2).validate(json.loads(revision_journal_path(provider).read_text()))
    before = revision_journal_path(provider).read_bytes()
    write_state(tmp_path, [changed])
    assert revision_journal_path(provider).read_bytes() == before


def test_multiple_same_payload_contexts_and_reappearance_keep_distinct_history(tmp_path):
    MirrorStore(tmp_path / "mirror").write_bytes(KEY, ORIGINAL)
    old = paper()
    second = replace(old, exam_name_raw="第二個原始標題")
    third = replace(old, exam_name_raw="第三個原始標題")
    for current in (old, second, third, old, second, third):
        write_state(tmp_path, [current])
    provider = provider_paths(tmp_path, "moex")
    entries = load_source_revisions(provider)
    assert len(entries) == 3
    assert {row["source_record"]["exam_name_raw"] for row in entries} == {
        old.exam_name_raw,
        second.exam_name_raw,
        third.exam_name_raw,
    }
    assert len({row["id"] for row in entries}) == 3
    assert len({row["blob_storage_key"] for row in entries}) == 1
    assert audit_source_revisions(provider, verify_mirror=True)["verified_payload_count"] == 3
    before = revision_journal_path(provider).read_bytes()
    write_state(tmp_path, [])
    assert revision_journal_path(provider).read_bytes() == before


def test_one_surviving_context_does_not_hide_another_retired_context(tmp_path):
    MirrorStore(tmp_path / "mirror").write_bytes(KEY, ORIGINAL)
    first = paper()
    second = replace(first, subject_name_raw="另一個原始科目名稱")
    provider = provider_paths(tmp_path, "moex")
    assert retain_superseded_sources(provider, [], [first], [], [first, second]) == 0
    assert retain_superseded_sources(provider, [], [first, second], [], [first]) == 1
    entry, = load_source_revisions(provider)
    assert entry["reason"] == "source_context_changed"
    assert entry["source_record"]["subject_name_raw"] == second.subject_name_raw


@pytest.mark.parametrize(
    "field,value",
    [
        ("canonical_id", "reviewed-name"),
        ("canonical_name", "Reviewed name"),
        ("catalog_version", "new-catalog"),
        ("domain_id", "new-domain"),
        ("exam_family_id", "new-family"),
        ("exam_series_id", "new-series"),
        ("level_id", "new-level"),
        ("track_id", "new-track"),
        ("variant_ids", ["reviewed-variant"]),
        ("stage_id", "new-stage"),
        ("exam_event_id", "reviewed-event"),
        ("bundle_id", "new-bundle"),
        ("bundle_name", "New bundle"),
        ("bundle_policy_id", "new-policy"),
        ("classification_confidence", "review"),
        ("classification_reason", "Review"),
        ("exam_class", "考試分類"),
        ("exam_subclass", "考試子分類"),
        ("paper_code", "reviewed-code"),
        ("download_url_mirror", "https://example.test/new-mirror.pdf"),
        ("download_url_bundle", "https://example.test/new-bundle.zip"),
        ("storage_key", KEY.replace("question.pdf", "recovered.pdf")),
    ],
)
def test_derived_identity_and_delivery_changes_do_not_create_source_history(tmp_path, field, value):
    store = MirrorStore(tmp_path / "mirror")
    store.write_bytes(KEY, ORIGINAL)
    changed = replace(paper(), **{field: value})
    if field == "storage_key":
        store.write_bytes(changed.storage_key, ORIGINAL)
    write_state(tmp_path, [paper()])
    write_state(tmp_path, [changed])
    assert not revision_journal_path(provider_paths(tmp_path, "moex")).exists()


@pytest.mark.parametrize("field", ["title", "exam_name_raw", "source_material"])
def test_attachment_context_and_inherited_material_are_retained(tmp_path, field):
    MirrorStore(tmp_path / "mirror").write_bytes(KEY, ORIGINAL)
    old = paper()
    attachment = ExamAttachment(
        "原始答案", "all_answers", old.download_url_source, storage_key=KEY, checksum=old.checksum
    )
    page = SourceExamPage("115030", 2026, 115, old.exam_name_raw, [attachment], [], "moex")
    material = SourceMaterial(
        "administered", SourceDate("exam_year", 2026), "https://example.test/rules", "官方考試日期"
    )
    if field == "title":
        updated = replace(page, attachments=[replace(attachment, title="修訂答案標題")])
    elif field == "exam_name_raw":
        updated = replace(page, exam_name_raw="修訂原始考試標題")
    else:
        updated = replace(page, source_material=material)
    write_state(tmp_path, [], [page])
    write_state(tmp_path, [], [updated])
    (entry,) = load_source_revisions(provider_paths(tmp_path, "moex"))
    assert entry["reason"] == "source_context_changed"
    assert entry["record_type"] == "attachment"
    assert entry["source_record"]["title"] == attachment.title
    assert entry["source_record"].get("source_material") is None
    assert (tmp_path / "mirror" / entry["blob_storage_key"]).read_bytes() == ORIGINAL


def test_reviewed_material_change_keeps_previous_facts_and_bytes(tmp_path):
    MirrorStore(tmp_path / "mirror").write_bytes(KEY, ORIGINAL)
    material = SourceMaterial(
        "administered",
        SourceDate("exam_year", 2026),
        "https://example.test/rules",
        "115年官方考試日期",
    )
    old = paper(schema_version=3, source_material=material)
    revised = replace(old, source_material=replace(material, evidence_label="官方修訂日期"))
    write_state(tmp_path, [old])
    write_state(tmp_path, [revised])
    (entry,) = load_source_revisions(provider_paths(tmp_path, "moex"))
    assert entry["source_record"]["source_material"]["evidence_label"] == material.evidence_label
    assert entry["reason"] == "source_context_changed"


def test_unverified_metadata_context_does_not_claim_retained_bytes(tmp_path):
    old = paper(checksum="", storage_key="")
    write_state(tmp_path, [old])
    write_state(tmp_path, [replace(old, subject_name_raw="新標題")])
    (entry,) = load_source_revisions(provider_paths(tmp_path, "moex"))
    assert entry["blob_storage_key"] is None
    assert entry["reason"] == "source_context_changed"


def legacy_journal(tmp_path):
    provider = provider_paths(tmp_path, "moex")
    MirrorStore(tmp_path / "mirror").write_bytes(KEY, ORIGINAL)
    retain_superseded_sources(provider, [], [paper()], [], [])
    path = revision_journal_path(provider)
    document = json.loads(path.read_text())
    entry = document["revisions"][0]
    entry.pop("identity_version")
    key = ["paper", "moex", 115, "115030", "101", "0101", "question", paper().download_url_source]
    entry["id"] = hashlib.sha256(
        json.dumps([key, paper().checksum], ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    document["schema_version"] = 1
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    revision_validator(1).validate(document)
    return provider, entry


def test_legacy_ids_reviews_and_journal_bytes_survive_new_contexts(tmp_path):
    provider, legacy = legacy_journal(tmp_path)
    before = revision_journal_path(provider).read_bytes()
    second = replace(paper(), exam_name_raw="新的官方標題")
    write_state(tmp_path, [second])
    assert revision_journal_path(provider).read_bytes() == before
    previous = provider_paths(tmp_path / "previous", "moex")
    previous.data_dir.mkdir(parents=True)
    revision_journal_path(previous).write_bytes(before)
    write_state(tmp_path, [replace(second, subject_name_raw="修訂科目")])
    entries = load_source_revisions(provider)
    assert len(entries) == 2
    assert entries[0] == legacy
    assert entries[1]["identity_version"] == 2
    assert entries[1]["id"] != legacy["id"]
    validate_revision_conservation(previous, provider)
    revision_validator(2).validate(json.loads(revision_journal_path(provider).read_text()))
    # Re-observing and retiring the legacy context must not duplicate it.
    retain_superseded_sources(provider, [], [paper()], [], [])
    assert load_source_revisions(provider) == entries


@pytest.mark.parametrize(
    "mutation",
    ["title", "identity_version", "identity_type", "legacy_wrapper", "duplicate_observation"],
)
def test_context_identity_validation_rejects_changed_or_ambiguous_evidence(tmp_path, mutation):
    provider, legacy = legacy_journal(tmp_path)
    second = replace(paper(), exam_name_raw="第二個官方標題")
    retain_superseded_sources(provider, [], [second], [], [])
    path = revision_journal_path(provider)
    document = json.loads(path.read_text())
    entry = document["revisions"][1]
    if mutation == "title":
        entry["source_record"]["exam_name_raw"] += "未保留的改動"
    elif mutation == "identity_version":
        entry["identity_version"] = 3
    elif mutation == "identity_type":
        entry["identity_version"] = True
    elif mutation == "legacy_wrapper":
        document["schema_version"] = 1
    else:
        # A valid legacy ID must not also hide the same observation under v2.
        duplicate = json.loads(json.dumps(entry))
        duplicate.pop("identity_version")
        duplicate["id"] = legacy["id"]
        document["revisions"] = [duplicate, entry]
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        load_source_revisions(provider)


def test_missing_original_bytes_stop_metadata_only_write_before_state_changes(tmp_path):
    store = MirrorStore(tmp_path / "mirror")
    store.write_bytes(KEY, ORIGINAL)
    write_state(tmp_path, [paper()])
    provider = provider_paths(tmp_path, "moex")
    before = {path: path.read_bytes() for path in provider.data_dir.rglob("*.json")}
    (store.root / KEY).unlink()
    with pytest.raises(ValueError, match="restore the checksum-verified original"):
        write_state(tmp_path, [replace(paper(), exam_name_raw="新標題")])
    assert {path: path.read_bytes() for path in provider.data_dir.rglob("*.json")} == before


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


def test_verified_reuse_and_mutable_provider_refresh_policy(tmp_path):
    store = MirrorStore(tmp_path / "mirror")
    previous, _, failures = sync(MutableClient(ORIGINAL), store)
    assert failures == []
    client = MutableClient(REVISED)
    _pages, catalog, failures = sync(client, store, previous_pages=previous)
    assert failures == []
    assert client.downloads == []
    assert catalog.papers[0].checksum == paper().checksum
    client.refresh_files_on_sync = True
    _pages, catalog, failures = sync(client, store, previous_pages=previous)
    assert failures == []
    assert catalog.papers[0].checksum == paper(REVISED).checksum
    assert SfiCertProvider.refresh_files_on_sync is True
    assert TabfCertProvider.refresh_files_on_sync is True
    assert TiiCertProvider.refresh_files_on_sync is True


def test_existing_journal_recovers_from_checksum_verified_older_cache(tmp_path):
    store = MirrorStore(tmp_path / "mirror")
    store.write_bytes(KEY, ORIGINAL)
    write_state(tmp_path, [paper()])
    # Retiring a source reference creates its journal without changing its
    # original locator. An older cache can retain that locator but lack blobs.
    write_state(tmp_path, [])
    provider = provider_paths(tmp_path, "moex")
    entry = load_source_revisions(provider)[0]
    blob = store.root / entry["blob_storage_key"]
    blob.unlink()
    journal_before = revision_journal_path(provider).read_bytes()
    write_state(tmp_path, [])
    assert blob.read_bytes() == ORIGINAL
    assert not blob.samefile(store.root / KEY)
    assert revision_journal_path(provider).read_bytes() == journal_before


@pytest.mark.parametrize("damage", ["missing", "changed", "corrupt_blob"])
def test_existing_revision_loss_blocks_state_write_without_rebinding_bytes(tmp_path, damage):
    store = MirrorStore(tmp_path / "mirror")
    store.write_bytes(KEY, ORIGINAL)
    write_state(tmp_path, [paper()])
    write_state(tmp_path, [])
    provider = provider_paths(tmp_path, "moex")
    entry = load_source_revisions(provider)[0]
    blob = store.root / entry["blob_storage_key"]
    if damage == "corrupt_blob":
        blob.write_bytes(REVISED)
    else:
        blob.unlink()
        if damage == "missing":
            (store.root / KEY).unlink()
        else:
            (store.root / KEY).write_bytes(REVISED)
    before = {p: p.read_bytes() for p in provider.data_dir.rglob("*.json")}
    with pytest.raises(ValueError, match="checksum-verified original|revision checksum mismatch"):
        write_state(tmp_path, [paper(REVISED)])
    assert {p: p.read_bytes() for p in provider.data_dir.rglob("*.json")} == before
