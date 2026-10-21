"""Native Taigi source boundaries, material dates and original packaging."""

import hashlib
import io
import json
import zipfile
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from app.classification import classify_paper
from app.providers.base import DownloadedFile
from app.providers.taigi_cert import evidence
from app.providers.taigi_cert.client import TaigiCertClient, parse_downloads


def listing(resources):
    return "".join(f'<a href="{r.source_url}">{r.source_label}</a>' for r in resources)


@pytest.fixture
def snapshot(monkeypatch):
    originals = evidence.native_resources()
    bodies = {}
    for resource in originals:
        if resource.payload_format == "zip":
            continue
        signature = b"%PDF-1.7 fixture " if resource.payload_format == "pdf" else b"ID3 fixture "
        bodies[resource.source_url] = signature + resource.source_url.encode()
    for resource in originals:
        if resource.payload_format != "zip":
            continue
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for member in resource.archive_members:
                archive.writestr(member.native_name, bodies[member.source_url])
        bodies[resource.source_url] = buffer.getvalue()
    checksums = {url: hashlib.sha256(body).hexdigest() for url, body in bodies.items()}
    resources = tuple(
        replace(
            resource,
            checksum=checksums[resource.source_url],
            id=resource.id.rsplit("-", 1)[0] + "-" + checksums[resource.source_url][:20],
            archive_members=tuple(
                replace(
                    member,
                    checksum=checksums[member.source_url],
                    bytes=len(bodies[member.source_url]),
                )
                for member in resource.archive_members
            ),
        )
        for resource in originals
    )
    monkeypatch.setattr(evidence, "native_resources", lambda: resources)
    client = TaigiCertClient()
    client._fetch_text = Mock(return_value=listing(resources))
    client.http.download = Mock(
        side_effect=lambda url, **kwargs: DownloadedFile(
            bodies[url], "application/octet-stream", "source"
        )
    )
    return client, resources, bodies


def test_sample_roles_separate_original_archives_and_audio_skills(snapshot):
    client, resources, _ = snapshot
    assert Counter(item.file_type for item in parse_downloads(listing(resources))) == {
        "question": 12,
        "answer": 12,
        "listening_audio": 3,
        "speaking_audio": 3,
        "dictation_audio": 2,
        "source_archive": 3,
    }
    pages = [client.fetch_exam_page(f"taigi-cert-{form}-samples", 2026) for form in "abc"]
    assert sum(len(page.papers) for page in pages) == 32
    assert sum(len(page.attachments) for page in pages) == 3
    assert all(a.file_type == "source_archive" for page in pages for a in page.attachments)
    assert all(p.source_material.kind == "sample" for page in pages for p in page.papers)
    client.http.download.assert_not_called()


def test_stable_ids_and_native_publication_dates_are_independent_of_capture_partition(snapshot):
    client, resources, _ = snapshot
    assert client.discover_available_years() == [2026]
    assert [event.code for event in client.discover_exams(2026)] == [
        "taigi-cert-a-samples",
        "taigi-cert-b-samples",
        "taigi-cert-c-samples",
    ]
    assert client.discover_exams(2027) == []
    assert (
        client.fetch_exam_page("taigi-cert-a-2026", 2026).source_exam_id == "taigi-cert-a-samples"
    )
    assert {r.material.date.year_ad for r in resources} == {2017, None}
    assert sum(r.published_on is not None for r in resources) == 19
    assert sum(r.revised_on == "2023-02-22" for r in resources) == 18
    assert not any(r.material.date.basis == "exam_year" for r in resources)
    assert client._fetch_text.call_count == 1


@pytest.mark.parametrize(
    "code,year",
    [
        ("taigi-cert-materials", 2026),
        ("taigi-cert-d-samples", 2026),
        ("taigi-cert-a-samples", 2027),
    ],
)
def test_unknown_selector_cannot_collect_every_resource(snapshot, code, year):
    client, _, _ = snapshot
    with pytest.raises(ValueError, match="reviewed form/partition"):
        client.fetch_exam_page(code, year)
    client._fetch_text.assert_not_called()
    client.http.download.assert_not_called()


@pytest.mark.parametrize("change", ["label", "url", "missing", "foreign"])
def test_listing_changes_require_review_without_asset_requests(snapshot, change):
    client, resources, _ = snapshot
    first = resources[0]
    html = listing(resources)
    if change == "label":
        html = html.replace(first.source_label, "A卷新增範例")
    elif change == "url":
        html = html.replace(first.source_url, first.source_url.replace("file_2", "file_999"))
    elif change == "missing":
        html = html.replace(f'<a href="{first.source_url}">{first.source_label}</a>', "")
    else:
        html = html.replace(
            first.source_url, first.source_url.replace("ttg.moe.edu.tw", "foreign.test")
        )
    client._fetch_text.return_value = html
    with pytest.raises(ValueError):
        client.discover_available_years()
    client.http.download.assert_not_called()


def test_only_explicit_general_learning_resources_are_excluded(snapshot):
    _, resources, _ = snapshot
    extras = "".join(
        f'<a href="{url}">{label}</a>'
        for url, label in evidence.EXCLUDED_LEARNING_RESOURCES.items()
    )
    assert len(parse_downloads(listing(resources) + extras)) == 35
    with pytest.raises(ValueError, match="excluded learning-resource"):
        parse_downloads(listing(resources) + extras.replace("學習地圖", "另一項範例"))


