"""Metadata-only history cannot approve current or historical native payloads."""

from copy import deepcopy
from pathlib import Path

import pytest

from app.paths import provider_paths
from app.providers.taigi_cert import evidence as taigi
from app.providers.tocfl_cert import evidence as tocfl
from app.source_revisions import load_source_revisions
from app.state import load_provider_state

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=[("taigi_cert", taigi), ("tocfl_cert", tocfl)])
def native_state(request, monkeypatch):
    provider_id, evidence = request.param
    provider = provider_paths(ROOT, provider_id)
    pages, catalog, failures = deepcopy(load_provider_state(provider))
    revisions = [
        entry for entry in load_source_revisions(provider) if entry["blob_storage_key"] is not None
    ]
    monkeypatch.setattr("app.state.load_provider_state", lambda paths: (pages, catalog, failures))
    monkeypatch.setattr("app.source_revisions.load_source_revisions", lambda paths: revisions)
    return evidence, pages, catalog, revisions


def test_metadata_only_history_is_preserved_and_reported_without_payload_approval(native_state):
    evidence, _, _, revisions = native_state
    before = evidence.validate_native_resources(ROOT)
    observation = deepcopy(revisions[0])
    observation.update(id="metadata-only-observation", blob_storage_key=None)
    observation["source_record"].update(checksum="", storage_key="")
    revisions.append(observation)
    original = deepcopy(revisions)
    after = evidence.validate_native_resources(ROOT)
    assert revisions == original
    assert after["retired_source_references"] == before["retired_source_references"] + 1
    assert after["unverified_source_references"] == 1
    assert after["verified_payload_paths"] == 0
    assert after["native_resources"] == before["native_resources"]
    views = evidence.reviewed_native_revisions(revisions, repo_root=ROOT)
    assert not any(row["revision_id"] == observation["id"] for row in views)


@pytest.mark.parametrize("target", ["current_attachment", "historical_payload"])
def test_metadata_only_history_does_not_relax_exact_payload_anchor_checks(native_state, target):
    evidence, pages, _, revisions = native_state
    if target == "current_attachment":
        attachment = next(a for page in pages for a in page.attachments)
        attachment.checksum = ""
        attachment.storage_key = ""
    else:
        revisions[0]["source_record"]["checksum"] = "0" * 64
    with pytest.raises(ValueError, match="source reference lacks exact native"):
        evidence.validate_native_resources(ROOT)
