"""Generated checks for identity purity and repeatable normalization.

These use synthetic source records and every registered provider, without
loading generated provider state or contacting an official source.
"""

from dataclasses import asdict, replace
import json
from pathlib import Path
import re
import unicodedata

from hypothesis import example, given, settings, strategies as st
from jsonschema import Draft202012Validator
import pytest

from app.classification import (
    _classify_paper_uncached,
    classify_normalized_paper,
    classify_paper,
    identity_fields,
    normalize_text as normalize_identity_text,
)
from app.models import NormalizedCatalog, NormalizedPaper
from app.normalizer import normalize_text, renormalize_catalog
from app.providers.registry import _PROVIDER_FACTORIES


PAPER_CONTRACT = Draft202012Validator(json.loads(
    (Path(__file__).resolve().parents[1] / "schemas/normalized-paper-v2.schema.json").read_text(encoding="utf-8")
))
SOURCE_TEXT = st.one_of(
    st.sampled_from([
        "", " \t\u3000", "一般行政", "三等_一般行政類科", "中高級", "Ｎ２",
        "01機械類 專業科目(一)", "分科測驗－物理", "甲級", "士級晉佐級",
        "一般行政（兩岸組一）", "一般行政（選試英文）", "高考三級第一試",
    ]),
    st.text(alphabet=st.characters(exclude_categories=("Cs",)), max_size=60),
)
EXAM_TEXT = st.one_of(
    st.sampled_from([
        "公務人員高等考試三級", "公務人員普通考試", "地方特考", "升官等考試",
        "專門職業及技術人員高等考試", "分科測驗", "四技二專統一入學測驗",
        "教師甄試", "全民英檢", "其他特種考試",
    ]),
    SOURCE_TEXT,
)
SOURCE_KEY = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-", min_size=1, max_size=20)
RECORD = st.fixed_dictionaries({
    "source_exam_id": SOURCE_KEY,
    "year_ad": st.integers(min_value=1912, max_value=2100),
    "category_raw": SOURCE_TEXT,
    "exam_name_raw": EXAM_TEXT,
    "canonical_id": SOURCE_KEY,
    "canonical_name": SOURCE_TEXT,
    "subject_name_raw": SOURCE_TEXT,
    "subject_code": st.sampled_from(["", "0101", "0102"]),
})


def _paper(provider_id: str, record: dict) -> NormalizedPaper:
    return NormalizedPaper(
        provider_id=provider_id,
        source_exam_id=record["source_exam_id"],
        year_roc=record["year_ad"] - 1911,
        category_raw=record["category_raw"],
        exam_name_raw=record["exam_name_raw"],
        canonical_id=record["canonical_id"],
        canonical_name=record["canonical_name"],
        subject_name_raw=record["subject_name_raw"],
        subject_code=record["subject_code"],
        category_code="301",
        paper_code=f"301-{record['subject_code']}-question",
        file_type="question",
        download_url_source="https://official.example/question.pdf",
        storage_key=f"{record['source_exam_id']}/question.pdf",
        checksum="a" * 64,
        schema_version=2,
    )


@pytest.mark.parametrize("normalizer", [normalize_text, normalize_identity_text])
@example(text="\u3000Ａ\tＢ\n＿－\u3000")
@given(text=st.text(alphabet=st.characters(exclude_categories=("Cs",)), max_size=100))
def test_text_normalization_is_idempotent(normalizer, text: str) -> None:
    normalized = normalizer(text)
    assert normalizer(normalized) == normalized
    assert normalizer(unicodedata.normalize("NFKC", text)) == normalized
    assert normalized == normalized.strip()
    assert not re.search(r"\s{2,}", normalized)


