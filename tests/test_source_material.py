"""Reviewed dates must survive ingestion without becoming capture years."""

import json
from dataclasses import asdict, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest
from jsonschema import Draft202012Validator, ValidationError

from app.models import ExamAttachment, NormalizedPaper, ParsedPaper, SourceExamPage, to_plain_data
from app.bundler import build_bundles
from app.normalizer import normalize_papers, renormalize_catalog
from app.paths import provider_paths
from app.publisher import publish_site, write_provider_state
from app.provider_index import (
    PAPER_YEAR_ROC, build_provider_index, load_provider_index, paper_index_source_year_roc,
)
from app.source_material import SourceDate, SourceMaterial, source_year_roc
from app.source_revisions import load_source_revisions
from app.state import load_provider_state


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas/source-material-v1.schema.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA)


def workbook_material() -> SourceMaterial:
    return SourceMaterial(
        "practice_collection", SourceDate("edition_year", 2012),
        "https://www.jlpt.jp/samples/sampleindex.html", "Official Practice Workbook (published 2012)",
    )


def workbook_page() -> SourceExamPage:
    return SourceExamPage(
        "jlpt-cert-practice-2012", 2026, 115, "JLPT Official Practice Workbook", [],
        [ParsedPaper("JLPT_N1", "n1", "listening", "N1 listening", {"question": "https://www.jlpt.jp/samples/sample2012/pdf/N1L.pdf"})],
        provider_id="jlpt_cert", source_material=workbook_material(),
    )


def normalize(page: SourceExamPage):
    return normalize_papers(
        page.source_exam_id, page.year_ad, page.exam_name_raw, page.papers,
        [], "", {}, provider_id=page.provider_id, source_material=page.source_material,
    )


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(schema_version=2),
    lambda value: value.update(schema_version=True),
    lambda value: value.update(kind="unsupported"),
    lambda value: value.update(kind="unknown", review_reason=""),
    lambda value: value["date"].update(basis="unknown", year_ad=None),
    lambda value: value["date"].update(basis="undated"),
    lambda value: value["date"].update(year_ad=True),
    lambda value: value["date"].update(basis="exam_year"),
    lambda value: value.update(evidence_url="local-file.pdf"),
    lambda value: value.update(evidence_label=""),
    lambda value: value.update(extra_fact="unvalidated"),
])
def test_runtime_and_schema_reject_unsupported_or_invented_facts(mutate) -> None:
    value = asdict(workbook_material())
    mutate(value)
    with pytest.raises(ValueError):
        SourceMaterial.from_data(value)
    with pytest.raises(ValidationError):
        VALIDATOR.validate(value)


def test_edition_facts_survive_normalization_and_state_round_trip() -> None:
    page = workbook_page()
    catalog = normalize(page)
    paper = catalog.papers[0]
    assert paper.year_roc == 115  # Retained source key, not the public date.
    assert paper.schema_version == 3
    assert source_year_roc(paper) == 101
    assert "material-practice-collection" in paper.variant_ids
    assert "練習問題集" in paper.bundle_name
    assert renormalize_catalog(catalog, []).papers == catalog.papers
    with TemporaryDirectory() as temporary:
        provider = provider_paths(Path(temporary), "jlpt_cert")
        write_provider_state(
            provider, raw_pages=[page], normalized=catalog, aliases=[], failures=[], manifest=None,
        )
        loaded_pages, loaded, _ = load_provider_state(provider)
        assert loaded_pages == [page]
        assert loaded.papers == catalog.papers
        persisted = json.loads((provider.papers_dir / "2026.json").read_text())
        VALIDATOR.validate(persisted[0]["source_material"])
        index = load_provider_index(provider)
        assert index["schema_version"] == 2
        assert index["papers"][0][PAPER_YEAR_ROC] == 115
        assert paper_index_source_year_roc(index, index["papers"][0]) == 101


def test_undated_samples_and_unknown_dates_never_inherit_partition_year() -> None:
    page = workbook_page()
    page.source_material = SourceMaterial(
        "sample", SourceDate("undated", None), "https://www.jlpt.jp/samples/", "問題例",
    )
    paper = normalize(page).papers[0]
    assert source_year_roc(paper) is None
    assert paper.classification_confidence == "high"
    assert paper.source_material.date == SourceDate("undated", None)
    page.source_material = replace(
        page.source_material, date=SourceDate("unknown", None), review_reason="Official date evidence is missing",
    )
    unresolved = normalize(page)
    assert unresolved.papers[0].classification_confidence == "review"
    assert unresolved.review_queue
    assert source_year_roc(unresolved.papers[0]) is None


