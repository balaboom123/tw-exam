"""Each exact native PDF resolves to its own primary rule without merging programmes."""

import hashlib
import json
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from jsonschema import Draft202012Validator, ValidationError

from app.models import (
    NormalizedCatalog,
    NormalizedPaper,
    ParsedPaper,
    SourceExamPage,
    to_plain_data,
)
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
CASES = [(a, p) for a in written_exams() for p in a.papers]


def context(exam, paper):
    return {
        "source_exam_id": exam.source_exam_id,
        "year_roc": exam.year_roc,
        "exam_name_raw": exam.exam_name_raw,
        "category_code": str(exam.year_roc),
        "category_raw": "台糖新進工員甄試",
        **asdict(paper),
    }


@pytest.mark.parametrize(
    "exam,paper", CASES, ids=[f"{a.year_roc}/{p.subject_code}" for a, p in CASES]
)
def test_annual_stage_requires_every_exact_native_original(exam, paper):
    values = context(exam, paper)
    assert resolve_written_exam(**values) == exam
    for field, wrong in (
        ("source_exam_id", "taisugar-recruit-999"),
        ("year_roc", 999),
        ("exam_name_raw", exam.exam_name_raw + "更正"),
        ("category_code", "999"),
        ("category_raw", "博士甄試"),
        ("subject_code", "taisugar-file-unreviewed"),
        ("title", paper.title + "更正"),
        ("source_url", paper.source_url + "?v=2"),
        ("checksum", "0" * 64),
    ):
        assert resolve_written_exam(**{**values, field: wrong}) is None
    reference = exam.rule_attachment()
    assert reference.file_type == "instructions"
    assert reference.source_material.kind == "reference"
    assert reference.source_material.date.basis == "publication_year"
    assert exam.material.kind == "administered" and exam.material.needs_review


