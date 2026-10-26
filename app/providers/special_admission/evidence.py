"""Provider-owned official-source boundaries for reviewed answer corrections."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app.answer_corrections import AnswerCorrection
from app.answer_corrections import file_role as reviewed_file_role
from app.answer_corrections import read_corrections as read_reviewed_corrections
from app.answer_corrections import validate_corrections as validate_reviewed_corrections


def _validate_source(fact: AnswerCorrection) -> None:
    if (
        fact.source_exam_id != f"special-admission-{fact.year_roc}"
        or fact.category_code != "university-common"
        or re.fullmatch(r"[a-z]+(?:-[a-z]+)*", fact.subject_code) is None
    ):
        raise ValueError("Invalid special-admission correction source key")
    url = urlsplit(fact.source_url)
    query = parse_qs(url.query, keep_blank_values=True)
    if (
        url.scheme != "https"
        or url.netloc != "cis.ncu.edu.tw"
        or url.path != "/EnableSys/home/readExamInfoFile"
        or url.fragment
        or set(query) != {"examInfoId", "row"}
        or len(query["examInfoId"]) != 1
        or not query["examInfoId"][0].isdigit()
        or query["row"] != ["1"]
    ):
        raise ValueError("Corrections require an official native answer URL")


def corrections_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/special_admission/answer-corrections-v1.json"


def read_corrections(path: Path) -> tuple[AnswerCorrection, ...]:
    return read_reviewed_corrections(
        path, provider_id="special_admission", validate_source=_validate_source
    )


@lru_cache(maxsize=1)
def corrections() -> tuple[AnswerCorrection, ...]:
    return read_corrections(corrections_path(Path(__file__).resolve().parents[3]))


def file_role(
    file_type: str,
    *,
    source_exam_id: str,
    year_roc: int,
    category_code: str,
    subject_code: str,
    title: str,
    source_url: str,
    checksum: str,
) -> str:
    return reviewed_file_role(
        file_type,
        facts=corrections(),
        source_exam_id=source_exam_id,
        year_roc=year_roc,
        category_code=category_code,
        subject_code=subject_code,
        title=title,
        source_url=source_url,
        checksum=checksum,
    )


def validate_corrections(repo_root: Path, *, verify_mirror: bool = False) -> int:
    return validate_reviewed_corrections(
        repo_root,
        provider_id="special_admission",
        facts=read_corrections(corrections_path(repo_root)),
        verify_mirror=verify_mirror,
    )
