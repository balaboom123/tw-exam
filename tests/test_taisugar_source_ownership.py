"""Acquisition retains original delivery, stable references and review boundaries."""

import io
import zipfile
from copy import deepcopy
from unittest.mock import Mock

import pytest

from app.manifest import SourceManifest
from app.providers.base import DownloadedFile
from app.providers.taisugar_recruit.client import TaisugarRecruitClient, source_subject_code
from app.providers.taisugar_recruit.provider import TaisugarRecruitProvider
from app.storage import MirrorStore
from app.sync import sync_exam_pages
from tests.test_taisugar_recruit import DETAIL_PAGE_TWO_ZIPS_HTML, LISTING_PAGE_HTML


def client_for(detail):
    client = TaisugarRecruitClient()
    client._fetch_text = lambda url: LISTING_PAGE_HTML if "News_Index" in url else detail
    return client


def pdf_detail(items):
    return "".join(
        f'<a href="../upload/UserFiles/News/11543/{name}.pdf">{label}</a>' for name, label in items
    )


def test_source_keys_survive_reordering_insertion_and_label_changes():
    original = client_for(pdf_detail([("a", "民法概要"), ("b", "土地法規概要")]))
    changed = client_for(pdf_detail([("b", "土地法規概要 更正"), ("c", "新科目"), ("a", "民法")]))
    first = original.fetch_exam_page("taisugar-recruit-111", 2022)
    second = changed.fetch_exam_page("taisugar-recruit-111", 2022)

    def key_map(page):
        return {next(iter(p.files.values())): p.subject_code for p in page.papers}

    before, after = key_map(first), key_map(second)
    assert len(before) == 2 and len(after) == 3
    assert all(after[url] == code for url, code in before.items())
    assert len(set(after.values())) == 3
    assert all(code == source_subject_code(url) for url, code in after.items())


def test_source_labels_do_not_approve_native_identity_or_roles():
    from app.normalizer import normalize_papers

    client = client_for(pdf_detail([("a", "共同科目 試題與解答")]))
    page = client.fetch_exam_page("taisugar-recruit-111", 2022)
    assert len(page.papers) == 1
    material = page.papers[0].source_material
    assert material is not None and material.needs_review
    assert material.kind == "administered" and material.date.year_ad == 2022
    assert material.evidence_url.endswith("s=11543")
    assert "applicant group" in material.review_reason and "file role" in material.review_reason
    catalog = normalize_papers(
        page.source_exam_id,
        page.year_ad,
        page.exam_name_raw,
        page.papers,
        [],
        "",
        {},
        provider_id=page.provider_id,
        source_material=page.source_material,
    )
    assert len(catalog.papers) == 1
    paper = catalog.papers[0]
    assert paper.file_type == "source_document"
    assert paper.classification_confidence == "review"
    assert paper.stage_id == "unknown" and paper.track_id == "taisugar-worker-unknown"
    assert page.source_exam_id in paper.bundle_id


def archive_payload(label):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        z.writestr("original.pdf", b"%PDF-1.4\n" + label.encode() + b"\n%%EOF")
    return stream.getvalue()


