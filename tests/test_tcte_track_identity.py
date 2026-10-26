"""Historical source codes are acquisition keys, not stable admission meanings."""

import pytest

from app.classification import classify_paper


def identity(year, label, *, category_code=None, subject_code="professional-1", source=None):
    return classify_paper(
        provider_id="tcte_tve",
        source_exam_id=source or f"tcte-tve-{year}",
        year_ad=year + 1911,
        category_raw="四技二專統一入學測驗",
        exam_name_raw=f"{year}學年度四技二專統一入學測驗",
        canonical_id="tcte-tve",
        canonical_name="四技二專統一入學測驗",
        category_code=category_code or label[:2],
        subject_name_raw=f"{label} 專業科目(一)",
        subject_code=subject_code,
    )


@pytest.mark.parametrize(
    "year_a,label_a,year_b,label_b",
    [
        (97, "06衛生類", 98, "06土木與建築群"),
        (97, "07土木建築類", 98, "07設計群"),
        (91, "13海事水產類", 92, "13商業類"),
        (97, "52商業語文群(一)", 98, "52家政群"),
        (97, "56家政美容群", 102, "56商管外語群(四)"),
    ],
)
def test_reused_numeric_codes_do_not_merge_unrelated_candidate_groups(
    year_a, label_a, year_b, label_b
):
    assert identity(year_a, label_a).bundle_id != identity(year_b, label_b).bundle_id


@pytest.mark.parametrize(
    "year_a,label_a,year_b,label_b",
    [
        (90, "14商業類", 92, "13商業類"),
        (90, "15商業設計類", 92, "14商業設計類"),
        (90, "16幼保類", 92, "15幼保類"),
        (90, "17美容類", 92, "16美容類"),
        (90, "18家政類", 92, "17家政類"),
        (90, "19農業類", 92, "18農業類"),
        (90, "20語文類英文組", 92, "19語文類英文組"),
        (90, "21語文類日文組", 92, "20語文類日文組"),
        (91, "22餐旅類", 92, "21餐旅類"),
        (97, "07土木建築類", 98, "06土木與建築群"),
        (97, "09工程與管理類工程組", 98, "08工程與管理類"),
        (97, "12食品類", 98, "11食品群"),
        (97, "15幼保類", 98, "12家政群幼保類"),
        (97, "18農業類", 98, "14農業群"),
        (97, "19語文類英文組", 98, "15外語群英語類"),
        (97, "20語文類日文組", 98, "16外語群日語類"),
        (97, "21餐旅類", 98, "17餐旅群"),
        (97, "22海事類", 98, "18海事群"),
        (97, "23水產類", 98, "19水產群"),
        (97, "51電機電子群", 98, "51電機與電子群"),
        (101, "04電機與電子群電子類", 102, "04電機與電子群資電類"),
        (101, "09商管群", 102, "09商業與管理群"),
        (97, "54商業語文群(三)", 102, "55商管外語群(三)"),
    ],
)
def test_reviewed_renumbering_and_continuity_do_not_fragment_a_candidate_group(
    year_a, label_a, year_b, label_b
):
    assert identity(year_a, label_a).bundle_id == identity(year_b, label_b).bundle_id


@pytest.mark.parametrize(
    "old_a,old_b,new",
    [
        ("08工業設計類", "14商業設計類", "07設計群"),
        ("10工程與管理類管理組", "13商業類", "09商管群"),
        ("06衛生類", "11護理類", "10衛生與護理類"),
        ("16美容類", "17家政類", "13家政群生活應用類"),
    ],
)
def test_an_official_merger_does_not_make_the_preceding_groups_interchangeable(old_a, old_b, new):
    assert (
        len(
            {
                identity(97, old_a).bundle_id,
                identity(97, old_b).bundle_id,
                identity(98, new).bundle_id,
            }
        )
        == 3
    )


def test_combined_maritime_fisheries_route_is_not_one_of_its_later_individual_routes():
    assert (
        len(
            {
                identity(91, "13海事水產類").bundle_id,
                identity(92, "22海事類").bundle_id,
                identity(92, "23水產類").bundle_id,
            }
        )
        == 3
    )


