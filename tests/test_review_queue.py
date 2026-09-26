import copy
import json
from dataclasses import asdict
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st
from jsonschema import Draft202012Validator

from app.models import NormalizedCatalog, ReviewItem
from app.paths import provider_paths
from app.publisher import write_provider_state
from app.review_queue import REVIEW_FIELDS, decode_review_queue, encode_review_queue
from app.state import load_provider_state

ROOT = Path(__file__).resolve().parents[1]


@given(
    st.lists(
        st.builds(
            ReviewItem,
            raw_category=st.text(),
            normalized_candidate=st.text(),
            source_exam_id=st.text(),
            year_roc=st.integers(),
            provider_id=st.just("moex"),
            raw_exam_name=st.text(),
            classification_signature=st.text(),
            bundle_id=st.text(),
            reason=st.text(),
        ),
        max_size=20,
    )
)
@settings(max_examples=80, deadline=None)
def test_unicode_review_records_round_trip_without_reclassifying(reviews):
    packed = encode_review_queue(reviews, "moex")
    restored = decode_review_queue(json.loads(json.dumps(packed)), "moex")
    assert restored == reviews
    assert encode_review_queue(restored, "moex") == packed


def test_legacy_reviews_infer_their_provider_and_preserve_original_reasons():
    row = {
        "raw_category": "公職營養師",
        "normalized_candidate": "營養師",
        "source_exam_id": "082010",
        "year_roc": 82,
        "reason": "original reviewed evidence",
    }
    restored = decode_review_queue([row], "moex")
    assert restored[0].provider_id == "moex"
    assert restored[0].reason == "original reviewed evidence"
    assert restored == decode_review_queue(encode_review_queue(restored, "moex"), "moex")


def example():
    return encode_review_queue(
        [
            ReviewItem("category", "candidate", "event", 115, provider_id="moex", reason="review"),
        ],
        "moex",
    )


@pytest.mark.parametrize(
    "kind",
    [
        "version",
        "owner",
        "fields",
        "prefix",
        "suffix",
        "order",
        "row_size",
        "reference",
        "bool_reference",
        "bool_year",
        "table",
    ],
)
def test_malformed_compact_ledgers_fail_visibly(kind):
    payload = copy.deepcopy(example())
    if kind == "version":
        payload["schema_version"] = 3
    if kind == "owner":
        payload["provider_id"] = "ceec_ast"
    if kind == "fields":
        payload["fields"] = list(reversed(payload["fields"]))
    if kind == "prefix":
        payload["strings"][0] = [1, "invalid"]
    if kind == "suffix":
        payload["strings"][0] = [0, None]
    if kind == "order":
        payload["strings"].append([0, ""])
    if kind == "row_size":
        payload["items"][0].pop()
    if kind == "reference":
        payload["items"][0][0] = len(payload["strings"])
    if kind == "bool_reference":
        payload["items"][0][0] = True
    if kind == "bool_year":
        payload["items"][0][REVIEW_FIELDS.index("year_roc")] = True
    if kind == "table":
        payload["strings"] = {}
    with pytest.raises(ValueError):
        decode_review_queue(payload, "moex")


def test_queue_cannot_transfer_reviews_to_another_provider():
    with pytest.raises(ValueError, match="provider owner"):
        encode_review_queue(
            [ReviewItem("category", "candidate", "event", 115, provider_id="ceec_ast")], "moex"
        )
    with pytest.raises(ValueError, match="provider owner"):
        decode_review_queue(
            [asdict(ReviewItem("category", "candidate", "event", 115, provider_id="ceec_ast"))],
            "moex",
        )


def test_provider_writer_uses_compact_storage_and_state_reader_expands_it(tmp_path):
    provider = provider_paths(tmp_path, "moex")
    reviews = decode_review_queue(example(), "moex")
    write_provider_state(provider, [], NormalizedCatalog([], reviews), [], [], None)
    saved = json.loads(provider.review_queue_path.read_text())
    assert saved["schema_version"] == 2
    assert saved["provider_id"] == "moex"
    assert load_provider_state(provider)[1].review_queue == reviews


def test_empty_queue_keeps_its_existing_minimal_representation():
    assert encode_review_queue([], "moex") == []
    assert decode_review_queue([], "moex") == []


def test_compact_schema_and_dataclass_field_order_agree():
    schema = json.loads((ROOT / "schemas/review-queue-v2.schema.json").read_text())
    assert schema["oneOf"][1]["properties"]["fields"]["const"] == list(REVIEW_FIELDS)
    validator = Draft202012Validator(schema)
    validator.validate(json.loads(json.dumps(example())))
    validator.validate([])


def test_review_cli_expands_to_stdout_and_file_without_loading_catalog(tmp_path, capsys):
    from app.cli import main

    provider = provider_paths(tmp_path, "moex")
    provider.data_dir.mkdir(parents=True)
    provider.papers_dir.mkdir()
    (provider.papers_dir / "2026.json").write_text("not valid JSON; display must not read papers")
    provider.review_queue_path.write_text(json.dumps(example()))
    args = ["review-queue", "--repo-root", str(tmp_path), "--provider", "moex"]
    assert main(args) == 0
    rendered = json.loads(capsys.readouterr().out)
    assert rendered == [asdict(review) for review in decode_review_queue(example(), "moex")]
    output = tmp_path / "output/reviews.json"
    assert main([*args, "--output", str(output)]) == 0
    assert json.loads(output.read_text()) == rendered


def test_review_cli_surfaces_corrupt_queue_and_unknown_provider(tmp_path, capsys):
    from app.cli import main

    provider = provider_paths(tmp_path, "moex")
    provider.data_dir.mkdir(parents=True)
    provider.review_queue_path.write_text('{"schema_version": 99}')
    assert main(["review-queue", "--repo-root", str(tmp_path), "--provider", "moex"]) == 1
    assert "Unsupported review queue" in capsys.readouterr().out
    assert main(["review-queue", "--repo-root", str(tmp_path), "--provider", "../other"]) == 1
    assert "Unknown provider" in capsys.readouterr().out


def test_encoding_rejects_invalid_text_and_boolean_year():
    row = ReviewItem("category", "candidate", "event", 115, provider_id="moex")
    row.raw_exam_name = None
    with pytest.raises(ValueError, match="text fields"):
        encode_review_queue([row], "moex")
    row.raw_exam_name = ""
    row.year_roc = True
    with pytest.raises(ValueError, match="year must be an integer"):
        encode_review_queue([row], "moex")


@pytest.mark.parametrize(
    "entry",
    [
        None,
        {},
        {"year_roc": True},
        {
            "raw_category": "x",
            "normalized_candidate": "x",
            "source_exam_id": "event",
            "year_roc": 115,
            "reason": None,
        },
    ],
)
def test_display_rejects_malformed_legacy_records(entry):
    with pytest.raises(ValueError):
        decode_review_queue([entry], "moex")
