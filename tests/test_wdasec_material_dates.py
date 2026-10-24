"""Native annual dates correct public copies without discarding source contexts."""

import hashlib
import json
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from app.bundler import build_bundles, validate_public_materials
from app.models import NormalizedCatalog, ParsedPaper
from app.normalizer import normalize_papers, renormalize_catalog
from app.providers.wdasec_skill import papers as owner
from app.source_material import publication_year_roc
from tests.test_wdasec_forms import normalized

ROOT = Path(__file__).resolve().parents[1]
BEVERAGE = [p for p in owner.native_papers() if p.reviewed_material is not None]
CROSS_LISTED = [p for p in BEVERAGE if p.year_roc != p.reviewed_material.date.year_roc]


def test_reviewed_material_covers_both_complete_retained_beverage_histories():
    assert len(BEVERAGE) == 45
    assert len({p.checksum for p in BEVERAGE}) == 41
    assert len(CROSS_LISTED) == 4
    assert {p.category_code for p in BEVERAGE} == {"20600"}
    assert all(p.reviewed_material.kind == "administered" for p in BEVERAGE)
    assert all(p.reviewed_material.date.basis == "exam_year" for p in BEVERAGE)
    assert all(p.reviewed_role.question_count == 80 for p in BEVERAGE)


@pytest.mark.parametrize("native", CROSS_LISTED, ids=lambda p: p.source_exam_id + p.checksum[:8])
def test_acquisition_and_retained_dates_agree_without_rewriting_source_identity(native):
    original = replace(normalized(native), storage_key="original-source.pdf")
    (retained,) = renormalize_catalog(NormalizedCatalog([original], []), []).papers
    parsed = ParsedPaper(
        category_raw=original.category_raw,
        category_code=native.category_code,
        subject_code=native.subject_code,
        subject_name_raw=native.subject_name_raw,
        files={"question": native.source_url},
        mirror_files={
            "question": {"checksum": native.checksum, "storage_key": original.storage_key}
        },
    )
    (acquired,) = normalize_papers(
        native.source_exam_id,
        native.year_roc + 1911,
        native.exam_name_raw,
        [parsed],
        [],
        "",
        {},
        provider_id="wdasec_skill",
    ).papers
    for paper in (retained, acquired):
        assert paper.year_roc == native.year_roc
        assert publication_year_roc(paper) == native.reviewed_material.date.year_roc
        assert publication_year_roc(paper) != paper.year_roc
        assert paper.source_exam_id == native.source_exam_id
        assert paper.exam_name_raw == native.exam_name_raw
        assert paper.storage_key == original.storage_key
        assert paper.download_url_source == native.source_url
        assert paper.checksum == native.checksum
        assert paper.file_type == "question_answer"
        assert paper.schema_version == 3
    assert retained.bundle_id == acquired.bundle_id


@pytest.mark.parametrize("dimension", ["material", "role"])
def test_role_and_material_approval_have_independent_effects(monkeypatch, dimension):
    native = CROSS_LISTED[0]
    independent = replace(
        native,
        reviewed_role=native.reviewed_role if dimension == "role" else None,
        reviewed_material=native.reviewed_material if dimension == "material" else None,
    )
    monkeypatch.setattr(owner, "_index", lambda: {independent.key: independent})
    (paper,) = renormalize_catalog(NormalizedCatalog([normalized(native)], []), []).papers
    assert paper.file_type == ("question_answer" if dimension == "role" else "question")
    assert paper.source_material == independent.reviewed_material
    assert publication_year_roc(paper) == (
        native.reviewed_material.date.year_roc if dimension == "material" else native.year_roc
    )
    assert paper.level_id == ("class-b" if "乙級" in native.native_header else "class-c")
    assert not paper.variant_ids


@pytest.mark.parametrize("change", [{"checksum": "0" * 64}, {"source_exam_id": "209901010001"}])
def test_changed_source_clears_inherited_native_date_and_requires_new_review(change):
    native = CROSS_LISTED[0]
    original = replace(
        normalized(native), source_material=native.reviewed_material, schema_version=3, **change
    )
    (paper,) = renormalize_catalog(NormalizedCatalog([original], []), []).papers
    assert paper.source_material is None and paper.schema_version == 2
    assert paper.file_type == "question"
    assert publication_year_roc(paper) == paper.year_roc
    assert paper.classification_confidence == "review"
    assert "paper-form-unknown" in paper.variant_ids


