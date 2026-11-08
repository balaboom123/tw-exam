"""Native teacher posts, shared booklets, annual stages and retained originals."""

import copy
import hashlib
import json
from collections import Counter
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.bundler import build_bundles, validate_public_materials
from app.models import NormalizedCatalog, ParsedPaper, SourceExamPage
from app.normalizer import normalize_papers, renormalize_catalog
from app.providers.base import DownloadedFile
from app.providers.teacher_recruit_taoyuan_elementary.client import (
    ANSWER_URL,
    TaoyuanElementaryRecruitClient,
    parse_answer_page,
)
from app.providers.teacher_recruit_taoyuan_elementary.evidence import (
    CANONICAL_CATEGORY,
    PROVIDER_ID,
    annual_exams,
    legacy_category_targets,
    native_papers_path,
    project_native_page,
    read_annual_exams,
    reviewed_native_revisions,
    validate_native_papers,
    validate_public_papers,
)
from app.providers.teacher_recruit_taoyuan_elementary.provider import (
    TaoyuanElementaryRecruitProvider,
)
from app.storage import MirrorStore
from app.sync import sync_exam_pages

ROOT = Path(__file__).resolve().parents[1]


def acquisition_page(exam):
    return SourceExamPage(
        exam.source_exam_id,
        exam.year_roc + 1911,
        exam.year_roc,
        f"{exam.year_roc}年度{CANONICAL_CATEGORY}",
        [],
        [
            ParsedPaper(
                CANONICAL_CATEGORY,
                str(exam.year_roc),
                f.subject_code,
                f.subject_name_raw,
                {f.file_type: f.source_url},
            )
            for f in exam.files
        ],
        provider_id=PROVIDER_ID,
    )


def normalize(page, checksums):
    metadata = {
        (p.category_code, p.subject_code, role): {"checksum": checksums[url]}
        for p in page.papers
        for role, url in p.files.items()
    }
    return normalize_papers(
        page.source_exam_id,
        page.year_ad,
        page.exam_name_raw,
        page.papers,
        [],
        "",
        metadata,
        provider_id=PROVIDER_ID,
    )


def reviewed_catalog():
    result = []
    for exam in annual_exams():
        checksums = {f.source_url: f.checksum for f in exam.files}
        catalog = normalize(project_native_page(acquisition_page(exam), checksums), checksums)
        assert not catalog.review_queue
        result.extend(catalog.papers)
    return result


def test_native_post_boundaries_and_weighted_shared_papers_remain_distinct():
    exam = annual_exams()[0]
    assert len(exam.jobs) == 15
    rows = reviewed_catalog()
    assert len(rows) == 90
    assert len({p.checksum for p in rows}) == 21
    assert len({p.bundle_id for p in rows}) == len({p.bundle_name for p in rows}) == 15
    assert Counter(p.category_code for p in rows) == {f"{i:02d}": 6 for i in range(1, 16)}
    assert {p.level_id for p in rows} == {"not-applicable"}
    assert {p.stage_id for p in rows} == {"stage-1"}
    assert {p.classification_confidence for p in rows} == {"high"}
    assert all(not p.variant_ids and "初試（筆試）" in p.bundle_name for p in rows)

    def subjects(code):
        return {p.subject_name_raw for p in rows if p.category_code == code}

    assert subjects("01") == {"語文A", "教育A"}
    assert subjects("06") == {"語文B", "教育A"}
    assert subjects("10") == {"語文及輔導C", "教育B"}
    assert subjects("11") == {"語文A", "教育C"}
    assert subjects("12") == {"語文A", "教育D"}
    assert subjects("13") == subjects("14") == subjects("15") == subjects("01")
    assert [b.weight for b in exam.jobs[9].booklets] == [60, 40]
    assert all(
        [b.weight for b in j.booklets] == [50, 50] for j in exam.jobs if j.category_code != "10"
    )
    assert Counter(p.file_type for p in rows) == {
        "question": 30,
        "answer": 30,
        "corrected_answer": 30,
    }
    validate_public_papers(rows, complete_history=True)
    validate_public_materials(rows)