def test_required_original_archives_download_and_reuse_with_shared_collision_rules(tmp_path):
    client = client_for(DETAIL_PAGE_TWO_ZIPS_HTML)
    provider = TaisugarRecruitProvider(client)
    downloads = Mock(
        side_effect=lambda url, **_: DownloadedFile(
            archive_payload(url), "application/zip", "papers.zip"
        )
    )
    client.http.download = downloads
    store = MirrorStore(tmp_path / "mirror")
    pages, cat, failures = sync_exam_pages(
        provider, [("taisugar-recruit-111", 2022)], store, [], "", download_attachments=False
    )
    assert not failures and len(cat.papers) == 2 and downloads.call_count == 2
    assert len(pages[0].attachments) == 2
    original_keys = {a.download_url_source: a.storage_key for a in pages[0].attachments}
    assert len(set(original_keys.values())) == 2
    assert all("source_archive--source-" in key for key in original_keys.values())
    for item in pages[0].attachments:
        assert (store.root / item.storage_key).read_bytes() == archive_payload(
            item.download_url_source
        )
        assert item.file_type == "source_archive" and item.checksum
    warm, _, failures = sync_exam_pages(
        provider,
        [("taisugar-recruit-111", 2022)],
        store,
        [],
        "",
        download_attachments=False,
        previous_pages=pages,
    )
    assert not failures and downloads.call_count == 2
    assert {a.download_url_source: a.storage_key for a in warm[0].attachments} == original_keys
    # One disappeared archive must not move or overwrite the surviving source.
    survivor = pages[0].attachments[1]
    client._fetch_text = lambda url: (
        LISTING_PAGE_HTML
        if "News_Index" in url
        else f'<a href="{survivor.download_url_source}">{survivor.title}</a>'
    )
    reduced, _, failures = sync_exam_pages(
        provider,
        [("taisugar-recruit-111", 2022)],
        store,
        [],
        "",
        download_attachments=False,
        previous_pages=warm,
    )
    assert not failures and downloads.call_count == 2
    assert reduced[0].attachments[0].storage_key == survivor.storage_key


def mirrored_archives(tmp_path):
    client = client_for(DETAIL_PAGE_TWO_ZIPS_HTML)
    provider = TaisugarRecruitProvider(client)
    client.http.download = lambda url, **_: DownloadedFile(
        archive_payload(url), "application/zip", "papers.zip"
    )
    store = MirrorStore(tmp_path / "mirror")
    pages, _, failures = sync_exam_pages(provider, [("taisugar-recruit-111", 2022)], store, [], "")
    assert not failures
    return provider, pages, store


def test_manifest_uses_verified_current_paths_and_retires_the_prior_cycle_blocker(tmp_path):
    provider, pages, store = mirrored_archives(tmp_path)
    prior = SourceManifest(
        provider_id="taisugar_recruit",
        probe_policy={
            "asset_reconciliation_at": "2026-07-31T00:00:00Z",
            "current_cycle_blocker": {"status": "login_required"},
        },
        files={"obsolete": {}},
    )
    manifest = provider.refresh_source_manifest(prior, pages, store.root, "2026-10-27T00:00:00Z")
    assert set(manifest.files) == (
        {a.storage_key for a in pages[0].attachments}
        | {m["storage_key"] for p in pages[0].papers for m in p.mirror_files.values()}
    )
    assert len(manifest.files) == 4 and "current_cycle_blocker" not in manifest.probe_policy
    policy = manifest.probe_policy
    assert policy["accepted_asset_count"] == 2
    assert policy["accepted_asset_bytes"] == sum(
        len(archive_payload(a.download_url_source)) for a in pages[0].attachments
    )
    assert policy["retained_local_state"]["all_originals_SHA256_verified"] is True
    assert policy["coverage_status"] == "partial"
    assert policy["native_identity_status"] == "review_pending"
    assert policy["previous_cycle_route_observation"]["observed_at"] == "2026-07-31T00:00:00Z"
    assert policy["listing_evidence"] == {"declared_row_count": 4, "page_count": 1}


@pytest.mark.parametrize("mutation", ["checksum", "missing", "symlink", "escape", "owner"])
def test_manifest_rejects_unverified_or_misowned_originals_before_writing(tmp_path, mutation):
    provider, pages, store = mirrored_archives(tmp_path)
    pages = deepcopy(pages)
    attachment = pages[0].attachments[0]
    path = store.root / attachment.storage_key
    if mutation == "checksum":
        attachment.checksum = "0" * 64
    elif mutation == "missing":
        path.unlink()
    elif mutation == "symlink":
        original = path.with_suffix(".original")
        path.rename(original)
        path.symlink_to(original)
    elif mutation == "escape":
        attachment.storage_key = "providers/taisugar_recruit/../other/payload.zip"
    else:
        pages[0].provider_id = "other"
    prior = SourceManifest(provider_id="taisugar_recruit")
    with pytest.raises(ValueError):
        provider.refresh_source_manifest(prior, pages, store.root, "2026-10-27T00:00:00Z")
    assert prior.files == {}


