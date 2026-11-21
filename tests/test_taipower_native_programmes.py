from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from collections import Counter
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from app.bundler import _bundle_arcname, build_bundles
from app.models import NormalizedCatalog, SourceExamPage
from app.normalizer import normalize_papers, renormalize_catalog
from app.providers.base import DownloadedFile
from app.providers.taipower_recruit.client import (
    TaipowerRecruitClient,
    TaipowerRecruitDownload,
    TaipowerRecruitEntry,
)
from app.providers.taipower_recruit.evidence import (
    NativeCatalogue,
    legacy_category_targets,
    native_catalogue,
    native_programmes_path,
    project_native_page,
    read_native_catalogue,
    stable_subject_code,
    validate_native_programmes,
    validate_public_papers,
)
from app.providers.taipower_recruit.provider import TaipowerRecruitProvider
from app.publication_metadata import (
    derive_public_metadata,
    public_coverage_notes,
    publication_paper_subject,
)
from app.storage import MirrorStore
from app.sync import _mirror_prefix_for_paper, sync_exam_pages

ROOT = Path(__file__).resolve().parents[1]


def source_client(catalogue: NativeCatalogue, event_id: str) -> TaipowerRecruitClient:
    event = catalogue.events_by_id[event_id]
    suffix = event_id.removeprefix("taipower-recruit-").split("-")
    month = int(suffix[1]) if len(suffix) == 2 else None
    client = TaipowerRecruitClient()
    client._cached_entries = [
        TaipowerRecruitEntry(
            event.year_roc,
            event.year_roc + 1911,
            event.exam_name_raw,
            month,
            [TaipowerRecruitDownload(file.label, file.source_url)],
        )
        for file in event.files
    ]
    return client


def normalized_page(catalogue: NativeCatalogue, event_id: str) -> NormalizedCatalog:
    event = catalogue.events_by_id[event_id]
    page = project_native_page(
        source_client(catalogue, event_id).fetch_exam_page(event_id, event.year_roc + 1911)
    )
    for paper in page.papers:
        for role, url in paper.files.items():
            file = event.files_by_url[url]
            paper.mirror_files[role] = {
                "checksum": file.checksum,
                "storage_key": _mirror_prefix_for_paper(page, paper, role) + ".pdf",
            }
    return normalize_papers(
        source_exam_id=event_id,
        year_ad=event.year_roc + 1911,
        exam_name_raw=page.exam_name_raw,
        papers=page.papers,
        alias_rules=[],
        mirror_base_url="",
        mirror_metadata={},
        provider_id="taipower_recruit",
    )


@pytest.fixture(scope="module")
def complete_native_catalogue() -> NormalizedCatalog:
    catalogue = native_catalogue()
    papers = []
    reviews = []
    for exam in catalogue.annual_exams:
        normalized = normalized_page(catalogue, exam.source_exam_id)
        papers.extend(normalized.papers)
        reviews.extend(normalized.review_queue)
    return NormalizedCatalog(papers, reviews)


def test_contract_covers_originals_and_complete_reviewed_annual_population() -> None:
    catalogue = read_native_catalogue(native_programmes_path(ROOT))
    assert len(catalogue.source_events) == 23
    assert sum(len(event.files) for event in catalogue.source_events) == 610
    assert len(catalogue.annual_exams) == 16
    assert sum(len(exam.booklets) for exam in catalogue.annual_exams) == 245
    assert sum(len(exam.jobs) for exam in catalogue.annual_exams) == 245
    assert len(catalogue.occupations) == 26
    unresolved = [event for event in catalogue.source_events if event.unresolved_occupation_reason]
    assert [event.year_roc for event in unresolved] == [90, 92, 94, 95, 96, 97, 99]
    assert sum(len(event.files) for event in unresolved) == 122
    assert [event.year_roc for event in unresolved if event.selection_stage == "unknown"] == [
        90,
        94,
        96,
    ]


