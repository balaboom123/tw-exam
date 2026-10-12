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
    read_source_conflicts,
    reviewed_conflict,
    source_conflicts,
    source_conflicts_path,
)
from app.providers.moea_recruit.provider import MoeaRecruitProvider
from app.storage import MirrorStore
from app.sync import sync_exam_pages


def arguments(fact, file):
    return dict(
        source_exam_id=f"moea-recruit-{fact.year_roc}",
        year_roc=fact.year_roc,
        category_code=str(fact.year_roc),
        category_raw=fact.listing_group,
        subject_code=file.subject_code,
        title=file.source_label,
        source_url=file.source_url,
        checksum=file.checksum,
    )


def entry(fact):
    return MoeaRecruitEntry(
        fact.year_roc,
        fact.year_roc + 1911,
        fact.listing_group,
        [MoeaRecruitDownload(file.source_label, file.source_url) for file in fact.native_files],
    )


@pytest.fixture
def native_fixture():
    original = source_conflicts()[0]
    downloads = {
        file.source_url: DownloadedFile(
            b"%PDF-1.7\n" + file.native_subject.encode() + file.native_role.encode(),
            "application/pdf",
            "native.pdf",
        )
        for file in original.native_files
    }
    fact = replace(
        original,
        native_files=tuple(
            replace(file, checksum=hashlib.sha256(downloads[file.source_url].data).hexdigest())
            for file in original.native_files
        ),
    )
    return fact, downloads


def test_native_roles_preserve_questions_and_resolve_the_wrong_answer_label():
    fact = source_conflicts()[0]
    assert [
        native_file_role("question", **arguments(fact, file)) for file in fact.native_files
    ] == ["question", "question", "answer"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_exam_id", "moea-recruit-103"),
        ("year_roc", 103),
        ("category_code", "103"),
        ("category_raw", "102年度電機(甲)"),
        ("subject_code", "joint-76"),
        ("title", "專業科目A答案"),
        ("source_url", "https://www.taipower.com.tw/media/other/answer.pdf"),
    ],
)
def test_another_source_context_cannot_inherit_the_reviewed_role(field, value):
    fact = source_conflicts()[0]
    assert (
        native_file_role("question", **{**arguments(fact, fact.native_files[-1]), field: value})
        == "question"
    )


def test_changed_bytes_cannot_inherit_the_native_answer_role():
    fact = source_conflicts()[0]
    with pytest.raises(ValueError, match="changed or unreviewed"):
        native_file_role(
            "question", **{**arguments(fact, fact.native_files[-1]), "checksum": "0" * 64}
        )


def test_ingestion_and_migration_preserve_raw_labels_roles_and_locators():
    fact = source_conflicts()[0]
    file = fact.native_files[-1]
    parsed = ParsedPaper(
        fact.listing_group,
        str(fact.year_roc),
        file.subject_code,
        file.source_label,
        {"question": file.source_url},
        {
            "question": {
                "checksum": file.checksum,
                "storage_key": "providers/moea_recruit/retained/question.pdf",
            }
        },
    )
    current = normalize_papers(
        source_exam_id=f"moea-recruit-{fact.year_roc}",
        year_ad=fact.year_roc + 1911,
        exam_name_raw="經濟部所屬事業機構新進職員甄試",
        papers=[parsed],
        alias_rules=[],
        mirror_base_url="",
        mirror_metadata={},
        provider_id="moea_recruit",
    )
    answer = current.papers[0]
    assert answer.file_type == "answer"
    assert answer.subject_name_raw == file.source_label
    assert answer.storage_key == parsed.mirror_files["question"]["storage_key"]
    assert parsed.files == {"question": file.source_url}
    assert answer.classification_confidence == "review"
    old = replace(answer, file_type="question", paper_code=f"102-{file.subject_code}-question")
    migrated = renormalize_catalog(NormalizedCatalog([old], []), [])
    assert migrated.papers == current.papers
    assert renormalize_catalog(migrated, []).papers == migrated.papers


def test_only_the_complete_reviewed_url_set_can_resolve_a_conflict():
    fact = source_conflicts()[0]
    assert (
        reviewed_conflict(
            fact.year_roc, fact.listing_group, fact.conflict_label, set(fact.conflicting_urls)
        )
        == fact
    )
    assert (
        reviewed_conflict(
            fact.year_roc,
            fact.listing_group,
            fact.conflict_label,
            {*fact.conflicting_urls, "https://www.taipower.com.tw/media/extra/file.pdf"},
        )
        is None
    )


