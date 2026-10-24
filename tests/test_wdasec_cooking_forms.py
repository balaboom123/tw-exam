"""Content, component and role judgments preserve independent native meanings."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from app.classification import classify_normalized_paper
from app.models import NormalizedCatalog, ParsedPaper
from app.normalizer import normalize_papers, renormalize_catalog
from app.providers.wdasec_skill import papers as owner
from app.publication_metadata import publication_paper_subject, publication_paper_subject_code
from tests.test_wdasec_forms import normalized

ROOT = Path(__file__).resolve().parents[1]
SENIOR = [paper for paper in owner.native_papers() if paper.form == "senior-chef"]
COMPONENTS = [
    paper
    for paper in owner.native_papers()
    if paper.category_code in {"07600", "07601", "07602"}
    and paper.reviewed_component is not None
    and paper.reviewed_component.component_id == "theory"
]


@pytest.mark.parametrize("senior", SENIOR, ids=lambda p: f"{p.year_roc}-{p.category_code}")
def test_senior_cooking_content_stays_separate_from_its_ordinary_control(senior):
    ordinary = next(
        paper
        for paper in owner.native_papers()
        if paper.form == "ordinary"
        and paper.source_exam_id == senior.source_exam_id
        and paper.family == senior.family
    )
    a = classify_normalized_paper(normalized(senior))
    b = classify_normalized_paper(normalized(ordinary))
    assert a.level_id == b.level_id == "class-c"
    assert a.track_id == b.track_id
    assert a.bundle_id != b.bundle_id
    assert a.variant_ids == ("paper-form-senior-chef",)
    assert b.variant_ids == ("paper-form-ordinary",)
    assert a.stage_id == b.stage_id == "not-applicable"
    assert a.confidence == b.confidence == "high"
    assert "資深廚師學科試卷" in a.bundle_name
    assert senior.reviewed_role is not None
    assert senior.reviewed_role.question_count == 20


@pytest.mark.parametrize("native", COMPONENTS, ids=lambda p: f"{p.year_roc}-{p.category_code}")
def test_component_correction_projects_theory_without_rewriting_source_fields(native):
    original = normalized(native)
    (retained,) = renormalize_catalog(NormalizedCatalog([original], []), []).papers
    parsed = ParsedPaper(
        category_raw=original.category_raw,
        category_code=native.category_code,
        subject_code=native.subject_code,
        subject_name_raw=native.subject_name_raw,
        files={"question": native.source_url},
        mirror_files={"question": {"checksum": native.checksum}},
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
        assert paper.subject_name_raw == original.subject_name_raw
        assert paper.subject_code == original.subject_code
        assert paper.download_url_source == original.download_url_source
        assert paper.checksum == original.checksum
        assert paper.level_id == "class-c"
        assert paper.file_type == "question_answer"
        assert publication_paper_subject(paper).endswith("學科")
        assert publication_paper_subject_code(paper) == f"{native.family[1]}-question"
    assert acquired.bundle_id == retained.bundle_id


def test_component_approval_does_not_approve_grade_role_or_paper_form(monkeypatch):
    native = next(paper for paper in COMPONENTS if paper.form == "ordinary")
    component_only = replace(native, reviewed_role=None)
    monkeypatch.setattr(owner, "_index", lambda: {native.key: component_only})
    (paper,) = renormalize_catalog(NormalizedCatalog([normalized(native)], []), []).papers
    assert paper.file_type == "question"
    assert paper.level_id == "class-c"
    assert "paper-form-senior-chef" not in paper.variant_ids
    assert publication_paper_subject(paper).endswith("學科")
    assert component_only.reviewed_grade is None


def test_native_practical_component_does_not_turn_a_written_paper_into_theory(
    tmp_path, monkeypatch
):
    payload = document()
    row = next(
        row
        for row in payload["papers"]
        if row["form"] == "ordinary" and "reviewed_component" in row
    )
    row.pop("reviewed_role")
    row["subject_code"] = row["subject_code"].replace("-practical", "-question")
    row["subject_name_raw"] = row["subject_name_raw"].removesuffix("術科") + "學科"
    row["native_header"] = "中餐烹調 丙級 技能檢定術科測驗試題"
    row["reviewed_component"] = {
        "component_id": "practical",
        "native_component": "術科",
        "evidence": (
            "Exact native header establishes the practical component "
            "independently of delivery format."
        ),
    }
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(payload))
    matching = [
        paper
        for paper in owner.read_native_papers(path)
        if paper.source_exam_id == row["source_exam_id"]
        and paper.source_url == row["source_url"]
        and paper.checksum == row["checksum"]
        and paper.reviewed_component is not None
        and paper.component_id == "practical"
    ]
    assert len(matching) == 1
    assert matching[0].reviewed_role is None
    native = matching[0]
    monkeypatch.setattr(owner, "_index", lambda: {native.key: native})
    paper = normalized(native)
    assert publication_paper_subject(paper).endswith("術科")
    assert publication_paper_subject_code(paper).endswith("-practical")
    assert owner.native_file_role(paper) == "question"


def document():
    return json.loads(owner.native_papers_path(ROOT).read_text())


@pytest.mark.parametrize(
    "damage",
    [
        "missing_evidence",
        "wrong_component",
        "wrong_header",
        "missing_correction",
        "v2_component",
        "v2_form",
    ],
)
def test_incomplete_or_legacy_component_and_form_approvals_fail_closed(tmp_path, damage):
    payload = document()
    row = next(
        row
        for row in payload["papers"]
        if "reviewed_component" in row and row["form"] == "senior-chef"
    )
    if damage == "missing_evidence":
        row["reviewed_component"].pop("evidence")
    elif damage == "wrong_component":
        row["reviewed_component"]["component_id"] = "practical"
    elif damage == "wrong_header":
        row["native_header"] = row["native_header"].replace("學科", "")
    elif damage == "missing_correction":
        row.pop("reviewed_component")
    elif damage == "v2_component":
        payload["schema_version"] = 2
    else:
        payload["schema_version"] = 2
        for item in payload["papers"]:
            item.pop("reviewed_component", None)
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        owner.read_native_papers(path)


def test_v3_contract_and_v2_recovery_keep_one_owner_and_exact_context(tmp_path):
    schemas = [
        json.loads((ROOT / f"schemas/wdasec-native-paper-facts-v{version}.schema.json").read_text())
        for version in (1, 2, 3)
    ]
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas
    )
    payload = document()
    payload["schema_version"] = 3
    for row in payload["papers"]:
        row.pop("reviewed_material", None)
    Draft202012Validator(schemas[2], registry=registry).validate(payload)
    payload["schema_version"] = 2
    payload["papers"] = [
        row
        for row in payload["papers"]
        if row["form"] != "senior-chef" and "reviewed_component" not in row
    ]
    folder = tmp_path / "catalog/mappings/wdasec"
    folder.mkdir(parents=True)
    legacy = folder / "native-paper-facts-v2.json"
    legacy.write_text(json.dumps(payload))
    assert owner.native_papers_path(tmp_path) == legacy
    assert len(owner.read_native_papers(legacy)) == len(payload["papers"])
    (folder / "native-paper-facts-v3.json").write_text("{}")
    with pytest.raises(ValueError, match="competing versioned owners"):
        owner.native_papers_path(tmp_path)
