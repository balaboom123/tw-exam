"""A grouping migration must preserve declared old URLs without aliasing a group."""

import hashlib
import json
import zipfile
from dataclasses import asdict, replace
from unittest.mock import patch

import pytest
import yaml
from pathlib import Path

from app import archive_audit, publisher
from app.audit import build_release_plan
from app.bundler import build_bundles
from app.models import NormalizedCatalog
from app.paths import provider_paths, site_paths
from app.retained_downloads import (
    load_retained_downloads, release_download_records, retained_downloads_path,
    snapshot_existing_downloads, verify_retained_download, write_retained_downloads,
)
from app.site_registry import get_site_config
from tests.test_bundler import make_paper
from tests.test_workflows import _load_release_script


def migration(tmp_path, monkeypatch):
    site = site_paths(tmp_path, "default")
    records = []
    for year in (114, 115):
        key = f"providers/moex/{year}/native.pdf"
        payload = b"%PDF-1.7 native paper " + str(year).encode()
        path = tmp_path / "mirror" / key
        path.parent.mkdir(parents=True)
        path.write_bytes(payload)
        record = make_paper(
            canonical_id="nurse", canonical_name="Nurse", year_roc=year,
            source_exam_id=f"{year}030", subject_code="0101", storage_key=key,
        )
        record = replace(
            record, provider_id="moex", checksum=hashlib.sha256(payload).hexdigest(),
            bundle_id="moex-old-combined", bundle_name="Old combined", schema_version=2,
            catalog_version="exam-identity-v2", domain_id="professional",
            exam_family_id="professional-exam", exam_series_id="professional-high",
            level_id="professional-high", track_id="nurse", classification_confidence="high",
        )
        records.append(record)
    old = build_bundles(site.bundle_dir, tmp_path / "mirror", NormalizedCatalog(records, []), "")
    assert not old.failures and len(old.bundles) == 1
    old = replace(
        old.bundles[0], release_tag="default-bundles-v2-001",
        storage_key="bundles/sites/default/" + old.bundles[0].asset_name,
    )
    publisher.write_site_state(site, [old], [])
    current = NormalizedCatalog([
        replace(record, bundle_id=f"moex-new-{index}", bundle_name=f"Group {index}")
        for index, record in enumerate(records)
    ], [])
    publisher.write_provider_state(provider_paths(tmp_path, "moex"), [], current, [], [], None)
    # Model an independently verified identity split. Native bytes, source
    # keys, actual ZIP construction and the retention/pruning owners are real.
    monkeypatch.setattr(publisher, "load_site_catalog", lambda *a, **k: (current, []))
    monkeypatch.setattr(archive_audit, "load_site_catalog", lambda *a, **k: (current, []))
    config = replace(get_site_config("default"), public_min_years=1, release_shard_size=2)
    monkeypatch.setattr(publisher, "get_site_config", lambda *_: config)
    return site, old, current


def test_split_preserves_frozen_URL_bytes_capacity_and_repeat_publication(tmp_path, monkeypatch):
    site, old, _ = migration(tmp_path, monkeypatch)
    before = (tmp_path / old.storage_key).read_bytes()
    _, new = publisher.publish_site(
        tmp_path, site_id="default", repository="example/repo",
        retain_existing_download_ids={old.bundle_id},
    )
    assert len(new) == 2 and old.asset_name not in {bundle.asset_name for bundle in new}
    retained = load_retained_downloads(site)
    assert len(retained) == 1
    historical = retained[0]
    assert (historical.asset_name, historical.release_tag, historical.checksum) == (
        old.asset_name, old.release_tag, old.checksum,
    )
    assert (tmp_path / old.storage_key).read_bytes() == before
    assert {bundle.release_tag for bundle in new} == {
        "default-bundles-v2-001", "default-bundles-v2-002",
    }
    feed = json.loads(site.frontend_bundles_path.read_text())["bundles"]
    assert len(feed) == 2 and old.bundle_id not in {bundle["id"] for bundle in feed}
    assert json.loads(site.release_assets_path.read_text())["schema_version"] == 3
    assert len(release_download_records(site.release_assets_path)) == 3
    report = archive_audit.audit_site_archives(tmp_path, site_id="default", verify_content=True)
    assert not report["errors"] and not report["unreferenced_archives"]
    assert (report["archive_count"], report["retained_archive_count"],
            report["total_download_archives"], report["retained_source_record_count"]) == (2, 1, 3, 2)
    plan = build_release_plan(tmp_path)
    assert sum(shard["asset_count"] for shard in plan["shards"]) == 3
    frozen = retained_downloads_path(site).read_bytes()
    publisher.publish_site(tmp_path, site_id="default", repository="example/repo")
    assert retained_downloads_path(site).read_bytes() == frozen
    assert (tmp_path / old.storage_key).read_bytes() == before