def test_paper_override_preserves_reference_material_inside_an_event() -> None:
    page = workbook_page()
    page.source_material = SourceMaterial(
        "administered", SourceDate("exam_year", 2026), "https://www.jlpt.jp/", "2026 test",
    )
    page.papers[0].source_material = SourceMaterial(
        "reference", SourceDate("publication_year", 2021), "https://www.jlpt.jp/", "2021 reference",
    )
    reference = normalize(page).papers[0]
    assert reference.source_material.kind == "reference"
    assert source_year_roc(reference) == 110
    assert "material-reference" in reference.variant_ids


def test_legacy_records_keep_their_serialized_fields_and_version() -> None:
    page = workbook_page()
    page.source_material = None
    paper = normalize(page).papers[0]
    assert paper.schema_version == 2
    assert "source_material" not in to_plain_data(paper)
    assert "source_material" not in to_plain_data(page)
    assert "source_material" not in to_plain_data(page)["papers"][0]
    assert source_year_roc(paper) is None
    with pytest.raises(ValueError, match="v3"):
        NormalizedPaper(**{**to_plain_data(paper), "source_material": asdict(workbook_material())})


def test_publication_requires_upgraded_archive_and_frontend_readers() -> None:
    catalog = normalize(workbook_page())
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        with pytest.raises(ValueError, match="archive and frontend migration"):
            build_bundles(
                mirror_dir=root / "mirror", bundle_dir=root / "bundles", normalized=catalog,
                min_years=1, bundle_base_url="",
            )
        assert not (root / "bundles").exists()


def test_partial_publication_cannot_preserve_unsupported_material_records(tmp_path) -> None:
    catalog = normalize(workbook_page())
    with patch("app.publisher.load_site_catalog", return_value=(catalog, [])):
        with pytest.raises(ValueError, match="archive and frontend migration"):
            publish_site(
                tmp_path, site_id="default", repository="owner/repository",
                affected_canonical_ids={"unrelated-bundle"},
            )
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "bundles").exists()


def test_mixed_index_does_not_infer_dates_for_legacy_records() -> None:
    reviewed = normalize(workbook_page()).papers[0]
    legacy = replace(reviewed, schema_version=2, source_material=None)
    with TemporaryDirectory() as temporary:
        provider = provider_paths(Path(temporary), "jlpt_cert")
        index = build_provider_index(provider, [], [legacy, reviewed, legacy])
        assert index["schema_version"] == 2
        assert [paper_index_source_year_roc(index, row) for row in index["papers"]] == [None, 101, None]
        assert [row[PAPER_YEAR_ROC] for row in index["papers"]] == [115, 115, 115]


def test_revision_retains_facts_without_claiming_unverified_bytes() -> None:
    page = workbook_page()
    initial = normalize(page)
    with TemporaryDirectory() as temporary:
        provider = provider_paths(Path(temporary), "jlpt_cert")
        write_provider_state(provider, [page], initial, [], [], None)
        page.papers[0].files["question"] += "?revision=2"
        write_provider_state(provider, [page], normalize(page), [], [], None)
        journal = load_source_revisions(provider)
        assert len(journal) == 1
        record = journal[0]["source_record"]
        assert record["schema_version"] == 3
        assert record["source_material"] == asdict(workbook_material())
        assert journal[0]["blob_storage_key"] is None
        path = provider.data_dir / "source-revisions.json"
        data = json.loads(path.read_text())
        data["revisions"][0]["source_record"]["source_material"]["date"]["basis"] = "undated"
        path.write_text(json.dumps(data))
        with pytest.raises(ValueError, match="cannot claim a year"):
            load_source_revisions(provider)


@pytest.mark.parametrize("override", [False, True])
def test_retired_attachment_preserves_selected_material_evidence(tmp_path, override) -> None:
    page = workbook_page()
    attachment_material = SourceMaterial(
        "reference", SourceDate("undated", None), "https://www.jlpt.jp/", "Instructions",
    ) if override else None
    page.attachments = [ExamAttachment(
        "Instructions", "question", "https://www.jlpt.jp/instructions.pdf",
        storage_key="providers/jlpt_cert/instructions.pdf", source_material=attachment_material,
    )]
    provider = provider_paths(tmp_path, "jlpt_cert")
    write_provider_state(provider, [page], normalize(page), [], [], None)
    page.attachments = []
    write_provider_state(provider, [page], normalize(page), [], [], None)
    entry, = load_source_revisions(provider)
    assert entry["record_type"] == "attachment"
    assert entry["source_record"]["source_material"] == asdict(attachment_material or page.source_material)
    assert entry["blob_storage_key"] is None
