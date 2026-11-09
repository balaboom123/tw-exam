"""A changed or unreviewed upstream file is held for review, never a sync failure."""

import pytest

from app.providers.base import UnreviewedSourceFile
from app.providers.teacher_recruit_newtaipei.archive_plan import native_archives, verify_parent
from app.providers.teacher_recruit_taipei_elementary.provider import (
    TaipeiElementaryRecruitProvider,
)
from app.providers.teacher_recruit_taipei_junior.provider import TaipeiJuniorRecruitProvider

UNKNOWN = "https://example.test/unreviewed.pdf"


@pytest.mark.parametrize("provider", [TaipeiJuniorRecruitProvider, TaipeiElementaryRecruitProvider])
def test_an_unreviewed_taipei_download_is_held(provider):
    with pytest.raises(UnreviewedSourceFile) as held:
        provider().download_file(UNKNOWN)
    assert held.value.signature == f"unreviewed-source-file:{UNKNOWN}"


def test_a_changed_new_taipei_rar_is_held_under_its_source_url():
    archive = native_archives()[0]
    with pytest.raises(UnreviewedSourceFile) as held:
        verify_parent(archive, b"Rar!\x1a\x07\x01\x00changed")
    assert held.value.url == archive.source_url