def test_missing_or_corrupt_retained_bytes_cannot_pass_audit(tmp_path, monkeypatch):
    site, old, _ = migration(tmp_path, monkeypatch)
    held = snapshot_existing_downloads(tmp_path, site, [old], {old.bundle_id})
    write_retained_downloads(site, held)
    path = tmp_path / old.storage_key
    path.write_bytes(b"corrupt")
    with pytest.raises(zipfile.BadZipFile):
        verify_retained_download(tmp_path, site, held[0], verify_content=True)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        verify_retained_download(tmp_path, site, held[0], verify_content=True)


def test_retention_requires_existing_identity_and_native_history_anchors(tmp_path, monkeypatch):
    site, old, _ = migration(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="existing published"):
        snapshot_existing_downloads(tmp_path, site, [old], {"unpublished"})
    provider_paths(tmp_path, "moex").papers_dir.joinpath("2025.json").unlink()
    with pytest.raises(ValueError, match="history anchor"):
        snapshot_existing_downloads(tmp_path, site, [old], {old.bundle_id})


def test_frozen_snapshot_cannot_be_removed_rewritten_or_isolated(tmp_path, monkeypatch):
    site, old, _ = migration(tmp_path, monkeypatch)
    held = snapshot_existing_downloads(tmp_path, site, [old], {old.bundle_id})
    write_retained_downloads(site, held)
    with pytest.raises(ValueError, match="remove or rewrite"):
        write_retained_downloads(site, [])
    with pytest.raises(ValueError, match="remove or rewrite"):
        write_retained_downloads(site, [replace(held[0], checksum="a" * 64)])
    stale_report = {"site_id": "default", "release_tag": None, "verified_content": True,
                    "errors": [], "unreferenced_archives": [old.asset_name]}
    with pytest.raises(ValueError, match="retained historical"):
        archive_audit.isolate_unreferenced_archives(tmp_path, site_id="default", report=stale_report)
    archive_audit.prune_redundant_archives(tmp_path, site_id="default", report=stale_report)
    assert (tmp_path / old.storage_key).exists()
    assert stale_report["removed_redundant_archives"] == []


def test_release_prune_keeps_frozen_URL_and_refuses_missing_snapshot_digest(tmp_path, monkeypatch):
    site, old, _ = migration(tmp_path, monkeypatch)
    _, new = publisher.publish_site(
        tmp_path, site_id="default", repository="example/repo",
        retain_existing_download_ids={old.bundle_id},
    )
    records = release_download_records(site.release_assets_path)
    remote = {}
    for row in records:
        remote.setdefault(row["release_tag"], {})[row["asset_name"]] = row["checksum"]
    remote[old.release_tag]["unreferenced.zip"] = "other"
    module = _load_release_script()
    with patch.object(module, "RELEASE_ASSETS_PATH", site.release_assets_path), \
            patch.object(module, "_release_zip_digests", side_effect=lambda tag, **k: remote[tag]), \
            patch.object(module.subprocess, "run") as mutate:
        assert module.prune() == 0
        mutate.assert_called_once_with([
            "gh", "release", "delete-asset", old.release_tag, "unreferenced.zip", "--yes",
        ], check=True)
        mutate.reset_mock()
        remote[old.release_tag].pop(old.asset_name)
        assert module.prune() == 1
        mutate.assert_not_called()


@pytest.mark.parametrize("version", [0, 4, True, "3"])
def test_release_helper_rejects_unknown_contracts(tmp_path, version):
    path = tmp_path / "release-assets.json"
    path.write_text(json.dumps({"schema_version": version, "assets": []}))
    with pytest.raises(ValueError, match="version"):
        release_download_records(path)


def test_release_inventory_ownership_and_duplicate_physical_assets_fail_closed(tmp_path, monkeypatch):
    site, old, _ = migration(tmp_path, monkeypatch)
    held = snapshot_existing_downloads(tmp_path, site, [old], {old.bundle_id})
    document = {"schema_version": 3, "site_id": "default", "assets": [],
                "retained_downloads": [asdict(held[0])]}
    site.release_assets_path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="another site"):
        release_download_records(site.release_assets_path, expected_site_id="another")
    document["assets"] = [asdict(held[0])]
    site.release_assets_path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="duplicate physical"):
        release_download_records(site.release_assets_path)


