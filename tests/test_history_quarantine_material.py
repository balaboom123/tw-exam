"""Unknown withheld dates must not crash or shrink the history audit."""

import hashlib
import io
import json
import zipfile

import pytest

from app.history_audit import build_history_coverage_audit, history_audit_exit_code
from app.models import BundleAsset, ParsedPaper, SourceExamPage, to_plain_data
from app.normalizer import normalize_papers
from app.paths import provider_paths
from app.publisher import write_provider_state
from app.source_material import MaterialSummary, SourceDate, SourceMaterial


@pytest.fixture
def unresolved_retained_archive(tmp_path):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("native.pdf", b"%PDF-1.7 unreviewed native fixture")
    data = stream.getvalue()
    key = "providers/twc_recruit/104/original.zip"
    path = tmp_path / "mirror" / key
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    raw = SourceExamPage(
        "twc-recruit-104", 2015, 104, "104年試題(解答).zip", [],
        [ParsedPaper(
            "台水評價職位人員甄試", "104", "twc-recruit-104-bundle", "104年試題(解答).zip",
            {"question_answer": "https://www.water.gov.tw/ch/ServerFile/Get/00000000-0000-0000-0000-000000000104?nodeId=715"},
            mirror_files={"question_answer": {"storage_key": key, "checksum": hashlib.sha256(data).hexdigest()}},
        )], provider_id="twc_recruit",
    )
    catalog = normalize_papers(raw.source_exam_id, raw.year_ad, raw.exam_name_raw,
                               raw.papers, [], "", {}, provider_id="twc_recruit")
    assert catalog.papers[0].source_material.date.basis == "unknown"
    write_provider_state(provider_paths(tmp_path, "twc_recruit"), raw_pages=[raw],
                         normalized=catalog, aliases=[], failures=[], manifest=None)
    site = tmp_path / "data/sites/default"
    site.mkdir(parents=True)
    (site / "bundles.json").write_text(json.dumps({
        "schema_version": 2, "catalog_version": "exam-identity-v2", "bundles": [],
    }))
    return tmp_path, path


def test_withheld_unknown_dates_preserve_coverage_and_still_require_mirrors(unresolved_retained_archive, monkeypatch):
    root, path = unresolved_retained_archive
    monkeypatch.setattr("app.history_audit.quarantined_provider_ids", lambda *args, **kwargs: frozenset({"twc_recruit"}))
    report = build_history_coverage_audit(root, provider_ids=("twc_recruit",), check_mirror=True)
    assert report["mirror_checked"]
    assert report["providers"][0]["raw_exam_pages"] == 1
    assert report["providers"][0]["normalized_paper_records"] == 1
    assert report["providers"][0]["events"][0]["status"] == "withheld_by_quarantine"
    assert report["summary"]["withheld_by_quarantine"] == 1
    assert history_audit_exit_code(report, strict=True) == 0
    path.unlink()
    missing = build_history_coverage_audit(root, provider_ids=("twc_recruit",), check_mirror=True)
    assert missing["providers"][0]["normalized_paper_records"] == 1
    assert missing["summary"]["download_gap"] == 1
    assert history_audit_exit_code(missing, strict=True) == 1


def test_unquarantined_unknown_dates_still_fail_public_eligibility(unresolved_retained_archive):
    root, _ = unresolved_retained_archive
    with pytest.raises(ValueError, match="Unresolved source material cannot be published"):
        build_history_coverage_audit(root, provider_ids=("twc_recruit",), check_mirror=True)


@pytest.mark.parametrize("kind,source_date,public_years", [
    ("administered", SourceDate("exam_year", 2025), [114]),
    ("sample", SourceDate("undated", None), []),
])
def test_history_reconciles_reviewed_dates_and_undated_material_instead_of_source_partition(
    tmp_path, kind, source_date, public_years,
):
    material = SourceMaterial(kind, source_date, "https://tqa.rcpet.edu.tw/", "Reviewed native fixture")
    source = SourceExamPage(
        "teacher-qual-2026", 2026, 115, "115年教師資格考試", [],
        [ParsedPaper("教師資格考試", "elementary", "education", "教育原理與制度",
                     {"question": "https://tqa.rcpet.edu.tw/native.pdf"}, source_material=material)],
        provider_id="teacher_qual",
    )
    catalog = normalize_papers(source.source_exam_id, source.year_ad, source.exam_name_raw,
                               source.papers, [], "", {}, provider_id="teacher_qual")
    paper = catalog.papers[0]
    key = "providers/teacher_qual/115/native.pdf"
    paper.storage_key = key
    mirror = tmp_path / "mirror" / key
    mirror.parent.mkdir(parents=True)
    mirror.write_bytes(b"%PDF-1.7 native fixture")
    write_provider_state(provider_paths(tmp_path, "teacher_qual"), raw_pages=[source],
                         normalized=catalog, aliases=[], failures=[], manifest=None)
    asset = BundleAsset(
        paper.canonical_id, paper.canonical_name, public_years, 1, "sites/default/native.zip", "native.zip",
        bundle_id=paper.bundle_id, schema_version=3, catalog_version="exam-identity-v2",
        source_material=MaterialSummary(kind, (source_date,)),
    )
    site = tmp_path / "data/sites/default"
    site.mkdir(parents=True)
    target = site / "bundles.json"
    def write_asset(value):
        target.write_text(json.dumps({"schema_version": 3, "catalog_version": "exam-identity-v2",
                                      "bundles": [to_plain_data(value)]}))
    write_asset(asset)
    report = build_history_coverage_audit(tmp_path, provider_ids=("teacher_qual",), check_mirror=True)
    assert report["providers"][0]["events"][0]["year_ad"] == 2026
    assert report["providers"][0]["events"][0]["status"] == "published_complete"
    assert history_audit_exit_code(report, strict=True) == 0
    if kind == "administered":
        asset.years = [115]
        asset.source_material = MaterialSummary(kind, (SourceDate("exam_year", 2026),))
        write_asset(asset)
        wrong = build_history_coverage_audit(tmp_path, provider_ids=("teacher_qual",), check_mirror=True)
        assert wrong["providers"][0]["events"][0]["status"] == "normalized_not_published"
        assert history_audit_exit_code(wrong, strict=True) == 1