def test_all_reviewed_original_keys_and_mirror_paths_survive_reordered_listing() -> None:
    catalogue = native_catalogue()
    for event in catalogue.source_events:
        client = source_client(catalogue, event.source_exam_id)
        first = client.fetch_exam_page(event.source_exam_id, event.year_roc + 1911)
        client._cached_entries = list(reversed(client._cached_entries or []))
        second = client.fetch_exam_page(event.source_exam_id, event.year_roc + 1911)

        def bindings(page: SourceExamPage) -> dict[str, tuple[str, str]]:
            return {
                url: (paper.subject_code, _mirror_prefix_for_paper(page, paper, role))
                for paper in page.papers
                for role, url in paper.files.items()
            }

        expected = {
            file.source_url: (
                file.subject_code,
                f"providers/taipower_recruit/{event.year_roc}/{event.source_exam_id}/"
                f"{event.source_category_code}/{file.subject_code}/{file.source_file_type}",
            )
            for file in event.files
        }
        assert bindings(first) == bindings(second) == expected


def test_new_source_reference_does_not_renumber_its_siblings() -> None:
    catalogue = native_catalogue()
    event = catalogue.events_by_id["taipower-recruit-115"]
    original = event.files[0]
    inserted = "https://www.taipower.com.tw/media/new/new-paper.pdf?mediaDL=true"
    assert stable_subject_code(event.source_exam_id, original.source_url) == original.subject_code
    fresh = stable_subject_code(event.source_exam_id, inserted)
    assert fresh.startswith("src-") and len(fresh) == 24
    assert fresh == stable_subject_code(event.source_exam_id, inserted)
    assert fresh != stable_subject_code(
        event.source_exam_id, inserted.replace("new-paper.pdf", "another-paper.pdf")
    )
    client = source_client(catalogue, event.source_exam_id)
    assert client._cached_entries is not None
    client._cached_entries.insert(
        0,
        TaipowerRecruitEntry(
            115,
            2026,
            "115年度新科目",
            None,
            [TaipowerRecruitDownload("115年度新科目試題", inserted)],
        ),
    )
    page = client.fetch_exam_page(event.source_exam_id, 2026)
    assert page.papers[0].subject_code == fresh
    assert (
        next(p for p in page.papers if original.source_url in p.files.values()).subject_code
        == original.subject_code
    )
    with pytest.raises(ValueError, match="complete native population"):
        project_native_page(page)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/media/native.pdf",
        "http://www.taipower.com.tw/media/native.pdf",
        "https://www.taipower.com.tw/login.pdf",
        "https://www.taipower.com.tw/media/native.zip",
        "https://www.taipower.com.tw/media/native.pdf#different",
    ],
)
def test_new_source_keys_reject_foreign_or_non_native_routes(url: str) -> None:
    with pytest.raises(ValueError, match="official native PDF"):
        stable_subject_code("taipower-recruit-116", url)


def test_115_occupations_share_files_and_population_categories_share_one_bundle() -> None:
    catalogue = native_catalogue()
    event = catalogue.events_by_id["taipower-recruit-115"]
    page = project_native_page(
        source_client(catalogue, event.source_exam_id).fetch_exam_page(event.source_exam_id, 2026)
    )
    assert len(page.papers) == 84
    assert len({p.category_code for p in page.papers}) == 14
    prefixes = {_mirror_prefix_for_paper(page, p, role) for p in page.papers for role in p.files}
    assert len(prefixes) == 26
    assert all(p.source_category_code == "115" for p in page.papers)
    normal = normalized_page(catalogue, event.source_exam_id)
    ordinary = [p for p in normal.papers if p.category_code == "job-general-administration"]
    disabled = [
        p for p in normal.papers if p.category_code == "job-general-administration-disability"
    ]
    assert len(ordinary) == len(disabled) == 6
    assert {p.storage_key for p in ordinary} == {p.storage_key for p in disabled}
    assert {p.bundle_id for p in ordinary} == {p.bundle_id for p in disabled}
    assert {tuple(p.variant_ids) for p in disabled} == {()}
    assert {p.stage_id for p in normal.papers} == {"stage-1"}
    assert {p.level_id for p in normal.papers} == {"not-applicable"}