def test_normal_sync_updates_provider_owned_manifest_without_a_probe_flag(tmp_path):
    from app.cli import build_parser, command_sync
    from app.manifest import load_source_manifest
    from app.paths import provider_paths

    client = client_for(DETAIL_PAGE_TWO_ZIPS_HTML)
    client.http.download = lambda url, **_: DownloadedFile(
        archive_payload(url), "application/zip", "papers.zip"
    )
    provider = TaisugarRecruitProvider(client)
    paths = provider_paths(tmp_path, provider.provider_id)
    args = build_parser().parse_args(
        [
            "sync-full",
            "--provider",
            provider.provider_id,
            "--years",
            "2022",
            "--data-dir",
            str(tmp_path / "data"),
            "--mirror-dir",
            str(tmp_path / "mirror"),
            "--aliases",
            str(paths.aliases_path),
        ]
    )
    assert command_sync(args, provider) == 0
    manifest = load_source_manifest(paths.source_manifest_path, provider_id=provider.provider_id)
    assert len(manifest.files) == 4
    assert manifest.probe_policy["retained_local_state"]["source_archives"] == 2
    assert paths.sync_status_path.is_file()


def test_rejected_retention_write_cannot_advance_the_source_manifest(tmp_path, monkeypatch):
    from app import cli
    from app.manifest import write_source_manifest
    from app.paths import provider_paths

    client = client_for(DETAIL_PAGE_TWO_ZIPS_HTML)
    client.http.download = lambda url, **_: DownloadedFile(
        archive_payload(url), "application/zip", "papers.zip"
    )
    provider = TaisugarRecruitProvider(client)
    paths = provider_paths(tmp_path, provider.provider_id)
    paths.data_dir.mkdir(parents=True)
    write_source_manifest(
        paths.source_manifest_path,
        SourceManifest(
            provider_id=provider.provider_id, probe_policy={"prior_observation": "retained"}
        ),
    )
    before = paths.source_manifest_path.read_bytes()
    args = cli.build_parser().parse_args(
        [
            "sync-full",
            "--provider",
            provider.provider_id,
            "--years",
            "2022",
            "--data-dir",
            str(tmp_path / "data"),
            "--mirror-dir",
            str(tmp_path / "mirror"),
            "--aliases",
            str(paths.aliases_path),
        ]
    )
    monkeypatch.setattr(
        cli, "write_provider_state", Mock(side_effect=ValueError("Missing previous bytes"))
    )
    with pytest.raises(ValueError, match="Missing previous bytes"):
        cli.command_sync(args, provider)
    assert paths.source_manifest_path.read_bytes() == before
    assert not paths.sync_status_path.exists()


def test_retained_file_reconciliation_preserves_discovery_observations(tmp_path):
    from app.providers.taisugar_recruit.manifest import refresh_retained_files

    _, pages, store = mirrored_archives(tmp_path)
    prior = SourceManifest(
        provider_id="taisugar_recruit",
        probe_policy={
            "last_discovery_at": "2026-07-31T00:00:00Z",
            "listing_evidence": {"unchanged": True},
        },
    )
    manifest = refresh_retained_files(prior, pages, store.root, "2026-10-27T00:00:00Z")
    assert len(manifest.files) == 4
    assert manifest.probe_policy["last_discovery_at"] == "2026-07-31T00:00:00Z"
    assert manifest.probe_policy["listing_evidence"] == {"unchanged": True}
    assert manifest.probe_policy["asset_reconciliation_at"] == "2026-10-27T00:00:00Z"