@pytest.mark.repo_data
def test_fresh_and_retained_normalization_agree_without_guessing_occupation():
    _, catalog, _ = load_provider_state(provider_paths(ROOT, "taisugar_recruit"))
    for exam, native in CASES:
        paper = next(p for p in catalog.papers if p.checksum == native.checksum)
        retained = renormalize_catalog(NormalizedCatalog([paper], []), []).papers[0]
        fresh = normalize_papers(
            exam.source_exam_id,
            exam.year_roc + 1911,
            exam.exam_name_raw,
            [
                ParsedPaper(
                    "台糖新進工員甄試",
                    str(exam.year_roc),
                    native.subject_code,
                    native.title,
                    {"source_document": native.source_url},
                    {
                        "source_document": {
                            "checksum": native.checksum,
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
            assert reviewed.file_type == paper.file_type
            assert reviewed.source_material == exam.material
            assert reviewed.download_url_source == paper.download_url_source
            assert reviewed.storage_key == paper.storage_key and reviewed.checksum == paper.checksum
    assert validate_written_exams(ROOT) == 124


@pytest.mark.repo_data
def test_one_programmes_rule_does_not_stage_unreviewed_members_in_the_same_source_event():
    _, catalog, _ = load_provider_state(provider_paths(ROOT, "taisugar_recruit"))
    selected = [p for p in catalog.papers if p.year_roc == 111]
    assert len(selected) == 30
    assert sum(p.stage_id == "stage-1" for p in selected) == 19
    assert sum(p.stage_id == "unknown" for p in selected) == 11
    school = [p for p in selected if "638382326767185760.zip#" in p.download_url_source]
    assert len(school) == 11
    assert all(p.stage_id == "unknown" and p.source_material.needs_review for p in school)
    assert sum(p.file_type == "question" for p in school) == 1
    assert sum(p.file_type == "question_answer" for p in school) == 10


@pytest.mark.repo_data
def test_later_sitting_year_keeps_source_partitions_and_separate_school_uncertainty():
    _, catalog, _ = load_provider_state(provider_paths(ROOT, "taisugar_recruit"))
    exam = next(a for a in written_exams() if a.year_roc == 112)
    assert exam.written_test_date == "2024-01-27"
    assert exam.rule_publication_date == "2023-11-10"
    assert exam.material.date.year_ad == 2024
    selected = [p for p in catalog.papers if p.year_roc == 112]
    keys = {(p.subject_code, p.source_url, p.checksum) for p in exam.papers}
    general = [p for p in selected if (p.subject_code, p.download_url_source, p.checksum) in keys]
    school = [p for p in selected if p not in general]
    assert len(general) == 45 and len(school) == 17
    assert sum(p.file_type == "question" for p in general) == 23
    assert sum(p.file_type == "answer" for p in general) == 22
    assert sum(p.file_type == "question" for p in school) == 9
    assert sum(p.file_type == "answer" for p in school) == 8
    for paper in general:
        assert paper.stage_id == "stage-1" and paper.source_material == exam.material
        assert paper.year_roc == 112 and "/112/taisugar-recruit-112/" in paper.storage_key
    assert all(p.stage_id == "unknown" and p.source_material.needs_review for p in school)
    assert len({p.download_url_source.split("#")[0] for p in general}) == 2
    assert len({p.download_url_source.split("#")[0] for p in school}) == 2


@pytest.mark.parametrize("corrupt_rule", [False, True])
def test_sync_retains_one_checksum_guarded_rule_for_ten_reviewed_papers(
    tmp_path, monkeypatch, corrupt_rule
):
    from app.providers.taisugar_recruit import provider as provider_module

    rule_bytes = b"%PDF-1.4\nreference rule fixture\n%%EOF"
    exam = replace(
        next(a for a in written_exams() if a.year_roc == 109),
        rule_checksum=hashlib.sha256(rule_bytes).hexdigest(),
    )
    assert len(exam.papers) == 10
    monkeypatch.setattr(provider_module, "written_exams", lambda: (exam,))
    # A newly observed original in that same year still needs its own review.
    url = "https://www.taisugar.com.tw/upload/UserFiles/News/9972/unreviewed.pdf"
    page = SourceExamPage(
        exam.source_exam_id,
        exam.year_roc + 1911,
        exam.year_roc,
        exam.exam_name_raw,
        [],
        [
            ParsedPaper(
                "台糖新進工員甄試",
                str(exam.year_roc),
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
        if source == exam.rule_url
        else b"%PDF-1.4\nunreviewed native\n%%EOF",
        "application/pdf",
        "original.pdf",
    )
    provider = TaisugarRecruitProvider(client)
    pages, normalized, failures = sync_exam_pages(
        provider,
        [(exam.source_exam_id, exam.year_roc + 1911)],
        MirrorStore(tmp_path / "mirror"),
        [],
        "",
        download_attachments=False,
    )
    assert normalized.papers[0].stage_id == "unknown"
    assert normalized.papers[0].file_type == "source_document"
    assert len(pages[0].attachments) == 1 and client.download_file.call_count == 2
    reference = pages[0].attachments[0]
    assert reference.file_type == "instructions"
    if corrupt_rule:
        assert len(failures) == 1 and failures[0].file_type == "instructions"
        assert not reference.checksum and not reference.storage_key
    else:
        assert not failures and reference.checksum == exam.rule_checksum
        stored = tmp_path / "mirror" / reference.storage_key
        assert stored.read_bytes() == rule_bytes
        client.download_file.side_effect = lambda source: DownloadedFile(
            rule_bytes + b"changed" if source == exam.rule_url else b"%PDF-1.4\nnative\n%%EOF",
            "application/pdf",
            "original.pdf",
        )
        _, _, failed = sync_exam_pages(
            provider,
            [(exam.source_exam_id, exam.year_roc + 1911)],
            MirrorStore(tmp_path / "mirror"),
            [],
            "",
            previous_pages=pages,
        )
        assert len(failed) == 1 and failed[0].file_type == "instructions"
        assert stored.read_bytes() == rule_bytes


@pytest.mark.parametrize("history_only", [False, True])
def test_every_paper_and_shared_rule_is_verified_in_current_or_history(
    tmp_path, monkeypatch, history_only
):
    from app import source_revisions, state

    pages, catalog, _ = load_provider_state(provider_paths(ROOT, "taisugar_recruit"))
    by_checksum = {p.checksum: p for p in catalog.papers}
    current_pages, current_papers, revisions, targets, events = [], [], [], [], []

    def store(key, data):
        path = tmp_path / "mirror" / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        targets.append(path)
        return hashlib.sha256(data).hexdigest()

    for exam in written_exams():
        rule_key = f"providers/taisugar_recruit/{exam.year_roc}/{exam.rule_checksum}/rule.pdf"
        rule_checksum = store(
            rule_key, b"%PDF-1.4\nrule " + exam.rule_checksum.encode() + b"\n%%EOF"
        )
        revised_papers = []
        for native in exam.papers:
            original = by_checksum[native.checksum]
            checksum = store(
                original.storage_key, b"%PDF-1.4\npaper " + native.checksum.encode() + b"\n%%EOF"
            )
            revised_papers.append(replace(native, checksum=checksum))
            paper = replace(original, checksum=checksum)
            if history_only:
                revisions.append(
                    {
                        "record_type": "paper",
                        "source_record": to_plain_data(paper),
                        "blob_storage_key": paper.storage_key,
                    }
                )
            else:
                current_papers.append(paper)
        revised = replace(exam, papers=tuple(revised_papers), rule_checksum=rule_checksum)
        events.append(asdict(revised))
        reference = revised.rule_attachment()
        reference.storage_key, reference.checksum = rule_key, rule_checksum
        page = replace(
            next(p for p in pages if p.year_roc == exam.year_roc), attachments=[reference]
        )
        if history_only:
            revisions.append(
                {
                    "record_type": "attachment",
                    "source_record": {
                        **to_plain_data(reference),
                        "source_exam_id": exam.source_exam_id,
                        "year_roc": exam.year_roc,
                    },
                    "blob_storage_key": rule_key,
                }
            )
        else:
            current_pages.append(page)
    assert len(targets) == 133  # 124 native originals and nine primary rules.
    from tests.taisugar_native_fixtures import bind_parent_fixtures

    all_papers = current_papers + [
        NormalizedPaper(**r["source_record"]) for r in revisions if r["record_type"] == "paper"
    ]
    bound, parent_pages, parent_paths = bind_parent_fixtures(tmp_path, all_papers)
    urls = {
        a.download_url_source: b.download_url_source for a, b in zip(all_papers, bound, strict=True)
    }
    current_papers[:] = bound[: len(current_papers)]
    for revision in revisions:
        if revision["record_type"] == "paper":
            row = revision["source_record"]
            row["download_url_source"] = urls[row["download_url_source"]]
    for event in events:
        for native in event["papers"]:
            native["source_url"] = urls[native["source_url"]]
    if history_only:
        for page in parent_pages:
            for attachment in page.attachments:
                revisions.append(
                    {
                        "record_type": "attachment",
                        "source_record": {
                            **to_plain_data(attachment),
                            "source_exam_id": page.source_exam_id,
                            "year_roc": page.year_roc,
                        },
                        "blob_storage_key": attachment.storage_key,
                    }
                )
    else:
        current_pages.extend(parent_pages)
    targets.extend(parent_paths)
    path = annual_exams_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"schema_version": 2, "provider_id": "taisugar_recruit", "events": events})
    )
    monkeypatch.setattr(
        state,
        "load_provider_state",
        lambda _: (current_pages, NormalizedCatalog(current_papers, []), []),
    )
    monkeypatch.setattr(source_revisions, "load_source_revisions", lambda _: revisions)
    assert validate_written_exams(tmp_path, verify_mirror=True) == 124
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
        revisions[:] = [r for r in revisions if r["source_record"]["file_type"] != "instructions"]
    else:
        current_pages[0].attachments.clear()
    with pytest.raises(ValueError, match="retained original or rule anchor"):
        validate_written_exams(tmp_path)


@pytest.mark.parametrize(
    "year,publication,written,revision",
    [
        (108, "2019-05-22", "2019-07-20", "2019-07-31"),
        (109, "2020-05-12", "2020-09-12", "2020-09-14"),
        (114, "2025-01-14", "2025-03-29", "2025-04-11"),
    ],
)
def test_publication_revision_and_written_dates_remain_independent(
    tmp_path, year, publication, written, revision
):
    exam = next(a for a in written_exams() if a.year_roc == year)
    assert exam.rule_publication_date == publication
    assert exam.written_test_date == written
    assert exam.rule_revision_date == revision
    document = json.loads(annual_exams_path(ROOT).read_text())
    event = next(e for e in document["events"] if e["year_roc"] == year)
    later_year = year + 1912
    event["rule_revision_date"] = f"{later_year}-01-01"
    path = tmp_path / "later-reviewed-edition.json"
    path.write_text(json.dumps(document))
    revised = next(a for a in annual.read_written_exams(path) if a.year_roc == year)
    assert revised.material.date.year_ad == year + 1911
    assert revised.rule_attachment().source_material.date.year_ad == later_year


def test_v2_contract_rejects_missing_fields_and_invalid_evidence(tmp_path):
    document = json.loads(annual_exams_path(ROOT).read_text())
    schema = json.loads((ROOT / "schemas/taisugar-annual-written-exams-v2.schema.json").read_text())
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    validator.validate(document)
    for field in document["events"][0]:
        changed = deepcopy(document)
        del changed["events"][0][field]
        with pytest.raises(ValidationError):
            validator.validate(changed)
    for field in document["events"][0]["papers"][0]:
        changed = deepcopy(document)
        del changed["events"][0]["papers"][0][field]
        with pytest.raises(ValidationError):
            validator.validate(changed)
    for field, value in (
        ("rule_url", "https://example.com/forged.pdf"),
        ("rule_checksum", "missing"),
        ("written_test_date", "2017-02-31"),
        ("rule_pdf_pages", [2, 2]),
        ("reviewed_at", "20261028"),
        ("rule_revision_date", "2020-02-31"),
    ):
        changed = deepcopy(document)
        changed["events"][0][field] = value
        with pytest.raises(ValidationError):
            validator.validate(changed)
        path = tmp_path / "invalid.json"
        path.write_text(json.dumps(changed))
        with pytest.raises(ValueError):
            annual.read_written_exams(path)
    for duplicate_rule in [False, True]:
        changed = deepcopy(document)
        if duplicate_rule:
            changed["events"].append(deepcopy(changed["events"][0]))
        else:
            changed["events"][2]["papers"].append(deepcopy(changed["events"][2]["papers"][0]))
        path = tmp_path / "duplicate.json"
        path.write_text(json.dumps(changed))
        with pytest.raises(ValueError, match="duplicate"):
            annual.read_written_exams(path)


def test_disjoint_programmes_in_one_year_resolve_independently_of_rule_order(tmp_path, monkeypatch):
    groups = tuple(a for a in written_exams() if a.year_roc == 110)
    assert len(groups) == 2
    assert sorted(len(a.papers) for a in groups) == [11, 20]
    assert len({a.source_exam_id for a in groups}) == 1
    assert len({a.rule_url for a in groups}) == 2
    assert all(a.written_test_date == "2021-11-13" for a in groups)
    assert all(a.rule_publication_date == "2021-04-07" for a in groups)
    assert all(a.rule_revision_date == "2021-06-16" for a in groups)
    document = json.loads(annual_exams_path(ROOT).read_text())
    document["events"].reverse()
    path = tmp_path / "reordered.json"
    path.write_text(json.dumps(document))
    monkeypatch.setattr(annual, "written_exams", lambda: annual.read_written_exams(path))
    for group in groups:
        for native in group.papers:
            assert resolve_written_exam(**context(group, native)) == group


@pytest.mark.parametrize("altered_bytes", [False, True])
def test_a_native_paper_slot_cannot_be_assigned_to_two_primary_rules(tmp_path, altered_bytes):
    document = json.loads(annual_exams_path(ROOT).read_text())
    groups = [a for a in document["events"] if a["year_roc"] == 110]
    overlap = deepcopy(groups[0]["papers"][0])
    if altered_bytes:
        overlap["checksum"] = "0" * 64
        overlap["title"] += "改版"
    groups[1]["papers"].append(overlap)
    path = tmp_path / "overlap.json"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="Duplicate.*paper rule context"):
        annual.read_written_exams(path)


@pytest.mark.parametrize("same_checksum", [False, True])
def test_one_rule_url_cannot_have_two_disjoint_group_definitions(tmp_path, same_checksum):
    document = json.loads(annual_exams_path(ROOT).read_text())
    groups = [a for a in document["events"] if a["year_roc"] == 110]
    groups[1]["rule_url"] = groups[0]["rule_url"]
    if same_checksum:
        groups[1]["rule_checksum"] = groups[0]["rule_checksum"]
    path = tmp_path / "ambiguous-rule.json"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="duplicate.*annual source context"):
        annual.read_written_exams(path)


@pytest.mark.parametrize("corrupt_index", [None, 0, 1])
@pytest.mark.parametrize("warm", [False, True])
def test_same_year_rules_are_retained_once_and_checksum_guarded_independently(
    tmp_path, monkeypatch, corrupt_index, warm
):
    from app.providers.taisugar_recruit import provider as provider_module

    payloads = [b"%PDF-1.4\nprimary rule one\n%%EOF", b"%PDF-1.4\nprimary rule two\n%%EOF"]
    groups = tuple(
        replace(a, rule_checksum=hashlib.sha256(data).hexdigest())
        for a, data in zip((a for a in written_exams() if a.year_roc == 110), payloads, strict=True)
    )
    monkeypatch.setattr(provider_module, "written_exams", lambda: groups)
    page = SourceExamPage(
        groups[0].source_exam_id,
        2021,
        110,
        groups[0].exam_name_raw,
        [],
        [
            ParsedPaper(
                "台糖新進工員甄試",
                "110",
                "unreviewed",
                "new original",
                {
                    "source_document": "https://www.taisugar.com.tw/upload/UserFiles/News/10749/new.pdf"
                },
            )
        ],
        provider_id="taisugar_recruit",
    )
    client = Mock()
    client.fetch_exam_page.side_effect = lambda *_: deepcopy(page)
    rule_data = {a.rule_url: data for a, data in zip(groups, payloads, strict=True)}
    client.download_file.side_effect = lambda url: DownloadedFile(
        rule_data.get(url, b"%PDF-1.4\nnew paper\n%%EOF"), "application/pdf", "original.pdf"
    )
    provider = TaisugarRecruitProvider(client)
    mirror = MirrorStore(tmp_path / "mirror")
    previous = []
    if warm:
        previous, _, failures = sync_exam_pages(
            provider, [(page.source_exam_id, 2021)], mirror, [], "", download_attachments=False
        )
        assert not failures
    if corrupt_index is not None:
        rule_data[groups[corrupt_index].rule_url] += b"changed"
    pages, catalog, failures = sync_exam_pages(
        provider,
        [(page.source_exam_id, 2021)],
        mirror,
        [],
        "",
        previous_pages=previous,
        download_attachments=False,
    )
    assert len(pages[0].attachments) == 2
    assert {a.download_url_source for a in pages[0].attachments} == set(rule_data)
    assert len(failures) == (corrupt_index is not None)
    assert catalog.papers[0].stage_id == "unknown"
    assert catalog.papers[0].file_type == "source_document"
    for index, (exam, original) in enumerate(zip(groups, payloads, strict=True)):
        attachment = next(a for a in pages[0].attachments if a.download_url_source == exam.rule_url)
        if index == corrupt_index:
            # A changed response fails closed even when an old file exists.
            # Its retained bytes survive, but cannot mark this refresh successful.
            assert not attachment.storage_key and not attachment.checksum
            if warm:
                old = next(
                    a for a in previous[0].attachments if a.download_url_source == exam.rule_url
                )
                assert (tmp_path / "mirror" / old.storage_key).read_bytes() == original
        else:
            assert attachment.checksum == exam.rule_checksum
            assert (tmp_path / "mirror" / attachment.storage_key).read_bytes() == original
    # Already retained rules are never appended a second time.
    client.fetch_exam_page.side_effect = lambda *_: deepcopy(pages[0])
    assert len(provider.fetch_exam_page(page.source_exam_id, 2021).attachments) == 2


@pytest.mark.repo_data
def test_general_annual_rule_does_not_approve_school_papers_in_one_combined_zip():
    _, catalog, _ = load_provider_state(provider_paths(ROOT, "taisugar_recruit"))
    exam = next(a for a in written_exams() if a.year_roc == 114)
    selected = [p for p in catalog.papers if p.year_roc == 114]
    keys = {(p.subject_code, p.source_url, p.checksum) for p in exam.papers}
    general = [p for p in selected if (p.subject_code, p.download_url_source, p.checksum) in keys]
    school = [p for p in selected if p not in general]
    assert len(general) == 16 and len(school) == 5
    assert len({p.download_url_source.split("#")[0] for p in selected}) == 1
    assert len({p.checksum for p in selected}) == 21
    assert sum(p.file_type == "question" for p in general) == 1
    assert sum(p.file_type == "question" for p in school) == 1
    for paper in general:
        assert paper.stage_id == "stage-1" and paper.source_material == exam.material
        assert paper.track_id == "taisugar-worker-unknown"
    assert all(p.stage_id == "unknown" and p.source_material.needs_review for p in school)
