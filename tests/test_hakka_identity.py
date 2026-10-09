"""Native Hakka boundaries and upstream-conflict isolation."""

from dataclasses import replace
import json
import hashlib
from pathlib import Path

import pytest

from app.classification import classify_paper
from app.hakka_identity import (
    hakka_conflict_reason,
    hakka_dialect,
    hakka_level,
    hakka_material,
    validate_hakka_conflicts,
    validate_hakka_historical_grades,
)
from app.models import NormalizedCatalog, ParsedPaper
from app.normalizer import normalize_papers, renormalize_catalog

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "label,kind,year",
    [
        ("107 年度客語能力認證初級題庫（四縣腔）PDF 下載", "question_bank", 2018),
        ("111 年度客語能力認證初級題庫音檔（海陸腔）下載", "question_bank", 2022),
        ("107 年度客語能力認證初級題庫及樣卷（四縣腔）下載", "practice_collection", 2018),
        ("107 年度客語能力認證中級暨中高級樣卷（大埔腔）下載", "sample", 2018),
        ("高級-閱讀測驗試題範例-詔安", "sample", None),
        ("中級暨中高級語料選粹(四縣腔)", "reference", None),
        ("客語能力各級通過標準與題型說明", "reference", None),
    ],
)
def test_source_resources_are_not_administered_sittings(label, kind, year):
    material = hakka_material(label, "hakka-cert-basic-elementary-2026")
    assert material.kind == kind
    assert material.date.year_ad == year
    assert material.date.basis == ("edition_year" if year else "undated")
    assert material.evidence_label == label


def test_new_generic_question_or_answer_label_requires_material_and_date_review():
    label = "115 年度客語能力認證高級試題答案（海陸腔）"
    material = hakka_material(label, "hakka-cert-advanced-2026")
    assert material.kind == "unknown"
    assert material.date.year_ad is None
    assert material.date.basis == "unknown"
    assert material.needs_review
    identity = _identity(label, "hailu")
    assert identity.confidence == "review"
    assert "event-" in identity.bundle_id


@pytest.mark.parametrize(
    "label,level",
    [
        ("107 年度初級題庫(四縣腔)", "hakka-elementary"),
        ("112 年度基礎級暨初級題庫(四縣腔)", "basic-elementary"),
        ("中級暨中高級樣卷(四縣腔)", "intermediate-high-intermediate"),
        ("高級-閱讀測驗試題範例-四縣", "advanced"),
        ("初級 高級題庫", "unknown"),
        ("中高級題庫", "unknown"),
        ("專業級試題", "unknown"),
        ("未標級別試題", "unknown"),
        ("114 年度初級題庫(四縣腔)", "unknown"),
    ],
)
def test_download_grade_does_not_inherit_navigation_group(label, level):
    assert hakka_level(label, hakka_material(label, "hakka-cert-basic-elementary-2026"))[0] == level


@pytest.mark.parametrize(
    "label,code",
    [
        ("南四縣初級題庫", "sixian"),
        ("海陸初級題庫", "sixian"),
        ("海陸四縣初級題庫", "hailu"),
        ("初級題庫", "general"),
    ],
)
def test_unsupported_or_conflicting_dialect_stays_unresolved(label, code):
    assert hakka_dialect(label, code) is None


def _identity(label, code="sixian", checksum="", subject_code=""):
    return classify_paper(
        provider_id="hakka_cert",
        source_exam_id="hakka-cert-basic-elementary-2018",
        year_ad=2018,
        category_raw=f"客語能力認證官方教材及試題_基礎級暨初級_{code}",
        exam_name_raw="客語能力認證官方教材及試題 基礎級暨初級",
        canonical_id="hakka-cert-basic-elementary",
        canonical_name="客語能力認證 基礎級暨初級",
        subject_name_raw=label,
        category_code=code,
        source_material=hakka_material(label, "hakka-cert-basic-elementary-2018"),
        source_checksum=checksum,
        subject_code=subject_code,
    )


