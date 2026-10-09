"""TQC grade evidence must not merge distinct subjects or content versions."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.classification import classify_paper, classify_normalized_paper, identity_fields
from app.models import NormalizedCatalog, NormalizedPaper, ParsedPaper, to_plain_data
from app.normalizer import normalize_papers, renormalize_catalog
from app.source_material import SourceDate, SourceMaterial
from app.tqc_identity_evidence import (
    read_tqc_identities,
    tqc_evidence_path,
    tqc_identity_catalog,
    validate_tqc_identity_evidence,
)

ROOT = Path(__file__).resolve().parents[1]


def classify(title: str, *, checksum: str | None = None, event: str = "tqc-cert-samples-2026"):
    fact = next(f for f in tqc_identity_catalog().facts if f.title == title)
    return classify_paper(
        provider_id="tqc_cert",
        source_exam_id=event,
        year_ad=2026,
        category_raw="TQC範例試卷_辦公軟體應用類",
        exam_name_raw="TQC官方範例試卷",
        canonical_id="tqc-cert",
        canonical_name="TQC電腦技能基金會認證",
        subject_name_raw=title,
        subject_code="same-unreliable-native-code",
        source_material=SourceMaterial(
            "sample",
            SourceDate("publication_year", 2026),
            "https://www.tqc.org.tw/TQCNet/ExamPaper.aspx",
            title,
        ),
        source_checksum=fact.checksum if checksum is None else checksum,
    )


@pytest.mark.parametrize(
    "title,track,level,variant",
    [
        ("Word 2016", "tqc-word", "tqc-professional", "software-version-2016"),
        ("Excel 2016", "tqc-excel", "tqc-professional", "software-version-2016"),
        (
            "商務軟體應用能力Microsoft Office 2016",
            "tqc-microsoft-office",
            "tqc-professional",
            "software-version-2016",
        ),
        ("AI數據分析Excel", "tqc-ai-excel", "tqc-professional", None),
        ("電子商務與AI應用", "tqc-ecommerce-ai", "tqc-professional", None),
        ("MySQL 5", "tqc-mysql", "tqc-professional", "software-version-5"),
        ("日文輸入(第三版)", "tqc-japanese-input", "tqc-input-performance-grades", "edition-3"),
        ("中文輸入(2016年版)", "tqc-chinese-input", "tqc-input-performance-grades", "edition-2016"),
        ("數字輸入(2023年版)", "tqc-numeric-input", "tqc-input-performance-grades", "edition-2023"),
        ("Linux網路管理(第二版)", "tqc-linux-network", "tqc-professional", "edition-2"),
        (
            "Linux系統管理 Ubuntu 22.04 LTS",
            "tqc-linux-system",
            "tqc-professional",
            "software-ubuntu-22-04-lts",
        ),
    ],
)
def test_native_subject_grade_and_content_version_are_separate(title, track, level, variant):
    identity = classify(title)
    assert identity.domain_id == "certification"
    assert identity.exam_family_id == "skill-certification"
    assert identity.exam_series_id == "tqc-skills-certification"
    assert identity.track_id == track
    assert identity.level_id == level
    assert identity.confidence == "high"
    assert identity.stage_id == "not-applicable"
    assert identity.variant_ids == (
        (variant, "material-sample") if variant else ("material-sample",)
    )
    assert "企業人才技能認證" in identity.bundle_name
    assert identity.track_label in identity.bundle_name
    if level == "tqc-input-performance-grades":
        assert identity.level_label == "實用級／進階級／專業級"


def test_equal_native_code_or_version_does_not_merge_subjects_and_editions():
    titles = [
        "Word 2016",
        "Word 2019",
        "Excel 2016",
        "商務軟體應用能力Microsoft Office 2016",
        "中文輸入(2016年版)",
        "中文輸入(2021年版)",
        "英文輸入(2016年版)",
        "Linux網路管理(第二版)",
        "Linux系統管理(第二版)",
    ]
    identities = [classify(title) for title in titles]
    assert len({identity.bundle_id for identity in identities}) == len(titles)
    later = classify("Word 2016", event="tqc-cert-samples-2027")
    assert later.bundle_id == identities[0].bundle_id


def test_changed_pdf_or_missing_checksum_cannot_reuse_a_reviewed_grade():
    for checksum in ["", "0" * 64, "1" * 64]:
        identity = classify("Word 2016", checksum=checksum)
        assert identity.level_id == "unknown"
        assert identity.confidence == "review"
        assert "event-tqc-cert-samples-2026" in identity.bundle_id
    assert (
        classify("Word 2016", checksum="0" * 64).bundle_id
        != classify("Word 2016", checksum="1" * 64).bundle_id
    )


def test_normalization_and_read_projection_use_the_same_checksum_evidence():
    fact = next(f for f in tqc_identity_catalog().facts if f.title == "Word 2016")
    material = SourceMaterial(
        "sample", SourceDate("publication_year", 2016), fact.source_url, fact.title
    )
    parsed = ParsedPaper(
        "TQC範例試卷_辦公軟體應用類",
        "office",
        "2016",
        fact.title,
        {"question": fact.source_url},
        {"question": {"checksum": fact.checksum, "storage_key": "providers/tqc_cert/word.pdf"}},
        material,
    )
    normalized = normalize_papers(
        "tqc-cert-samples-2016",
        2016,
        "TQC官方範例試卷",
        [parsed],
        [],
        "",
        {},
        provider_id="tqc_cert",
    )
    paper = normalized.papers[0]
    assert paper.level_id == "tqc-professional"
    assert not normalized.review_queue
    assert identity_fields(classify_normalized_paper(paper))["bundle_id"] == paper.bundle_id
    assert renormalize_catalog(normalized, []).papers == normalized.papers
    changed = replace(paper, checksum="0" * 64)
    reviewed = renormalize_catalog(NormalizedCatalog([changed], []), [])
    assert reviewed.papers[0].level_id == "unknown" and reviewed.review_queue
    for kind in ["administered", "reference"]:
        other_material = SourceMaterial(
            kind, SourceDate("publication_year", 2016), fact.source_url, fact.title
        )
        assert (
            classify_normalized_paper(replace(paper, source_material=other_material)).level_id
            == "unknown"
        )


@pytest.mark.parametrize(
    "defect",
    [
        "duplicate_context",
        "duplicate_id",
        "checksum",
        "provider",
        "subject",
        "level",
        "external_source",
        "variant",
    ],
)
def test_conflicting_or_unanchored_catalog_facts_are_rejected(tmp_path, defect):
    document = deepcopy(json.loads(tqc_evidence_path(ROOT).read_text()))
    first = document["facts"][0]
    if defect == "duplicate_context":
        duplicate = deepcopy(first)
        duplicate["id"] = "another-id"
        duplicate["title"] += " "
        document["facts"].append(duplicate)
    elif defect == "duplicate_id":
        document["facts"][1]["id"] = first["id"]
    elif defect == "checksum":
        first["checksum"] = "invalid"
    elif defect == "provider":
        document["provider_id"] = "wdasec_skill"
    elif defect == "subject":
        first["track_id"] = "missing-subject"
    elif defect == "level":
        first["level_id"] = "grade-3"
    elif defect == "external_source":
        first["source_url"] = "https://example.com/user/Example/fake.pdf"
    elif defect == "variant":
        first["variants"].append(deepcopy(first["variants"][0]))
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        read_tqc_identities(path)


def test_catalog_contract_and_all_distinct_sample_identities():
    schema = json.loads((ROOT / "schemas/tqc-sample-identity-v1.schema.json").read_text())
    Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(
        json.loads(tqc_evidence_path(ROOT).read_text())
    )
    catalog = tqc_identity_catalog()
    assert len(catalog.facts) == 44
    identities = [classify(fact.title) for fact in catalog.facts]
    assert len({identity.bundle_id for identity in identities}) == 44
    assert len({identity.track_id for identity in identities}) == 25
    assert sum(identity.level_id == "tqc-professional" for identity in identities) == 30
    assert sum(identity.level_id == "tqc-input-performance-grades" for identity in identities) == 14


def test_native_evidence_gate_checks_original_bytes_when_requested(tmp_path):
    import hashlib

    document = deepcopy(json.loads(tqc_evidence_path(ROOT).read_text()))
    document["facts"] = [document["facts"][0]]
    fact = document["facts"][0]
    payload = b"%PDF-1.7 native sample evidence fixture\n"
    fact["checksum"] = hashlib.sha256(payload).hexdigest()
    evidence = tqc_evidence_path(tmp_path)
    evidence.parent.mkdir(parents=True)
    evidence.write_text(json.dumps(document))
    key = "providers/tqc_cert/fixture/question.pdf"
    paper = NormalizedPaper(
        "tqc-cert",
        "TQC",
        104,
        "TQC官方範例試卷",
        "TQC範例試卷",
        fact["title"],
        "sample-question",
        "question",
        fact["source_url"],
        storage_key=key,
        checksum=fact["checksum"],
        provider_id="tqc_cert",
    )
    papers = tmp_path / "data/providers/tqc_cert/papers/2015.json"
    papers.parent.mkdir(parents=True)
    papers.write_text(json.dumps([to_plain_data(paper)]))
    original = tmp_path / "mirror" / key
    original.parent.mkdir(parents=True)
    original.write_bytes(payload)
    assert validate_tqc_identity_evidence(tmp_path, verify_mirror=True) == 1
    original.write_bytes(b"changed native bytes")
    assert validate_tqc_identity_evidence(tmp_path) == 1
    with pytest.raises(ValueError, match="checksum differs"):
        validate_tqc_identity_evidence(tmp_path, verify_mirror=True)
    original.unlink()
    with pytest.raises(ValueError, match="mirror is missing"):
        validate_tqc_identity_evidence(tmp_path, verify_mirror=True)


@pytest.mark.repo_data
def test_all_tqc_native_evidence_matches_retained_provider_anchors():
    assert validate_tqc_identity_evidence(ROOT) == 44
