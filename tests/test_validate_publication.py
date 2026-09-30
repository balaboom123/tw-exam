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


def test_readable_title_gate_rejects_internal_ids():
    import pytest
    from scripts.validate_publication import validate_readable_titles
    for name in ("高等考試｜三等／高考三級｜政風｜stage-2",
                 "警察特考｜三等｜外事警察人員｜elective-language-c20d34fdc5ae",
                 "高等考試｜一等｜一般行政｜cross-strait-group-1"):
        with pytest.raises(ValueError, match="internal identifiers"):
            validate_readable_titles([{"name": name}])


def test_readable_title_gate_accepts_latin_subject_names():
    from scripts.validate_publication import validate_readable_titles
    validate_readable_titles([
        {"name": "全國技術士技能檢定｜乙級｜電腦軟體設計(C++)"},
        {"name": "全國技術士技能檢定｜乙級｜銑床─CNC銑床"},
        {"name": "全國技術士技能檢定｜乙級｜視覺傳達設計─平面設計PC"},
        {"name": "高等考試｜三等／高考三級｜政風｜選試日文｜第二試"},
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