def test_complete_native_projection_preserves_roles_and_reclassifies_every_linked_event(
    complete_native_catalogue: NormalizedCatalog,
) -> None:
    papers = complete_native_catalogue.papers
    assert len(papers) == 1468
    assert len({p.bundle_id for p in papers}) == 26
    assert not complete_native_catalogue.review_queue
    assert Counter(p.file_type for p in papers) == {
        "question": 735,
        "answer": 686,
        "corrected_answer": 47,
    }
    assert (
        len({(p.source_exam_id, p.subject_code, p.download_url_source, p.checksum) for p in papers})
        == 488
    )
    validate_public_papers(papers, complete_history=True)
    normalized_again = renormalize_catalog(complete_native_catalogue, [], collect_reviews=True)
    assert normalized_again == complete_native_catalogue


@pytest.mark.parametrize(
    "mutation", ["missing-file", "duplicate-file", "wrong-role", "changed-bytes"]
)
def test_publication_rejects_incomplete_or_forged_native_projection(
    complete_native_catalogue: NormalizedCatalog, mutation: str
) -> None:
    papers = list(complete_native_catalogue.papers)
    if mutation == "missing-file":
        papers.pop()
    elif mutation == "duplicate-file":
        papers.append(papers[0])
    elif mutation == "wrong-role":
        papers[0] = replace(papers[0], file_type="answer")
    else:
        papers[0] = replace(papers[0], checksum="0" * 64)
    with pytest.raises(ValueError, match="Taipower publication"):
        validate_public_papers(papers, complete_history=True)


@pytest.mark.parametrize(
    "event_id",
    [
        "taipower-recruit-90",
        "taipower-recruit-92",
        "taipower-recruit-94",
        "taipower-recruit-95",
        "taipower-recruit-96",
        "taipower-recruit-97",
        "taipower-recruit-99",
    ],
)
def test_missing_early_occupational_rules_do_not_receive_guessed_job_identities(
    event_id: str,
) -> None:
    catalogue = native_catalogue()
    event = catalogue.events_by_id[event_id]
    normalized = normalized_page(catalogue, event_id)
    assert len(normalized.papers) == len(event.files)
    assert {p.classification_confidence for p in normalized.papers} == {"review"}
    assert {p.level_id for p in normalized.papers} == {"unknown"}
    assert {p.track_id for p in normalized.papers} == {"taipower-unknown"}
    assert {p.stage_id for p in normalized.papers} == {event.selection_stage}


def test_creative_written_booklets_do_not_gain_fabricated_answer_keys() -> None:
    catalogue = native_catalogue()
    missing = [
        (exam.source_exam_id, booklet.subject_name, booklet.written_period)
        for exam in catalogue.annual_exams
        for booklet in exam.booklets
        if booklet.answer_source_code is None
    ]
    assert missing == [
        ("taipower-recruit-104", "圖文創作、資料視覺化設計", "B"),
        ("taipower-recruit-107-12", "分鏡腳本設計", "B"),
    ]


def test_changed_reviewed_source_bytes_are_rejected_before_mirror_assignment() -> None:
    resource = native_catalogue().source_events[-1].files[0]
    provider = TaipowerRecruitProvider()
    with patch.object(
        provider.client,
        "download_file",
        return_value=DownloadedFile(b"%PDF-1.7 changed source", "application/pdf", "paper.pdf"),
    ):
        with pytest.raises(ValueError, match="preserve earlier bytes"):
            provider.download_file(resource.source_url)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-source",
        "repeated-role",
        "wrong-period",
        "wrong-weights",
        "unknown-occupation",
        "occupation-evidence",
        "stage-evidence",
        "review-date",
    ],
)
def test_native_reader_rejects_broken_cross_links(tmp_path: Path, mutation: str) -> None:
    document = json.loads(native_programmes_path(ROOT).read_text())
    exam = document["annual_exams"][0]
    if mutation == "missing-source":
        exam["booklets"][0]["question_source_code"] = "unknown"
    elif mutation == "repeated-role":
        exam["booklets"][0]["answer_source_code"] = exam["booklets"][1]["answer_source_code"]
    elif mutation == "wrong-period":
        exam["booklets"][0]["written_period"] = "B"
    elif mutation == "wrong-weights":
        exam["jobs"][0]["weights"] = [10, 10, 30, 40]
    elif mutation == "unknown-occupation":
        exam["jobs"][0]["occupation_id"] = "unknown"
    elif mutation == "occupation-evidence":
        occupation = next(o for o in document["occupations"] if o["correspondence_evidence"])
        occupation["correspondence_evidence"][0]["checksum"] = "0" * 64
    elif mutation == "stage-evidence":
        event = next(
            e for e in document["source_events"] if e["source_exam_id"] == exam["source_exam_id"]
        )
        event["source_evidence"][0]["checksum"] = "0" * 64
    else:
        document["reviewed_at"] = "20261102"
    path = tmp_path / "native.json"
    path.write_text(json.dumps(document, ensure_ascii=False))
    with pytest.raises(ValueError):
        read_native_catalogue(path)