@pytest.mark.parametrize(
    "field",
    [
        "event",
        "year",
        "programme",
        "category",
        "category-label",
        "subject",
        "subject-label",
        "URL",
        "checksum",
        "material",
        "role",
        "other-native-booklet",
    ],
)
def test_unreviewed_context_cannot_borrow_a_reviewed_native_identity(field):
    rows = reviewed_catalog()
    original = rows[0]
    changes = {
        "event": dict(source_exam_id="teacher-recruit-taoyuan-elementary-other-115"),
        "year": dict(year_roc=116),
        "programme": dict(exam_name_raw="其他教師甄選"),
        "category": dict(category_code="06"),
        "category-label": dict(category_raw="其他教師"),
        "subject": dict(subject_code="subject-00000000"),
        "subject-label": dict(subject_name_raw="另一科目"),
        "URL": dict(download_url_source=original.download_url_source + "&changed=1"),
        "checksum": dict(checksum="0" * 64),
        "material": dict(
            source_material=replace(
                original.source_material, date=replace(original.source_material.date, year_ad=2025)
            )
        ),
        "role": dict(file_type="answer"),
        # A real reviewed English paper is still wrong for a general-teacher post.
        "other-native-booklet": dict(category_code="06", category_raw="普通班英語專長教師"),
    }
    # Choose Language A, which is not applicable to category 06.
    if field in {"category", "other-native-booklet"}:
        original = next(
            p for p in rows if p.category_code == "01" and p.subject_name_raw == "語文A"
        )
    altered = replace(original, **changes[field])
    with pytest.raises(ValueError, match="Taoyuan publication"):
        validate_public_materials([altered])
    if field != "role":
        changed = renormalize_catalog(NormalizedCatalog([altered], []), [])
        assert changed.papers[0].classification_confidence == "review"
        assert changed.papers[0].stage_id == "unknown"


def test_complete_history_and_partial_bundle_guards_fail_before_archive_mutation(tmp_path):
    rows = reviewed_catalog()
    partial = [p for p in rows if p.category_code == "10"]
    validate_public_materials(partial)
    for missing in (rows[1:], [p for p in rows if p.category_code != "10"], []):
        with pytest.raises(ValueError, match="omitted or duplicated"):
            validate_public_papers(missing, complete_history=True)
    with pytest.raises(ValueError, match="omitted or duplicated"):
        validate_public_materials([*rows, rows[0]])
    with pytest.raises(ValueError, match="omitted or duplicated"):
        build_bundles(
            tmp_path / "must-not-exist", tmp_path / "mirror", NormalizedCatalog(partial[1:], []), ""
        )
    assert not (tmp_path / "must-not-exist").exists()


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "changed-bytes", "changed-year"])
def test_changed_listing_or_payload_cannot_replace_retained_source_state(mutation):
    exam = annual_exams()[0]
    raw = acquisition_page(exam)
    checksums = {f.source_url: f.checksum for f in exam.files}
    if mutation == "missing":
        raw.papers.pop()
    if mutation == "duplicate":
        raw.papers.append(copy.deepcopy(raw.papers[0]))
    if mutation == "changed-bytes":
        checksums[exam.files[0].source_url] = "0" * 64
    if mutation == "changed-year":
        raw.year_ad += 1
    with pytest.raises(ValueError, match="Taoyuan"):
        project_native_page(raw, checksums)


def test_parser_and_rolling_source_reject_conflicting_urls_and_cross_year_reuse():
    first = '<a href="download_file.aspx?ids=one">115桃教育A-試題.pdf</a>'
    assert len(parse_answer_page(ANSWER_URL, first + first)) == 1
    with pytest.raises(ValueError, match="Conflicting Taoyuan"):
        parse_answer_page(ANSWER_URL, first + first.replace("ids=one", "ids=two"))
    for html in (first.replace("115桃", "116桃"), first + first.replace("115桃", "116桃")):
        client = TaoyuanElementaryRecruitClient(answer_html=html)
        with pytest.raises(ValueError, match="rolling source year changed"):
            client.fetch_exam_page("teacher-recruit-taoyuan-elementary-115", 2026)


