"""Annual stage approval is separate from file role and occupational identity."""

import hashlib
import json
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from jsonschema import Draft202012Validator, ValidationError

from app.models import NormalizedCatalog, ParsedPaper, SourceExamPage, to_plain_data
from app.normalizer import normalize_papers, renormalize_catalog
from app.paths import provider_paths
from app.providers.base import DownloadedFile
from app.providers.taisugar_recruit import annual
from app.providers.taisugar_recruit.annual import (
    annual_exams_path,
    resolve_written_exam,
    validate_written_exams,
    written_exams,
)
from app.providers.taisugar_recruit.provider import TaisugarRecruitProvider
from app.state import load_provider_state
from app.storage import MirrorStore
from app.sync import sync_exam_pages

ROOT = Path(__file__).resolve().parents[1]


def context(fact):
    return {
        "source_exam_id": fact.source_exam_id,
        "year_roc": fact.year_roc,
        "exam_name_raw": fact.exam_name_raw,
        "category_code": str(fact.year_roc),
        "category_raw": "台糖新進工員甄試",
        "subject_code": fact.subject_code,
        "title": fact.title,
        "source_url": fact.source_url,
        "checksum": fact.checksum,
    }


@pytest.mark.parametrize("fact", written_exams(), ids=lambda f: f.source_exam_id)
def test_annual_stage_requires_the_exact_native_original(fact):
    values = context(fact)
    assert resolve_written_exam(**values) == fact
    for field, wrong in (
        ("source_exam_id", "taisugar-recruit-999"),
        ("year_roc", 999),
        ("exam_name_raw", fact.exam_name_raw + "更正"),
        ("category_code", "999"),
        ("category_raw", "博士甄試"),
        ("subject_code", "taisugar-file-unreviewed"),
        ("title", fact.title + "更正"),
        ("source_url", fact.source_url + "?v=2"),
        ("checksum", "0" * 64),
    ):
        assert resolve_written_exam(**{**values, field: wrong}) is None
    attachment = fact.rule_attachment()
    assert attachment.file_type == "instructions"
    assert attachment.source_material.kind == "reference"
    assert attachment.source_material.date.basis == "publication_year"
    assert fact.material.kind == "administered"
    assert fact.material.needs_review  # A compound original still needs occupation review.


@pytest.mark.repo_data
def test_fresh_and_retained_normalization_agree_without_guessing_occupation():
    _, catalog, _ = load_provider_state(provider_paths(ROOT, "taisugar_recruit"))
    for fact in written_exams():
        paper = next(p for p in catalog.papers if p.checksum == fact.checksum)
        retained = renormalize_catalog(NormalizedCatalog([paper], []), []).papers[0]
        fresh = normalize_papers(
            fact.source_exam_id,
            fact.year_roc + 1911,
            fact.exam_name_raw,
            [
                ParsedPaper(
                    "台糖新進工員甄試",
                    str(fact.year_roc),
                    fact.subject_code,
                    fact.title,
                    {"source_document": fact.source_url},
                    {
                        "source_document": {
                            "checksum": fact.checksum,
                            "storage_key": paper.storage_key,
                        }
                    },
                )
            ],
            [],
            "",
            {},
            provider_id="taisugar_recruit",
        ).papers[0]
        for reviewed in (fresh, retained):
            assert reviewed.stage_id == "stage-1"
            assert reviewed.track_id == "taisugar-worker-unknown"
            assert reviewed.classification_confidence == "review"
            assert reviewed.file_type == "question_answer"
            assert reviewed.source_material == fact.material
            assert reviewed.download_url_source == paper.download_url_source
            assert reviewed.storage_key == paper.storage_key
            assert reviewed.checksum == paper.checksum
    assert validate_written_exams(ROOT) == 2


@pytest.mark.parametrize("corrupt_rule", [False, True])
def test_normal_sync_keeps_rules_as_required_checksum_guarded_references(
    tmp_path, monkeypatch, corrupt_rule
):
    from app.providers.taisugar_recruit import provider as provider_module

    rule_bytes = b"%PDF-1.4\nreference rule fixture\n%%EOF"
    fact = replace(written_exams()[0], rule_checksum=hashlib.sha256(rule_bytes).hexdigest())
    monkeypatch.setattr(provider_module, "written_exams", lambda: (fact,))
    # An unreviewed new original in the same year must not inherit stage approval.
    url = "https://www.taisugar.com.tw/upload/UserFiles/News/6900/unreviewed.pdf"
    page = SourceExamPage(
        fact.source_exam_id,
        fact.year_roc + 1911,
        fact.year_roc,
        fact.exam_name_raw,
        [],
        [
            ParsedPaper(
                "台糖新進工員甄試",
                str(fact.year_roc),
                "unreviewed",
                "new paper",
                {"source_document": url},
            )
        ],
        provider_id="taisugar_recruit",
    )
    client = Mock()
    client.fetch_exam_page.side_effect = lambda *_: deepcopy(page)
    client.download_file.side_effect = lambda source: DownloadedFile(
        (rule_bytes + (b"changed" if corrupt_rule else b""))
        if source == fact.rule_url
        else b"%PDF-1.4\nunreviewed native\n%%EOF",
        "application/pdf",
        "original.pdf",
    )
    provider = TaisugarRecruitProvider(client)
    pages, normalized, failures = sync_exam_pages(
        provider,
        [(fact.source_exam_id, fact.year_roc + 1911)],
        MirrorStore(tmp_path / "mirror"),
        [],
        "",
        download_attachments=False,
    )
    assert normalized.papers[0].stage_id == "unknown"
    assert normalized.papers[0].file_type == "source_document"
    assert len(pages[0].attachments) == 1
    reference = pages[0].attachments[0]
    assert reference.file_type == "instructions"
    assert client.download_file.call_count == 2
    if corrupt_rule:
        assert len(failures) == 1 and failures[0].file_type == "instructions"
        assert not reference.checksum and not reference.storage_key
    else:
        assert not failures and reference.checksum == fact.rule_checksum
        stored_rule = tmp_path / "mirror" / reference.storage_key
        assert stored_rule.read_bytes() == rule_bytes
        # A changed refresh cannot overwrite the last verified rule either.
        client.download_file.side_effect = lambda source: DownloadedFile(
            rule_bytes + b"changed"
            if source == fact.rule_url
            else b"%PDF-1.4\nunreviewed native\n%%EOF",
            "application/pdf",
            "original.pdf",
        )
        _, _, refresh_failures = sync_exam_pages(
            provider,
            [(fact.source_exam_id, fact.year_roc + 1911)],
            MirrorStore(tmp_path / "mirror"),
            [],
            "",
            previous_pages=pages,
        )
        assert len(refresh_failures) == 1 and refresh_failures[0].file_type == "instructions"
        assert stored_rule.read_bytes() == rule_bytes


