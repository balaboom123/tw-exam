from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.classification import classify_paper
from app.providers.base import DownloadedFile
from app.providers.sfi_cert import evidence
from app.providers.sfi_cert.client import SfiCertClient, parse_sfi_archive
from app.providers.sfi_cert.evidence import NativePaper, read_native_papers
from app.providers.sfi_cert.identity import programme_for_slug
from app.source_material import SourceDate, SourceMaterial


def source_pair(slug="futures-dealer", code="01", *, year=115, round_no=2, venue=""):
    programme = programme_for_slug(slug)
    label = programme.listing_labels[0]
    html = f"<tr><td>{label}</td>"
    facts = []
    payloads = {}
    for role, suffix in [("question", ""), ("answer", "a")]:
        url = f"https://examweb.sfi.org.tw/Download/01/{code}{suffix}.pdf"
        body = (
            b"%PDF-1.7 native fixture " + (slug + str(year) + str(round_no) + role + venue).encode()
        )
        digest = hashlib.sha256(body).hexdigest()
        heading = f"{year}年第{round_no}次 {programme.label}"
        heading += "（高雄考區）" if venue else ""
        heading += "試題解答" if suffix else "試題"
        fact = NativePaper(
            "sfi-native-" + digest[:20],
            digest,
            code,
            slug,
            year,
            round_no,
            venue,
            role,
            ("原生科目",),
            (url,),
            heading,
            "2026-10-20",
            (1,),
            1,
        )
        facts.append(fact)
        payloads[url] = DownloadedFile(body, "application/pdf", code + suffix + ".pdf")
        html += f'<td><a href="{url}">{role}</a></td>'
    return "<table>" + html + "</tr></table>", facts, payloads


def client_for(monkeypatch, *, slug="futures-dealer", code="01", year=115, round_no=2, venue=""):
    html, facts, payloads = source_pair(slug, code, year=year, round_no=round_no, venue=venue)
    monkeypatch.setattr(evidence, "native_papers", lambda: tuple(facts))
    client = SfiCertClient()
    client._fetch_text = Mock(return_value="<p>前一次：115年度第1次</p>" + html)
    client.http.download = Mock(side_effect=lambda url, **kwargs: payloads[url])
    return client, facts, payloads


def test_table_label_defines_programme_without_numeric_code_guessing():
    html, _, _ = source_pair("futures-dealer", "01")
    rows = parse_sfi_archive(html)
    assert len(rows) == 1
    assert rows[0].programme.slug == "futures-dealer"
    assert rows[0].category_code == "01"
    assert set(rows[0].files) == {"question", "answer"}


def test_native_pdf_context_overrides_page_heading_and_is_pinned_for_the_sync(monkeypatch):
    client, facts, payloads = client_for(monkeypatch)
    assert client.discover_available_years() == [2026]
    exams = client.discover_exams(2026)
    assert [e.code for e in exams] == ["sfi-cert-futures-dealer-2026-2"]
    page = client.fetch_exam_page(exams[0].code, 2026)
    assert page.source_material.date == SourceDate("exam_year", 2026)
    assert page.papers[0].category_raw == "期貨商業務員"
    assert page.papers[0].subject_name_raw == "原生科目"
    for url, payload in payloads.items():
        assert client.download_file(url) is payload
    assert client.http.download.call_count == 2
    client._fetch_text.assert_called_once()


def test_global_current_year_does_not_relabel_an_older_native_programme_event(monkeypatch):
    client, _, _ = client_for(monkeypatch, slug="aml", code="99", year=113, round_no=4)
    assert client.discover_available_years() == [2024]
    assert client.discover_exams(2024)[0].code == "sfi-cert-aml-2024-4"
    assert client.discover_exams(2026) == []


def test_native_regional_form_remains_a_distinct_event_and_variant(monkeypatch):
    client, facts, _ = client_for(
        monkeypatch, slug="sustainability", code="82", year=114, round_no=4, venue="kaohsiung"
    )
    assert client.discover_exams(2025)[0].code == "sfi-cert-sustainability-2025-4-kaohsiung"
    identity = classify_native(facts[0])
    assert "venue-kaohsiung" in identity.variant_ids
    assert "高雄考區" in identity.bundle_name
    assert identity.stage_id == "not-applicable"


