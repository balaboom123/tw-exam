"""Native grade, role and form approvals have separate effects and recovery."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from app.models import NormalizedCatalog, ParsedPaper
from app.normalizer import normalize_papers, renormalize_catalog
from app.providers.wdasec_skill import papers as owner
from app.publication_metadata import publication_paper_subject
from tests.test_wdasec_forms import normalized

ROOT = Path(__file__).resolve().parents[1]


def document():
    payload = json.loads(owner.native_papers_path(ROOT).read_text())
    payload["papers"] = [
        next(
            row
            for row in payload["papers"]
            if row["category_code"] == "06800" and row["year_roc"] == 90
        )
    ]
    return payload


def read(tmp_path, payload):
    path = tmp_path / "native.json"
    path.write_text(json.dumps(payload))
    return owner.read_native_papers(path)[0]


@pytest.mark.parametrize("dimension", ["grade", "role", "form"])
def test_approving_one_native_dimension_does_not_approve_the_other(
    tmp_path, monkeypatch, dimension
):
    payload = document()
    row = payload["papers"][0]
    if dimension != "grade":
        row.pop("reviewed_grade")
    if dimension != "role":
        row.pop("reviewed_role")
    native = read(tmp_path, payload)
    monkeypatch.setattr(owner, "_index", lambda: {native.key: native})
    original = normalized(native)
    (paper,) = renormalize_catalog(NormalizedCatalog([original], []), []).papers
    assert paper.level_id == ("single" if dimension == "grade" else "class-a")
    assert paper.file_type == ("question_answer" if dimension == "role" else "question")
    assert publication_paper_subject(paper) == (
        "鋼琴調音 單一級 學科" if dimension == "grade" else original.subject_name_raw
    )
    assert paper.subject_name_raw == original.subject_name_raw
    assert paper.download_url_source == original.download_url_source
    assert paper.checksum == original.checksum
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
    assert acquired.level_id == paper.level_id
    assert acquired.file_type == paper.file_type
    assert publication_paper_subject(acquired) == publication_paper_subject(paper)
    assert set(parsed.files) == {"question"}


def test_legacy_combined_evidence_decodes_to_the_same_two_approved_facts(tmp_path):
    current = document()
    expected = read(tmp_path, current)
    legacy = document()
    legacy["schema_version"] = 1
    row = legacy["papers"][0]
    grade, role = row.pop("reviewed_grade"), row.pop("reviewed_role")
    row["reviewed_identity"] = {**grade, "file_role": role["file_role"]}
    restored = read(tmp_path, legacy)
    assert asdict(restored) == asdict(expected)
    assert restored.key == expected.key
    assert restored.family == expected.family


@pytest.mark.parametrize(
    "damage",
    [
        "missing_grade_evidence",
        "missing_role_evidence",
        "role_on_practical",
        "unknown_role",
        "count_bool",
        "count_null",
        "count_zero",
        "count_too_large",
        "v1_fields_in_v2",
        "v2_fields_in_v1",
    ],
)
def test_each_dimension_rejects_incomplete_or_cross_version_approval(tmp_path, damage):
    payload = document()
    row = payload["papers"][0]
    if damage == "missing_grade_evidence":
        row["reviewed_grade"].pop("evidence")
    elif damage == "missing_role_evidence":
        row["reviewed_role"].pop("evidence")
    elif damage == "role_on_practical":
        row.pop("reviewed_grade")
        row["subject_code"] = "06800-single-practical"
    elif damage == "unknown_role":
        row["reviewed_role"]["file_role"] = "answer"
    elif damage.startswith("count_"):
        row["reviewed_role"]["question_count"] = {
            "count_bool": True,
            "count_null": None,
            "count_zero": 0,
            "count_too_large": 1001,
        }[damage]
    elif damage == "v1_fields_in_v2":
        row["reviewed_identity"] = {
            **row.pop("reviewed_grade"),
            "file_role": row.pop("reviewed_role")["file_role"],
        }
    else:
        payload["schema_version"] = 1
    with pytest.raises(ValueError):
        read(tmp_path, payload)


def test_native_evidence_has_one_authoritative_versioned_file(tmp_path):
    folder = tmp_path / "catalog/mappings/wdasec"
    folder.mkdir(parents=True)
    legacy = folder / "native-paper-facts-v1.json"
    current = folder / "native-paper-facts-v3.json"
    assert owner.native_papers_path(tmp_path) == current
    legacy.write_text("{}")
    assert owner.native_papers_path(tmp_path) == legacy
    current.write_text("{}")
    with pytest.raises(ValueError, match="competing versioned owners"):
        owner.native_papers_path(tmp_path)


def test_v2_contract_reuses_legacy_native_context_and_validates_separate_approvals():
    paths = [
        ROOT / f"schemas/wdasec-native-paper-facts-v{version}.schema.json" for version in (1, 2)
    ]
    schemas = [json.loads(path.read_text()) for path in paths]
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas
    )
    validator = Draft202012Validator(schemas[1], registry=registry)
    payload = document()
    payload["schema_version"] = 2
    validator.validate(payload)
    payload["papers"][0]["reviewed_role"]["question_count"] = 100
    validator.validate(payload)
    payload["papers"][0].pop("reviewed_grade")
    validator.validate(payload)
