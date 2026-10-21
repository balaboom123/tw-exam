import pytest
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from app.providers.base import DownloadedFile
from app.providers.tabf_cert.client import TabfCertClient
from app.providers.tabf_cert import evidence
from app.providers.tabf_cert.evidence import NativeEvidence, read_native_evidence
from app.providers.tabf_cert.identity import programme_for_code
from app.providers.tabf_cert.listing import (
    HISTORY_INDEX_URL,
    HISTORY_URL,
    parse_native_events,
    parse_native_files,
)


def table(category, *links):
    return (
        "<h2>歷屆試題</h2><table><tr><td>"
        + category
        + "</td><td>"
        + "".join(
            f'<a href="LicenseHistoryExam.aspx?PHID={phid}">{label}</a>' for phid, label in links
        )
        + "</td></tr></table>"
    )


def test_native_rows_keep_grade_and_qualification_context_without_inventing_years():
    html = table("初階授信人員專業能力測驗", ("421", "第45屆試題"))
    html += table("進階授信人員專業能力測驗", ("381", "第23屆試題"))
    html += table("信託業業務人員信託業務專業測驗【信託法規乙科】", ("425", "第42期試題"))
    rows = parse_native_events(html)
    assert [r.programme_code for r in rows] == [
        "credit-junior",
        "credit-advanced",
        "trust-law-only",
    ]
    assert all(r.explicit_year_ad is None for r in rows)
    assert [programme_for_code(r.programme_code).level_id for r in rows] == [
        "tabf-junior",
        "tabf-advanced",
        "not-applicable",
    ]
    dated = parse_native_events(table("永續發展基礎能力測驗", ("431", "114年第5次試題")))[0]
    assert dated.explicit_year_ad == 2025 and dated.ordinal == 5 and dated.ordinal_kind == "次"


def test_unknown_subjects_cannot_fall_back_to_bank_internal_control():
    with pytest.raises(ValueError, match="Unreviewed TABF native category"):
        parse_native_events(table("新證照測驗", ("999", "第1屆試題")))


def test_conflicting_same_page_keys_and_foreign_endpoints_require_review():
    original = table("初階授信人員專業能力測驗", ("421", "第45屆試題"))
    with pytest.raises(ValueError, match="Conflicting TABF"):
        parse_native_events(original + table("進階授信人員專業能力測驗", ("421", "第45屆試題")))
    with pytest.raises(ValueError, match="endpoint"):
        parse_native_events(
            original.replace(
                "LicenseHistoryExam.aspx?", "https://foreign.test/LicenseHistoryExam.aspx?"
            )
        )
    assert len(parse_native_events(original + original.replace("第45屆試題", "第 45 屆 試題"))) == 1


def test_external_delegation_is_narrow_and_unknown_sources_require_review():
    owned = table("初階授信人員專業能力測驗", ("421", "第45屆試題"))
    delegated = '<table><tr><td>金融市場常識與職業道德</td><td><a href="http://webline.sfi.org.tw/T/ethics/">試題下載</a></td></tr></table>'
    assert len(parse_native_events(owned + delegated)) == 1
    with pytest.raises(ValueError, match="external source row"):
        parse_native_events(owned + delegated.replace("webline.sfi.org.tw", "foreign.test"))
    with pytest.raises(ValueError, match="external source row"):
        parse_native_events(owned + delegated.replace("金融市場常識與職業道德", "未審核證照"))


def test_fit_modules_are_selectable_assessments_and_sections_are_not_grades():
    first = programme_for_code("fit-subject-i")
    second = programme_for_code("fit-subject-ii")
    assert first.is_aptitude and second.is_aptitude
    assert first.canonical_id == second.canonical_id and first.track_id != second.track_id
    assert first.level_id == second.level_id == "not-applicable"
    assert programme_for_code("senior-financial-planning-advisor").level_id == "not-applicable"


@pytest.mark.parametrize("change", ["foreign", "format", "query", "conflict"])
def test_download_parser_rejects_unreviewed_owners_formats_and_ambiguous_labels(change):
    url = "https://service.tabf.org.tw/BEExam/Doc/ExamHistoryEdit/123456-1.pdf"
    html = f'<a href="{url}">信託法規</a>'
    if change == "foreign":
        html = html.replace("service.tabf.org.tw", "foreign.test")
    elif change == "format":
        html = html.replace(".pdf", ".zip")
    elif change == "query":
        html = html.replace(".pdf", ".pdf?generation=2")
    else:
        html += f'<a href="{url}">答案</a>'
    with pytest.raises(ValueError):
        parse_native_files(html)


