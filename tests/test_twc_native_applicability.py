"""Reviewed jobs share native papers without inheriting unrelated annual dates."""

import json
from dataclasses import replace

import pytest

from app.classification import classify_paper
from app.normalizer import renormalize_catalog
from app.paths import provider_paths
from app.providers.twc_recruit.archive import (
    NativeCategory,
    native_archive_path,
    native_archives,
    native_material,
    read_native_archives,
    validate_native_archives,
)
from app.providers.twc_recruit.provider import TwcRecruitProvider
from app.publisher import write_provider_state
from app.source_material import source_year_roc
from app.source_revisions import load_source_revisions
from app.storage import MirrorStore
from app.sync import sync_exam_pages
from tests.test_twc_native_archives import (
    PDF,
    ROOT,
    URL,
    catalogue_document,
    configured_client,
    make_archive,
)


def shared_jobs():
    fact, data = make_archive(
        {
            "原目錄/共同.pdf": PDF + b"common",
            "原目錄/配管.pdf": PDF + b"plumbing",
            "原目錄/儀器.pdf": PDF + b"instruments",
        }
    )
    categories = tuple(
        NativeCategory(
            category_id,
            label,
            "twc-" + category_id,
            (label,),
            (),
            "https://www.water.gov.tw/rules",
            "Original annual recruitment rules",
            "",
        )
        for category_id, label in (
            ("repair-a", "技術士裝修類-甲(新裝、修漏)"),
            ("repair-b", "技術士裝修類-乙(檢漏)"),
        )
    )
    slots = ("common", "professional-1", "professional-2")
    members = tuple(
        replace(member, category_ids=("repair-a", "repair-b"), paper_slot=slot)
        for member, slot in zip(fact.members, slots, strict=True)
    )
    return replace(fact, members=members, categories=categories), data


def classify_native(fact, member, category, **changes):
    values = dict(
        provider_id="twc_recruit",
        source_exam_id=f"twc-recruit-{fact.year_roc}",
        year_ad=fact.year_roc + 1911,
        category_raw=category.label,
        category_code=category.category_code(fact.year_roc),
        subject_code=member.subject_code,
        subject_name_raw=member.subject_label,
        exam_name_raw="115年試題(解答).zip",
        canonical_id="twc-recruit",
        canonical_name="台水評價職位人員甄試",
        source_material=native_material(fact, member, category.category_code(fact.year_roc)),
        source_checksum=member.checksum,
    )
    values.update(changes)
    return classify_paper(**values)


def test_shared_written_papers_keep_distinct_jobs_and_one_payload(tmp_path, monkeypatch):
    fact, data = shared_jobs()
    client, requests = configured_client(monkeypatch, fact, data)
    pages, catalog, failures = sync_exam_pages(
        TwcRecruitProvider(client),
        [("twc-recruit-115", 2026)],
        MirrorStore(tmp_path / "mirror"),
        [],
        "",
    )
    assert not failures
    assert requests == [URL]
    assert len(pages[0].papers) == len(catalog.papers) == 6
    assert len({paper.download_url_source for paper in catalog.papers}) == 3
    paths = [tmp_path / "mirror" / paper.storage_key for paper in catalog.papers]
    assert len({path.stat().st_ino for path in paths}) == 3
    assert {paper.track_id for paper in catalog.papers} == {"twc-repair-a", "twc-repair-b"}
    assert len({paper.bundle_id for paper in catalog.papers}) == 2
    assert all(paper.classification_confidence == "high" for paper in catalog.papers)
    assert all(
        paper.level_id == "not-applicable" and paper.stage_id == "stage-1"
        for paper in catalog.papers
    )
    assert all(source_year_roc(paper) == 115 for paper in catalog.papers)
    assert all(paper.category_raw in paper.bundle_name for paper in catalog.papers)
    assert renormalize_catalog(catalog, []).papers == catalog.papers


@pytest.mark.parametrize(
    "change",
    [
        {"category_code": "115-engineering"},
        {"category_raw": "技術士裝修類-乙(檢漏)"},
        {"source_exam_id": "twc-recruit-114"},
        {"source_checksum": "0" * 64},
        {"source_material": None},
    ],
)
def test_mismatched_source_context_never_approves_a_job(monkeypatch, change):
    fact, data = shared_jobs()
    configured_client(monkeypatch, fact, data)
    identity = classify_native(fact, fact.members[0], fact.categories[0], **change)
    assert identity.confidence == "review"
    assert identity.stage_id == "unknown"


def test_industry_relationship_keeps_unknown_date_and_separate_identity(tmp_path, monkeypatch):
    fact, data = shared_jobs()
    industry = replace(
        fact.categories[0],
        category_id="operating-a-industry",
        label="技術士操作類-甲(機電)(產學)",
        track_id="twc-operating-a",
        native_categories=("操作類-甲(機電)(產學)",),
        variants=(("industry-school", "產學合作"),),
        review_reason=(
            "The industry's annual written date and common-paper applicability require review"
        ),
    )
    members = tuple(
        replace(member, category_ids=(*member.category_ids, industry.category_id))
        if member.paper_slot != "common"
        else member
        for member in fact.members
    )
    fact = replace(fact, members=members, categories=(*fact.categories, industry))
    client, requests = configured_client(monkeypatch, fact, data)
    _, catalog, failures = sync_exam_pages(
        TwcRecruitProvider(client),
        [("twc-recruit-115", 2026)],
        MirrorStore(tmp_path / "mirror"),
        [],
        "",
    )
    assert not failures and requests == [URL]
    pending = [
        paper for paper in catalog.papers if paper.category_code == "115-operating-a-industry"
    ]
    ordinary = [
        paper for paper in catalog.papers if paper.category_code != "115-operating-a-industry"
    ]
    assert len(pending) == 2 and len(ordinary) == 6
    assert all(paper.source_material.date.basis == "unknown" for paper in pending)
    assert all(source_year_roc(paper) is None for paper in pending)
    assert all(
        paper.stage_id == "unknown" and paper.classification_confidence == "review"
        for paper in pending
    )
    assert all(paper.variant_ids == ["industry-school"] for paper in pending)
    assert all(source_year_roc(paper) == 115 for paper in ordinary)
    assert renormalize_catalog(catalog, []).papers == catalog.papers


