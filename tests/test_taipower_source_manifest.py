from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest

from app.manifest import SourceManifest
from app.models import ExamAttachment, ParsedPaper, SourceExamPage
from app.providers.taipower_recruit.manifest import refresh_source_manifest

PROVIDER = "taipower_recruit"
OLD_URL = "https://www.taipower.com.tw/media/old/paper.pdf"
NEW_URL = "https://www.taipower.com.tw/media/new/paper.pdf"


def stored_paper(root: Path, name: str, url: str, data: bytes) -> ParsedPaper:
    key = f"providers/{PROVIDER}/97/taipower-recruit-97/97/{name}/question.pdf"
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return ParsedPaper(
        category_raw="電子儀表",
        category_code="job-electronics",
        subject_code=name,
        subject_name_raw="電子學",
        files={"question": url},
        source_category_code="97",
        mirror_files={
            "question": {"storage_key": key, "checksum": hashlib.sha256(data).hexdigest()}
        },
    )


def page(papers: list[ParsedPaper]) -> SourceExamPage:
    return SourceExamPage("taipower-recruit-97", 2008, 97, "97年甄試", [], papers, PROVIDER)


def test_relocated_source_preserves_both_payloads_and_distinguishes_shared_references(
    tmp_path: Path,
) -> None:
    old = stored_paper(tmp_path, "old", OLD_URL, b"old original PDF")
    current = stored_paper(tmp_path, "new", NEW_URL, b"new original PDF")
    shared = deepcopy(current)
    shared.category_code = "job-other-occupation"
    shared.category_raw = "另一類科"
    source = page([current, shared, old])
    rule_key = f"providers/{PROVIDER}/97/taipower-recruit-97/rule.pdf"
    (tmp_path / rule_key).write_bytes(b"native rules")
    source.attachments = [
        ExamAttachment(
            "原始簡章",
            "instructions",
            "https://www.taipower.com.tw/media/rules/rule.pdf",
            storage_key=rule_key,
            checksum=hashlib.sha256(b"native rules").hexdigest(),
        )
    ]
    manifest = SourceManifest(
        provider_id=PROVIDER,
        probe_policy={
            "native_review": {"reviewed_at": "2008-01-01", "remaining_scope": "earlier review"},
            "payload_reconciliation": {"historical_evidence": "preserve"},
        },
    )
    result = refresh_source_manifest(
        manifest, [source], tmp_path, "2026-11-07", listed_source_urls={NEW_URL}
    )
    assert len(result.files) == 3
    assert {entry["sha256"] for entry in result.files.values()} == {
        hashlib.sha256(data).hexdigest()
        for data in [b"old original PDF", b"new original PDF", b"native rules"]
    }
    assert result.probe_policy["current_archive"]["source_file_count"] == 1
    assert result.probe_policy["current_archive"]["acquired_source_file_count"] == 1
    assert result.probe_policy["retained_local_state"] == {
        "events": 1,
        "original_files": 3,
        "paper_files": 2,
        "reference_documents": 1,
        "paper_references": 3,
        "unlisted_paper_originals": 1,
        "all_originals_SHA256_verified": True,
    }
    assert result.probe_policy["coverage_status"] == "complete"
    assert result.probe_policy["previous_native_review"]["remaining_scope"] == "earlier review"
    assert result.probe_policy["payload_reconciliation"] == {"historical_evidence": "preserve"}


def test_unacquired_official_link_cannot_claim_complete_coverage(tmp_path: Path) -> None:
    paper = stored_paper(tmp_path, "old", OLD_URL, b"original")
    result = refresh_source_manifest(
        SourceManifest(provider_id=PROVIDER),
        [page([paper])],
        tmp_path,
        "2026-11-07",
        listed_source_urls={OLD_URL, NEW_URL},
    )
    assert result.probe_policy["coverage_status"] == "partial"
    assert result.probe_policy["current_archive"]["unacquired_source_urls"] == [NEW_URL]


def test_conflicting_shared_locator_is_rejected(tmp_path: Path) -> None:
    paper = stored_paper(tmp_path, "old", OLD_URL, b"original")
    conflict = deepcopy(paper)
    conflict.files["question"] = NEW_URL
    with pytest.raises(ValueError, match="Competing"):
        refresh_source_manifest(
            SourceManifest(provider_id=PROVIDER),
            [page([paper, conflict])],
            tmp_path,
            "2026-11-07",
            listed_source_urls={OLD_URL, NEW_URL},
        )


@pytest.mark.parametrize("mutation", ["changed-bytes", "missing", "foreign", "symlink"])
def test_manifest_does_not_approve_unverified_or_foreign_originals(
    tmp_path: Path, mutation: str
) -> None:
    paper = stored_paper(tmp_path, "old", OLD_URL, b"original")
    metadata = paper.mirror_files["question"]
    original = tmp_path / metadata["storage_key"]
    if mutation == "changed-bytes":
        original.write_bytes(b"changed")
    elif mutation == "missing":
        original.unlink()
    elif mutation == "foreign":
        metadata["storage_key"] = "providers/another-provider/original.pdf"
    else:
        copy = tmp_path / "unowned-copy.pdf"
        original.rename(copy)
        original.symlink_to(copy)
    manifest = SourceManifest(provider_id=PROVIDER, files={"previous": {"sha256": "keep"}})
    with pytest.raises(ValueError):
        refresh_source_manifest(
            manifest, [page([paper])], tmp_path, "2026-11-07", listed_source_urls={OLD_URL}
        )
    assert manifest.files == {"previous": {"sha256": "keep"}}