def test_old_elementary_banks_and_shared_format_have_distinct_identity_and_titles():
    old = _identity("107 年度初級題庫（四縣腔）PDF 下載")
    shared = _identity("112 年度基礎級暨初級題庫（四縣腔）PDF 下載")
    assert old.level_id == "hakka-elementary"
    assert old.bundle_id != shared.bundle_id
    assert old.bundle_name == "客語能力認證｜初級｜四縣腔｜題庫"
    assert shared.bundle_name == "客語能力認證｜基礎級暨初級｜四縣腔｜題庫"


def test_dialects_and_mixed_collection_change_content_identity():
    sixian = _identity("107 年度初級題庫（四縣腔）PDF 下載")
    hailu = _identity("107 年度初級題庫（海陸腔）PDF 下載", "hailu")
    mixed = _identity("107 年度初級題庫及樣卷（四縣腔）下載")
    assert len({sixian.bundle_id, hailu.bundle_id, mixed.bundle_id}) == 3
    assert "dialect-hailu" in hailu.variant_ids
    assert "練習問題集" in mixed.bundle_name


@pytest.mark.parametrize(
    "checksum", ["e86ee4647786580c464a021a9c020a6719837e6cc5148d8c9dda5b80d2aafd1a", "a" * 64, ""]
)
def test_known_source_conflict_remains_isolated_until_rechecked(checksum):
    label = "108 年度客語能力認證初級題庫 ( 饒平腔 ) EXCEL 下載"
    identity = _identity(label, "raoping", checksum)
    assert identity.confidence == "review"
    assert "dialect-unknown" in identity.variant_ids
    assert "event-" in identity.bundle_id
    assert hakka_conflict_reason(label, checksum)
    assert not hakka_conflict_reason(label.replace("饒平", "大埔"), checksum)


def test_identical_official_label_does_not_quarantine_the_correct_distinct_package():
    label = "111 年度客語能力認證初級題庫音檔 ( 詔安腔 ) 下載"
    checksum = "7a4d5cf876d6b4f9a9161867b19181a1e41c15fc6ca8eee6a4782bb660120ec3"
    assert not hakka_conflict_reason(label, checksum, "310-111")
    assert "unreviewed bytes" in hakka_conflict_reason(label, checksum, "307-111")


def test_audio_conflict_uses_its_own_payload_metadata_and_renormalization_is_stable():
    label = "111 年度客語能力認證初級題庫音檔 ( 詔安腔 ) 下載"
    checksum = "c477d1117cd3a33be27b10979d21e24e08eb5b6e2732ec7eb23172090d1b5b53"
    paper = ParsedPaper(
        category_raw="客語能力認證官方教材及試題_基礎級暨初級_zhaoan",
        category_code="zhaoan",
        subject_code="307-111",
        subject_name_raw=label,
        files={"listening_audio": "https://elearning.hakka.gov.tw/hakka/files/downloads/307.zip"},
    )
    catalog = normalize_papers(
        "hakka-cert-basic-elementary-2022",
        2022,
        "客語能力認證官方教材及試題 基礎級暨初級",
        [paper],
        [],
        "",
        {("zhaoan", "307-111", "listening_audio"): {"checksum": checksum}},
        provider_id="hakka_cert",
    )
    result = catalog.papers[0]
    assert result.classification_confidence == "review"
    assert "78 WAV" in result.classification_reason
    assert "unreviewed bytes" not in result.classification_reason
    # Current catalogue migration has the persisted per-file checksum.
    result = replace(result, checksum=checksum)
    again = renormalize_catalog(NormalizedCatalog([result], []), [])
    assert again.papers[0].bundle_id == result.bundle_id
    assert again.papers[0].source_material == result.source_material
    assert again.papers[0].file_type == "listening_audio"


def test_legacy_bank_migration_upgrades_material_contract_before_constructing_record():
    label = "107 年度客語能力認證初級題庫 ( 四縣腔 ) PDF 下載"
    parsed = ParsedPaper(
        category_raw="客語能力認證官方教材及試題_基礎級暨初級_sixian",
        category_code="sixian",
        subject_code="114-107-pdf",
        subject_name_raw=label,
        files={"question": "https://elearning.hakka.gov.tw/hakka/files/downloads/114.pdf"},
    )
    normalized = normalize_papers(
        "hakka-cert-basic-elementary-2018",
        2018,
        "客語能力認證官方教材及試題 基礎級暨初級",
        [parsed],
        [],
        "",
        {},
        provider_id="hakka_cert",
    ).papers[0]
    legacy = replace(normalized, schema_version=2, source_material=None)
    migrated = renormalize_catalog(NormalizedCatalog([legacy], []), []).papers[0]
    assert migrated.schema_version == 3
    assert migrated.source_material.kind == "question_bank"
    assert migrated.source_material.date.year_ad == 2018
    assert migrated.level_id == "hakka-elementary"
    assert migrated.storage_key == legacy.storage_key
    assert migrated.download_url_source == legacy.download_url_source


