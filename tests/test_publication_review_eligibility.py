"""A classification hold must survive both publication paths and year policy."""

import hashlib
import json
import zipfile
from dataclasses import replace
from unittest.mock import patch

import pytest

from app.bundler import build_bundles, public_bundle_ids, public_bundle_ids_from_indexes
from app.models import NormalizedCatalog, NormalizedPaper
from app.paths import provider_paths
from app.provider_index import (
    PAPER_CLASSIFICATION_CONFIDENCE,
    build_provider_index,
    indexed_material_summaries,
    load_provider_index,
    write_provider_index,
)
from app.publisher import load_site_provider_indexes, write_provider_state
from app.site_registry import SiteConfig


def paper(**changes):
    fields = dict(
        provider_id="example",
        source_exam_id="exam-115",
        year_roc=115,
        canonical_id="example",
        canonical_name="Example",
        bundle_id="example-bundle",
        schema_version=2,
        exam_name_raw="Example",
        category_raw="Category",
        subject_name_raw="Subject",
        subject_code="subject",
        paper_code="paper-1",
        file_type="question",
        download_url_source="https://example.test/paper.pdf",
        storage_key="115/paper.pdf",
        classification_confidence="high",
    )
    return NormalizedPaper(**(fields | changes))


@pytest.mark.parametrize("minimum_years", [1, 2])
def test_review_rows_do_not_establish_bundle_eligibility_on_either_path(tmp_path, minimum_years):
    approved = paper()
    held_same_bundle = replace(
        approved,
        year_roc=114,
        source_exam_id="exam-114",
        classification_confidence="review",
    )
    held_other_bundle = replace(held_same_bundle, bundle_id="unresolved-bundle")
    catalog = NormalizedCatalog([approved, held_same_bundle, held_other_bundle], [])
    index = build_provider_index(provider_paths(tmp_path, "example"), [], catalog.papers)
    policy = {"min_years": minimum_years}
    expected = {"example-bundle"} if minimum_years == 1 else set()
    assert public_bundle_ids(catalog, **policy) == expected
    assert public_bundle_ids_from_indexes([index], **policy) == expected
    assert indexed_material_summaries([index]) == {}


def test_archive_build_omits_review_rows_without_needing_their_missing_bytes(tmp_path):
    payload = b"%PDF-1.7\napproved source\n"
    approved = paper(checksum=hashlib.sha256(payload).hexdigest())
    held = replace(
        approved,
        paper_code="held",
        subject_code="held",
        storage_key="missing.pdf",
        classification_confidence="review",
    )
    mirror = tmp_path / "mirror"
    (mirror / approved.storage_key).parent.mkdir(parents=True)
    (mirror / approved.storage_key).write_bytes(payload)
    result = build_bundles(
        tmp_path / "bundles",
        mirror,
        NormalizedCatalog([approved, held], []),
        "",
    )
    assert not result.failures
    assert len(result.bundles) == 1
    assert result.bundles[0].file_count == 1
    with zipfile.ZipFile(tmp_path / "bundles" / result.bundles[0].asset_name) as archive:
        members = [name for name in archive.namelist() if name.endswith(".pdf")]
        assert len(members) == 1
        assert archive.read(members[0]) == payload


@pytest.mark.parametrize("version,row_length", [(1, 11), (2, 12), (3, 15)])
def test_older_indexes_remain_readable_but_cannot_decide_publication(tmp_path, version, row_length):
    provider = provider_paths(tmp_path, "example")
    write_provider_state(
        provider,
        [],
        NormalizedCatalog([paper()], []),
        [],
        [],
        None,
    )
    index = load_provider_index(provider)
    index["schema_version"] = version
    index["papers"] = [row[:row_length] for row in index["papers"]]
    write_provider_index(provider, index)
    assert load_provider_index(provider) == index
    with pytest.raises(ValueError, match="Rebuild provider index"):
        public_bundle_ids_from_indexes([index])
    with pytest.raises(ValueError, match="Rebuild provider index"):
        indexed_material_summaries([index])
    config = SiteConfig("test", ("example",), "test-bundles")
    with (
        patch("app.publisher.get_site_config", return_value=config),
        patch("app.publisher.quarantined_provider_ids", return_value=set()),
    ):
        assert load_site_provider_indexes(tmp_path, site_id="test") is None


@pytest.mark.parametrize("confidence", [None, False, [], "unreviewed"])
def test_index_reader_rejects_invalid_classification_confidence(tmp_path, confidence):
    provider = provider_paths(tmp_path, "example")
    index = build_provider_index(provider, [], [paper()])
    index["papers"][0][PAPER_CLASSIFICATION_CONFIDENCE] = confidence
    provider.index_path.parent.mkdir(parents=True)
    provider.index_path.write_text(json.dumps(index))
    with pytest.raises(ValueError, match="classification confidence is invalid"):
        load_provider_index(provider)