@pytest.mark.parametrize("history_only", [False, True])
def test_original_and_rule_anchor_bytes_are_verified_in_current_or_history(
    tmp_path, monkeypatch, history_only
):
    from app import source_revisions, state

    pages, catalog, _ = load_provider_state(provider_paths(ROOT, "taisugar_recruit"))
    facts = written_exams()
    selected = [p for p in catalog.papers if p.checksum in {f.checksum for f in facts}]
    current_pages = []
    current_papers = []
    revisions = []
    targets = []
    rows = []
    for fact in facts:
        payload = b"%PDF-1.4\n" + fact.checksum.encode() + b"\n%%EOF"
        rule = b"%PDF-1.4\n" + fact.rule_checksum.encode() + b"\n%%EOF"
        revised = replace(
            fact,
            checksum=hashlib.sha256(payload).hexdigest(),
            rule_checksum=hashlib.sha256(rule).hexdigest(),
        )
        rows.append(asdict(revised))
        paper = replace(
            next(p for p in selected if p.checksum == fact.checksum), checksum=revised.checksum
        )
        key = f"providers/taisugar_recruit/{fact.year_roc}/rules.pdf"
        reference = revised.rule_attachment()
        reference.storage_key = key
        reference.checksum = revised.rule_checksum
        page = replace(
            next(p for p in pages if p.year_roc == fact.year_roc), attachments=[reference]
        )
        for storage_key, data in ((paper.storage_key, payload), (key, rule)):
            target = tmp_path / "mirror" / storage_key
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            targets.append(target)
        if history_only:
            revisions.extend(
                [
                    {
                        "record_type": "paper",
                        "source_record": to_plain_data(paper),
                        "blob_storage_key": paper.storage_key,
                    },
                    {
                        "record_type": "attachment",
                        "source_record": {
                            **to_plain_data(reference),
                            "source_exam_id": page.source_exam_id,
                            "year_roc": page.year_roc,
                        },
                        "blob_storage_key": key,
                    },
                ]
            )
        else:
            current_papers.append(paper)
            current_pages.append(page)
    path = annual_exams_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"schema_version": 1, "provider_id": "taisugar_recruit", "facts": rows})
    )
    monkeypatch.setattr(
        state,
        "load_provider_state",
        lambda _: (current_pages, NormalizedCatalog(current_papers, []), []),
    )
    monkeypatch.setattr(source_revisions, "load_source_revisions", lambda _: revisions)
    assert validate_written_exams(tmp_path, verify_mirror=True) == 2
    for path in targets:
        original = path.read_bytes()
        path.write_bytes(original + b"corrupt")
        with pytest.raises(ValueError, match="mirror bytes"):
            validate_written_exams(tmp_path, verify_mirror=True)
        path.unlink()
        with pytest.raises(ValueError, match="mirror bytes"):
            validate_written_exams(tmp_path, verify_mirror=True)
        path.write_bytes(original)
    if history_only:
        revisions[:] = [r for r in revisions if r["record_type"] != "attachment"]
    else:
        current_pages[0].attachments.clear()
    with pytest.raises(ValueError, match="retained original or rule anchor"):
        validate_written_exams(tmp_path)


def test_annual_schema_and_runtime_reject_invalid_primary_evidence(tmp_path):
    document = json.loads(annual_exams_path(ROOT).read_text())
    schema = json.loads((ROOT / "schemas/taisugar-annual-written-exams-v1.schema.json").read_text())
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    validator.validate(document)
    for field in document["facts"][0]:
        changed = deepcopy(document)
        del changed["facts"][0][field]
        with pytest.raises(ValidationError):
            validator.validate(changed)
    for field, value in (
        ("rule_url", "https://example.com/forged.pdf"),
        ("rule_checksum", "missing"),
        ("written_test_date", "2017-02-31"),
        ("rule_pdf_pages", [2, 2]),
        ("reviewed_at", "20261028"),
    ):
        changed = deepcopy(document)
        changed["facts"][0][field] = value
        with pytest.raises(ValidationError):
            validator.validate(changed)
        path = tmp_path / "invalid.json"
        path.write_text(json.dumps(changed))
        with pytest.raises(ValueError):
            annual.read_written_exams(path)