@pytest.fixture
def snapshot(monkeypatch):
    real = evidence.native_evidence()
    events = {}
    for event in real.events:
        events.setdefault(event.programme_code, event)
    bodies = {
        p.checksum: b"%PDF-1.7 native TABF fixture " + p.checksum.encode() for p in real.papers
    }
    checksums = {sha: hashlib.sha256(body).hexdigest() for sha, body in bodies.items()}
    facts = tuple(
        replace(
            p,
            checksum=checksums[p.checksum],
            id=p.id.rsplit("-", 1)[0] + "-" + checksums[p.checksum][:20],
        )
        for p in real.papers
    )
    native = NativeEvidence(
        tuple(
            replace(e, year_evidence_checksum=checksums[e.year_evidence_checksum])
            for e in real.events
        ),
        facts,
    )
    payloads = {
        p.source_url: DownloadedFile(bodies[p.checksum], "application/pdf", "native.pdf")
        for p in real.papers
    }
    monkeypatch.setattr(evidence, "native_evidence", lambda: native)
    monkeypatch.setattr("app.providers.tabf_cert.client.native_evidence", lambda: native)
    homepage = "".join(table(e.programme.label, (e.phid, e.listing_label)) for e in events.values())

    def files(phid):
        return "".join(
            f'<a href="{p.source_url}">{p.source_label}</a>' for p in facts if p.phid == phid
        )

    def html(url):
        if url == HISTORY_INDEX_URL:
            return homepage + files("424")
        return homepage + files(url.rsplit("=", 1)[-1])

    client = TabfCertClient()
    client._fetch_text = Mock(side_effect=html)
    client.http.download = Mock(side_effect=lambda url, **kwargs: payloads[url])
    return client, native, payloads, events


def test_native_event_years_are_independent_of_the_process_and_requested_year(snapshot):
    client, native, _, events = snapshot
    assert client.discover_available_years() == [2020, 2023, 2025]
    assert client.discover_exams(2026) == []
    event = events["credit-advanced"]
    page = client.fetch_exam_page(event.source_exam_id, 2023)
    assert page.source_exam_id == event.source_exam_id and page.year_ad == 2023
    assert {p.category_code for p in page.papers} == {"credit-advanced"}
    assert {role for p in page.papers for role in p.files} == {"question", "answer"}
    assert all(p.source_material.date.year_ad == 2023 for p in page.papers)
    # A repeat selector or a second use by sync consumes the same pinned bytes.
    count = client.http.download.call_count
    assert client.fetch_exam_page(event.source_exam_id, 2023) is page
    assert client.http.download.call_count == count
    with pytest.raises(ValueError, match="event/year"):
        client.fetch_exam_page(event.source_exam_id, 2026)


def test_an_unreviewed_phid_is_reported_without_a_synthetic_year_or_asset_request(snapshot):
    client, _, _, events = snapshot
    original = client._fetch_text.side_effect
    event = events["credit-junior"]
    client._fetch_text.side_effect = lambda url: original(url).replace(
        "PHID=" + event.phid, "PHID=999999"
    )
    with pytest.raises(ValueError, match="review.*999999"):
        client.discover_available_years()
    client.http.download.assert_not_called()
    assert not client._pages


@pytest.mark.parametrize(
    "body,expected",
    [
        (b"<html>outage</html>", "not a native PDF"),
        (b"%PDF-1.7 unfamiliar revision", "replacement bytes"),
    ],
)
def test_unreviewed_or_invalid_payloads_cannot_expose_a_partial_event(snapshot, body, expected):
    client, native, payloads, events = snapshot
    event = events["credit-junior"]
    fact = next(p for p in native.papers if p.phid == event.phid)
    payloads[fact.source_url] = DownloadedFile(body, "application/pdf", "native.pdf")
    with pytest.raises(ValueError, match=expected):
        client.fetch_exam_page(event.source_exam_id, event.year_ad)
    assert event.source_exam_id not in client._pages and fact.source_url not in client._payloads


def test_unknown_source_urls_are_rejected_before_any_asset_request(snapshot):
    client, _, _, events = snapshot
    event = events["credit-junior"]
    original = client._fetch_text.side_effect
    client._fetch_text.side_effect = lambda url: (
        original(url)
        + '<a href="https://service.tabf.org.tw/BEExam/Doc/ExamHistoryEdit/999999-1.pdf">新試題</a>'
    )
    with pytest.raises(ValueError, match="paper links require review"):
        client.fetch_exam_page(event.source_exam_id, event.year_ad)
    client.http.download.assert_not_called()
    with pytest.raises(ValueError, match="cannot be acquired automatically"):
        client.download_file("https://service.tabf.org.tw/BEExam/Doc/ExamHistoryEdit/999999-1.pdf")
    client.http.download.assert_not_called()