def test_ordinary_and_repeated_refresh_sync_keep_one_original_locator_per_source(
    tmp_path: Path,
) -> None:
    original = native_catalogue()
    event = original.events_by_id["taipower-recruit-115"]
    exam = original.exams_by_id[event.source_exam_id]
    payloads = {
        file.source_url: b"%PDF-1.7\nfixture " + file.subject_code.encode() for file in event.files
    }
    payloads[exam.rule.source_url] = b"%PDF-1.7\nfixture annual rule"
    files = tuple(
        replace(
            file,
            checksum=hashlib.sha256(payloads[file.source_url]).hexdigest(),
            size=len(payloads[file.source_url]),
        )
        for file in event.files
    )
    fixture_event = replace(event, files=files)
    fixture_exam = replace(
        exam,
        rule=replace(
            exam.rule,
            checksum=hashlib.sha256(payloads[exam.rule.source_url]).hexdigest(),
            size=len(payloads[exam.rule.source_url]),
        ),
    )
    catalogue = replace(
        original,
        source_events=tuple(
            fixture_event if e.source_exam_id == event.source_exam_id else e
            for e in original.source_events
        ),
        annual_exams=tuple(
            fixture_exam if e.source_exam_id == event.source_exam_id else e
            for e in original.annual_exams
        ),
    )
    client = source_client(catalogue, event.source_exam_id)
    store = MirrorStore(tmp_path)
    downloads = Counter()

    def download(url: str) -> DownloadedFile:
        downloads[url] += 1
        return DownloadedFile(payloads[url], "application/pdf", "source.pdf")

    with (
        patch("app.providers.taipower_recruit.evidence.native_catalogue", return_value=catalogue),
        patch("app.providers.taipower_recruit.provider.native_catalogue", return_value=catalogue),
        patch.object(client, "download_file", side_effect=download),
    ):
        pages, first, failures = sync_exam_pages(
            client=TaipowerRecruitProvider(client),
            exam_codes=[(event.source_exam_id, 2026)],
            mirror_store=store,
            alias_rules=[],
            mirror_base_url="",
            download_attachments=False,
        )
        assert not failures
        client._cached_entries = list(reversed(client._cached_entries or []))
        _pages, second, failures = sync_exam_pages(
            client=TaipowerRecruitProvider(client),
            exam_codes=[(event.source_exam_id, 2026)],
            mirror_store=store,
            alias_rules=[],
            mirror_base_url="",
            download_attachments=False,
            previous_pages=pages,
        )
        assert not failures
        built = build_bundles(tmp_path / "bundles", tmp_path, second, "")
        assert not built.failures
        assert len(built.bundles) == 13
        for bundle in built.bundles:
            with zipfile.ZipFile(tmp_path / "bundles" / bundle.asset_name) as archive:
                manifest = json.loads(archive.read("bundle.json"))
                expected_references = 12 if "general-administration" in bundle.bundle_id else 6
                assert len(manifest["papers"]) == expected_references
                assert len({row["bundle_entry"] for row in manifest["papers"]}) == 6
                for row in manifest["papers"]:
                    assert archive.read(row["bundle_entry"]) == payloads[row["download_url_source"]]
    assert len(first.papers) == 84
    assert first == second
    assert downloads == Counter({url: 2 for url in payloads})
    assert len(list(tmp_path.rglob("*.pdf"))) == 27
    assert {p.storage_key for p in first.papers} == {
        f"providers/taipower_recruit/115/taipower-recruit-115/115/{file.subject_code}/{file.source_file_type}.pdf"
        for file in files
    }


