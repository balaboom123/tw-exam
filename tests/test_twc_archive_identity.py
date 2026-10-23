"""Programme years must not become sitting years or erase selection stages."""

import hashlib
import io
import json
import zipfile
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from app.bundler import _bundle_arcname, build_bundles
from app.cli import main
from app.models import NormalizedCatalog, ParsedPaper, SourceExamPage, SyncFailure
from app.normalizer import normalize_papers, renormalize_catalog
from app.paths import provider_paths
from app.providers.twc_recruit.identity import (
    archive_identities, archive_identity_path, archive_material, read_archive_identities,
    resolve_archive_identity, validate_archive_identities,
)
from app.publisher import write_provider_state
from app.source_material import source_year_roc
from app.source_revisions import load_source_revisions
from app.state import load_provider_state
from app.storage import MirrorStore


ROOT = Path(__file__).resolve().parents[1]


def page(fact):
    year = fact.year_roc
    return SourceExamPage(
        f"twc-recruit-{year}", year + 1911, year, f"{year}年試題(解答).zip", [],
        [ParsedPaper(
            "台水評價職位人員甄試", str(year), f"twc-recruit-{year}-bundle",
            f"{year}年試題(解答).zip", {"question_answer": fact.source_url},
            mirror_files={"question_answer": {"checksum": fact.checksum}},
        )], provider_id="twc_recruit",
    )


def normalize(source):
    return normalize_papers(
        source.source_exam_id, source.year_ad, source.exam_name_raw,
        source.papers, [], "", {}, provider_id="twc_recruit",
    )


@pytest.mark.parametrize("programme_year,sitting_year", [(107, 108), (111, 112), (112, 113)])
def test_cross_year_dates_drive_year_folders_without_renaming_source_keys(programme_year, sitting_year):
    fact = next(row for row in archive_identities() if row.year_roc == programme_year)
    catalog = normalize(page(fact))
    paper = catalog.papers[0]
    assert paper.year_roc == programme_year
    assert paper.source_exam_id == f"twc-recruit-{programme_year}"
    assert paper.schema_version == 3
    assert paper.file_type == "question_answer"
    assert source_year_roc(paper) == sitting_year
    assert _bundle_arcname(paper).startswith(f"{sitting_year}/")
    assert paper.stage_id == "stage-1"
    assert "初試（筆試）" in paper.bundle_name
    assert paper.level_id == "not-applicable"
    assert paper.classification_confidence == "review"
    assert "occupational applicability" in paper.classification_reason
    assert renormalize_catalog(catalog, []).papers == catalog.papers


def test_unreviewed_annual_rules_and_changed_bytes_cannot_inherit_label_years():
    fact = next(row for row in archive_identities() if row.year_roc == 105)
    unresolved = normalize(page(fact))
    assert unresolved.papers[0].source_material.date.basis == "unknown"
    assert source_year_roc(unresolved.papers[0]) is None
    assert unresolved.papers[0].stage_id == "unknown"
    assert unresolved.review_queue
    reviewed = next(row for row in archive_identities() if row.year_roc == 111)
    changed = normalize(page(replace(reviewed, checksum="a" * 64)))
    assert changed.papers[0].stage_id == "unknown"
    assert source_year_roc(changed.papers[0]) is None
    assert changed.papers[0].bundle_id != normalize(page(reviewed)).papers[0].bundle_id


def test_material_resolver_does_not_override_other_source_contexts():
    fact = archive_identities()[0]
    context = dict(source_exam_id=f"twc-recruit-{fact.year_roc}", year_roc=fact.year_roc,
                   category_code=str(fact.year_roc), subject_code=f"twc-recruit-{fact.year_roc}-bundle",
                   label=f"{fact.year_roc}年試題(解答).zip", source_url=fact.source_url, checksum=fact.checksum)
    for changed in (
        {"source_exam_id": "another-programme"}, {"subject_code": "native-child"},
        {"category_code": "another-category"}, {"source_url": "https://example.test/archive.zip"},
        {"label": "無障礙試題(解答).zip"}, {"label": "其他資料.zip"},
    ):
        assert archive_material(**(context | changed)) is None