@pytest.mark.parametrize(
    "defect",
    [
        "unknown_category",
        "duplicate_category",
        "missing_slot",
        "missing_member_relationship",
        "artifact_relationship",
    ],
)
def test_reader_rejects_incomplete_or_conflicting_applicability(tmp_path, defect):
    fact, _ = shared_jobs()
    document = catalogue_document(fact)
    row = document["archives"][0]
    if defect == "unknown_category":
        row["members"][0]["category_ids"].append("unknown")
    elif defect == "duplicate_category":
        row["categories"].append(row["categories"][0])
    elif defect == "missing_slot":
        row["members"][0]["category_ids"] = ["repair-b"]
    elif defect == "missing_member_relationship":
        row["members"][0]["category_ids"] = []
    else:
        row["members"][0]["file_role"] = "source_artifact"
    path = tmp_path / "catalogue.json"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        read_native_archives(path)


def test_current_occupation_loss_cannot_hide_behind_retained_generic_anchor(tmp_path, monkeypatch):
    fact, data = shared_jobs()
    generic = replace(
        fact,
        categories=(),
        members=tuple(replace(member, category_ids=(), paper_slot="") for member in fact.members),
    )
    client, _ = configured_client(monkeypatch, generic, data)
    provider = provider_paths(tmp_path, "twc_recruit")
    store = MirrorStore(tmp_path / "mirror")
    old_pages, old_catalog, _ = sync_exam_pages(
        TwcRecruitProvider(client),
        [("twc-recruit-115", 2026)],
        store,
        [],
        "",
    )
    write_provider_state(provider, old_pages, old_catalog, [], [], None)
    client, _ = configured_client(monkeypatch, fact, data)
    pages, catalog, _ = sync_exam_pages(
        TwcRecruitProvider(client),
        [("twc-recruit-115", 2026)],
        store,
        [],
        "",
        previous_pages=old_pages,
    )
    write_provider_state(provider, pages, catalog, [], [], None)
    assert len(load_source_revisions(provider)) == 3
    path = native_archive_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(catalogue_document(fact)))
    assert validate_native_archives(tmp_path, verify_mirror=True) == 1
    missing = catalog.papers[0]
    pages[0].papers = [
        paper
        for paper in pages[0].papers
        if (paper.category_code, paper.subject_code)
        != (missing.category_code, missing.subject_code)
    ]
    catalog.papers.remove(missing)
    write_provider_state(provider, pages, catalog, [], [], None)
    with pytest.raises(ValueError, match="paper/category anchor"):
        validate_native_archives(tmp_path, verify_mirror=True)


def test_unreviewed_archive_cannot_acquire_occupation_contexts(tmp_path, monkeypatch):
    fact, data = make_archive({"原目錄/題目.pdf": PDF})
    client, _ = configured_client(monkeypatch, fact, data)
    pages, catalog, _ = sync_exam_pages(
        TwcRecruitProvider(client),
        [("twc-recruit-115", 2026)],
        MirrorStore(tmp_path / "mirror"),
        [],
        "",
    )
    pages[0].papers[0].category_code = "115-unreviewed-job"
    catalog.papers[0].category_code = "115-unreviewed-job"
    provider = provider_paths(tmp_path, "twc_recruit")
    write_provider_state(provider, pages, catalog, [], [], None)
    path = native_archive_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(catalogue_document(fact)))
    with pytest.raises(ValueError, match="lacks reviewed category evidence"):
        validate_native_archives(tmp_path, verify_mirror=True)


@pytest.mark.repo_data
def test_reviewed_historical_matrix_keeps_changed_routes_and_source_revisions():
    facts = native_archives()
    assert sum(len(fact.categories) for fact in facts) == 49
    assert (
        sum(
            len(category.native_categories)
            for fact in facts
            for category in fact.categories
            if not category.review_reason
        )
        == 75
    )
    by_year = {fact.year_roc: fact for fact in facts}
    assert all(not by_year[year].categories for year in (103, 104, 105, 106, 107, 108))
    eleven = [
        member
        for member in by_year[111].members
        if "operating-a" in member.category_ids and member.paper_slot == "professional-1"
    ]
    twelve = [
        member
        for member in by_year[112].members
        if "operating-a" in member.category_ids and member.paper_slot == "professional-1"
    ]
    industry = [
        member
        for member in by_year[112].members
        if "operating-a-industry" in member.category_ids and member.paper_slot == "professional-1"
    ]
    assert all("物理化學" in member.subject_label for member in eleven)
    assert all("電機(工)機械" in member.subject_label for member in twelve)
    assert all(
        "物理化學" in member.subject_label and "operating-a" not in member.category_ids
        for member in industry
    )
    assert all(
        "operating-a-industry" not in member.category_ids
        for fact in facts
        for member in fact.members
        if member.paper_slot == "common"
    )
    assert validate_native_archives(ROOT) == 11
