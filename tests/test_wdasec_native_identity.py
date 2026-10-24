"""Native paper facts correct publication without rewriting source observations."""

import json
from dataclasses import asdict, replace

import pytest

from app.bundler import _bundle_arcname
from app.models import NormalizedCatalog, ParsedPaper, file_type_label
from app.normalizer import normalize_papers, renormalize_catalog
from app.providers.wdasec_skill.papers import native_papers, native_papers_path, read_native_papers
from app.publication_metadata import publication_paper_subject, publication_paper_subject_code
from tests.test_wdasec_forms import normalized

PIANO = [paper for paper in native_papers() if paper.category_code == "06800"]
SOURCE_FIELDS = (
    "year_roc",
    "source_exam_id",
    "exam_name_raw",
    "category_code",
    "subject_code",
    "subject_name_raw",
    "download_url_source",
    "checksum",
    "storage_key",
)


@pytest.mark.parametrize("native", PIANO, ids=lambda paper: str(paper.year_roc))
def test_complete_piano_history_has_one_native_grade_and_combined_file_role(native):
    assert len(PIANO) == 9
    original = replace(
        normalized(native), storage_key="native.doc" if native.year_roc == 90 else "native.pdf"
    )
    result = renormalize_catalog(NormalizedCatalog([original], []), [])
    paper = result.papers[0]
    assert paper.level_id == "single"
    assert paper.bundle_id == "wdasec-skill-skill-certification-single-skill-7982cc17114e"
    assert paper.file_type == "question_answer"
    assert paper.classification_confidence == "high"
    assert not result.review_queue
    assert {field: getattr(paper, field) for field in SOURCE_FIELDS} == {
        field: getattr(original, field) for field in SOURCE_FIELDS
    }
    assert asdict(renormalize_catalog(result, []).papers[0]) == asdict(paper)
    assert publication_paper_subject(paper) == "鋼琴調音 單一級 學科"
    assert publication_paper_subject_code(paper) == "06800-single-question"
    path = _bundle_arcname(paper)
    assert "甲級" not in path and "class_a" not in path
    assert "單一級" in path and file_type_label("question_answer") in path

    raw = ParsedPaper(
        category_raw=original.category_raw,
        category_code=native.category_code,
        subject_code=native.subject_code,
        subject_name_raw=native.subject_name_raw,
        files={"question": native.source_url},
        mirror_files={
            "question": {"checksum": native.checksum, "storage_key": original.storage_key}
        },
    )
    acquired = normalize_papers(
        native.source_exam_id,
        native.year_roc + 1911,
        native.exam_name_raw,
        [raw],
        [],
        "",
        {},
        provider_id="wdasec_skill",
    ).papers[0]
    assert acquired.level_id == paper.level_id
    assert acquired.bundle_id == paper.bundle_id
    assert acquired.file_type == paper.file_type
    assert raw.subject_name_raw == native.subject_name_raw
    assert set(raw.files) == {"question"}


@pytest.mark.parametrize("native", [paper for paper in PIANO if paper.year_roc in {90, 93}])
@pytest.mark.parametrize(
    "field,value",
    [
        ("year_roc", 899),
        ("source_exam_id", "209901010001"),
        ("exam_name_raw", "Unreviewed source title"),
        ("category_code", "99999"),
        ("subject_code", "06800-single-practical"),
        ("subject_name_raw", "Different source label"),
        ("download_url_source", "https://owinform.wdasec.gov.tw/new.pdf"),
        ("checksum", "0" * 64),
    ],
)
def test_grade_role_and_display_cannot_transfer_to_an_unreviewed_context(native, field, value):
    original = replace(normalized(native), **{field: value})
    paper = renormalize_catalog(NormalizedCatalog([original], []), []).papers[0]
    assert paper.classification_confidence == "review"
    assert paper.file_type == original.file_type == "question"
    assert "paper-form-unknown" in paper.variant_ids
    assert publication_paper_subject_code(paper) == original.subject_code