def test_pinned_payloads_and_exact_archive_members_are_verified(snapshot):
    client, resources, bodies = snapshot
    for resource in resources:
        assert client.download_file(resource.source_url).data == bodies[resource.source_url]
        assert client.download_file(resource.source_url).data == bodies[resource.source_url]
    assert client.http.download.call_count == 35
    archives = [resource for resource in resources if resource.payload_format == "zip"]
    assert sum(len(resource.archive_members) for resource in archives) == 32
    bad = replace(archives[0], archive_members=archives[0].archive_members[:-1])
    with pytest.raises(ValueError, match="member coverage"):
        evidence.validate_native_payload(bad, bodies[bad.source_url])


def test_unknown_urls_and_changed_payloads_fail_closed(snapshot):
    client, resources, bodies = snapshot
    with pytest.raises(ValueError, match="cannot be acquired"):
        client.download_file("https://ttg.moe.edu.tw/tmt/src/upload/file/file_999.pdf")
    client.http.download.assert_not_called()
    first = resources[0]
    bodies[first.source_url] = b"%PDF-1.7 unreviewed edition"
    with pytest.raises(ValueError, match="replacement bytes"):
        client.download_file(first.source_url)
    assert first.source_url not in client._payloads


@pytest.mark.parametrize(
    "change", ["form", "role", "skill", "date", "foreign", "member", "duplicate"]
)
def test_native_owner_rejects_contradictory_facts(tmp_path, change):
    root = Path(__file__).resolve().parents[1]
    payload = json.loads(evidence.native_materials_path(root).read_text())
    first = payload["resources"][0]
    if change == "form":
        first["form_code"] = "b"
    elif change == "role":
        first["file_type"] = "answer"
    elif change == "skill":
        first["skill"] = "書寫"
    elif change == "date":
        first["revised_on"] = "2016-01-01"
    elif change == "foreign":
        first["source_url"] = first["source_url"].replace("ttg.moe.edu.tw", "foreign.test")
    elif change == "member":
        next(r for r in payload["resources"] if r["file_type"] == "source_archive")[
            "archive_members"
        ][0]["checksum"] = "0" * 64
    else:
        payload["resources"].append(deepcopy(first))
    path = tmp_path / "native.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        evidence.read_native_resources(path)


def test_approved_replacement_preserves_the_earlier_native_context(tmp_path):
    root = Path(__file__).resolve().parents[1]
    payload = json.loads(evidence.native_materials_path(root).read_text())
    old = payload["resources"][0]
    old["current"] = False
    new = deepcopy(old)
    new.update(current=True, checksum="1" * 64, id=new["id"].rsplit("-", 1)[0] + "-" + "1" * 20)
    payload["resources"].append(new)
    path = tmp_path / "native.json"
    path.write_text(json.dumps(payload))
    resources = evidence.read_native_resources(path)
    assert len(resources) == 36 and sum(r.current for r in resources) == 35
    assert len({r.checksum for r in resources if r.source_url == old["source_url"]}) == 2


def classify_resource(resource, **overrides):
    fields = dict(
        provider_id="taigi_cert",
        source_exam_id=resource.source_exam_id,
        year_ad=2026,
        category_code=resource.form_code,
        canonical_id="taigi-cert-" + resource.form_code,
        canonical_name="臺灣台語語言能力認證",
        category_raw="不能由這個標題猜卷別",
        exam_name_raw="範例資源",
        subject_code=resource.subject_code,
        subject_name_raw=resource.source_label,
        source_checksum=resource.checksum,
        source_material=resource.material,
    )
    fields.update(overrides)
    return classify_paper(**fields)


def test_reviewed_materials_keep_paired_bands_and_sample_identity(snapshot):
    _, resources, _ = snapshot
    groups = set()
    for resource in resources:
        if resource.file_type == "source_archive":
            continue
        identity = classify_resource(resource)
        assert identity.confidence == "high"
        assert (
            identity.level_id
            == {"a": "cefr-a1-a2", "b": "cefr-b1-b2", "c": "cefr-c1-c2"}[resource.form_code]
        )
        assert identity.stage_id == "not-applicable"
        assert set(identity.variant_ids) == {"paper-form-" + resource.form_code, "material-sample"}
        groups.add(identity.bundle_id)
    assert len(groups) == 3


@pytest.mark.parametrize("change", ["checksum", "form", "subject", "date", "event"])
def test_correct_bytes_do_not_approve_another_context(snapshot, change):
    _, resources, _ = snapshot
    resource = resources[0]
    changes = {
        "checksum": {"source_checksum": "0" * 64},
        "form": {"category_code": "b"},
        "subject": {"subject_code": "different"},
        "date": {
            "source_material": replace(
                resource.material, date=evidence.SourceDate("publication_year", 2023)
            )
        },
        "event": {"source_exam_id": "taigi-cert-b-samples"},
    }
    assert classify_resource(resource, **changes[change]).confidence == "review"


def test_historical_role_and_date_views_do_not_rewrite_the_original(snapshot):
    _, resources, _ = snapshot
    resource = next(r for r in resources if r.file_type == "answer" and r.published_on)
    entry = {
        "id": "original",
        "source_record": {
            "provider_id": "taigi_cert",
            "source_exam_id": "taigi-cert-a-2026",
            "year_roc": 115,
            "download_url_source": resource.source_url,
            "checksum": resource.checksum,
            "file_type": "question",
        },
    }
    before = deepcopy(entry)
    with patch.object(evidence, "read_native_resources", return_value=resources):
        reviewed = evidence.reviewed_native_revisions([entry], repo_root=Path.cwd())
    assert entry == before and reviewed[0]["file_type"] == "answer"
    assert reviewed[0]["source_material"]["date"] == {"basis": "publication_year", "year_ad": 2017}