def test_exact_legacy_references_require_every_native_successor(complete_native_catalogue):
    catalogue = native_catalogue()
    event = catalogue.events_by_id["taipower-recruit-115"]
    file = event.files[0]
    native = next(
        p
        for p in complete_native_catalogue.papers
        if p.source_exam_id == event.source_exam_id and p.subject_code == file.subject_code
    )
    previous = replace(native, **event.legacy_source(file), source_material=None, schema_version=2)
    targets = legacy_category_targets(previous, complete_native_catalogue.papers)
    assert len(targets) == 14
    assert len({p.bundle_id for p in targets}) == 13
    missing = [p for p in complete_native_catalogue.papers if p is not targets[0]]
    assert legacy_category_targets(previous, missing) == []
    assert (
        legacy_category_targets(
            replace(previous, checksum="0" * 64), complete_native_catalogue.papers
        )
        == []
    )
    assert (
        legacy_category_targets(
            replace(previous, category_raw="台電"), complete_native_catalogue.papers
        )
        == []
    )


def test_native_public_labels_and_answer_limits_preserve_source_context(complete_native_catalogue):
    catalogue = native_catalogue()
    for paper in complete_native_catalogue.papers:
        exam = catalogue.exams_by_id[paper.source_exam_id]
        job = exam.jobs_by_code[paper.category_code]
        booklet = next(
            b
            for b in exam.booklets
            if b.booklet_id in job.booklets and paper.subject_code in b.source_codes
        )
        assert publication_paper_subject(paper) == booklet.public_subject_label
        notes = public_coverage_notes(paper)
        has_answer_limit = booklet.written_period == "common" or booklet.answer_source_code is None
        assert (booklet.answer_coverage_note in notes) == has_answer_limit
        assert len(notes) == int(has_answer_limit) + int(job.population == "disability")
        if paper.source_exam_id in {"taipower-recruit-107-5", "taipower-recruit-107-12"}:
            month = paper.source_exam_id.rsplit("-", 1)[1]
            assert _bundle_arcname(paper).startswith(f"107/{int(month):02d}月/")
    changed = replace(complete_native_catalogue.papers[0], checksum="0" * 64)
    assert public_coverage_notes(changed) == []
    shared = [
        p
        for p in complete_native_catalogue.papers
        if p.track_id == "taipower-general-administration"
    ]
    aliases, _ = derive_public_metadata(
        shared,
        bundle_id=shared[0].bundle_id,
        canonical_name=shared[0].bundle_name,
    )
    assert any("限身心障礙人員報考" in alias for alias in aliases)
    assert all(
        "不補造" not in note
        for paper in complete_native_catalogue.papers
        for note in public_coverage_notes(paper)
    )


def test_changed_population_weights_cannot_silently_share_an_occupation_bundle(tmp_path):
    document = json.loads(native_programmes_path(ROOT).read_text())
    exam = next(
        e for e in document["annual_exams"] if e["source_exam_id"] == "taipower-recruit-115"
    )
    job = next(j for j in exam["jobs"] if j["population"] == "disability")
    job["weights"] = [20, 20, 20, 40]
    path = tmp_path / "changed-population.json"
    path.write_text(json.dumps(document, ensure_ascii=False))
    with pytest.raises(ValueError, match="paper-form review"):
        read_native_catalogue(path)


@pytest.mark.repo_data
def test_metadata_gate_detects_missing_category_even_when_shared_sources_survive(tmp_path):
    provider = tmp_path / "data/providers/taipower_recruit"
    shutil.copytree(ROOT / "data/providers/taipower_recruit", provider)
    evidence = native_programmes_path(tmp_path)
    evidence.parent.mkdir(parents=True)
    shutil.copy2(native_programmes_path(ROOT), evidence)
    assert validate_native_programmes(tmp_path) == 626
    path = provider / "papers/2026.json"
    original = json.loads(path.read_text())
    remaining = [
        p for p in original if p["category_code"] != "job-general-administration-disability"
    ]
    assert len(original) - len(remaining) == 6
    path.write_text(json.dumps(remaining, ensure_ascii=False))
    with pytest.raises(ValueError, match="omitted or duplicated"):
        validate_native_programmes(tmp_path)