def test_unreviewed_replacement_bytes_fail_without_assigning_a_new_event(monkeypatch):
    client, _, payloads = client_for(monkeypatch)
    url = next(iter(payloads))
    payloads[url] = DownloadedFile(b"%PDF-1.7 unreviewed new sitting", "application/pdf", "01.pdf")
    with pytest.raises(ValueError, match="needs native context review.*SHA256"):
        client.discover_available_years()


def test_question_and_answer_native_events_must_agree(monkeypatch):
    client, facts, _ = client_for(monkeypatch)
    facts[1] = replace(facts[1], round_no=3)
    with pytest.raises(ValueError, match="contexts disagree"):
        client.discover_available_years()


@pytest.mark.parametrize("mutation", ["unknown_label", "missing_answer", "wrong_host"])
def test_source_drift_is_reported_instead_of_silently_omitting_rows(mutation):
    html, _, _ = source_pair()
    if mutation == "unknown_label":
        html = html.replace("期貨商業務員", "未審核新測驗")
    elif mutation == "missing_answer":
        html = html.replace(
            'href="https://examweb.sfi.org.tw/Download/01/01a.pdf"', 'href="/no-answer"'
        )
    else:
        html = html.replace("examweb.sfi.org.tw", "other.example")
    with pytest.raises(ValueError):
        parse_sfi_archive(html)


def classify_native(fact, **overrides):
    fields = dict(
        provider_id="sfi_cert",
        source_exam_id=fact.source_exam_id,
        year_ad=fact.year_roc + 1911,
        category_raw="錯誤舊名稱",
        category_code=fact.category_code,
        exam_name_raw="不可靠的舊標籤",
        canonical_id="incorrect-old-alias",
        canonical_name="錯誤舊名稱",
        source_checksum=fact.checksum,
        source_material=fact.material,
    )
    fields.update(overrides)
    return classify_paper(**fields)


def test_reviewed_native_programme_wins_over_wrong_compatibility_labels(monkeypatch):
    _, facts, _ = client_for(monkeypatch)
    identity = classify_native(facts[0])
    assert identity.track_id == "sfi-futures-dealer"
    assert identity.bundle_name == "期貨商業務員"
    assert identity.confidence == "high"


def test_sitca_source_collection_does_not_become_the_separate_law_programme(monkeypatch):
    _, facts, _ = client_for(monkeypatch, slug="sitca", code="06")
    identity = classify_native(facts[0])
    assert identity.track_id == "sfi-sitca"
    assert "subject-route-collection" in identity.variant_ids
    assert identity.bundle_name == "投信投顧業務員｜一科、二科及三科"


@pytest.mark.parametrize("change", ["checksum", "event", "category", "material_date"])
def test_changed_native_context_is_isolated_for_review(monkeypatch, change):
    _, facts, _ = client_for(monkeypatch)
    overrides = (
        {"source_checksum": "a" * 64}
        if change == "checksum"
        else {"source_exam_id": "sfi-cert-securities-analyst-2026-2"}
        if change == "event"
        else {"category_code": "99"}
        if change == "category"
        else {
            "source_material": SourceMaterial(
                "administered",
                SourceDate("exam_year", 2025),
                facts[0].material.evidence_url,
                "不相符的年份",
            )
        }
    )
    assert classify_native(facts[0], **overrides).confidence == "review"


@pytest.mark.parametrize("change", ["date", "role", "venue", "endpoint", "duplicate"])
def test_reviewed_catalog_rejects_inconsistent_native_evidence(tmp_path, change):
    _, facts, _ = source_pair()
    rows = [asdict(f) for f in facts]
    if change == "date":
        rows[0]["year_roc"] = 114
    elif change == "role":
        rows[1]["native_heading"] = rows[0]["native_heading"]
    elif change == "venue":
        rows[0]["venue"] = "kaohsiung"
    elif change == "endpoint":
        rows[0]["source_urls"] = ["https://other.example/Download/01/01.pdf"]
    else:
        rows.append(rows[0])
    path = tmp_path / "facts.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provider_id": "sfi_cert",
                "evidence_url": evidence.ARCHIVE_URL,
                "facts": rows,
            }
        )
    )
    with pytest.raises(ValueError):
        read_native_papers(path)


