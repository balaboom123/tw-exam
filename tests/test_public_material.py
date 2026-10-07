"""Material-aware contract consumers must preserve native keys and source dates."""

import hashlib
import json
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from app.archive_audit import inspect_archive
from app.bundler import (
    _bundle_manifest_data, _manifest_digest, _paper_bundle_key, _resolve_arcnames,
    _load_existing_entries_by_canonical, _resolve_entry_ref, _split_bundle_archive,
    public_bundle_ids, public_bundle_ids_from_indexes,
)
from app.models import BundleAsset, NormalizedCatalog, to_plain_data
from app.paths import provider_paths, site_paths
from app.provider_index import (
    build_provider_index, indexed_material_summaries, load_provider_index,
    paper_index_public_year_roc, write_provider_index,
)
from app.publisher import apply_bundle_download_urls, write_site_state
from app.source_material import MaterialSummary, SourceDate, SourceMaterial, summarize_papers
from app.state import load_site_bundles
from tests.test_source_material import normalize, workbook_page

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = b"%PDF-1.7 retained workbook question\n" + b"x" * 5000


def material_paper(*, undated=False, partition=2026):
    page = workbook_page()
    page.year_ad, page.year_roc = partition, partition - 1911
    if undated:
        page.source_material = SourceMaterial(
            "sample", SourceDate("undated", None), "https://www.jlpt.jp/samples/", "問題例",
        )
    paper = normalize(page).papers[0]
    return replace(paper, storage_key="question.pdf", checksum=hashlib.sha256(PAYLOAD).hexdigest())


def fixture_archive(path, papers):
    names = _resolve_arcnames(papers)
    entries = {_paper_bundle_key(paper): name for paper, name in zip(papers, names, strict=True)}
    manifest = _bundle_manifest_data(papers[0].bundle_id, papers[0].bundle_name, papers, entries)
    with zipfile.ZipFile(path, "w") as archive:
        for name in sorted(set(names)):
            archive.writestr(name, PAYLOAD)
        archive.writestr("bundle.json", json.dumps(manifest, ensure_ascii=False))
    return manifest


def validator(name):
    documents = [json.loads(path.read_text()) for path in (ROOT / "schemas").glob("*.json")]
    registry = Registry().with_resources(
        (document.get("$id", f"https://tw-exam.invalid/schemas/anonymous-{index}"), Resource.from_contents(document, default_specification=DRAFT202012))
        for index, document in enumerate(documents)
    )
    return Draft202012Validator(json.loads((ROOT / "schemas" / name).read_text()), registry=registry)


@pytest.mark.parametrize("undated,folder,years", [(False, "edition-2012", [101]), (True, "undated", [])])
def test_archive_reader_preserves_source_partitions_without_publishing_them(tmp_path, undated, folder, years):
    paper = material_paper(undated=undated)
    archive = tmp_path / "material.zip"
    manifest = fixture_archive(archive, [paper])
    assert manifest["manifest_version"] == 4
    assert manifest["schema_version"] == 3
    assert manifest["years"] == years
    assert manifest["papers"][0]["year_roc"] == 115
    assert manifest["papers"][0]["bundle_entry"].startswith(folder + "/")
    assert manifest["papers"][0]["source_material"] == to_plain_data(paper.source_material)
    validator("bundle-archive-manifest-v4.schema.json").validate(manifest)
    assert inspect_archive(archive, verify_content=True) == manifest


@pytest.mark.parametrize("undated", [False, True])
def test_index_and_full_record_eligibility_do_not_count_acquisition_years(tmp_path, undated):
    papers = [material_paper(undated=undated, partition=year) for year in (2026, 2027)]
    catalog = NormalizedCatalog(papers, [])
    provider = provider_paths(tmp_path, "jlpt_cert")
    index = build_provider_index(provider, [], papers)
    write_provider_index(provider, index)
    assert load_provider_index(provider) == index
    assert index["schema_version"] == 3
    validator("provider-index-v3.schema.json").validate(index)
    assert [paper_index_public_year_roc(index, row) for row in index["papers"]] == ([None, None] if undated else [101, 101])
    assert public_bundle_ids(catalog, min_years=2) == set()
    assert public_bundle_ids_from_indexes([index], min_years=2) == set()
    assert public_bundle_ids(catalog, min_years=1) == {papers[0].bundle_id}
    assert public_bundle_ids_from_indexes([index], min_years=1) == {papers[0].bundle_id}
    assert indexed_material_summaries([index]) == {papers[0].bundle_id: summarize_papers(papers)}


