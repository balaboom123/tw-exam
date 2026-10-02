import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.models import NormalizedCatalog
from scripts.validate_publication import validate_provider_site_coverage


class PublicationFailureGateTests(unittest.TestCase):
    def test_unresolved_failure_blocks_without_reloading_provider_papers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            provider = root / "data/providers/ceec_ast"
            (provider / "papers").mkdir(parents=True)
            # The site catalog was already loaded. This second pass only needs
            # the failure ledger, even when the paper tree is large or damaged.
            (provider / "papers/2026.json").write_text("invalid JSON", encoding="utf-8")
            (provider / "sync-failures.json").write_text(json.dumps([{
                "stage": "download", "source_exam_id": "ast-115", "year_roc": 115,
                "paper_code": "math-question", "file_type": "question",
                "url": "https://example.test/math.pdf", "message": "source unavailable",
            }]), encoding="utf-8")

            with patch("scripts.validate_publication.load_site_catalog", return_value=(
                NormalizedCatalog(papers=[], review_queue=[]), [],
            )):
                with self.assertRaisesRegex(ValueError, "1 unresolved sync failures"):
                    validate_provider_site_coverage(set(), repo_root=root)


if __name__ == "__main__":
    unittest.main()


def test_subject_tracks_must_have_distinct_public_titles():
    import pytest
    from scripts.validate_publication import validate_distinct_track_titles
    rows = [dict(id=f'ceec-ast-{track}', name='分科測驗', seriesId='ast',
                 levelId='not-applicable', trackId=track) for track in ('physics', 'chemistry')]
    with pytest.raises(ValueError, match='share a public title'):
        validate_distinct_track_titles(rows)
    rows[0]['name'] += '｜物理'
    rows[1]['name'] += '｜化學'
    validate_distinct_track_titles(rows)


def test_readable_title_gate_rejects_all_emitted_variant_and_stage_ids():
    import pytest
    from app.classification import classify_paper
    from scripts.validate_publication import validate_readable_titles

    # Exercise each real source pattern through its owner, including hashed
    # variants, rather than inventing another ID taxonomy for the validator.
    for category in (
        "一般行政（一般組）", "一般行政（兩岸組一）", "一般行政（兩岸組二）",
        "一般行政（兩岸組三）", "外交領事人員（選試日文）",
        "一般行政（國防部）", "一般行政（臺北錄取分發區）",
    ):
        identity = classify_paper(
            provider_id="moex", source_exam_id="115-test", year_ad=2026,
            category_raw=category, exam_name_raw="115年公務人員高等考試三級",
            canonical_id="general-administration", canonical_name="一般行政",
        )
        assert identity.variant_ids, category
        readable = {"name": identity.bundle_name, "variantIds": list(identity.variant_ids),
                    "stageId": identity.stage_id}
        validate_readable_titles([readable])
        for identifier in identity.variant_ids:
            for leaked in (identifier, f"考試｜{identifier}", f"考試｜一般組、{identifier}"):
                with pytest.raises(ValueError, match="public titles expose internal identifiers"):
                    # IDs belong to the whole feed, not just the offending row.
                    validate_readable_titles([readable, {"name": leaked}])

    for identifier in ("stage-1", "stage-2", "stage-3", "pretest"):
        with pytest.raises(ValueError, match="public titles expose internal identifiers"):
            validate_readable_titles([{"name": f"考試｜{identifier}"}])


def test_readable_title_gate_accepts_latin_subject_names():
    from scripts.validate_publication import validate_readable_titles
    validate_readable_titles([
        {"name": "全國技術士技能檢定｜乙級｜電腦軟體設計(C++)"},
        {"name": "全國技術士技能檢定｜乙級｜銑床─CNC銑床"},
        {"name": "全國技術士技能檢定｜乙級｜視覺傳達設計─平面設計PC"},
        {"name": "高等考試｜三等／高考三級｜政風｜選試日文｜第二試"},
        {"name": "Computer-based Test"},
        {"name": "跨平台 cross-platform 程式設計"},
        {"name": "English pretest"},
        {"name": "Pretesting and stage-1-based teaching"},
    ])


def test_track_title_prefixes_have_one_owner():
    from app.classification import TRACK_TITLED_PROVIDERS
    from scripts.validate_publication import GENERIC_SUBJECT_PREFIXES
    assert set(GENERIC_SUBJECT_PREFIXES) == {
        "wdasec-skill-", "ceec-gsat-", "ceec-ast-", "tcte-tve-",
    }
    assert set(GENERIC_SUBJECT_PREFIXES) == {
        provider.replace("_", "-") + "-" for provider in TRACK_TITLED_PROVIDERS
    }
