import copy
import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest

from app.paths import provider_paths
from app.source_revision_review import (
    reference_revision_evidence_path,
    reviewed_reference_revisions,
    validate_reference_revision_evidence,
)
from app.source_revisions import audit_source_revisions, load_source_revisions, revision_blob_key

ROOT = Path(__file__).resolve().parents[1]


def reference_fact():
    return json.loads(reference_revision_evidence_path(ROOT).read_text())["facts"][0]


def write_fact(root, fact):
    path = reference_revision_evidence_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "facts": [fact]}, ensure_ascii=False))


@pytest.fixture
def reference_state(tmp_path):
    fact = reference_fact()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for index, member in enumerate(fact["native_members"]):
            data = f"%PDF-1.7 reference document {index}".encode()
            archive.writestr(member["name"], data)
            member["checksum"] = hashlib.sha256(data).hexdigest()
    payload = buffer.getvalue()
    fact["source_record"]["checksum"] = hashlib.sha256(payload).hexdigest()
    record = {
        **fact["source_record"], "schema_version": 2,
        "storage_key": "providers/hakka_cert/115/legacy/general/134/listening_audio.zip",
    }
    fields = ("provider_id", "year_roc", "source_exam_id", "category_code", "subject_code", "file_type", "download_url_source")
    key = ("paper",) + tuple(record[field] for field in fields)
    fact["revision_id"] = hashlib.sha256(json.dumps([key, record["checksum"]], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    entry = {
        "id": fact["revision_id"], "record_type": "paper", "source_record": record,
        "blob_storage_key": revision_blob_key("hakka_cert", record["checksum"], ".zip"),
        "retained_at": "2026-10-09T02:43:40+00:00", "reason": "source_reference_retired",
    }
    provider = provider_paths(tmp_path, "hakka_cert")
    provider.data_dir.mkdir(parents=True)
    journal = provider.data_dir / "source-revisions.json"
    journal.write_text(json.dumps({"schema_version": 1, "provider_id": "hakka_cert", "revisions": [entry]}, ensure_ascii=False))
    blob = tmp_path / "mirror" / entry["blob_storage_key"]
    blob.parent.mkdir(parents=True)
    blob.write_bytes(payload)
    write_fact(tmp_path, fact)
    return tmp_path, provider, fact, entry, blob


def test_reference_disposition_preserves_evidence_without_an_examination_identity(reference_state):
    root, provider, fact, entry, blob = reference_state
    original = copy.deepcopy(entry)
    journal_before = (provider.data_dir / "source-revisions.json").read_bytes()
    payload_before = blob.read_bytes()
    assert validate_reference_revision_evidence(root, verify_mirror=True) == 1
    report = audit_source_revisions(provider, verify_mirror=True)
    assert report["revision_count"] == report["verified_payload_count"] == 1
    reviewed = report["reviewed_non_paper_references"]
    assert len(reviewed) == 1
    assert reviewed[0]["disposition"] == "non_paper_reference"
    assert reviewed[0]["source_material"]["kind"] == "reference"
    assert reviewed[0]["source_material"]["date"] == {"basis": "undated", "year_ad": None}
    assert not {"level_id", "exam_event_id", "bundle_id", "file_type"} & reviewed[0].keys()
    assert reviewed[0]["native_members"] == [member["name"] for member in fact["native_members"]]
    assert entry == original
    assert (provider.data_dir / "source-revisions.json").read_bytes() == journal_before
    assert blob.read_bytes() == payload_before


@pytest.mark.parametrize("field", ["provider_id", "source_exam_id", "year_roc", "category_code", "subject_code", "subject_name_raw", "file_type", "download_url_source", "checksum"])
def test_reference_review_requires_complete_source_context(reference_state, field):
    root, _, fact, entry, _ = reference_state
    changed = copy.deepcopy(entry)
    changed["source_record"][field] = 114 if field == "year_roc" else "moex" if field == "provider_id" else "different"
    assert reviewed_reference_revisions([changed], repo_root=root) == []
    fact["source_record"][field] = changed["source_record"][field]
    write_fact(root, fact)
    with pytest.raises(ValueError, match="anchor is missing or changed"):
        validate_reference_revision_evidence(root)


def test_reference_member_checksums_and_unique_names_are_enforced(reference_state):
    root, _, fact, _, _ = reference_state
    fact["native_members"][0]["checksum"] = "0" * 64
    write_fact(root, fact)
    with pytest.raises(ValueError, match="native reference PDF differs"):
        validate_reference_revision_evidence(root, verify_mirror=True)
    fact["native_members"].append(copy.deepcopy(fact["native_members"][0]))
    write_fact(root, fact)
    with pytest.raises(ValueError, match="duplicate native reference member"):
        validate_reference_revision_evidence(root)


def test_changed_revision_id_or_unresolved_material_is_never_promoted(reference_state):
    root, _, fact, entry, _ = reference_state
    other = {**entry, "id": "0" * 64}
    assert reviewed_reference_revisions([other], repo_root=root) == []
    fact["source_material"]["date"] = {"basis": "unknown", "year_ad": None}
    fact["source_material"]["review_reason"] = "Date unresolved"
    write_fact(root, fact)
    with pytest.raises(ValueError, match="resolved reference facts"):
        validate_reference_revision_evidence(root)


def test_older_recovery_roots_without_review_facts_claim_no_judgment(tmp_path):
    assert reviewed_reference_revisions([], repo_root=tmp_path) == []
    with pytest.raises(ValueError, match="Missing reference revision evidence"):
        validate_reference_revision_evidence(tmp_path)


@pytest.mark.repo_data
def test_retained_hakka_reference_has_an_explicit_separate_disposition():
    provider = provider_paths(ROOT, "hakka_cert")
    before = (provider.data_dir / "source-revisions.json").read_bytes()
    entries = load_source_revisions(provider)
    assert len(entries) == 56
    assert validate_reference_revision_evidence(ROOT) == 1
    reviewed = reviewed_reference_revisions(entries)
    assert [row["revision_id"] for row in reviewed] == [reference_fact()["revision_id"]]
    assert len([entry for entry in entries if entry["id"] not in {row["revision_id"] for row in reviewed}]) == 55
    assert (provider.data_dir / "source-revisions.json").read_bytes() == before
