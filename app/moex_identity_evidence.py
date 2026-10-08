"""Reviewed exact category facts and conflicting historical MOEX subjects.

The catalog stores decisions, not extracted PDF text or generated paper state.
Every fact matches the entire retained category context and cites a question
with a checksum. Native codes are locators, never a grade numbering rule.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


@dataclass(frozen=True)
class MoexQuestionEvidence:
    source_url: str
    subject_code: str
    checksum: str


@dataclass(frozen=True)
class MoexSubjectReview:
    subject_code: str
    reason: str


@dataclass(frozen=True)
class MoexCategoryIdentity:
    fact_id: str
    source_exam_id: str
    year_ad: int
    category_code: str
    category_raw: str
    exam_name_raw: str
    series_id: str
    level_id: str
    evidence: tuple[MoexQuestionEvidence, ...]
    subject_reviews: tuple[MoexSubjectReview, ...] = ()


def moex_evidence_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/moex/category-identity-v1.json"


def _text(row: dict[str, Any], field: str, path: Path) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}: {field} must be nonempty text")
    return value


def read_moex_category_identities(path: Path) -> tuple[MoexCategoryIdentity, ...]:
    """Reject unsupported versions and ambiguous facts before classifying."""
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{path}: expected an evidence object")
    if document.get("schema_version") != 1 or document.get("provider_id") != "moex":
        raise ValueError(f"{path}: unsupported evidence version or provider ownership")
    if document.get("catalog_version") != "exam-identity-v2":
        raise ValueError(f"{path}: unsupported catalog version")
    rows = document.get("facts")
    if not isinstance(rows, list):
        raise ValueError(f"{path}: facts must be an array")
    facts = []
    contexts: set[tuple[str, int, str]] = set()
    fact_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"{path}: each fact must be an object")
        year_ad = row.get("year_ad")
        if type(year_ad) is not int or not 1912 <= year_ad <= 2100:
            raise ValueError(f"{path}: year_ad must be an official examination year")
        date.fromisoformat(_text(row, "reviewed_at", path))
        _text(row, "reason", path)
        anchors = row.get("evidence")
        if not isinstance(anchors, list) or not anchors:
            raise ValueError(f"{path}: every fact needs official question evidence")
        evidence = []
        for anchor in anchors:
            if not isinstance(anchor, dict):
                raise ValueError(f"{path}: each evidence anchor must be an object")
            source_url = _text(anchor, "source_url", path)
            subject_code = _text(anchor, "subject_code", path)
            checksum = _text(anchor, "checksum", path)
            _text(anchor, "observation", path)
            url = urlparse(source_url)
            expected = {
                "t": ["Q"],
                "code": [row.get("source_exam_id")],
                "c": [row.get("category_code")],
                "s": [subject_code],
            }
            query = parse_qs(url.query)
            if (
                url.scheme != "https"
                or url.netloc != "wwwq.moex.gov.tw"
                or url.path != "/exam/wHandExamQandA_File.ashx"
                or any(query.get(key) != value for key, value in expected.items())
                or re.fullmatch(r"[0-9a-f]{64}", checksum) is None
            ):
                raise ValueError(f"{path}: evidence must identify this official question context")
            evidence.append(MoexQuestionEvidence(source_url, subject_code, checksum))
        reviews = row.get("subject_reviews", [])
        if not isinstance(reviews, list):
            raise ValueError(f"{path}: subject_reviews must be an array")
        subject_reviews = []
        reviewed_subjects: set[str] = set()
        anchored_subjects = {anchor.subject_code for anchor in evidence}
        for review in reviews:
            if not isinstance(review, dict):
                raise ValueError(f"{path}: each subject review must be an object")
            subject_code = _text(review, "subject_code", path)
            if subject_code not in anchored_subjects or subject_code in reviewed_subjects:
                raise ValueError(f"{path}: subject review needs one distinct anchored subject")
            subject_reviews.append(MoexSubjectReview(subject_code, _text(review, "reason", path)))
            reviewed_subjects.add(subject_code)
        fact = MoexCategoryIdentity(
            fact_id=_text(row, "id", path),
            source_exam_id=_text(row, "source_exam_id", path),
            year_ad=year_ad,
            category_code=_text(row, "category_code", path),
            category_raw=_text(row, "category_raw", path),
            exam_name_raw=_text(row, "exam_name_raw", path),
            series_id=_text(row, "series_id", path),
            level_id=_text(row, "level_id", path),
            evidence=tuple(evidence),
            subject_reviews=tuple(subject_reviews),
        )
        context = (fact.source_exam_id, fact.year_ad, fact.category_code)
        if context in contexts or fact.fact_id in fact_ids:
            raise ValueError(f"{path}: duplicate category context or fact id: {fact.fact_id}")
        contexts.add(context)
        fact_ids.add(fact.fact_id)
        facts.append(fact)
    return tuple(facts)


def validate_moex_category_evidence(repo_root: Path, *, verify_mirror: bool = False) -> int:
    """Reconcile approved facts with retained source contexts and exact bytes."""
    # Classification reads catalog facts independently of publication. Import
    # the existing mirror resolver only for this development evidence gate.
    from app.bundler import resolve_mirror_storage_path

    facts = read_moex_category_identities(moex_evidence_path(repo_root))
    contexts = {(fact.source_exam_id, fact.year_ad, fact.category_code): fact for fact in facts}
    retained: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
    for path in sorted((repo_root / "data/providers/moex/papers").glob("*.json")):
        for paper in json.loads(path.read_text(encoding="utf-8")):
            key = (paper["source_exam_id"], paper["year_roc"] + 1911, paper["category_code"])
            if key in contexts:
                retained.setdefault(key, []).append(paper)
    for key, fact in contexts.items():
        papers = retained.get(key, [])
        if not papers or any(
            paper["category_raw"] != fact.category_raw
            or paper["exam_name_raw"] != fact.exam_name_raw
            for paper in papers
        ):
            raise ValueError(f"{fact.fact_id}: retained category context is missing or changed")
        for anchor in fact.evidence:
            matches = [
                paper
                for paper in papers
                if paper["file_type"] == "question"
                and paper["subject_code"] == anchor.subject_code
                and paper["download_url_source"] == anchor.source_url
                and paper["checksum"] == anchor.checksum
            ]
            if len(matches) != 1:
                raise ValueError(
                    f"{fact.fact_id}: retained evidence checksum/locator does not match"
                )
            if verify_mirror:
                mirror_path = resolve_mirror_storage_path(
                    repo_root / "mirror", matches[0]["storage_key"], "moex"
                )
                if mirror_path is None:
                    raise ValueError(f"{fact.fact_id}: official question mirror is missing")
                with mirror_path.open("rb") as stream:
                    checksum = hashlib.file_digest(stream, "sha256").hexdigest()
                if checksum != anchor.checksum:
                    raise ValueError(
                        f"{fact.fact_id}: official question mirror checksum does not match"
                    )
    return len(facts)


@lru_cache(maxsize=1)
def _category_index() -> dict[tuple[str, int, str], MoexCategoryIdentity]:
    repo_root = Path(__file__).resolve().parents[1]
    return {
        (fact.source_exam_id, fact.year_ad, fact.category_code): fact
        for fact in read_moex_category_identities(moex_evidence_path(repo_root))
    }


def resolve_moex_category_identity(
    source_exam_id: str,
    year_ad: int,
    category_code: str,
    category_raw: str,
    exam_name_raw: str,
) -> MoexCategoryIdentity | None:
    fact = _category_index().get((source_exam_id, year_ad, category_code))
    if fact is None:
        return None
    # Changed source wording requires another evidence review. Do not extend
    # a historical decision by a similar label or a reused category code.
    if fact.category_raw != category_raw or fact.exam_name_raw != exam_name_raw:
        return None
    return fact