def test_evidence_loader_rejects_duplicate_context_and_silent_unknown_date(tmp_path):
    original = json.loads(archive_identity_path(ROOT).read_text())
    candidate = tmp_path / "facts.json"
    original["facts"].append(original["facts"][0])
    candidate.write_text(json.dumps(original))
    with pytest.raises(ValueError, match="Duplicate"):
        read_archive_identities(candidate)
    original["facts"].pop()
    original["facts"][0].update(written_test_date=None, review_reason="")
    candidate.write_text(json.dumps(original))
    with pytest.raises(ValueError, match="review reason"):
        read_archive_identities(candidate)


def test_unknown_material_prevents_archive_creation(tmp_path):
    fact = next(row for row in archive_identities() if row.year_roc == 103)
    with pytest.raises(ValueError, match="reviewed evidence"):
        build_bundles(mirror_dir=tmp_path / "mirror", bundle_dir=tmp_path / "bundles",
                      normalized=normalize(page(fact)), min_years=1, bundle_base_url="")
    assert not (tmp_path / "bundles").exists()


def test_reviewed_revisions_and_relocated_copies_keep_their_exact_evidence(tmp_path, monkeypatch):
    document = json.loads(archive_identity_path(ROOT).read_text())
    original = next(row for row in document["facts"] if row["programme_year_roc"] == 111)
    replacement = dict(original, checksum="a" * 64, evidence_label="Reviewed corrected archive")
    relocated = dict(
        original,
        source_url="https://www.water.gov.tw/ch/ServerFile/Get/11111111-1111-1111-1111-111111111111?nodeId=715",
        evidence_url="https://www.water.gov.tw/relocation-evidence",
        evidence_label="Reviewed relocated original",
    )
    document["facts"] = [original, replacement, relocated]
    candidate = tmp_path / "facts.json"
    candidate.write_text(json.dumps(document))
    facts = read_archive_identities(candidate)
    monkeypatch.setattr("app.providers.twc_recruit.identity.archive_identities", lambda: facts)
    assert len(facts) == 3
    for fact in facts:
        context = dict(
            source_exam_id="twc-recruit-111", year_roc=111, category_code="111",
            subject_code="twc-recruit-111-bundle", label="111年試題(解答).zip",
            source_url=fact.source_url, checksum=fact.checksum,
        )
        assert archive_material(**context) == fact.material
        assert resolve_archive_identity(111, fact.checksum, source_url=fact.source_url) == fact
        assert normalize(page(fact)).papers[0].stage_id == "stage-1"
        assert source_year_roc(normalize(page(fact)).papers[0]) == 112
    assert resolve_archive_identity(111, facts[0].checksum) == facts[0]
    assert resolve_archive_identity(111, "b" * 64) is None
    wrong_locator = dict(context, source_url=facts[1].source_url, checksum="b" * 64)
    assert archive_material(**wrong_locator).date.basis == "unknown"
    # A reviewed payload at another URL must not donate its material evidence.
    unreviewed_locator = dict(context, source_url=facts[2].source_url, checksum=facts[1].checksum)
    assert archive_material(**unreviewed_locator).date.basis == "unknown"


@pytest.mark.parametrize("conflicting_date", [None, "2023-03-12"])
def test_same_payload_cannot_have_conflicting_dates_after_relocation(tmp_path, conflicting_date):
    document = json.loads(archive_identity_path(ROOT).read_text())
    original = next(row for row in document["facts"] if row["programme_year_roc"] == 111)
    relocated = dict(
        original,
        source_url="https://www.water.gov.tw/ch/ServerFile/Get/11111111-1111-1111-1111-111111111111?nodeId=715",
        written_test_date=conflicting_date,
        review_reason="Date requires review" if conflicting_date is None else "",
    )
    document["facts"] = [original, relocated]
    candidate = tmp_path / "facts.json"
    candidate.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="Conflicting.*dates"):
        read_archive_identities(candidate)