def test_shared_mirror_slots_and_annual_rule_use_one_fresh_observation_per_command(
    tmp_path, monkeypatch
):
    exam = annual_exams()[0]
    payloads = {
        f.source_url: b"%PDF-1.7 native fixture " + str(i).encode()
        for i, f in enumerate(exam.files)
    }
    payloads[exam.rule_url] = b"%PDF-1.7 native annual rule"
    altered = replace(
        exam,
        files=tuple(
            replace(f, checksum=hashlib.sha256(payloads[f.source_url]).hexdigest())
            for f in exam.files
        ),
        rule_checksum=hashlib.sha256(payloads[exam.rule_url]).hexdigest(),
    )
    for module in ("evidence", "provider"):
        monkeypatch.setattr(
            f"app.providers.teacher_recruit_taoyuan_elementary.{module}.annual_exams",
            lambda: (altered,),
        )
    client = Mock()
    client.fetch_exam_page.side_effect = lambda *_: acquisition_page(altered)
    client.download_file.side_effect = lambda u: DownloadedFile(
        payloads[u], "application/pdf", "native.pdf"
    )
    for _ in range(2):
        provider = TaoyuanElementaryRecruitProvider(client)
        pages, catalog, failures = sync_exam_pages(
            provider,
            [(exam.source_exam_id, 2026)],
            MirrorStore(tmp_path / "mirror"),
            [],
            "",
            download_attachments=False,
        )
        assert not failures and not catalog.review_queue
        assert len(pages[0].papers) == len(catalog.papers) == 90
        assert len({p.storage_key for p in catalog.papers}) == 21
        assert len(list((tmp_path / "mirror").rglob("*.pdf"))) == 22
        assert pages[0].attachments[0].checksum == altered.rule_checksum
        assert pages[0].attachments[0].source_material.kind == "reference"
    assert Counter(c.args[0] for c in client.download_file.call_args_list) == {
        u: 2 for u in payloads
    }
    # A changed annual rule fails the whole event before sync can save new facts.
    payloads[exam.rule_url] = b"%PDF-1.7 unexpected replacement rule"
    _, catalog, failures = sync_exam_pages(
        TaoyuanElementaryRecruitProvider(client),
        [(exam.source_exam_id, 2026)],
        MirrorStore(tmp_path / "mirror"),
        [],
        "",
    )
    assert failures and not catalog.papers
    assert "annual rule checksum changed" in failures[0].message


@pytest.mark.parametrize(
    "mutation",
    ["weight", "orphan-booklet", "missing-role", "incomplete-visual-review", "native-code"],
)
def test_invalid_annual_evidence_is_rejected(tmp_path, mutation):
    payload = json.loads(native_papers_path(ROOT).read_text())
    event = payload["events"][0]
    if mutation == "weight":
        event["jobs"][9]["booklets"][0]["weight"] = 50
    if mutation == "orphan-booklet":
        event["jobs"][5]["booklets"][0] = copy.deepcopy(event["jobs"][0]["booklets"][0])
    if mutation == "missing-role":
        event["files"].pop()
    if mutation == "incomplete-visual-review":
        event["files"][0]["inspected_pages"].pop()
    if mutation == "native-code":
        event["jobs"][0]["category_code"] = "115"
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="Taoyuan"):
        read_annual_exams(path)


def test_old_generic_reference_stays_immutable_and_requires_exact_native_bytes():
    exam = annual_exams()[0]
    file = exam.files[0]
    record = dict(
        source_exam_id=exam.source_exam_id,
        year_roc=115,
        download_url_source=file.source_url,
        checksum=file.checksum,
        category_raw="old generic",
    )
    entry = dict(id="old-generic", record_type="paper", source_record=record)
    assert reviewed_native_revisions([entry]) == ["old-generic"]
    assert (
        reviewed_native_revisions([dict(entry, source_record=dict(record, checksum="0" * 64))])
        == []
    )
    assert record["category_raw"] == "old generic"


