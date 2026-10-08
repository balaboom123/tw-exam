import pytest

from app.models import file_type_label
from app.providers.ceec import listing_file_type
from app.publication_metadata import file_subject_label


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("試題內容", "question"),
        ("封面", "question_cover"),
        ("答題卷", "answer_sheet"),
        ("非選擇題評分原則", "scoring_guidelines"),
        ("非選擇題評分標準", "scoring_guidelines"),
        ("非選擇題評分說明", "scoring_guidelines"),
        ("選擇(填)題答案", "answer"),
        ("參考答案", "answer"),
        ("更正答案", "corrected_answer"),
        ("選擇題修正答案", "corrected_answer"),
    ],
)
def test_ceec_source_labels_distinguish_file_meaning(label, expected):
    assert listing_file_type(label) == expected


def test_alternate_formats_do_not_change_cover_or_scoring_roles():
    assert listing_file_type("試題內容", alternative_question=True) == "question_alt"
    assert listing_file_type("封面", alternative_question=True) == "question_cover"
    assert listing_file_type("非選擇題評分原則", alternative_question=True) == "scoring_guidelines"


def test_unknown_ceec_links_cannot_become_corrected_answers():
    with pytest.raises(RuntimeError, match="Unrecognized CEEC download role"):
        listing_file_type("考試簡章")


def test_archive_filenames_expose_cover_and_scoring_meanings():
    assert file_type_label("question_cover") == "試題封面"
    assert file_type_label("scoring_guidelines") == "評分原則"


@pytest.mark.parametrize("suffix", ["封面", "非選擇題評分原則", "非選擇題評分標準", "非選擇題評分說明"])
def test_file_roles_do_not_create_extra_subject_labels(suffix):
    assert file_subject_label(f"國文 {suffix}") == "國文"
    assert file_subject_label("封面設計") == "封面設計"