def test_unreviewed_replacement_does_not_inherit_an_older_payload_date(tmp_path, monkeypatch):
    document = json.loads(archive_identity_path(ROOT).read_text())
    original = next(row for row in document["facts"] if row["programme_year_roc"] == 111)
    replacement = dict(original, checksum="a" * 64, written_test_date=None,
                       review_reason="Replacement bytes await calendar and native review")
    document["facts"] = [original, replacement]
    candidate = tmp_path / "facts.json"
    candidate.write_text(json.dumps(document))
    facts = read_archive_identities(candidate)
    monkeypatch.setattr("app.providers.twc_recruit.identity.archive_identities", lambda: facts)
    old, new = [normalize(page(fact)).papers[0] for fact in facts]
    assert source_year_roc(old) == 112 and old.stage_id == "stage-1"
    assert source_year_roc(new) is None and new.stage_id == "unknown"
    assert old.bundle_id != new.bundle_id


def test_reviewed_revision_keeps_previous_source_blob_and_date_facts(tmp_path, monkeypatch):
    provider = provider_paths(tmp_path, "twc_recruit")
    original = next(row for row in archive_identities() if row.year_roc == 111)
    facts, sources, payloads = [], [], []
    key = "providers/twc_recruit/111/original.zip"
    path = tmp_path / "mirror" / key
    path.parent.mkdir(parents=True)
    store = MirrorStore(tmp_path / "mirror")
    for version in (b"original questions", b"corrected questions and answers"):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("native.pdf", b"%PDF-1.7 fixture " + version)
        data = stream.getvalue()
        fact = replace(original, checksum=hashlib.sha256(data).hexdigest())
        source = page(fact)
        source.papers[0].mirror_files["question_answer"]["storage_key"] = key
        facts.append(fact)
        sources.append(source)
        payloads.append(data)
    monkeypatch.setattr("app.providers.twc_recruit.identity.archive_identities", lambda: tuple(facts))
    # Save the first verified source, then update the same native locator.
    for fact, source, data in zip(facts, sources, payloads, strict=True):
        store.write_bytes(key, data, overwrite=True)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == fact.checksum
        write_provider_state(provider, raw_pages=[source], normalized=normalize(source),
                             aliases=[], failures=[], manifest=None)
    journal = load_source_revisions(provider)
    assert len(journal) == 1
    prior = journal[0]
    assert prior["source_record"]["checksum"] == facts[0].checksum
    previous = tmp_path / "mirror" / prior["blob_storage_key"]
    assert hashlib.sha256(previous.read_bytes()).hexdigest() == facts[0].checksum
    assert hashlib.sha256(path.read_bytes()).hexdigest() == facts[1].checksum
    candidate = archive_identity_path(tmp_path)
    candidate.parent.mkdir(parents=True)
    document = json.loads(archive_identity_path(ROOT).read_text())
    row = next(row for row in document["facts"] if row["programme_year_roc"] == 111)
    document["facts"] = [dict(row, checksum=fact.checksum) for fact in facts]
    candidate.write_text(json.dumps(document))
    assert validate_archive_identities(tmp_path, verify_mirror=True) == 2
    for fact in read_archive_identities(candidate):
        paper = normalize(page(fact)).papers[0]
        assert source_year_roc(paper) == 112 and paper.stage_id == "stage-1"
    # Every approved URL/version still needs its own retained history anchor.
    document["facts"].append(dict(row, checksum="b" * 64))
    candidate.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="lacks retained anchor"):
        validate_archive_identities(tmp_path, verify_mirror=True)