def test_file_reordering_does_not_rename_acquisition_keys(snapshot):
    client, native, _, events = snapshot
    event = events["credit-junior"]
    first = client.fetch_exam_page(event.source_exam_id, event.year_ad)
    original = client._fetch_text.side_effect
    second_client = TabfCertClient()

    def reversed_html(url):
        html = original(url)
        if url != HISTORY_INDEX_URL:
            files = parse_native_files(html)
            for f in files:
                html = html.replace(f'<a href="{f.url}">{f.label}</a>', "")
            html += "".join(f'<a href="{f.url}">{f.label}</a>' for f in reversed(files))
        return html

    second_client._fetch_text = Mock(side_effect=reversed_html)
    second_client.http.download = client.http.download
    second = second_client.fetch_exam_page(event.source_exam_id, event.year_ad)

    def keys(page):
        return {next(iter(p.files.values())): p.subject_code for p in page.papers}

    assert keys(first) == keys(second) and len(set(keys(first).values())) == 3


@pytest.mark.parametrize(
    "change", ["grade", "year", "role", "variant", "module", "foreign", "duplicate"]
)
def test_native_evidence_rejects_contradictory_programme_role_date_and_module_facts(
    tmp_path, change
):
    root = Path(__file__).resolve().parents[1]
    payload = json.loads(evidence.native_papers_path(root).read_text())
    if change == "grade":
        next(e for e in payload["events"] if e["phid"] == "381")["programme_code"] = "credit-junior"
    elif change == "year":
        next(e for e in payload["events"] if e["phid"] == "381")["year_ad"] = 2024
    elif change == "role":
        payload["papers"][0]["file_type"] = "answer"
    elif change == "variant":
        next(e for e in payload["events"] if e["phid"] == "419")["programme_code"] = (
            "bank-internal-control-consumer"
        )
    elif change == "module":
        next(e for e in payload["events"] if e["phid"] == "274")["programme_code"] = "fit-subject-i"
    elif change == "foreign":
        payload["papers"][0]["source_url"] = "https://foreign.test/paper.pdf"
    else:
        payload["papers"].append(payload["papers"][0])
    path = tmp_path / "native.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        read_native_evidence(path)


def test_legacy_wrong_identity_gets_an_independent_review_without_rewriting_original_fields():
    from copy import deepcopy

    native = evidence.native_evidence()
    event = native.event("274")
    paper = next(p for p in native.papers if p.phid == "274")
    entry = {
        "id": "earlier-source",
        "source_record": {
            "provider_id": "tabf_cert",
            "source_exam_id": "tabf-cert-bank-internal-control-2026-phid-274",
            "download_url_source": paper.source_url,
            "checksum": paper.checksum,
            "file_type": "question",
            "year_roc": 115,
        },
    }
    before = deepcopy(entry)
    root = Path(__file__).resolve().parents[1]
    reviewed = evidence.reviewed_native_revisions([entry], repo_root=root)
    assert entry == before and len(reviewed) == 1
    assert reviewed[0]["year_ad"] == 2020 and reviewed[0]["programme_code"] == "fit-subject-ii"
    entry["source_record"]["checksum"] = "0" * 64
    assert evidence.reviewed_native_revisions([entry], repo_root=root) == []


def classify_context(context, **overrides):
    from app.classification import classify_paper

    event, paper = context.event, context.paper
    fields = dict(
        provider_id="tabf_cert",
        source_exam_id=event.source_exam_id,
        year_ad=event.year_ad,
        category_code=event.programme_code,
        canonical_id=event.programme.canonical_id,
        canonical_name=event.programme.canonical_label,
        category_raw="無法用此名稱猜等級",
        exam_name_raw="第一節不代表第一試",
        subject_code=paper.subject_code,
        subject_name_raw=paper.subject_label,
        source_checksum=paper.checksum,
        source_material=context.material,
    )
    fields.update(overrides)
    return classify_paper(**fields)


def context_for(native, programme_code, role="question"):
    event = next(e for e in native.events if e.programme_code == programme_code)
    paper = next(p for p in native.papers if p.phid == event.phid and p.file_type == role)
    return native.context(event.phid, paper.source_url, paper.checksum)


def test_native_credit_grades_and_sections_have_distinct_meanings(snapshot):
    _, native, _, _ = snapshot
    junior = classify_context(context_for(native, "credit-junior"))
    advanced = classify_context(context_for(native, "credit-advanced"))
    assert junior.confidence == advanced.confidence == "high"
    assert junior.level_id == "tabf-junior" and advanced.level_id == "tabf-advanced"
    assert junior.bundle_id != advanced.bundle_id
    assert junior.stage_id == advanced.stage_id == "not-applicable"
    assert junior.bundle_name.count("初階") == 1 and advanced.bundle_name.count("進階") == 1