@pytest.mark.repo_data
def test_catalog_conflicts_reconcile_with_both_retained_source_references():
    assert validate_hakka_conflicts(ROOT) == 2
    current = [
        r
        for p in (ROOT / "data/providers/hakka_cert/papers").glob("*.json")
        for r in json.loads(p.read_text())
    ]
    assert len(current) == 160
    assert all(r.get("source_material") for r in current)
    assert sum(r["classification_confidence"] == "review" for r in current) == 2
    assert {r["level_id"] for r in current} == {
        "hakka-elementary",
        "basic-elementary",
        "intermediate-high-intermediate",
        "advanced",
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("checksum", "a" * 64),
        ("subject_code", "changed"),
        ("download_url_source", "https://elearning.hakka.gov.tw/hakka/files/downloads/999.ods"),
    ],
)
def test_conflict_gate_rejects_a_missing_or_changed_retained_anchor(tmp_path, field, value):
    document = json.loads((ROOT / "catalog/mappings/hakka/native-conflicts-v1.json").read_text())
    owner = tmp_path / "catalog/mappings/hakka/native-conflicts-v1.json"
    owner.parent.mkdir(parents=True)
    owner.write_text(json.dumps(document))
    urls = {r[key] for r in document["facts"] for key in ("source_url", "matching_source_url")}
    records = [
        r
        for p in (ROOT / "data/providers/hakka_cert/papers").glob("*.json")
        for r in json.loads(p.read_text())
        if r["download_url_source"] in urls
    ]
    changed = next(r for r in records if r["download_url_source"].endswith("109.ods"))
    changed[field] = value
    folder = tmp_path / "data/providers/hakka_cert/papers"
    folder.mkdir(parents=True)
    for year in {r["year_roc"] for r in records}:
        (folder / f"{year + 1911}.json").write_text(
            json.dumps([r for r in records if r["year_roc"] == year])
        )
    with pytest.raises(ValueError, match="anchor is missing or changed"):
        validate_hakka_conflicts(tmp_path)


def _historical_grade_facts():
    return json.loads((ROOT / "catalog/mappings/hakka/historical-grades-v1.json").read_text())[
        "facts"
    ]


@pytest.mark.parametrize("fact", _historical_grade_facts(), ids=lambda fact: fact["id"])
def test_reviewed_historical_grade_requires_exact_label_checksum_and_source_key(fact):
    identity = _identity(
        fact["title"], fact["dialect_code"], fact["checksum"], fact["subject_code"]
    )
    assert identity.level_id == "basic-elementary"
    assert identity.confidence == "high"
    assert fact["id"] in identity.reason
    changed_bytes = _identity(fact["title"], fact["dialect_code"], "a" * 64, fact["subject_code"])
    changed_key = _identity(fact["title"], fact["dialect_code"], fact["checksum"], "unreviewed")
    for changed in (changed_bytes, changed_key):
        assert changed.level_id == "unknown"
        assert changed.confidence == "review"
        assert "event-" in changed.bundle_id