def test_complete_cli_migration_preserves_originals_failures_and_previous_identity(tmp_path, monkeypatch):
    provider = provider_paths(tmp_path, "twc_recruit")
    facts, sources, papers = [], [], []
    for original in archive_identities():
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("native.pdf", b"%PDF-1.7 fixture " + str(original.year_roc).encode())
        data = stream.getvalue()
        fact = replace(original, checksum=hashlib.sha256(data).hexdigest())
        facts.append(fact)
        source = page(fact)
        key = f"providers/twc_recruit/{fact.year_roc}/original.zip"
        path = tmp_path / "mirror" / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        source.papers[0].mirror_files["question_answer"]["storage_key"] = key
        sources.append(source)
    monkeypatch.setattr("app.providers.twc_recruit.identity.archive_identities", lambda: tuple(facts))
    for source in sources:
        current = normalize(source).papers[0]
        papers.append(replace(current, source_material=None, schema_version=2,
                              stage_id="not-applicable", bundle_id="previous-archive-identity",
                              classification_confidence="medium"))
    failure = SyncFailure(
        stage="download", source_exam_id="twc-recruit-107", year_roc=107,
        paper_code="107-twc-recruit-107-bundle-question_answer", file_type="question_answer",
        url=next(fact.source_url for fact in facts if fact.year_roc == 107),
        message="retained corrupt source",
    )
    legacy_roles = [replace(paper, file_type="accessible_bundle",
                            paper_code=paper.paper_code.replace("question_answer", "accessible_bundle"))
                    for paper in papers]
    write_provider_state(provider, raw_pages=sources, normalized=NormalizedCatalog(legacy_roles, []),
                         aliases=[], failures=[failure], manifest=None)
    write_provider_state(provider, raw_pages=sources, normalized=NormalizedCatalog(papers, []),
                         aliases=[], failures=[failure], manifest=None)
    previous_journal = load_source_revisions(provider)
    receipts = provider.data_dir / "sync-status.json"
    receipts.write_text('{"retained_marker": "no invented success"}\n')
    assert main(["migrate-catalog", "--repo-root", str(tmp_path), "--provider", "twc_recruit"]) == 0
    _, migrated, failures = load_provider_state(provider)
    assert len(migrated.papers) == len(migrated.review_queue) == 11
    assert failures == [failure]
    assert json.loads(receipts.read_text()) == {"retained_marker": "no invented success"}
    assert sum(paper.stage_id == "stage-1" for paper in migrated.papers) == 6
    assert sum(paper.stage_id == "unknown" for paper in migrated.papers) == 5
    journal = load_source_revisions(provider)
    assert journal[:11] == previous_journal
    assert len(journal) == 22
    assert {entry["reason"] for entry in journal[11:]} == {"source_context_changed"}
    assert {entry["source_record"]["file_type"] for entry in journal[:11]} == {"accessible_bundle"}
    assert {entry["source_record"]["file_type"] for entry in journal[11:]} == {"question_answer"}
    assert len({entry["blob_storage_key"] for entry in journal}) == 11
    for before, after in zip(sorted(papers, key=lambda p: p.year_roc),
                             sorted(migrated.papers, key=lambda p: p.year_roc), strict=True):
        for field in ("year_roc", "source_exam_id", "category_code", "subject_code", "paper_code",
                      "file_type", "download_url_source", "storage_key", "checksum", "canonical_id"):
            assert getattr(before, field) == getattr(after, field)
    for entry in journal:
        old = entry["source_record"]
        assert old["bundle_id"] == "previous-archive-identity"
        assert old["stage_id"] == "not-applicable"
        assert hashlib.sha256((tmp_path / "mirror" / entry["blob_storage_key"]).read_bytes()).hexdigest() == old["checksum"]
    path = archive_identity_path(tmp_path)
    path.parent.mkdir(parents=True)
    document = json.loads(archive_identity_path(ROOT).read_text())
    for row, fact in zip(document["facts"], facts, strict=True):
        row["checksum"] = fact.checksum
    path.write_text(json.dumps(document))
    assert validate_archive_identities(tmp_path, verify_mirror=True) == 11
    anchor = tmp_path / "mirror" / migrated.papers[0].storage_key
    anchor.write_bytes(b"mismatched original")
    with pytest.raises(ValueError, match="checksum mismatch"):
        validate_archive_identities(tmp_path, verify_mirror=True)


@pytest.mark.repo_data
def test_reviewed_catalog_covers_retained_current_and_historical_anchors():
    assert validate_archive_identities(ROOT) == 11
    assert sum(fact.written_test_date is not None for fact in archive_identities()) == 6
    assert next(fact for fact in archive_identities() if fact.year_roc == 111).written_test_date == date(2023, 3, 11)
