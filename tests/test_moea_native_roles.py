import hashlib
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from app.models import NormalizedCatalog, ParsedPaper
from app.normalizer import normalize_papers, renormalize_catalog
from app.providers.base import DownloadedFile
from app.providers.moea_recruit.client import (
    MoeaRecruitClient,
    MoeaRecruitDownload,
    MoeaRecruitEntry,
)
from app.providers.moea_recruit.evidence import (
    native_file_role,
    native_roles,
    native_roles_path,
    read_native_roles,
    source_conflicts,
    validate_native_role_evidence,
)
from app.providers.moea_recruit.provider import MoeaRecruitProvider
from app.storage import MirrorStore
from app.sync import sync_exam_pages


def arguments(fact):
    return {
        key: getattr(fact, key)
        for key in (
            "source_exam_id",
            "year_roc",
            "category_code",
            "category_raw",
            "subject_code",
            "title",
            "source_url",
            "checksum",
        )
    }


@pytest.mark.parametrize("fact", native_roles())
def test_every_exact_native_role_applies_and_is_idempotent(fact):
    assert native_file_role(fact.source_role, **arguments(fact)) == fact.native_role
    assert native_file_role(fact.native_role, **arguments(fact)) == fact.native_role


def test_each_exact_reference_has_one_evidence_owner():
    assert validate_native_role_evidence(Path(__file__).resolve().parents[1]) == 24
    conflict = source_conflicts()[0]
    file = conflict.native_files[0]
    review = replace(
        native_roles()[0],
        source_exam_id=f"moea-recruit-{conflict.year_roc}",
        year_roc=conflict.year_roc,
        category_code=str(conflict.year_roc),
        category_raw=conflict.listing_group,
        subject_code=file.subject_code,
        title=file.source_label,
        source_url=file.source_url,
    )
    with patch("app.providers.moea_recruit.evidence.read_native_roles", return_value=(review,)):
        with pytest.raises(ValueError, match="Overlapping"):
            validate_native_role_evidence(Path(__file__).resolve().parents[1])


def test_ordinary_answered_papers_do_not_inherit_a_correction_date():
    facts = native_roles()
    assert sum(f.source_role != f.native_role for f in facts) == 13
    corrections = [f for f in facts if f.native_role == "corrected_answer"]
    assert len(corrections) == 1 and corrections[0].native_correction_date == "2012-08-03"
    assert all(
        f.native_correction_date is None for f in facts if f.native_role != "corrected_answer"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_exam_id", "moea-recruit-104"),
        ("year_roc", 104),
        ("category_code", "104"),
        ("category_raw", "103年度其他類別"),
        ("subject_code", "joint-00"),
        ("title", "其他答案"),
        ("source_url", "https://www.taipower.com.tw/media/other/paper.pdf"),
    ],
)
def test_role_review_does_not_apply_to_another_source_context(field, value):
    fact = next(f for f in native_roles() if f.native_role == "question_answer")
    assert (
        native_file_role(fact.source_role, **{**arguments(fact), field: value}) == fact.source_role
    )


def test_changed_bytes_or_source_role_cannot_inherit_reviewed_content():
    fact = native_roles()[0]
    with pytest.raises(ValueError, match="changed or unreviewed bytes"):
        native_file_role(fact.source_role, **{**arguments(fact), "checksum": "0" * 64})
    with pytest.raises(ValueError, match="unreviewed source role"):
        native_file_role("transcript", **arguments(fact))


def test_ingestion_and_migration_correct_false_answer_labels_without_rewriting_source():
    fact = next(
        f for f in native_roles() if f.source_role == "answer" and f.native_role == "question"
    )
    parsed = ParsedPaper(
        fact.category_raw,
        fact.category_code,
        fact.subject_code,
        fact.title,
        {fact.source_role: fact.source_url},
        {
            fact.source_role: {
                "checksum": fact.checksum,
                "storage_key": "providers/moea_recruit/retained/answer.pdf",
            }
        },
    )
    current = normalize_papers(
        source_exam_id=fact.source_exam_id,
        year_ad=fact.year_roc + 1911,
        exam_name_raw="經濟部所屬事業機構新進職員甄試",
        papers=[parsed],
        alias_rules=[],
        mirror_base_url="",
        mirror_metadata={},
        provider_id="moea_recruit",
    )
    question = current.papers[0]
    assert question.file_type == "question"
    assert question.subject_name_raw == fact.title
    assert question.storage_key.endswith("/answer.pdf")
    assert parsed.files == {"answer": fact.source_url}
    assert question.classification_confidence == "review"
    old = replace(
        question, file_type="answer", paper_code=f"{fact.category_code}-{fact.subject_code}-answer"
    )
    migrated = renormalize_catalog(NormalizedCatalog([old], []), [])
    assert migrated.papers == current.papers


