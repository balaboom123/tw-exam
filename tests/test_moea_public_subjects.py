"""Native subjects must agree across discovery metadata and archive entries."""

import hashlib
import json
import zipfile
from dataclasses import asdict, replace
from unittest.mock import patch

import pytest

from app.bundler import build_bundles
from app.models import NormalizedCatalog, NormalizedPaper
from app.providers.moea_recruit.evidence import native_roles, source_conflicts
from app.publication_metadata import derive_public_metadata, publication_paper_subject


def role_paper(fact):
    return NormalizedPaper(
        canonical_id="moea-recruit",
        canonical_name="經濟部所屬事業機構新進職員甄試",
        year_roc=fact.year_roc,
        exam_name_raw="經濟部所屬事業機構新進職員甄試",
        category_raw=fact.category_raw,
        subject_name_raw=fact.title,
        paper_code=f"{fact.category_code}-{fact.subject_code}-{fact.native_role}",
        file_type=fact.native_role,
        download_url_source=fact.source_url,
        category_code=fact.category_code,
        source_exam_id=fact.source_exam_id,
        subject_code=fact.subject_code,
        storage_key=f"providers/moea_recruit/{fact.subject_code}.pdf",
        checksum=fact.checksum,
        provider_id="moea_recruit",
    )


def conflict_paper(conflict, file):
    template = role_paper(native_roles()[0])
    return replace(
        template,
        year_roc=conflict.year_roc,
        source_exam_id=f"moea-recruit-{conflict.year_roc}",
        category_code=str(conflict.year_roc),
        category_raw=conflict.listing_group,
        subject_code=file.subject_code,
        subject_name_raw=file.source_label,
        file_type=file.native_role,
        download_url_source=file.source_url,
        checksum=file.checksum,
        storage_key=f"providers/moea_recruit/{file.subject_code}.pdf",
    )


CASES = [(role_paper(fact), fact.native_subject) for fact in native_roles()] + [
    (conflict_paper(conflict, file), file.native_subject)
    for conflict in source_conflicts()
    for file in conflict.native_files
]


@pytest.mark.parametrize("paper,subject", CASES)
def test_all_reviewed_subjects_project_from_objects_and_mappings(paper, subject):
    before = asdict(paper)
    assert publication_paper_subject(paper) == subject
    assert publication_paper_subject(before) == subject
    aliases, _ = derive_public_metadata(
        [paper], bundle_id=paper.canonical_id, canonical_name=paper.canonical_name
    )
    assert subject in aliases
    assert asdict(paper) == before


@pytest.mark.parametrize("case", [0, -1])
@pytest.mark.parametrize(
    "field,value",
    [
        ("provider_id", "taipower_recruit"),
        ("year_roc", 99),
        ("source_exam_id", "other-event"),
        ("category_code", "other-category"),
        ("category_raw", "另一類組"),
        ("subject_code", "other-subject"),
        ("subject_name_raw", "另一科目"),
        ("download_url_source", "https://example.test/other.pdf"),
    ],
)
def test_reviewed_subjects_never_transfer_to_another_context(case, field, value):
    original, subject = CASES[case]
    paper = replace(original, **{field: value})
    assert publication_paper_subject(paper) != subject


@pytest.mark.parametrize("case", [0, -1])
def test_changed_bytes_or_unmigrated_roles_cannot_claim_native_subjects(case):
    paper, _ = CASES[case]
    with pytest.raises(ValueError, match="changed or unreviewed"):
        publication_paper_subject(replace(paper, checksum="0" * 64))
    with pytest.raises(ValueError, match="reviewed native file role"):
        publication_paper_subject(replace(paper, file_type="transcript"))


def test_archive_names_and_shared_payloads_preserve_all_source_references(tmp_path):
    chemistry = tuple(f for f in native_roles() if f.year_roc == 109)
    assert len(chemistry) == 2
    conflict = source_conflicts()[0]
    payloads = {}
    for fact in chemistry:
        payloads[fact.source_url] = b"%PDF-1.7 identical chemistry questions and answers"
    for file in conflict.native_files:
        payloads[file.source_url] = (
            b"%PDF-1.7 " + file.native_subject.encode() + file.native_role.encode()
        )
    chemistry = tuple(
        replace(f, checksum=hashlib.sha256(payloads[f.source_url]).hexdigest())
        for f in chemistry
    )
    conflict = replace(
        conflict,
        native_files=tuple(
            replace(f, checksum=hashlib.sha256(payloads[f.source_url]).hexdigest())
            for f in conflict.native_files
        ),
    )
    papers = [role_paper(f) for f in chemistry] + [
        conflict_paper(conflict, f) for f in conflict.native_files
    ]
    for paper in papers:
        path = tmp_path / "mirror" / paper.storage_key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payloads[paper.download_url_source])
    before = [asdict(p) for p in papers]
    with (
        patch("app.providers.moea_recruit.evidence.native_roles", return_value=chemistry),
        patch("app.providers.moea_recruit.evidence.source_conflicts", return_value=(conflict,)),
    ):
        result = build_bundles(
            tmp_path / "bundles", tmp_path / "mirror", NormalizedCatalog(papers, []), ""
        )
    assert not result.failures and len(result.bundles) == 1
    with zipfile.ZipFile(tmp_path / "bundles" / result.bundles[0].asset_name) as archive:
        records = json.loads(archive.read("bundle.json"))["papers"]
        assert len(records) == 5
        assert len({r["bundle_entry"] for r in records}) == 4
        assert len(archive.namelist()) == 5  # Four payloads and the complete manifest.
        by_url = {p.download_url_source: p for p in papers}
        for record in records:
            paper = by_url[record["download_url_source"]]
            name = record["bundle_entry"]
            assert name.startswith(f"{paper.year_roc}/")
            assert archive.read(name) == payloads[paper.download_url_source]
            assert "新進職員" not in name
        answer = next(r for r in records if r["file_type"] == "answer")
        assert "計算機概論、電子學" in answer["bundle_entry"]
        assert "專業科目A" in answer["bundle_entry"]
        assert "電路學、電磁學" not in answer["bundle_entry"]
    assert [asdict(p) for p in papers] == before
