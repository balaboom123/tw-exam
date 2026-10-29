"""Review de-duplication must preserve each identity in its actual source context."""

from dataclasses import replace
from pathlib import Path

import pytest

from app.audit import audit_exit_code, build_catalog_audit
from app.models import NormalizedCatalog, NormalizedPaper, ReviewItem
from app.normalizer import _deduplicate_review_queue, normalize_papers, renormalize_catalog
from app.paths import provider_paths
from app.publisher import write_provider_state
from app.review_queue import review_key
from app.state import load_provider_state

ROOT = Path(__file__).resolve().parents[1]


def test_distinct_signatures_survive_legacy_and_duplicate_review_rows():
    legacy = ReviewItem("shared", "candidate", "event", 115, provider_id="moex")
    first = replace(
        legacy, classification_signature="first", bundle_id="first-event", reason="review"
    )
    second = replace(
        legacy, classification_signature="second", bundle_id="second-event", reason="review"
    )
    for rows in ([legacy, first, second, first], [second, first, legacy, second]):
        selected = _deduplicate_review_queue(rows)
        assert selected == [first, second]
    assert _deduplicate_review_queue([legacy, legacy]) == [legacy]
    assert _deduplicate_review_queue([replace(first, reason=""), first]) == [first]


@pytest.mark.repo_data
@pytest.mark.parametrize("reverse", [False, True])
def test_fresh_and_retained_papers_keep_both_review_identities_in_one_category(reverse):
    pages, catalog, _ = load_provider_state(provider_paths(ROOT, "taisugar_recruit"))
    page = next(p for p in pages if p.year_roc == 111)
    retained = [p for p in catalog.papers if p.year_roc == 111]
    parsed = list(page.papers)
    if reverse:
        retained.reverse()
        parsed.reverse()
    fresh = normalize_papers(
        page.source_exam_id,
        page.year_ad,
        page.exam_name_raw,
        parsed,
        [],
        "",
        {},
        provider_id="taisugar_recruit",
    )
    rebuilt = renormalize_catalog(NormalizedCatalog(retained, []), [])
    for result in (fresh, rebuilt):
        assert {p.stage_id for p in result.papers} == {"stage-1", "unknown"}
        expected = {
            (p.provider_id, p.source_exam_id, p.category_raw, p.stage_id) for p in result.papers
        }
        assert len(expected) == 2
        assert len(result.review_queue) == 2
        assert len({review_key(r) for r in result.review_queue}) == 2
        for paper in result.papers:
            assert any(
                review_key(r)[:3] == (paper.provider_id, paper.source_exam_id, paper.category_raw)
                and r.bundle_id == paper.bundle_id
                for r in result.review_queue
            )


def test_another_events_matching_signature_cannot_hide_a_missing_review(tmp_path):
    papers = [
        NormalizedPaper(
            provider_id="taisugar_recruit",
            canonical_id="taisugar-recruit",
            canonical_name="台糖新進工員甄試",
            year_roc=115,
            exam_name_raw="未審查新進工員甄試",
            category_raw="台糖新進工員甄試",
            category_code="115",
            source_exam_id=event,
            subject_code="unreviewed",
            subject_name_raw="原始文件",
            paper_code=event + "-source_document",
            file_type="source_document",
            download_url_source="https://source.example/" + event + ".pdf",
        )
        for event in ("first-event", "second-event")
    ]
    catalog = renormalize_catalog(NormalizedCatalog(papers, []), [])
    assert len(catalog.review_queue) == 2
    assert len({r.classification_signature for r in catalog.review_queue}) == 1
    catalog.review_queue = catalog.review_queue[:1]
    write_provider_state(
        provider_paths(tmp_path, "taisugar_recruit"),
        raw_pages=[],
        normalized=catalog,
        aliases=[],
        failures=[],
        manifest=None,
    )
    report = build_catalog_audit(tmp_path)
    assert report["approved_review_isolated_records"] == 1
    assert report["unapproved_review_records"] == 1
    assert report["review_queue_missing_entries"] == 1
    assert audit_exit_code(report, strict=True) == 1
