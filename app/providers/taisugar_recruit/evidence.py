"""Checksum-bound combined question/answer reviews of native Taiwan Sugar PDFs."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from app.answer_corrections import AnswerCorrection, read_corrections, validate_corrections
from app.answer_corrections import file_role as reviewed_file_role
from app.providers.taisugar_recruit.archives import member_subject_code, parse_member_locator
from app.providers.taisugar_recruit.client import source_subject_code


def native_roles_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/taisugar/native-file-roles-v1.json"


def _validate_source(fact: AnswerCorrection) -> None:
    source = urlsplit(fact.source_url)
    if source.fragment:
        parent, _parent_checksum, name = parse_member_locator(fact.source_url)
        if Path(name).suffix.lower() != ".pdf":
            raise ValueError("Taiwan Sugar native role requires a PDF archive member")
        expected_subject = member_subject_code(parent, name)
    else:
        if (
            source.scheme != "https"
            or source.netloc != "www.taisugar.com.tw"
            or re.fullmatch(r"/upload/UserFiles/News/\d+/[^/]+\.pdf", source.path, re.IGNORECASE)
            is None
            or source.query
        ):
            raise ValueError("Taiwan Sugar native role requires an exact original PDF URL")
        expected_subject = source_subject_code(fact.source_url)
    if (
        fact.source_exam_id != f"taisugar-recruit-{fact.year_roc}"
        or fact.category_code != str(fact.year_roc)
        or fact.subject_code != expected_subject
    ):
        raise ValueError("Taiwan Sugar native role requires its exact original source context")


def read_native_roles(path: Path) -> tuple[AnswerCorrection, ...]:
    return read_corrections(path, provider_id="taisugar_recruit", validate_source=_validate_source)


@lru_cache(maxsize=1)
def native_roles() -> tuple[AnswerCorrection, ...]:
    return read_native_roles(native_roles_path(Path(__file__).resolve().parents[3]))


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
        facts=native_roles(),
        source_exam_id=source_exam_id,
        year_roc=year_roc,
        category_code=category_code,
        subject_code=subject_code,
        title=title,
        source_url=source_url,
        checksum=checksum,
        source_role="source_document",
        reviewed_role="question_answer",
    )


def validate_native_roles(repo_root: Path, *, verify_mirror: bool = False) -> int:
    return validate_corrections(
        repo_root,
        provider_id="taisugar_recruit",
        facts=read_native_roles(native_roles_path(repo_root)),
        verify_mirror=verify_mirror,
        allowed_roles=frozenset({"source_document", "question_answer"}),
    )