@pytest.mark.parametrize("code,numeral", [("53", "一"), ("54", "二"), ("55", "三"), ("56", "四")])
def test_full_native_parenthesized_cross_group_ordinal_is_visible(code, numeral):
    result = identity(115, f"{code}商管外語群({numeral})")
    assert f"商管外語群({numeral})" in result.bundle_name


@pytest.mark.parametrize(
    "arguments",
    [
        {"year": 116, "label": "01機械群"},
        {"year": 115, "label": "01未知群"},
        {"year": 115, "label": "01機械群", "category_code": "02"},
        {"year": 115, "label": "01機械群", "source": "tcte-tve-114"},
        {"year": 115, "label": "01機械群", "subject_code": "unreviewed"},
    ],
)
def test_missing_or_conflicting_native_context_remains_explicitly_in_review(arguments):
    result = identity(**arguments)
    assert result.confidence == "review"
    assert result.track_id.endswith("unknown")
    assert "event-" in result.bundle_id


def test_correct_existing_fisheries_id_is_preserved_across_the_renumbering():
    assert (
        identity(97, "23水產類").track_id == identity(115, "19水產群").track_id == "tcte-group-23"
    )


def test_every_reviewed_native_alias_resolves_at_both_year_boundaries():
    from app.providers.tcte_tve.identity import group_catalogue, resolve_group

    for group in group_catalogue().tracks:
        for native in group.source_groups:
            for year in {native.from_year_roc, native.through_year_roc}:
                if group.kind == "common":
                    subject = native.source_label
                    code = "all" if subject == "各科標準答案" else "chinese"
                else:
                    subject, code = f"{native.source_label} 專業科目(一)", "professional-1"
                result = resolve_group(
                    source_exam_id=f"tcte-tve-{year}",
                    year_roc=year,
                    category_code=native.category_code,
                    subject_code=code,
                    subject_name=subject,
                )
                assert result == group


def test_common_resources_cannot_swallow_an_unresolved_professional_group():
    result = identity(115, "99待確認類")
    assert result.track_id != "common-subject"
    assert result.confidence == "review"


def test_reviewed_single_group_and_shared_paper_combination_remain_distinct():
    assert (
        identity(115, "03電機與電子群電機類").bundle_id != identity(115, "51電機與電子群").bundle_id
    )


def test_wrong_professional_part_cannot_inherit_a_reviewed_group():
    assert identity(115, "01機械群", subject_code="professional-2").confidence == "review"


@pytest.mark.parametrize(
    "mutation",
    [
        "version",
        "owner",
        "overlap",
        "unknown_member",
        "duplicate_id",
        "boolean_year",
        "inconsistent_event",
    ],
)
def test_invalid_reviewed_group_catalogues_are_rejected(tmp_path, mutation):
    import json
    from pathlib import Path

    from app.providers.tcte_tve.identity import group_identity_path, read_group_catalogue

    document = json.loads(group_identity_path(Path(__file__).resolve().parents[1]).read_text())
    if mutation == "version":
        document["schema_version"] = True
    elif mutation == "owner":
        document["provider_id"] = "ceec_ast"
    elif mutation == "overlap":
        document["tracks"][0]["source_groups"].append(document["tracks"][0]["source_groups"][0])
    elif mutation == "unknown_member":
        group = next(g for g in document["tracks"] if g["kind"] == "composite")
        group["member_tracks"][0] = "tcte-unreviewed"
    elif mutation == "duplicate_id":
        document["tracks"][1]["id"] = document["tracks"][0]["id"]
    elif mutation == "boolean_year":
        document["tracks"][0]["source_groups"][0]["from_year_roc"] = True
    elif mutation == "inconsistent_event":
        document["annual_sources"][0]["source_exam_id"] = "tcte-tve-115"
    path = tmp_path / "groups.json"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        read_group_catalogue(path)


@pytest.mark.parametrize("dimension", ["category", "year"])
def test_conflicting_unreviewed_source_contexts_do_not_merge_with_one_event(dimension):
    arguments = {"year": 115, "label": "01未知群", "category_code": "01", "source": "tcte-tve-115"}
    a = identity(**arguments)
    changed = dict(arguments)
    if dimension == "category":
        changed["category_code"] = "02"
    else:
        changed["year"] = 114
    b = identity(**changed)
    assert a.confidence == b.confidence == "review"
    assert a.bundle_id != b.bundle_id