def legacy_papers(rows):
    originals = {}
    for paper in rows:
        originals.setdefault(
            paper.download_url_source,
            replace(
                paper,
                category_code=str(paper.year_roc),
                category_raw=CANONICAL_CATEGORY,
                exam_name_raw=f"{paper.year_roc}學年度{CANONICAL_CATEGORY}",
                source_material=None,
                schema_version=2,
            ),
        )
    return list(originals.values())


@pytest.mark.parametrize("field", ["year", "event", "category", "label", "role", "checksum"])
def test_legacy_navigation_requires_the_exact_generic_source_context(field):
    rows = reviewed_catalog()
    old = next(
        p
        for p in legacy_papers(rows)
        if p.subject_name_raw == "教育A" and p.file_type == "question"
    )
    assert len(legacy_category_targets(old, rows)) == 12
    assert legacy_category_targets(old, [p for p in rows if p.category_code != "01"]) == []
    changes = {
        "year": dict(year_roc=116),
        "event": dict(source_exam_id="other-event"),
        "category": dict(category_code="01"),
        "label": dict(category_raw="另一甄選"),
        "role": dict(file_type="answer"),
        "checksum": dict(checksum="0" * 64),
    }
    assert legacy_category_targets(replace(old, **changes[field]), rows) == []


def test_saved_generic_landing_page_follows_all_native_category_successors(tmp_path, monkeypatch):
    from app.paths import site_paths
    from app.retained_downloads import derive_compatibility_pages

    rows = reviewed_catalog()
    old = legacy_papers(rows)
    original = copy.deepcopy(old)
    frozen = SimpleNamespace(
        provider_id=PROVIDER_ID, bundle_id="old-generic-taoyuan",
        asset_name="old-generic-taoyuan.zip", checksum="f" * 64, validate=lambda _site_id: None,
    )
    monkeypatch.setattr(
        "app.source_revisions.load_source_revisions",
        lambda *_: [dict(record_type="paper", source_record=vars(p)) for p in old],
    )
    # Publication reads the recorded sources of a frozen archive, never the ZIP.
    monkeypatch.setattr(
        "app.retained_downloads.load_retained_sources",
        lambda _site: {frozen.asset_name: {
            "checksum": frozen.checksum,
            "canonical_name": CANONICAL_CATEGORY,
            "papers": [vars(p) for p in old],
        }},
    )
    monkeypatch.setattr("app.retained_downloads._check_retained_papers", lambda *a, **kw: None)
    bundles = [SimpleNamespace(bundle_id=k) for k in sorted({p.bundle_id for p in rows})]
    expected = {
        "id": frozen.bundle_id,
        "name": CANONICAL_CATEGORY,
        "successorIds": sorted(b.bundle_id for b in bundles),
    }
    site = site_paths(tmp_path, "default")
    assert derive_compatibility_pages(tmp_path, site, [frozen], rows, bundles) == [expected]
    assert derive_compatibility_pages(tmp_path, site, [frozen], rows, bundles[1:]) == [
        dict(expected, successorIds=expected["successorIds"][1:], partial=True)
    ]
    assert old == original
    old[0] = replace(old[0], checksum="0" * 64)
    assert derive_compatibility_pages(tmp_path, site, [frozen], rows, bundles) == []


@pytest.mark.repo_data
def test_checked_in_native_anchors_and_full_recruitment_history():
    from app.paths import provider_paths
    from app.state import load_provider_state

    assert validate_native_papers(ROOT) == 22
    _, catalog, _ = load_provider_state(provider_paths(ROOT, PROVIDER_ID))
    validate_public_papers(catalog.papers, complete_history=True)
