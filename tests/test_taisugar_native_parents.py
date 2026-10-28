"""An intact extracted PDF cannot replace evidence of its exact source container."""

import hashlib
from dataclasses import replace

import pytest

from app.models import NormalizedCatalog, to_plain_data
from app.paths import provider_paths
from app.providers.taisugar_recruit.archives import member_locator, parse_member_locator
from app.providers.taisugar_recruit.native import validate_member_parents
from app.state import load_provider_state
from tests.taisugar_native_fixtures import bind_parent_fixtures


@pytest.fixture
def native_parent(tmp_path, monkeypatch):
    from pathlib import Path

    from app import source_revisions, state

    root = Path(__file__).resolve().parents[1]
    _, catalog, _ = load_provider_state(provider_paths(root, "taisugar_recruit"))
    original = next(p for p in catalog.papers if p.year_roc == 111)
    payload = b"%PDF-1.4\nexact member fixture\n%%EOF"
    path = tmp_path / "mirror" / original.storage_key
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)
    paper = replace(original, checksum=hashlib.sha256(payload).hexdigest())
    papers, pages, paths = bind_parent_fixtures(tmp_path, [paper])
    paper = papers[0]
    parent = pages[0].attachments[0]
    historical_key = parent.storage_key.replace("fixture-parents", "historical-parents")
    historical_path = tmp_path / "mirror" / historical_key
    historical_path.parent.mkdir(parents=True)
    historical_path.write_bytes(paths[0].read_bytes())
    revisions = [
        {
            "record_type": "attachment",
            "source_record": {
                **to_plain_data(parent),
                "source_exam_id": paper.source_exam_id,
                "year_roc": paper.year_roc,
            },
            "blob_storage_key": historical_key,
        }
    ]
    monkeypatch.setattr(
        state, "load_provider_state", lambda _: (pages, NormalizedCatalog(papers, []), [])
    )
    monkeypatch.setattr(source_revisions, "load_source_revisions", lambda _: revisions)
    anchor = (paper.source_exam_id, paper.year_roc, paper.download_url_source, paper.checksum)
    return anchor, pages, revisions, paths[0], historical_path


def test_every_matching_parent_is_independently_verified(tmp_path, native_parent):
    anchor, _, _, current, historical = native_parent
    validate_member_parents(tmp_path, [anchor], verify_mirror=True)
    assert not current.samefile(historical)
    for path in (current, historical):
        data = path.read_bytes()
        path.write_bytes(data + b"changed")
        with pytest.raises(ValueError, match="mirror bytes"):
            validate_member_parents(tmp_path, [anchor], verify_mirror=True)
        path.unlink()
        with pytest.raises(ValueError, match="mirror bytes"):
            validate_member_parents(tmp_path, [anchor], verify_mirror=True)
        path.write_bytes(data)


def test_member_checksum_must_match_the_actual_container_member(tmp_path, native_parent):
    anchor, *_ = native_parent
    with pytest.raises(ValueError, match="member differs"):
        validate_member_parents(tmp_path, [(*anchor[:-1], "0" * 64)], verify_mirror=True)


@pytest.mark.parametrize("field", ["event", "year", "parent_version", "member_path"])
def test_exact_parent_context_is_required(tmp_path, native_parent, field):
    anchor, *_ = native_parent
    event, year, url, checksum = anchor
    parent, version, name = parse_member_locator(url)
    if field == "event":
        event += "-other"
    elif field == "year":
        year += 1
    elif field == "parent_version":
        url = member_locator(parent, "0" * 64, name)
    else:
        url = member_locator(parent, version, "different.pdf")
    with pytest.raises(ValueError, match="parent anchor|member differs"):
        validate_member_parents(tmp_path, [(event, year, url, checksum)], verify_mirror=True)


def test_parent_bytes_do_not_substitute_for_a_retained_reference(tmp_path, native_parent):
    anchor, pages, revisions, *_ = native_parent
    pages.clear()
    revisions.clear()
    with pytest.raises(ValueError, match="parent anchor"):
        validate_member_parents(tmp_path, [anchor], verify_mirror=False)


def test_parent_mirror_rejects_symlinks_and_outside_paths(tmp_path, native_parent):
    anchor, pages, revisions, current, historical = native_parent
    revisions.clear()
    current.unlink()
    current.symlink_to(historical)
    with pytest.raises(ValueError, match="mirror bytes"):
        validate_member_parents(tmp_path, [anchor], verify_mirror=True)
    pages[0].attachments[0].storage_key = str(historical)
    with pytest.raises(ValueError, match="invalid mirror locator"):
        validate_member_parents(tmp_path, [anchor], verify_mirror=True)