def test_separate_retirement_requires_exact_asset_and_frozen_checksum(tmp_path, monkeypatch):
    site, old, _ = migration(tmp_path, monkeypatch)
    held = snapshot_existing_downloads(tmp_path, site, [old], {old.bundle_id})
    write_retained_downloads(site, held)
    with pytest.raises(ValueError, match="remove or rewrite"):
        write_retained_downloads(site, [], approved_retirements={old.asset_name: "a" * 64})
    assert load_retained_downloads(site) == held
    write_retained_downloads(site, [], approved_retirements={old.asset_name: old.checksum})
    assert load_retained_downloads(site) == []
    # Retiring metadata is separate from external deletion; the original bytes
    # remain until the checksum-guarded Release operation is separately invoked.
    assert (tmp_path / old.storage_key).exists()


def test_frozen_identity_is_protected_before_archive_rebuild(tmp_path, monkeypatch):
    site, old, current = migration(tmp_path, monkeypatch)
    publisher.publish_site(tmp_path, site_id="default", repository="example/repo",
                           retain_existing_download_ids={old.bundle_id})
    before = (tmp_path / old.storage_key).read_bytes()
    resurrected = NormalizedCatalog([replace(paper, bundle_id=old.bundle_id)
                                    for paper in current.papers], [])
    monkeypatch.setattr(publisher, "load_site_catalog", lambda *a, **k: (resurrected, []))
    monkeypatch.setattr(publisher, "build_bundles", lambda *a, **k: pytest.fail("must fail before mutation"))
    with pytest.raises(ValueError, match="frozen identity"):
        publisher.publish_site(tmp_path, site_id="default", repository="example/repo")
    assert (tmp_path / old.storage_key).read_bytes() == before


def test_frozen_aggregate_cannot_be_an_alias_for_one_successor(tmp_path, monkeypatch):
    site, old, _ = migration(tmp_path, monkeypatch)
    _, new = publisher.publish_site(tmp_path, site_id="default", repository="example/repo",
                                    retain_existing_download_ids={old.bundle_id})
    with pytest.raises(ValueError, match="cannot alias"):
        publisher.write_site_state(site, [replace(new[0], legacy_asset_names=[old.asset_name])], [])


def test_scheduled_matrix_includes_a_shard_with_only_a_retained_download(tmp_path, monkeypatch, capsys):
    site, old, _ = migration(tmp_path, monkeypatch)
    config = replace(get_site_config("default"), public_min_years=1, release_shard_size=1)
    monkeypatch.setattr(publisher, "get_site_config", lambda *_: config)
    _, new = publisher.publish_site(tmp_path, site_id="default", repository="example/repo",
                                    retain_existing_download_ids={old.bundle_id})
    assert old.release_tag not in {bundle.release_tag for bundle in new}
    workflow = Path(__file__).resolve().parents[1] / ".github/workflows/verify-archives.yml"
    run = yaml.safe_load(workflow.read_text())["jobs"]["shards"]["steps"][-1]["run"]
    script = run.split("\n", 1)[1].rsplit("\nPY", 1)[0]
    monkeypatch.chdir(tmp_path)
    exec(compile(script, str(workflow), "exec"), {})
    tags = json.loads(capsys.readouterr().out.strip().removeprefix("tags="))
    assert tags == ["default-bundles-v2-001", "default-bundles-v2-002", "default-bundles-v2-003"]


def test_rollback_can_promote_original_and_retain_successors_without_breaking_URLs(tmp_path, monkeypatch):
    site, old, _ = migration(tmp_path, monkeypatch)
    _, new = publisher.publish_site(tmp_path, site_id="default", repository="example/repo",
                                    retain_existing_download_ids={old.bundle_id})
    old_bytes = (tmp_path / old.storage_key).read_bytes()
    held = snapshot_existing_downloads(tmp_path, site, new, {bundle.bundle_id for bundle in new})
    successors = [download for download in held if download.bundle_id != old.bundle_id]
    with pytest.raises(ValueError, match="primary promotion"):
        publisher.write_site_state(site, [replace(old, checksum="a" * 64)], [], successors)
    publisher.write_site_state(site, [old], [], successors)
    assert {download.bundle_id for download in load_retained_downloads(site)} == {
        bundle.bundle_id for bundle in new
    }
    assert len(release_download_records(site.release_assets_path)) == 3
    assert (tmp_path / old.storage_key).read_bytes() == old_bytes
    assert all((tmp_path / bundle.storage_key).is_file() for bundle in new)
