"""Native question PDFs exposed by an incorrect MOEX answer endpoint."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app.answer_corrections import (
    AnswerCorrection,
)
from app.answer_corrections import (
    file_role as reviewed_file_role,
)
from app.answer_corrections import (
    read_corrections as read_reviewed_corrections,
)
from app.answer_corrections import (
    validate_corrections as validate_reviewed_corrections,
)


def question_references_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/moex/native-question-references-v1.json"


def _validate_source(fact: AnswerCorrection) -> None:
    url = urlsplit(fact.source_url)
    query = parse_qs(url.query, keep_blank_values=True)
    if (
        re.fullmatch(rf"{fact.year_roc:03}\d{{3}}", fact.source_exam_id) is None
        or re.fullmatch(r"\d{3}", fact.category_code) is None
        or re.fullmatch(r"[a-z]\d{3}", fact.subject_code) is None
        or url.scheme != "https"
        or url.netloc != "wwwq.moex.gov.tw"
        or url.path != "/exam/wHandExamQandA_File.ashx"
        or url.fragment
        or query
        != {
            "t": ["S"],
            "code": [fact.source_exam_id],
            "c": [fact.category_code],
            "s": [fact.subject_code],
            "q": ["1"],
        }
    ):
        raise ValueError(
            "Native question correction requires an exact official MOEX answer endpoint"
        )


def read_question_references(path: Path) -> tuple[AnswerCorrection, ...]:
    return read_reviewed_corrections(path, provider_id="moex", validate_source=_validate_source)


@lru_cache(maxsize=1)
def question_references() -> tuple[AnswerCorrection, ...]:
    return read_question_references(question_references_path(Path(__file__).resolve().parents[3]))


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
        facts=question_references(),
        source_exam_id=source_exam_id,
        year_roc=year_roc,
        category_code=category_code,
        subject_code=subject_code,
        title=title,
        source_url=source_url,
        checksum=checksum,
        reviewed_role="question",
        # Ordinary question endpoints need no answer-reference correction.
        require_reviewed_role=False,
    )


def validate_question_references(repo_root: Path, *, verify_mirror: bool = False) -> int:
    return validate_reviewed_corrections(
        repo_root,
        provider_id="moex",
        facts=read_question_references(question_references_path(repo_root)),
        verify_mirror=verify_mirror,
        allowed_roles=frozenset({"answer", "question"}),
    )