@pytest.mark.parametrize(
    "first,second",
    [
        ("trust-business", "trust-law-only"),
        ("bank-internal-control-general", "bank-internal-control-consumer"),
    ],
)
def test_identical_native_questions_do_not_merge_qualification_variants(snapshot, first, second):
    _, native, _, _ = snapshot
    a = context_for(native, first)
    b = context_for(native, second)
    assert a.paper.checksum == b.paper.checksum
    one, two = classify_context(a), classify_context(b)
    assert one.confidence == two.confidence == "high" and one.bundle_id != two.bundle_id
    assert one.variant_ids != two.variant_ids


def test_fit_is_an_employment_assessment_with_separate_modules_not_awarded_grades(snapshot):
    from app.classification import public_facets

    _, native, _, _ = snapshot
    identities = [
        classify_context(context_for(native, "fit-subject-" + module))
        for module in ["i", "ii", "iii", "iv"]
    ]
    assert len({i.bundle_id for i in identities}) == 4
    for identity in identities:
        assert (
            identity.domain_id == "employment"
            and identity.exam_family_id == "employment-assessment"
        )
        assert (
            identity.exam_series_id == "financial-aptitude"
            and identity.level_id == "not-applicable"
        )
        assert identity.stage_id == "not-applicable" and identity.confidence == "high"
        assert public_facets(identity)["exam_class"] == "就業能力測驗"


@pytest.mark.parametrize("change", ["year", "grade", "checksum", "subject", "material"])
def test_a_native_checksum_cannot_approve_different_context_fields(snapshot, change):
    _, native, _, _ = snapshot
    context = context_for(native, "credit-junior")
    changes = {
        "year": {"year_ad": 2026},
        "grade": {"category_code": "credit-advanced"},
        "checksum": {"source_checksum": "0" * 64},
        "subject": {"subject_code": "wrong"},
        "material": {"source_material": None},
    }
    assert classify_context(context, **changes[change]).confidence == "review"


def test_every_reviewed_native_paper_resolves_to_its_own_scope_and_purpose():
    native = evidence.native_evidence()
    groups = set()
    for paper in native.papers:
        context = native.context(paper.phid, paper.source_url, paper.checksum)
        identity = classify_context(context)
        assert identity.confidence == "high" and identity.stage_id == "not-applicable"
        groups.add(identity.bundle_id)
    assert len(groups) == 19


def test_current_native_projection_cannot_duplicate_one_reviewed_anchor(tmp_path, monkeypatch):
    from app.models import NormalizedCatalog, NormalizedPaper

    native = evidence.native_evidence()
    fact = native.papers[0]
    context = native.context(fact.phid, fact.source_url, fact.checksum)
    event = context.event
    paper = NormalizedPaper(
        canonical_id=event.programme.canonical_id,
        canonical_name=event.programme.canonical_label,
        year_roc=event.year_ad - 1911,
        exam_name_raw="native event",
        category_raw=event.programme.label,
        subject_name_raw=fact.subject_label,
        paper_code="native",
        file_type=fact.file_type,
        download_url_source=fact.source_url,
        category_code=event.programme_code,
        source_exam_id=event.source_exam_id,
        subject_code=fact.subject_code,
        checksum=fact.checksum,
        storage_key="providers/tabf_cert/native.pdf",
        provider_id="tabf_cert",
        schema_version=3,
        source_material=context.material,
    )
    monkeypatch.setattr(evidence, "read_native_evidence", lambda path: native)
    monkeypatch.setattr(
        "app.state.load_provider_state",
        lambda provider: ([], NormalizedCatalog([paper, paper], []), []),
    )
    with pytest.raises(ValueError, match="Duplicate TABF current"):
        evidence.validate_native_papers(tmp_path)


def test_review_capture_keeps_unknown_events_and_HTML_failures_without_PDF_requests(
    snapshot, tmp_path, monkeypatch
):
    from app.models import NormalizedCatalog
    from scripts import capture_tabf_review

    client, native, _, _ = snapshot
    original = client._fetch_text.side_effect

    def html(url):
        if url == HISTORY_INDEX_URL:
            return original(url) + table("初階授信人員專業能力測驗", ("999999", "第99屆試題"))
        if url.endswith("PHID=999999"):
            raise OSError("captured HTML failure")
        return original(url)

    client._fetch_text.side_effect = html
    monkeypatch.setattr(capture_tabf_review, "native_evidence", lambda: native)
    monkeypatch.setattr(
        capture_tabf_review,
        "load_provider_state",
        lambda provider: ([], NormalizedCatalog([], []), []),
    )
    report = capture_tabf_review.capture_review(
        tmp_path / "review", repo_root=tmp_path, client=client
    )
    unknown = next(row for row in report["events"] if row["phid"] == "999999")
    assert unknown["native_review_required"] and unknown["reviewed_event"] is None
    assert not report["HTML_capture_complete"] and report["examination_PDF_requests"] == 0
    assert any("captured HTML failure" in row.get("error", "") for row in report["surfaces"])
    assert json.loads((tmp_path / "review/review.json").read_text()) == report
    client.http.download.assert_not_called()
