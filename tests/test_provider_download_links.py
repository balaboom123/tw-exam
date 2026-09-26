import json
from dataclasses import asdict

from app.models import NormalizedCatalog, NormalizedPaper
from app.paths import provider_paths
from app.publisher import write_provider_state
from app.state import load_provider_state


def paper():
    return NormalizedPaper(
        provider_id="moex",
        canonical_id="nurse",
        canonical_name="Nurse",
        year_roc=115,
        exam_name_raw="115 Nurse Exam",
        category_raw="Nurse",
        subject_name_raw="Medicine",
        paper_code="101-0101-question",
        file_type="question",
        source_exam_id="115030",
        category_code="101",
        subject_code="0101",
        download_url_source="https://example.test/paper.pdf",
        storage_key="115/paper.pdf",
        checksum="abc",
        download_url_bundle="https://example.test/old-site.zip",
    )


def test_scoped_state_keeps_acquisition_evidence_without_site_url(tmp_path):
    provider = provider_paths(tmp_path, "moex")
    record = paper()
    catalog = NormalizedCatalog([record], [])
    write_provider_state(provider, [], catalog, [], [], None)
    saved = json.loads((provider.papers_dir / "2026.json").read_text())[0]
    assert saved == {
        key: value for key, value in asdict(record).items() if key != "download_url_bundle"
    }
    restored = load_provider_state(provider)[1].papers[0]
    assert restored.download_url_bundle == ""
    assert restored.download_url_source == record.download_url_source
    assert restored.checksum == record.checksum
    assert record.download_url_bundle == "https://example.test/old-site.zip"


def test_older_provider_record_with_site_url_remains_readable(tmp_path):
    provider = provider_paths(tmp_path, "moex")
    provider.papers_dir.mkdir(parents=True)
    record = paper()
    (provider.papers_dir / "2026.json").write_text(json.dumps([asdict(record)]))
    assert load_provider_state(provider)[1].papers == [record]
