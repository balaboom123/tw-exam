"""Byte validation needs exact fixture inputs, independent of external native mirrors.

The repository-data tests bind the real reviewed facts to checked-in references.
These tests exercise full byte validation, corruption and missing-file rejection
at every matched locator. Operator audits separately verify real native bytes.
"""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.models import NormalizedCatalog
from app.paths import provider_paths
from app.state import load_provider_state

ROOT = Path(__file__).resolve().parents[1]


def native_inputs(provider_id):
    if provider_id == "tcte_tve":
        from app.providers.tcte_tve.booklets import shared_booklets_path, validate_shared_booklets

        path = shared_booklets_path(ROOT)
        document = json.loads(path.read_text())
        return path, document, document["facts"], validate_shared_booklets, 2
    if provider_id == "moex":
        from app.providers.moex.file_roles import (
            question_references_path,
            validate_question_references,
        )

        path = question_references_path(ROOT)
        document = json.loads(path.read_text())
        return path, document, document["facts"], validate_question_references, 1
    if provider_id == "taisugar_recruit":
        from app.providers.taisugar_recruit.evidence import native_roles_path, validate_native_roles

        path = native_roles_path(ROOT)
        document = json.loads(path.read_text())
        return path, document, document["facts"], validate_native_roles, 2
    from app.providers.hce_cmu.identity import annual_papers_path, validate_annual_papers

    path = annual_papers_path(ROOT)
    document = json.loads(path.read_text())
    facts = [f for event in document["events"] for f in event["files"]]
    return path, document, facts, validate_annual_papers, 44


@pytest.mark.parametrize("provider_id", ["tcte_tve", "moex", "hce_cmu", "taisugar_recruit"])
def test_native_mirror_validation_rejects_corruption_or_missing_bytes_at_every_anchor(
    tmp_path, monkeypatch, provider_id
):
    from app import source_revisions, state

    path, document, facts, validate, expected_count = native_inputs(provider_id)
    _, catalog, _ = load_provider_state(provider_paths(ROOT, provider_id))
    payloads = {
        f["checksum"]: b"%PDF-1.4\nfixture " + f["checksum"].encode() + b"\n%%EOF" for f in facts
    }
    if provider_id == "moex":
        reviewed_url = facts[0]["source_url"]
        selected = [p for p in catalog.papers if p.download_url_source == reviewed_url]
    else:
        selected = [p for p in catalog.papers if p.checksum in payloads]
    assert (
        len(selected)
        == {"tcte_tve": 6, "moex": 1, "hce_cmu": 44, "taisugar_recruit": 2}[provider_id]
    )
    locators = set()
    papers = []
    for paper in selected:
        payload = payloads[paper.checksum]
        key = Path(paper.storage_key)
        relative = key if key.parts[0] == "providers" else Path("providers") / provider_id / key
        target = tmp_path / "mirror" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        locators.add(target)
        papers.append(replace(paper, checksum=hashlib.sha256(payload).hexdigest()))
    for fact in facts:
        fact["checksum"] = hashlib.sha256(payloads[fact["checksum"]]).hexdigest()
    target_facts = tmp_path / path.relative_to(ROOT)
    target_facts.parent.mkdir(parents=True, exist_ok=True)
    target_facts.write_text(json.dumps(document, ensure_ascii=False))
    monkeypatch.setattr(
        state, "load_provider_state", lambda _: ([], NormalizedCatalog(papers, []), [])
    )
    monkeypatch.setattr(source_revisions, "load_source_revisions", lambda _: [])
    assert validate(tmp_path, verify_mirror=True) == expected_count
    for locator in locators:
        original = locator.read_bytes()
        locator.write_bytes(original + b"corrupt")
        with pytest.raises(ValueError, match="checksum"):
            validate(tmp_path, verify_mirror=True)
        locator.unlink()
        with pytest.raises((ValueError, FileNotFoundError)) as missing:
            validate(tmp_path, verify_mirror=True)
        if isinstance(missing.value, FileNotFoundError):
            assert Path(missing.value.filename) == locator
        else:
            message = str(missing.value)
            assert "checksum" in message or "mirror is missing" in message
        locator.write_bytes(original)
    assert validate(tmp_path, verify_mirror=True) == expected_count