def test_capture_helper_preserves_unknown_bytes_without_approving_an_identity(
    tmp_path, monkeypatch
):
    from scripts.capture_sfi_review import capture_review

    client, facts, payloads = client_for(monkeypatch)
    html, _, _ = source_pair()
    first_url = next(iter(payloads))
    payloads[first_url] = DownloadedFile(
        b"%PDF-1.7 unfamiliar replacement", "application/pdf", "01.pdf"
    )
    payloads[evidence.ARCHIVE_URL] = DownloadedFile(
        html.encode(), "text/html; charset=utf-8", "source.html"
    )
    client.http.download = Mock(side_effect=lambda url, **kwargs: payloads[url])
    monkeypatch.setattr("scripts.capture_sfi_review.shutil.which", lambda name: None)
    output = tmp_path / "review"
    report = capture_review(output, client)
    assert report["provider_state_changed"] is False
    assert report["capture_complete"] is True
    assert len(report["files"]) == 2
    unknown = next(row for row in report["files"] if "native_review_required" in row)
    assert "reviewed_native_context" not in unknown
    assert (output / unknown["payload"]).read_bytes() == payloads[first_url].data
    assert json.loads((output / "review.json").read_text()) == report
    with pytest.raises(ValueError, match="new output directory"):
        capture_review(output, client)


def test_capture_helper_retains_a_changed_listing_for_manual_review(tmp_path, monkeypatch):
    from scripts.capture_sfi_review import capture_review

    client = SfiCertClient()
    html, _, _ = source_pair()
    html = html.replace("期貨商業務員", "未審核新測驗")
    client.http.download = Mock(
        return_value=DownloadedFile(html.encode(), "text/html", "source.html")
    )
    output = tmp_path / "review"
    report = capture_review(output, client)
    assert "listing_review_required" in report and report["files"] == []
    assert report["capture_complete"] is False
    assert (output / "archive.html").read_text() == html
    assert json.loads((output / "review.json").read_text()) == report


@pytest.mark.parametrize("change", ["none", "checksum", "source_host", "role"])
def test_history_native_review_is_exact_and_does_not_rewrite_original_records(tmp_path, change):
    from copy import deepcopy
    from app.providers.sfi_cert.evidence import reviewed_native_revisions

    _, facts, _ = source_pair()
    fact = facts[0]
    path = tmp_path / "catalog/mappings/sfi/native-papers-v1.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provider_id": "sfi_cert",
                "evidence_url": evidence.ARCHIVE_URL,
                "facts": [asdict(f) for f in facts],
            }
        )
    )
    record = {
        "provider_id": "sfi_cert",
        "source_exam_id": "sfi-cert-securities-analyst-2026-1",
        "category_code": "01",
        "file_type": "question",
        "checksum": fact.checksum,
        "download_url_source": fact.source_urls[0],
    }
    if change == "checksum":
        record["checksum"] = "a" * 64
    elif change == "source_host":
        record["download_url_source"] = record["download_url_source"].replace(
            "examweb.sfi.org.tw", "other.example"
        )
    elif change == "role":
        record["file_type"] = "answer"
    entries = [{"id": "original-revision", "record_type": "paper", "source_record": record}]
    original = deepcopy(entries)
    reviewed = reviewed_native_revisions(entries, tmp_path)
    assert entries == original
    if change == "none":
        assert len(reviewed) == 1
        assert reviewed[0]["native_source_exam_id"] == "sfi-cert-futures-dealer-2026-2"
        assert reviewed[0]["evidence_id"] == fact.id
        assert reviewed[0]["source_material"]["date"] == {"basis": "exam_year", "year_ad": 2026}
    else:
        assert reviewed == []