@pytest.mark.parametrize("provider_id", sorted(_PROVIDER_FACTORIES))
@settings(max_examples=40)
@example(record={
    "source_exam_id": "event-115", "year_ad": 2026, "category_raw": "一般行政",
    "exam_name_raw": "教師甄試", "canonical_id": "general-administration", "canonical_name": "一般行政",
    "subject_name_raw": " \t\u3000", "subject_code": "0101",
})
@given(record=RECORD)
def test_classification_survives_projection_and_source_normalization(provider_id: str, record: dict) -> None:
    identity = classify_paper(provider_id=provider_id, **record)
    assert identity == _classify_paper_uncached(provider_id=provider_id, **record)

    projected = replace(_paper(provider_id, record), **identity_fields(identity))
    PAPER_CONTRACT.validate(asdict(projected))
    assert classify_normalized_paper(projected) == identity
    assert replace(projected, **identity_fields(classify_normalized_paper(projected))) == projected

    normalized_source = {
        key: normalize_identity_text(value) if key in {"category_raw", "exam_name_raw", "subject_name_raw"} else value
        for key, value in record.items()
    }
    normalized_identity = classify_paper(provider_id=provider_id, **normalized_source)
    assert normalized_identity.signature == identity.signature
    assert normalized_identity.bundle_id == identity.bundle_id
    assert projected.exam_class and projected.exam_subclass


@given(
    track=st.sampled_from(["一般行政", "資訊處理", "會計", "地政"]),
    year=st.integers(min_value=1950, max_value=2099),
    dimensions=st.lists(st.sampled_from([
        ("公務人員高等考試三級", ""),
        ("公務人員普通考試", ""),
        ("公務人員初等考試", ""),
        ("地方特考", "（三等）"),
        ("地方特考", "（四等）"),
        ("公務人員高等考試三級", "（一般組）"),
        ("公務人員高等考試三級", "（兩岸組一）"),
        ("公務人員高等考試三級第一試", ""),
        ("公務人員高等考試三級第二試", ""),
    ]), min_size=2, max_size=9, unique=True),
)
def test_bundles_preserve_series_level_variant_and_stage_distinctions(track, year, dimensions) -> None:
    identities = []
    for exam, suffix in dimensions:
        record = dict(
            provider_id="moex", source_exam_id=f"event-{year}", year_ad=year,
            category_raw=track + suffix, exam_name_raw=f"{year - 1911}年{exam}",
            canonical_id="general-administration", canonical_name=track,
        )
        current = classify_paper(**record)
        later = classify_paper(**{**record, "source_exam_id": f"event-{year + 1}", "year_ad": year + 1,
                                  "exam_name_raw": f"{year + 1 - 1911}年{exam}"})
        assert current.confidence != "review"
        assert current.bundle_id == later.bundle_id
        assert current.signature == later.signature
        assert current.exam_event_id != later.exam_event_id
        identities.append(current)
    assert len({identity.signature for identity in identities}) == len(dimensions)
    assert len({identity.bundle_id for identity in identities}) == len(dimensions)


@given(events=st.lists(st.integers(min_value=1, max_value=999999), min_size=2, max_size=8, unique=True))
def test_unresolved_identity_stays_isolated_by_event(events: list[int]) -> None:
    identities = [classify_paper(
        provider_id="moex", source_exam_id=f"event-{event}", year_ad=2026,
        category_raw="一般行政", exam_name_raw="其他特種考試", canonical_id="general-administration",
        canonical_name="一般行政",
    ) for event in events]
    assert {identity.confidence for identity in identities} == {"review"}
    assert len({identity.signature for identity in identities}) == 1
    assert len({identity.bundle_id for identity in identities}) == len(events)


@given(records=st.lists(st.tuples(st.sampled_from(sorted(_PROVIDER_FACTORIES)), RECORD), min_size=1, max_size=8))
def test_repeated_catalog_normalization_preserves_papers_and_review_evidence(records) -> None:
    original = NormalizedCatalog(papers=[_paper(provider_id, record) for provider_id, record in records], review_queue=[])
    before = asdict(original)
    first = renormalize_catalog(original, alias_rules=[])
    second = renormalize_catalog(first, alias_rules=[])
    assert asdict(original) == before
    assert second == first
    assert len(first.papers) == len(original.papers)
    for old, new in zip(original.papers, first.papers, strict=True):
        for field in (
            "provider_id", "source_exam_id", "year_roc", "exam_name_raw", "category_raw",
            "subject_name_raw", "paper_code", "file_type", "download_url_source", "storage_key", "checksum",
        ):
            assert getattr(new, field) == getattr(old, field), field
        if old.canonical_id and old.canonical_name:
            assert new.canonical_id == old.canonical_id
            assert new.canonical_name == old.canonical_name
    review_keys = [(row.provider_id, row.source_exam_id, row.raw_category) for row in first.review_queue]
    assert len(review_keys) == len(set(review_keys))
    assert all(row.classification_signature and row.bundle_id and row.reason for row in first.review_queue)
