"""Whole-original role approval does not approve its occupational identity."""

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from app.models import NormalizedCatalog, NormalizedPaper
from app.normalizer import renormalize_catalog
from app.paths import provider_paths
from app.providers.taisugar_recruit.evidence import file_role, native_roles, validate_native_roles
from app.source_revisions import load_source_revisions, revision_journal_path
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
def test_only_exact_reviewed_originals_approve_the_native_role(fact):
    values = context(fact)
    assert file_role("source_document", **values) == fact.native_role
    assert file_role("question", **values) == fact.native_role
    assert file_role("question_answer", **values) == fact.native_role
    assert file_role("answer", **values) == fact.native_role
    with pytest.raises(ValueError, match="changed or unreviewed bytes"):
        file_role("answer", **{**values, "checksum": "0" * 64})
    with pytest.raises(ValueError, match="changed or unreviewed bytes"):
        file_role("source_document", **{**values, "checksum": "0" * 64})
    for field, value in (
        ("source_exam_id", "taisugar-recruit-999"),
        ("year_roc", 999),
        ("category_code", "999"),
        ("subject_code", "taisugar-file-unreviewed"),
        ("title", fact.title + "改版"),
        ("source_url", fact.source_url + "?revision=2"),
    ):
        changed = {**values, field: value}
        assert file_role("source_document", **changed) == "source_document"
        assert file_role("question", **changed) == "question"
        with pytest.raises(ValueError, match="unreviewed source evidence"):
            file_role("question_answer", **changed)
        with pytest.raises(ValueError, match="unreviewed source evidence"):
            file_role("answer", **changed)


@pytest.mark.repo_data
def test_role_review_keeps_the_whole_original_and_all_identity_uncertainty():
    _, retained, _ = load_provider_state(provider_paths(ROOT, "taisugar_recruit"))
    checksums = {fact.checksum for fact in native_roles()}
    originals = [p for p in retained.papers if p.checksum in checksums]
    assert len(originals) == 136
    reviewed = renormalize_catalog(
        NormalizedCatalog([replace(p, file_type="source_document") for p in originals], []), []
    )
    for before, after in zip(originals, reviewed.papers, strict=True):
        assert after.file_type == next(
            f.native_role for f in native_roles() if f.checksum == after.checksum
        )
        assert after.checksum == before.checksum
        assert after.storage_key == before.storage_key
        assert after.download_url_source == before.download_url_source
        assert after.subject_code == before.subject_code
        assert after.track_id == "taisugar-worker-unknown"
        assert after.stage_id == before.stage_id
        assert after.classification_confidence == "review"
        assert after.source_material.needs_review
    # Repository-data validation binds checked-in metadata; byte validation has
    # independent corruption and missing-file fixtures plus operator evidence.
    assert validate_native_roles(ROOT) == 136


@pytest.mark.repo_data
def test_exact_native_evidence_reclassifies_affected_history_without_rewriting_it():
    owner = provider_paths(ROOT, "taisugar_recruit")
    journal = revision_journal_path(owner)
    before = journal.read_bytes()
    facts = {f.source_key for f in native_roles()}
    selected = []
    for revision in load_source_revisions(owner):
        row = revision["source_record"]
        if revision["record_type"] != "paper":
            continue
        key = (
            row["source_exam_id"],
            row["year_roc"],
            row["category_code"],
            row["subject_code"],
            row["subject_name_raw"],
            row["download_url_source"],
            row["checksum"],
        )
        if key in facts:
            selected.append(NormalizedPaper(**{**row, "storage_key": revision["blob_storage_key"]}))
    assert len(selected) == 182
    assert sum(p.file_type == "question" for p in selected) == 44
    reviewed = renormalize_catalog(NormalizedCatalog(selected, []), []).papers
    for original, current in zip(selected, reviewed, strict=True):
        assert current.file_type == next(
            f.native_role for f in native_roles() if f.checksum == current.checksum
        )
        assert current.stage_id == next(
            p.stage_id
            for p in load_provider_state(owner)[1].papers
            if p.checksum == current.checksum
        )
        assert current.storage_key == original.storage_key
        assert current.checksum == original.checksum
        assert current.download_url_source == original.download_url_source
        assert current.track_id == "taisugar-worker-unknown"
        assert current.classification_confidence == "review"
    assert journal.read_bytes() == before


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
            {"schema_version": 2, "provider_id": "taisugar_recruit", "facts": [asdict(fact)]},
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
                    "schema_version": 2,
                    "provider_id": "taisugar_recruit",
                    "facts": [asdict(invalid)],
                },
                ensure_ascii=False,
            )
        )
        with pytest.raises(ValueError):
            evidence.read_native_roles(path)


@pytest.mark.parametrize(
    "defect", ["old_version", "invalid_role", "duplicate", "conflicting_digest"]
)
def test_explicit_role_owner_rejects_ambiguous_or_implicit_judgments(tmp_path, defect):
    from app.providers.taisugar_recruit.evidence import read_native_roles

    fact = asdict(next(f for f in native_roles() if f.native_role == "question"))
    document = {"schema_version": 2, "provider_id": "taisugar_recruit", "facts": [fact]}
    if defect == "old_version":
        document["schema_version"] = 1
    elif defect == "invalid_role":
        fact["native_role"] = "audio"
    else:
        duplicate = dict(fact)
        if defect == "conflicting_digest":
            duplicate["checksum"] = "0" * 64
        document["facts"].append(duplicate)
    path = tmp_path / "role-review.json"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        read_native_roles(path)


def test_question_only_review_does_not_inherit_a_combined_container_label():
    questions = [f for f in native_roles() if f.native_role == "question" and f.year_roc == 111]
    assert len(questions) == 2
    assert all(f.year_roc == 111 for f in questions)
    assert len({f.checksum for f in questions}) == 2
    assert len({f.subject_code for f in questions}) == 2
    for fact in questions:
        assert file_role("source_document", **context(fact)) == "question"
        assert file_role("question_answer", **context(fact)) == "question"
        assert file_role("answer", **context(fact)) == "question"


def test_separate_native_answers_do_not_turn_question_booklets_into_combined_files():
    facts = [f for f in native_roles() if f.year_roc == 112]
    assert len(facts) == 62
    questions = [f for f in facts if f.native_role == "question"]
    answers = [f for f in facts if f.native_role == "answer"]
    assert len(questions) == 32 and len(answers) == 30
    assert len({f.checksum for f in facts}) == 62
    # Both official containers include independent general and school originals.
    # The approved native contexts, rather than a container's broad label, own role.
    assert len({f.source_url.split("#")[0] for f in questions}) == 1
    assert len({f.source_url.split("#")[0] for f in answers}) == 1
    assert questions[0].source_url.split("#")[0] != answers[0].source_url.split("#")[0]
    for fact in answers:
        assert file_role("question", **context(fact)) == "answer"
        assert file_role("question_answer", **context(fact)) == "answer"
