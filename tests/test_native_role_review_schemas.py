"""Shared review structure must preserve every provider's source boundaries."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ROOT = Path(__file__).resolve().parents[1]
PROVIDERS = [
    ("special-admission-answer-corrections-v1", "special_admission/answer-corrections-v1"),
    ("tcte-answer-corrections-v1", "tcte/answer-corrections-v1"),
    ("moex-native-question-references-v1", "moex/native-question-references-v1"),
    ("taisugar-native-file-roles-v2", "taisugar/native-file-roles-v2"),
]


@pytest.mark.parametrize("schema_name,data_name", PROVIDERS)
def test_native_review_shape_and_source_restrictions_survive_shared_schema(schema_name, data_name):
    documents = [(p.name, json.loads(p.read_text())) for p in (ROOT / "schemas").glob("*.json")]
    registry = Registry().with_resources(
        (
            s.get("$id", f"https://tw-exam.invalid/schemas/{name}"),
            Resource.from_contents(s, default_specification=DRAFT202012),
        )
        for name, s in documents
    )
    schema = json.loads((ROOT / "schemas" / (schema_name + ".schema.json")).read_text())
    validator = Draft202012Validator(
        schema, registry=registry, format_checker=Draft202012Validator.FORMAT_CHECKER
    )
    original = json.loads((ROOT / "catalog/mappings" / (data_name + ".json")).read_text())
    validator.validate(original)
    for field in original["facts"][0]:
        broken = deepcopy(original)
        del broken["facts"][0][field]
        assert not validator.is_valid(broken), field
    for field, value in (
        ("source_exam_id", "unrelated-provider-106"),
        ("category_code", "unrelated-category"),
        ("subject_code", "unrelated/subject"),
        ("source_url", "https://unrelated.example/paper.pdf"),
        ("checksum", "not-a-checksum"),
        ("native_page", 0),
        ("reviewed_at", "2026-02-30"),
        ("title", ""),
        ("reason", ""),
        ("year_roc", True),
    ):
        broken = deepcopy(original)
        broken["facts"][0][field] = value
        assert not validator.is_valid(broken), field
    for broken in (
        {**original, "provider_id": "unrelated_provider"},
        {**original, "schema_version": original["schema_version"] + 1},
        {**original, "facts": []},
        {**original, "unreviewed_dimension": "occupation"},
        {**original, "facts": [{**original["facts"][0], "unreviewed_dimension": "occupation"}]},
    ):
        assert not validator.is_valid(broken)