def test_equivalent_payloads_share_storage_without_losing_native_references(tmp_path):
    papers = [material_paper(undated=True, partition=year) for year in (2026, 2027)]
    archive = tmp_path / "shared.zip"
    manifest = fixture_archive(archive, papers)
    assert manifest["file_count"] == 1
    assert len(manifest["papers"]) == 2
    assert {_paper_bundle_key(paper)[0] for paper in manifest["papers"]} == {115, 116}
    assert inspect_archive(archive, verify_content=True) == manifest


@pytest.mark.parametrize("mutation", ["summary", "folder", "kind", "version", "native_year"])
def test_archive_reader_rejects_invalid_or_misleading_facts(tmp_path, mutation):
    paper = material_paper()
    archive = tmp_path / "bad.zip"
    manifest = fixture_archive(archive, [paper])
    if mutation == "summary":
        manifest["source_material"]["dates"][0]["year_ad"] = 2026
    elif mutation == "folder":
        manifest["papers"][0]["bundle_entry"] = "115/question.pdf"
    elif mutation == "kind":
        manifest["source_material"]["kind"] = "administered"
    elif mutation == "version":
        manifest["manifest_version"] = 4.0
    else:
        manifest["papers"][0]["year_roc"] = True
    assert _manifest_digest(manifest) is None
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr(manifest["papers"][0]["bundle_entry"], PAYLOAD)
        target.writestr("bundle.json", json.dumps(manifest))
    with pytest.raises(ValueError):
        inspect_archive(archive, verify_content=False)


def test_public_summary_does_not_accept_unresolved_dates_or_kind():
    with pytest.raises(ValueError):
        MaterialSummary("sample", (SourceDate("unknown", None),))
    with pytest.raises(ValueError):
        MaterialSummary("unknown", (SourceDate("undated", None),))


def test_mixed_site_reader_and_projection_keep_release_locators_compatible(tmp_path):
    paper = material_paper(undated=True)
    summary = summarize_papers([paper])
    reviewed = BundleAsset(
        "sample", paper.bundle_name, summary.years, 1, "sample.zip", "sample.zip",
        release_tag="shard-1", checksum=paper.checksum, schema_version=3,
        bundle_id=paper.bundle_id, catalog_version="exam-identity-v2",
        exam_class=paper.exam_class, exam_subclass=paper.exam_subclass,
        source_material=summary,
    )
    legacy = BundleAsset("legacy", "Legacy", [114], 2, "legacy.zip", "legacy.zip",
                         release_tag="shard-1", schema_version=2, bundle_id="legacy")
    _, bundles, frontend = apply_bundle_download_urls(NormalizedCatalog([paper], []), [reviewed, legacy], repository="owner/archive")
    site = site_paths(tmp_path, "material")
    write_site_state(site, bundles, frontend)
    assert load_site_bundles(site) == bundles
    assert json.loads(site.bundles_path.read_text())["schema_version"] == 3
    assert json.loads(site.frontend_bundles_path.read_text())["schema_version"] == 3
    assert json.loads(site.release_assets_path.read_text())["schema_version"] == 2
    assert frontend[0]["sourceMaterial"] == to_plain_data(summary)
    assert frontend[0]["years"] == []
    assert frontend[0]["url"] == "https://github.com/owner/archive/releases/download/shard-1/sample.zip"
    assert "sourceMaterial" not in frontend[1]
    legacy_wrapper = json.loads(site.bundles_path.read_text())
    legacy_wrapper["schema_version"] = 2
    site.bundles_path.write_text(json.dumps(legacy_wrapper))
    with pytest.raises(ValueError, match="inventory v3"):
        load_site_bundles(site)


def test_reviewed_manifest_recovery_and_multipart_keep_all_native_contexts(tmp_path):
    papers = [material_paper(undated=True, partition=year) for year in (2026, 2027)]
    answer = replace(papers[0], file_type="answer")
    papers.append(answer)
    path = tmp_path / "material.zip"
    manifest = fixture_archive(path, papers)
    _, by_key, _ = _load_existing_entries_by_canonical(tmp_path)
    recovered = by_key[papers[0].bundle_id]
    assert set(recovered) == {_paper_bundle_key(paper) for paper in papers}
    assert all(_resolve_entry_ref(reference) == PAYLOAD for reference in recovered.values())
    entries = {_paper_bundle_key(row): row["bundle_entry"] for row in manifest["papers"]}
    # Force one physical payload per part; shared question references stay together.
    parts = _split_bundle_archive(path, path.name, included_papers=papers,
                                 bundle_entries_by_paper_key=entries, max_bytes=10000)
    assert len(parts) == 2
    observed = [inspect_archive(part[0], verify_content=True) for part in parts]
    assert sum(row["file_count"] for row in observed) == 2
    assert sum(len(row["papers"]) for row in observed) == 3
    assert sorted(len(row["papers"]) for row in observed) == [1, 2]
    assert all(row["source_material"] == manifest["source_material"] for row in observed)


