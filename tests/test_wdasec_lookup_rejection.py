"""Unverified or structurally impossible keys do not need the native evidence index."""

import pytest

from app.providers.wdasec_skill import papers as owner


def native_context():
    return {
        "year_roc": 101,
        "source_exam_id": "201211120001",
        "exam_name_raw": "101年度技能檢定",
        "category_code": "14901",
        "subject_code": "14901-class_b-question",
        "subject_name_raw": "會計事務-人工記帳 乙級 學科",
        "source_url": "https://owinform.wdasec.gov.tw/ExamNet/owInform/DLowFileQ/101.pdf",
        "checksum": "a" * 64,
    }


@pytest.mark.parametrize(
    "field,value",
    [("checksum", ""), ("source_exam_id", "event-115"), ("category_code", "301")],
)
def test_impossible_or_unverified_key_does_not_load_the_catalogue(monkeypatch, field, value):
    def unexpected_load():
        raise AssertionError("Impossible native key triggered the full evidence index")

    monkeypatch.setattr(owner, "_index", unexpected_load)
    assert owner.resolve_native_paper(**{**native_context(), field: value}) is None


def test_complete_source_keys_still_use_exact_reviewed_context_and_checksum(monkeypatch):
    context = native_context()
    native = owner.NativePaper(**context, form="ordinary", native_header="原生試卷")
    monkeypatch.setattr(owner, "_index", lambda: {native.key: native})
    assert owner.resolve_native_paper(**context) is native
    assert owner.resolve_native_paper(**{**context, "checksum": "b" * 64}) is None
