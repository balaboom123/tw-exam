"""Whole-original role approval does not approve its occupational identity."""

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from app.models import NormalizedCatalog
from app.normalizer import renormalize_catalog
from app.paths import provider_paths
from app.providers.taisugar_recruit.evidence import file_role, native_roles, validate_native_roles
from app.state import load_provider_state

ROOT = Path(__file__).resolve().parents[1]


def context(fact):
    return {
        key: getattr(fact, key)
        for key in (
            "source_exam_id",
            "year_roc",
            "category_code",
            "subject_code",
            "title",
            "source_url",
            "checksum",
        )
    }


@pytest.mark.parametrize("fact", native_roles(), ids=lambda f: f.source_exam_id)
def test_only_exact_reviewed_originals_approve_the_combined_role(fact):
    values = context(fact)
    assert file_role("source_document", **values) == "question_answer"
    assert file_role("question_answer", **values) == "question_answer"
    with pytest.raises(ValueError, match="changed or unreviewed bytes"):
        file_role("source_document", **{**values, "checksum": "0" * 64})
    for field, value in (
        ("source_exam_id", "taisugar-recruit-108"),
        ("year_roc", 108),
        ("category_code", "108"),
        ("subject_code", "taisugar-file-unreviewed"),
        ("title", fact.title + "改版"),
        ("source_url", fact.source_url + "?revision=2"),
    ):
        changed = {**values, field: value}
        assert file_role("source_document", **changed) == "source_document"
        with pytest.raises(ValueError, match="unreviewed source evidence"):
            file_role("question_answer", **changed)


@pytest.mark.repo_data
def test_role_review_keeps_the_whole_original_and_all_identity_uncertainty():
    _, retained, _ = load_provider_state(provider_paths(ROOT, "taisugar_recruit"))
    checksums = {fact.checksum for fact in native_roles()}
    originals = [p for p in retained.papers if p.checksum in checksums]
    assert len(originals) == 2
    reviewed = renormalize_catalog(
        NormalizedCatalog([replace(p, file_type="source_document") for p in originals], []), []
    )
    for before, after in zip(originals, reviewed.papers, strict=True):
        assert after.file_type == "question_answer"
        assert after.checksum == before.checksum
        assert after.storage_key == before.storage_key
        assert after.download_url_source == before.download_url_source
        assert after.subject_code == before.subject_code
        assert after.track_id == "taisugar-worker-unknown"
        assert after.stage_id == "stage-1"
        assert after.classification_confidence == "review"
        assert after.source_material.needs_review
    # Repository-data validation binds checked-in metadata; byte validation has
    # independent corruption and missing-file fixtures plus operator evidence.
    assert validate_native_roles(ROOT) == 2


def test_native_member_roles_require_the_exact_parent_version_path_and_member_bytes(
    tmp_path, monkeypatch
):
    from app.providers.taisugar_recruit import evidence
    from app.providers.taisugar_recruit.archives import member_locator, member_subject_code

    parent = "https://www.taisugar.com.tw/upload/UserFiles/News/15158/original.zip"
    name = "115年度/國文及英文.pdf"
    fact = replace(
        native_roles()[0],
        source_exam_id="taisugar-recruit-115",
        year_roc=115,
        category_code="115",
        subject_code=member_subject_code(parent, name),
        source_url=member_locator(parent, "a" * 64, name),
        title="國文及英文.pdf",
    )
    path = tmp_path / "native-role-fixture.json"
    path.write_text(
        json.dumps(
            {"schema_version": 1, "provider_id": "taisugar_recruit", "facts": [asdict(fact)]},
            ensure_ascii=False,
        )
    )
    assert evidence.read_native_roles(path) == (fact,)
    monkeypatch.setattr(evidence, "native_roles", lambda: (fact,))
    assert evidence.file_role("source_document", **context(fact)) == "question_answer"
    changed = replace(fact, source_url=member_locator(parent, "b" * 64, name))
    assert changed.subject_code == fact.subject_code
    assert evidence.file_role("source_document", **context(changed)) == "source_document"
    with pytest.raises(ValueError, match="unreviewed source evidence"):
        evidence.file_role("question_answer", **context(changed))
    with pytest.raises(ValueError, match="changed or unreviewed bytes"):
        evidence.file_role("source_document", **context(replace(fact, checksum="0" * 64)))
    for member in ("../paper.pdf", "115年度/readme.txt"):
        invalid = replace(
            fact,
            subject_code=member_subject_code(parent, member),
            source_url=member_locator(parent, "a" * 64, member),
        )
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "provider_id": "taisugar_recruit",
                    "facts": [asdict(invalid)],
                },
                ensure_ascii=False,
            )
        )
        with pytest.raises(ValueError):
            evidence.read_native_roles(path)