def test_archive_cleanup_preserves_unique_material_evidence(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.archive_audit import prune_redundant_archives

    directory = site_paths(tmp_path, "default").bundle_dir
    directory.mkdir(parents=True)
    paper = material_paper()
    fixture_archive(directory / "active.zip", [paper])
    fixture_archive(directory / "duplicate.zip", [paper])
    revised_evidence = replace(paper, source_material=replace(
        paper.source_material, evidence_label="Earlier retained official label"))
    fixture_archive(directory / "unique-evidence.zip", [revised_evidence])
    monkeypatch.setattr("app.archive_audit.load_site_bundles",
                        lambda _: [SimpleNamespace(asset_name="active.zip")])
    report = {"site_id": "default", "verified_content": True, "errors": [],
              "unreferenced_archives": ["duplicate.zip", "unique-evidence.zip"]}
    prune_redundant_archives(tmp_path, site_id="default", report=report)
    assert report["removed_redundant_archives"] == ["duplicate.zip"]
    assert (directory / "unique-evidence.zip").exists()


@pytest.mark.parametrize("undated", [False, True])
def test_backlog_and_catalog_plan_use_reviewed_dates(tmp_path, monkeypatch, undated):
    from app.audit import build_catalog_audit, build_publication_backlog
    from app.publisher import write_provider_state
    from app.site_registry import get_site_config

    config = replace(get_site_config("default"), provider_ids=("jlpt_cert",),
                     required_provider_ids=(), public_min_years=2,
                     public_min_years_by_canonical_prefix={})
    monkeypatch.setattr("app.audit.get_site_config", lambda _: config)
    monkeypatch.setattr("app.publisher.get_site_config", lambda _: config)
    monkeypatch.setattr("app.publisher.quarantined_provider_ids", lambda *args, **kwargs: set())
    papers = [material_paper(undated=undated, partition=year) for year in (2026, 2027)]
    write_provider_state(provider_paths(tmp_path, "jlpt_cert"), raw_pages=[],
                         normalized=NormalizedCatalog(papers, []), aliases=[], failures=[], manifest=None)
    assert build_publication_backlog(tmp_path)["unpublished_bundle_count"] == 0
    assert build_catalog_audit(tmp_path)["planned_bundle_count"] == 0
    config = replace(config, public_min_years=1)
    assert build_publication_backlog(tmp_path)["unpublished_bundle_count"] == 1
    assert build_catalog_audit(tmp_path)["planned_bundle_count"] == 1


@pytest.mark.parametrize("undated", [False, True])
def test_publication_end_to_end_mixed_history_reuse_and_fact_only_rebuild(tmp_path, monkeypatch, undated):
    from app.archive_audit import audit_site_archives
    from app.publisher import publish_site, write_provider_state
    from app.site_registry import get_site_config
    from scripts import validate_publication as checks

    config = replace(get_site_config("default"), provider_ids=("jlpt_cert",),
                     required_provider_ids=(), public_min_years=1,
                     public_min_years_by_canonical_prefix={})
    monkeypatch.setattr("app.publisher.get_site_config", lambda _: config)
    monkeypatch.setattr("app.publisher.quarantined_provider_ids", lambda *args, **kwargs: set())
    monkeypatch.setattr(checks, "get_site_config", lambda _: config)
    monkeypatch.setattr(checks, "validate_source_inventory", lambda *args, **kwargs: None)
    paper = material_paper(undated=undated)
    legacy_page = workbook_page()
    legacy_page.source_material = None
    legacy = replace(normalize(legacy_page).papers[0], storage_key=paper.storage_key, checksum=paper.checksum)
    provider = provider_paths(tmp_path, "jlpt_cert")

    def save(reviewed):
        write_provider_state(provider, raw_pages=[], normalized=NormalizedCatalog([reviewed, legacy], []),
                             aliases=[], failures=[], manifest=None)

    save(paper)
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    (mirror / paper.storage_key).write_bytes(PAYLOAD)
    _, first = publish_site(tmp_path, site_id="default", repository="owner/archive")
    site = site_paths(tmp_path, "default")
    assert len(first) == 2
    assert {bundle.schema_version for bundle in first} == {2, 3}
    report = audit_site_archives(tmp_path, site_id="default", verify_content=True)
    assert report["errors"] == []
    assert report["source_record_count"] == 2
    assert checks.validate_publication(tmp_path) == (2, 2, 2)
    validator("frontend-bundle-feed-v3.schema.json").validate(json.loads(site.frontend_bundles_path.read_text()))
    for bundle in first:
        validator(f"bundle-v{bundle.schema_version}.schema.json").validate(to_plain_data(bundle))
    (mirror / paper.storage_key).unlink()
    # Exact manifest and archive checksum permit reuse without any mirror.
    _, reused = publish_site(tmp_path, site_id="default", repository="owner/archive")
    assert reused == first
    updated = replace(paper, source_material=replace(paper.source_material, evidence_label="Reviewed official source label"))
    save(updated)
    _, rebuilt = publish_site(tmp_path, site_id="default", repository="owner/archive")
    by_id = {bundle.bundle_id: bundle for bundle in first}
    for bundle in rebuilt:
        before = by_id[bundle.bundle_id]
        assert bundle.download_url == before.download_url
        assert (bundle.checksum != before.checksum) == (bundle.schema_version == 3)
    assert audit_site_archives(tmp_path, site_id="default", verify_content=True)["errors"] == []
    assert checks.validate_publication(tmp_path) == (2, 2, 2)
    # A malformed public date cannot survive either full-record or indexed validation.
    feed = json.loads(site.frontend_bundles_path.read_text())
    next(row for row in feed["bundles"] if row["id"] == paper.bundle_id)["sourceMaterial"]["kind"] = "reference"
    site.frontend_bundles_path.write_text(json.dumps(feed))
    with pytest.raises(ValueError, match="differ from site publication"):
        checks.validate_publication(tmp_path)


def test_material_validation_blocks_mixed_history_and_kinds_before_mutation(tmp_path):
    from app.bundler import build_bundles

    paper = material_paper()
    legacy = replace(paper, schema_version=2, source_material=None)
    different_kind = replace(paper, source_exam_id="other", source_material=replace(paper.source_material, kind="reference"))
    for records, error in [([paper, legacy], "complete bundle history"),
                           ([paper, different_kind], "mix material kinds")]:
        with pytest.raises(ValueError, match=error):
            build_bundles(tmp_path / "bundles", tmp_path / "mirror", NormalizedCatalog(records, []), "")
        assert not (tmp_path / "bundles").exists()


def test_reviewed_multipart_publication_conserves_all_shards_and_date_summaries(tmp_path, monkeypatch):
    from app.archive_audit import audit_site_archives
    from app.bundler import build_bundles
    from app.publisher import publish_site, write_provider_state
    from app.site_registry import get_site_config
    from scripts import validate_publication as checks

    config = replace(get_site_config("default"), provider_ids=("jlpt_cert",),
                     required_provider_ids=(), public_min_years=1, release_shard_size=1,
                     public_min_years_by_canonical_prefix={})
    monkeypatch.setattr("app.publisher.get_site_config", lambda _: config)
    monkeypatch.setattr("app.publisher.quarantined_provider_ids", lambda *args, **kwargs: set())
    monkeypatch.setattr(checks, "get_site_config", lambda _: config)
    monkeypatch.setattr(checks, "validate_source_inventory", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.publisher.build_bundles",
                        lambda **kwargs: build_bundles(**kwargs, max_bundle_bytes=10000))
    first = material_paper()
    later = replace(first, source_exam_id="later-edition", storage_key="later.pdf",
                    source_material=replace(first.source_material, date=SourceDate("edition_year", 2018)))
    papers = [first, later]
    write_provider_state(provider_paths(tmp_path, "jlpt_cert"), raw_pages=[],
                         normalized=NormalizedCatalog(papers, []), aliases=[], failures=[], manifest=None)
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    for paper in papers:
        (mirror / paper.storage_key).write_bytes(PAYLOAD)
    _, bundles = publish_site(tmp_path, site_id="default", repository="owner/archive")
    assert len(bundles) == 2
    assert len({bundle.release_tag for bundle in bundles}) == 2
    assert sorted(bundle.years for bundle in bundles) == [[101], [107]]
    site = site_paths(tmp_path, "default")
    feed = json.loads(site.frontend_bundles_path.read_text())
    assert feed["bundles"][0]["years"] == [107, 101]
    assert feed["bundles"][0]["sourceMaterial"] == to_plain_data(summarize_papers(papers))
    assert len(feed["bundles"][0]["parts"]) == 2
    validator("frontend-bundle-feed-v3.schema.json").validate(feed)
    assert checks.validate_publication(tmp_path) == (2, 1, 2)
    global_report = audit_site_archives(tmp_path, site_id="default", verify_content=True)
    assert global_report["errors"] == []
    assert global_report["source_record_count"] == 2
    for tag in {bundle.release_tag for bundle in bundles}:
        scoped = audit_site_archives(tmp_path, site_id="default", verify_content=True, release_tag=tag)
        assert scoped["errors"] == []
        assert scoped["source_record_count"] == 1