@pytest.mark.repo_data
def test_historical_grade_projection_conserves_current_state_and_immutable_history():
    assert validate_hakka_historical_grades(ROOT) == 10
    journal_path = ROOT / "data/providers/hakka_cert/source-revisions.json"
    original = journal_path.read_bytes()
    revisions = json.loads(original)["revisions"]
    from app.models import NormalizedPaper, to_plain_data

    before = [NormalizedPaper(**entry["source_record"]) for entry in revisions]
    projected = renormalize_catalog(NormalizedCatalog(before, []), []).papers
    facts = {row["subject_code"]: row for row in _historical_grade_facts()}
    assert len(projected) == 56
    for old, new in zip(before, projected, strict=True):
        for field in (
            "checksum",
            "storage_key",
            "download_url_source",
            "file_type",
            "year_roc",
            "source_exam_id",
        ):
            assert getattr(old, field) == getattr(new, field)
        if new.subject_code in facts:
            assert new.level_id == "basic-elementary"
            assert new.classification_confidence == "high"
    assert sum(p.classification_confidence == "review" for p in projected) == 1
    current = [
        NormalizedPaper(**r)
        for p in (ROOT / "data/providers/hakka_cert/papers").glob("*.json")
        for r in json.loads(p.read_text())
    ]
    again = renormalize_catalog(NormalizedCatalog(current, []), []).papers
    assert [to_plain_data(p) for p in again] == [to_plain_data(p) for p in current]
    assert journal_path.read_bytes() == original


@pytest.mark.parametrize("anchor", ["historical", "current"])
@pytest.mark.parametrize(
    "field,value",
    [("checksum", "a" * 64), ("subject_code", "changed"), ("category_code", "changed")],
)
def test_historical_grade_gate_rejects_tampered_source_context(tmp_path, anchor, field, value):
    document = json.loads((ROOT / "catalog/mappings/hakka/historical-grades-v1.json").read_text())
    owner = tmp_path / "catalog/mappings/hakka/historical-grades-v1.json"
    owner.parent.mkdir(parents=True)
    folder = tmp_path / "data/providers/hakka_cert"
    (folder / "papers").mkdir(parents=True)
    journal = json.loads((ROOT / "data/providers/hakka_cert/source-revisions.json").read_text())
    records = [
        r
        for p in (ROOT / "data/providers/hakka_cert/papers").glob("*.json")
        for r in json.loads(p.read_text())
    ]
    fact = document["facts"][0]
    if anchor == "historical":
        # Keep the journal valid and immutable: a false catalog claim must also
        # be rejected when no retained source reference supports it.
        fact[{"category_code": "dialect_code"}.get(field, field)] = value
    else:
        changed = next(
            r for r in records if r["download_url_source"] == fact["matching_source_url"]
        )
        changed[field] = value
    owner.write_text(json.dumps(document))
    (folder / "source-revisions.json").write_text(json.dumps(journal))
    for year in {r["year_roc"] for r in records}:
        (folder / "papers" / f"{year + 1911}.json").write_text(
            json.dumps([r for r in records if r["year_roc"] == year])
        )
    with pytest.raises(ValueError, match="historical grade anchor is missing or changed"):
        validate_hakka_historical_grades(tmp_path)


def test_reviewed_counterpart_can_retire_into_immutable_history(tmp_path):
    from app.models import NormalizedPaper
    from app.paths import provider_paths
    from app.source_revisions import retain_superseded_sources

    document = json.loads((ROOT / "catalog/mappings/hakka/historical-grades-v1.json").read_text())
    fact = document["facts"][0]
    journal = json.loads((ROOT / "data/providers/hakka_cert/source-revisions.json").read_text())
    old = next(
        entry["source_record"]
        for entry in journal["revisions"]
        if entry["source_record"]["subject_code"] == fact["subject_code"]
    )
    current = next(
        r
        for p in (ROOT / "data/providers/hakka_cert/papers").glob("*.json")
        for r in json.loads(p.read_text())
        if r["subject_code"] == fact["matching_subject_code"]
    )
    payload = b"retained source anchor fixture"
    checksum = hashlib.sha256(payload).hexdigest()
    fact["checksum"] = fact["matching_checksum"] = checksum
    document["facts"] = [fact]
    owner = tmp_path / "catalog/mappings/hakka/historical-grades-v1.json"
    owner.parent.mkdir(parents=True)
    owner.write_text(json.dumps(document))
    provider = provider_paths(tmp_path, "hakka_cert")
    for record in (old, current):
        paper = replace(NormalizedPaper(**record), checksum=checksum)
        path = tmp_path / "mirror" / paper.storage_key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        assert retain_superseded_sources(provider, [], [paper], [], []) == 1
    assert validate_hakka_historical_grades(tmp_path, verify_mirror=True) == 1