@pytest.mark.parametrize(
    "field,value",
    [
        ("file_role", "answer"),
        ("native_grade", "甲級"),
        ("evidence", ""),
        ("subject_label", "An unsupported label"),
        ("level_id", "professional-high"),
        ("level_id", "class-a"),
    ],
)
def test_native_identity_requires_complete_independent_grade_and_role_evidence(
    tmp_path, field, value
):
    from pathlib import Path

    document = json.loads(native_papers_path(Path(__file__).resolve().parents[1]).read_text())
    document["papers"] = [
        next(row for row in document["papers"] if row["category_code"] == "06800")
    ]
    dimension = "reviewed_role" if field == "file_role" else "reviewed_grade"
    document["papers"][0][dimension][field] = value
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        read_native_papers(path)


def test_retained_grade_page_follows_only_an_approved_role_and_identity_migration(
    tmp_path, monkeypatch
):
    import hashlib

    from app import publisher
    from app.bundler import build_bundles
    from app.paths import provider_paths, site_paths
    from app.providers.wdasec_skill import papers as owner
    from app.retained_downloads import derive_compatibility_pages, load_retained_downloads

    native_rows = []
    old_papers = []
    old_id = "wdasec-skill-skill-certification-class-a-skill-9dd0e2a8b616"
    for native in [row for row in PIANO if row.year_roc in {90, 93}]:
        payload = (
            b"%PDF-1.7 native single-grade questions and answers " + str(native.year_roc).encode()
        )
        checksum = hashlib.sha256(payload).hexdigest()
        native = replace(native, checksum=checksum)
        native_rows.append(native)
        key = f"providers/wdasec_skill/{native.year_roc}/native.pdf"
        path = tmp_path / "mirror" / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        old_papers.append(
            replace(
                normalized(native),
                storage_key=key,
                bundle_id=old_id,
                bundle_name="全國技術士技能檢定｜甲級｜鋼琴調音",
                schema_version=2,
                catalog_version="exam-identity-v2",
                domain_id="certification",
                exam_family_id="skill-certification",
                exam_series_id="skill-certification",
                level_id="class-a",
                track_id="skill-9dd0e2a8b616",
                classification_confidence="high",
            )
        )
    monkeypatch.setattr(owner, "_index", lambda: {row.key: row for row in native_rows})
    site = site_paths(tmp_path, "default")
    provider = provider_paths(tmp_path, "wdasec_skill")
    old_catalog = NormalizedCatalog(old_papers, [])
    publisher.write_provider_state(provider, [], old_catalog, [], [], None)
    built = build_bundles(site.bundle_dir, tmp_path / "mirror", old_catalog, "")
    assert not built.failures and len(built.bundles) == 1
    old = replace(
        built.bundles[0],
        release_tag="default-bundles-v2-001",
        storage_key="bundles/sites/default/" + built.bundles[0].asset_name,
    )
    publisher.write_site_state(site, [old], [])
    old_bytes = (tmp_path / old.storage_key).read_bytes()
    current = renormalize_catalog(old_catalog, [])
    publisher.write_provider_state(provider, [], current, [], [], None)
    monkeypatch.setattr(publisher, "load_site_catalog", lambda *args, **kwargs: (current, []))
    _, new = publisher.publish_site(
        tmp_path,
        site_id="default",
        repository="example/repo",
        retain_existing_download_ids={old_id},
    )
    held = load_retained_downloads(site)
    pages = derive_compatibility_pages(tmp_path, site, held, current.papers, new)
    assert len(pages) == 1 and pages[0]["successorIds"] == [current.papers[0].bundle_id]
    assert (tmp_path / old.storage_key).read_bytes() == old_bytes
    assert len(new) == 1 and new[0].file_count == 2
    assert all(paper.file_type == "question_answer" for paper in current.papers)
    assert all(paper.file_type == "question" for paper in old_papers)
    for change in [
        {"checksum": "0" * 64},
        {"subject_name_raw": "unreviewed label"},
        {"source_exam_id": "209901010001"},
    ]:
        changed = [replace(current.papers[0], **change), current.papers[1]]
        assert derive_compatibility_pages(tmp_path, site, held, changed, new) == []
    assert derive_compatibility_pages(tmp_path, site, held, current.papers[:1], new) == []
    assert derive_compatibility_pages(tmp_path, site, held, current.papers, []) == []