@pytest.fixture
def role_fixture():
    original = next(f for f in native_roles() if f.native_role == "question_answer")
    download = DownloadedFile(
        b"%PDF-1.7\nvisible questions and answers", "application/pdf", "paper.pdf"
    )
    fact = replace(original, checksum=hashlib.sha256(download.data).hexdigest())
    entry = MoeaRecruitEntry(
        fact.year_roc,
        fact.year_roc + 1911,
        fact.category_raw,
        [MoeaRecruitDownload(fact.title, fact.source_url)],
    )
    return fact, download, entry


def test_changed_role_payload_is_rejected_before_a_mirror_write(tmp_path, role_fixture):
    fact, _, entry = role_fixture
    client = MoeaRecruitClient()
    provider = MoeaRecruitProvider(client)
    store = MirrorStore(tmp_path / "mirror")
    with (
        patch("app.providers.moea_recruit.evidence.native_roles", return_value=(fact,)),
        patch.object(client, "_iter_entries", return_value=[entry]),
        patch.object(
            client.http,
            "download",
            return_value=DownloadedFile(b"%PDF-1.7\nchanged", "application/pdf", "new.pdf"),
        ),
    ):
        _, catalog, failures = sync_exam_pages(
            provider, [(fact.source_exam_id, fact.year_roc + 1911)], store, [], ""
        )
    assert not catalog.papers
    assert len(failures) == 1 and failures[0].stage == "download"
    assert "changed or unreviewed bytes" in failures[0].message
    assert not [p for p in store.root.rglob("*") if p.is_file()]


def test_stale_local_receipt_cannot_substitute_another_payload(tmp_path, role_fixture):
    fact, download, entry = role_fixture
    client = MoeaRecruitClient()
    provider = MoeaRecruitProvider(client)
    store = MirrorStore(tmp_path / "mirror")
    with (
        patch("app.providers.moea_recruit.evidence.native_roles", return_value=(fact,)),
        patch.object(client, "_iter_entries", return_value=[entry]),
        patch.object(client.http, "download", return_value=download),
    ):
        pages, _, failures = sync_exam_pages(
            provider, [(fact.source_exam_id, fact.year_roc + 1911)], store, [], ""
        )
        assert not failures
        parsed = pages[0].papers[0]
        key = parsed.mirror_files[fact.source_role]["storage_key"]
        stale = store.write_bytes(key, b"%PDF-1.7\nold unreviewed content", overwrite=True)
        parsed.mirror_files[fact.source_role]["checksum"] = stale.checksum
        _, current, failures = sync_exam_pages(
            provider,
            [(fact.source_exam_id, fact.year_roc + 1911)],
            store,
            [],
            "",
            previous_pages=pages,
        )
    assert not failures
    assert current.papers[0].file_type == "question_answer"
    assert current.papers[0].checksum == fact.checksum
    assert (store.root / key).read_bytes() == download.data
    assert any(
        p.read_bytes() == b"%PDF-1.7\nold unreviewed content"
        for p in (store.root / "providers/moea_recruit/recovery").rglob("*.pdf")
    )


@pytest.mark.parametrize(
    "mutation",
    ["duplicate", "missing_correction_date", "unproved_correction_date", "wrong_source_key"],
)
def test_invalid_or_conflicting_evidence_is_rejected(tmp_path, mutation):
    document = json.loads(native_roles_path(Path(__file__).resolve().parents[1]).read_text())
    if mutation == "duplicate":
        document["facts"].append(document["facts"][0])
    elif mutation == "missing_correction_date":
        next(f for f in document["facts"] if f["native_role"] == "corrected_answer")[
            "native_correction_date"
        ] = None
    elif mutation == "unproved_correction_date":
        document["facts"][0]["native_correction_date"] = "2026-10-12"
    else:
        document["facts"][0]["subject_code"] = "joint-00"
    path = tmp_path / "roles.json"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        read_native_roles(path)