def test_preflight_checks_every_native_control_and_reuses_its_download(native_fixture):
    fact, downloads = native_fixture
    client = MoeaRecruitClient()
    with (
        patch("app.providers.moea_recruit.evidence.source_conflicts", return_value=(fact,)),
        patch.object(client, "_iter_entries", return_value=[entry(fact)]),
        patch.object(
            client.http, "download", side_effect=lambda url, **kw: downloads[url]
        ) as acquire,
    ):
        page = client.fetch_exam_page("moea-recruit-102", 2013)
        assert len(page.papers) == 3
        assert acquire.call_count == 3
        for file in fact.native_files:
            assert client.download_file(file.source_url) == downloads[file.source_url]
            assert client.reviewed_file_checksum(file.source_url) == file.checksum
        assert acquire.call_count == 3


def test_changed_preflight_bytes_stop_before_any_mirror_write(tmp_path, native_fixture):
    fact, downloads = native_fixture
    client = MoeaRecruitClient()
    bad = {
        **downloads,
        fact.native_files[-1].source_url: DownloadedFile(
            b"%PDF-1.7\nchanged", "application/pdf", "changed.pdf"
        ),
    }
    store = MirrorStore(tmp_path / "mirror")
    provider = MoeaRecruitProvider(client)
    with (
        patch("app.providers.moea_recruit.evidence.source_conflicts", return_value=(fact,)),
        patch.object(client, "_iter_entries", return_value=[entry(fact)]),
        patch.object(client.http, "download", side_effect=lambda url, **kw: bad[url]),
    ):
        pages, catalog, failures = sync_exam_pages(
            provider, [("moea-recruit-102", 2013)], store, [], ""
        )
    assert not pages and not catalog.papers
    assert len(failures) == 1 and failures[0].stage == "fetch"
    assert "changed or unreviewed" in failures[0].message
    assert not [path for path in store.root.rglob("*") if path.is_file()]


def test_stale_verified_mirror_cannot_override_fresh_native_evidence(tmp_path, native_fixture):
    fact, downloads = native_fixture
    client = MoeaRecruitClient()
    store = MirrorStore(tmp_path / "mirror")
    provider = MoeaRecruitProvider(client)
    with (
        patch("app.providers.moea_recruit.evidence.source_conflicts", return_value=(fact,)),
        patch.object(client, "_iter_entries", return_value=[entry(fact)]),
        patch.object(client.http, "download", side_effect=lambda url, **kw: downloads[url]),
    ):
        pages, _, failures = sync_exam_pages(provider, [("moea-recruit-102", 2013)], store, [], "")
        assert not failures
        parsed = pages[0].papers[-1]
        key = parsed.mirror_files["question"]["storage_key"]
        stale = store.write_bytes(key, b"%PDF-1.7\nold unrelated content", overwrite=True)
        parsed.mirror_files["question"]["checksum"] = stale.checksum
        _, current, failures = sync_exam_pages(
            provider, [("moea-recruit-102", 2013)], store, [], "", previous_pages=pages
        )
    assert not failures
    answer = current.papers[-1]
    assert answer.file_type == "answer" and answer.checksum == fact.native_files[-1].checksum
    assert (store.root / answer.storage_key).read_bytes() == downloads[
        fact.native_files[-1].source_url
    ].data
    assert any(
        path.read_bytes() == b"%PDF-1.7\nold unrelated content"
        for path in (store.root / "providers/moea_recruit/recovery").rglob("*.pdf")
    )


def test_failed_recheck_does_not_leave_a_stale_preflight_cache(native_fixture):
    fact, downloads = native_fixture
    client = MoeaRecruitClient()
    with (
        patch("app.providers.moea_recruit.evidence.source_conflicts", return_value=(fact,)),
        patch.object(client, "_iter_entries", return_value=[entry(fact)]),
        patch.object(client.http, "download", side_effect=lambda url, **kw: downloads[url]),
    ):
        client.fetch_exam_page("moea-recruit-102", 2013)
    with (
        patch("app.providers.moea_recruit.evidence.source_conflicts", return_value=(fact,)),
        patch.object(client, "_iter_entries", return_value=[entry(fact)]),
        patch.object(
            client.http, "download", return_value=DownloadedFile(b"bad", "text/html", "error.html")
        ),
    ):
        with pytest.raises(ValueError, match="changed or unreviewed"):
            client.fetch_exam_page("moea-recruit-102", 2013)
    assert all(client.reviewed_file_checksum(file.source_url) == "" for file in fact.native_files)


def test_duplicate_conflict_facts_are_rejected(tmp_path):
    document = json.loads(source_conflicts_path(Path(__file__).resolve().parents[1]).read_text())
    document["facts"].append(document["facts"][0])
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="Duplicate"):
        read_source_conflicts(path)