@pytest.mark.parametrize("damage", ["header_year", "other_URL", "null", "legacy", "unresolved"])
def test_material_approval_requires_exact_resolved_native_annual_evidence(tmp_path, damage):
    payload = json.loads(owner.native_papers_path(ROOT).read_text())
    row = next(p for p in payload["papers"] if "reviewed_material" in p)
    payload["papers"] = [row]
    if damage == "header_year":
        row["native_header"] = "899年度 飲料調製乙級學科測試試題"
    elif damage == "other_URL":
        row["reviewed_material"]["evidence_url"] = "https://owinform.wdasec.gov.tw/other.pdf"
    elif damage == "null":
        row["reviewed_material"] = None
    elif damage == "legacy":
        payload["schema_version"] = 3
    else:
        row["reviewed_material"]["review_reason"] = "Native annual date not yet reviewed"
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        owner.read_native_papers(path)


def test_v4_material_contract_and_legacy_v3_recovery_remain_readable(tmp_path):
    paths = [
        ROOT / f"schemas/wdasec-native-paper-facts-v{version}.schema.json"
        for version in (1, 2, 3, 4)
    ]
    paths.append(ROOT / "schemas/source-material-v1.schema.json")
    schemas = [json.loads(p.read_text()) for p in paths]
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas
    )
    payload = json.loads(owner.native_papers_path(ROOT).read_text())
    current = next(
        s for s in schemas if s["$id"].endswith("wdasec-native-paper-facts-v4.schema.json")
    )
    Draft202012Validator(current, registry=registry).validate(payload)
    payload["schema_version"] = 3
    for row in payload["papers"]:
        row.pop("reviewed_material", None)
    path = tmp_path / "v3.json"
    path.write_text(json.dumps(payload))
    restored = owner.read_native_papers(path)
    assert len(restored) == len(owner.native_papers())
    assert all(p.reviewed_material is None for p in restored)


def test_partial_material_history_blocks_before_any_ZIP_is_written(tmp_path):
    native = CROSS_LISTED[0]
    (reviewed,) = renormalize_catalog(NormalizedCatalog([normalized(native)], []), []).papers
    missing = replace(reviewed, source_material=None, schema_version=2)
    target = tmp_path / "bundles"
    with pytest.raises(ValueError, match="[Cc]omplete|[Pp]artial|[Mm]ix"):
        build_bundles(target, tmp_path, NormalizedCatalog([reviewed, missing], []), "")
    assert not target.exists()


def test_cross_listed_native_years_share_four_payloads_and_retain_eight_contexts(
    tmp_path, monkeypatch
):
    originals = [p for p in BEVERAGE if p.source_exam_id in {"201202290006", "201202290007"}]
    assert len(originals) == 8
    index, records = {}, []
    for i, native in enumerate(originals):
        content = f"Independent test fixture for {native.checksum}".encode()
        fixture = replace(native, checksum=hashlib.sha256(content).hexdigest())
        index[fixture.key] = fixture
        path = tmp_path / f"source-{i}.pdf"
        path.write_bytes(content)
        records.append(replace(normalized(fixture), storage_key=path.name))
    monkeypatch.setattr(owner, "_index", lambda: index)
    catalog = renormalize_catalog(NormalizedCatalog(records, []), [])
    assert len(validate_public_materials(catalog.papers)) == 2
    built = build_bundles(tmp_path / "bundles", tmp_path, catalog, "")
    assert not built.failures and len(built.bundles) == 2
    for bundle in built.bundles:
        assert bundle.file_count == 2
        with zipfile.ZipFile(tmp_path / "bundles" / bundle.asset_name) as archive:
            manifest = json.loads(archive.read("bundle.json"))
            assert manifest["schema_version"] == 3 and manifest["manifest_version"] == 4
            papers = manifest["papers"]
            assert len(papers) == 4
            assert len({p["bundle_entry"] for p in papers}) == 2
            assert {p["source_exam_id"] for p in papers} == {"201202290006", "201202290007"}
            assert {p["year_roc"] for p in papers} == {99, 100}
            assert {name.split("/")[0] for name in archive.namelist() if name != "bundle.json"} == {
                "99",
                "100",
            }
            for paper in papers:
                native_year = paper["source_material"]["date"]["year_ad"] - 1911
                assert paper["bundle_entry"].startswith(f"{native_year}/")
                assert paper["file_type"] == "question_answer"
                assert (
                    hashlib.sha256(archive.read(paper["bundle_entry"])).hexdigest()
                    == paper["checksum"]
                )
