"""Checksum-bound whole-original role reviews of native Taiwan Sugar PDFs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.answer_corrections import AnswerCorrection, validate_correction, validate_corrections
from app.providers.taisugar_recruit.native import native_pdf_subject_code, validate_member_parents


@dataclass(frozen=True)
class NativeRoleReview(AnswerCorrection):
    native_role: str


def native_roles_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/taisugar/native-file-roles-v2.json"


def _validate_source(fact: AnswerCorrection) -> None:
    expected_subject = native_pdf_subject_code(fact.source_url)
    if (
        fact.source_exam_id != f"taisugar-recruit-{fact.year_roc}"
        or fact.category_code != str(fact.year_roc)
        or fact.subject_code != expected_subject
    ):
        raise ValueError("Taiwan Sugar native role requires its exact original source context")


def read_native_roles(path: Path) -> tuple[NativeRoleReview, ...]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "provider_id", "facts"}
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 2
        or document["provider_id"] != "taisugar_recruit"
        or not isinstance(document["facts"], list)
        or not document["facts"]
    ):
        raise ValueError("Unsupported Taiwan Sugar native role evidence")
    facts = []
    slots = set()
    for row in document["facts"]:
        fact = NativeRoleReview(**row)
        validate_correction(fact)
        _validate_source(fact)
        if fact.native_role not in {"question", "question_answer"}:
            raise ValueError("Invalid Taiwan Sugar native role judgment")
        if fact.source_key[:-1] in slots:
            raise ValueError("Duplicate native correction source slot")
        slots.add(fact.source_key[:-1])
        facts.append(fact)
    return tuple(facts)


@lru_cache(maxsize=1)
def native_roles() -> tuple[NativeRoleReview, ...]:
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
    if file_type not in {"source_document", "question", "question_answer"}:
        return file_type
    key = (source_exam_id, year_roc, category_code, subject_code, title, source_url, checksum)
    for fact in native_roles():
        if fact.source_key[:-1] == key[:-1]:
            if checksum != fact.checksum:
                raise ValueError("Native file-role review has changed or unreviewed bytes")
            return fact.native_role
    if file_type == "question_answer":
        raise ValueError("Reviewed file role has unreviewed source evidence")
    return file_type


def validate_native_roles(repo_root: Path, *, verify_mirror: bool = False) -> int:
    facts = read_native_roles(native_roles_path(repo_root))
    validate_member_parents(
        repo_root,
        ((f.source_exam_id, f.year_roc, f.source_url, f.checksum) for f in facts),
        verify_mirror=verify_mirror,
    )
    return validate_corrections(
        repo_root,
        provider_id="taisugar_recruit",
        facts=facts,
        verify_mirror=verify_mirror,
        allowed_roles=frozenset({"question", "source_document", "question_answer"}),
    )
