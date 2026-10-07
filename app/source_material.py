"""Source material nature and official dates, independent of file roles.

Legacy partition years remain traceability keys. Reviewed facts determine
public dates and distinguish administered papers from learning resources.
The versioned schema owns the vocabularies used by adapters and consumers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit


@lru_cache(maxsize=1)
def _definitions() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "schemas/source-material-v1.schema.json"
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8"))["$defs"])


def material_label(kind: str) -> str:
    if not isinstance(kind, str):
        raise ValueError("Invalid source material kind")
    try:
        return str(_definitions()["kind"]["x-display-labels"][kind])
    except KeyError as exc:
        raise ValueError(f"Unsupported source material kind: {kind}") from exc


@dataclass(frozen=True)
class SourceDate:
    basis: str
    year_ad: int | None

    def __post_init__(self) -> None:
        if self.basis not in _definitions()["date"]["properties"]["basis"]["enum"]:
            raise ValueError(f"Unsupported source date basis: {self.basis}")
        if self.basis in {"undated", "unknown"}:
            if self.year_ad is not None:
                raise ValueError("An undated or unknown source date cannot claim a year")
        elif type(self.year_ad) is not int or not 1600 <= self.year_ad <= 9999:
            raise ValueError("A dated source material requires a Gregorian year")

    @classmethod
    def from_data(cls, value: Any) -> SourceDate:
        if not isinstance(value, dict) or set(value) != {"basis", "year_ad"}:
            raise ValueError("Invalid source date facts")
        if not isinstance(value["basis"], str):
            raise ValueError("Invalid source date basis")
        return cls(value["basis"], value["year_ad"])

    @property
    def year_roc(self) -> int | None:
        return self.year_ad - 1911 if self.year_ad is not None else None


@dataclass(frozen=True)
class SourceMaterial:
    kind: str
    date: SourceDate
    evidence_url: str
    evidence_label: str
    review_reason: str = ""
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Unsupported source material facts version")
        material_label(self.kind)
        if not isinstance(self.date, SourceDate):
            raise ValueError("Invalid source material date")
        if not isinstance(self.evidence_url, str):
            raise ValueError("Invalid source material evidence URL")
        url = urlsplit(self.evidence_url)
        if url.scheme not in {"http", "https"} or not url.netloc:
            raise ValueError("Source material facts require an official evidence URL")
        if not isinstance(self.evidence_label, str) or not self.evidence_label.strip():
            raise ValueError("Source material facts require their source label")
        if not isinstance(self.review_reason, str):
            raise ValueError("Invalid source material review reason")
        if self.needs_review and not self.review_reason.strip():
            raise ValueError("Unknown material or date facts require a review reason")
        if self.date.basis == "exam_year" and self.kind != "administered":
            raise ValueError("Only administered papers can claim an examination year")

    @property
    def needs_review(self) -> bool:
        return self.kind == "unknown" or self.date.basis == "unknown" or bool(self.review_reason)

    @classmethod
    def from_data(cls, value: Any) -> SourceMaterial:
        fields = {
            "schema_version",
            "kind",
            "date",
            "evidence_url",
            "evidence_label",
            "review_reason",
        }
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError("Invalid source material facts")
        return cls(**{**value, "date": SourceDate.from_data(value["date"])})


def source_material(value: Any) -> SourceMaterial | None:
    if value is None or isinstance(value, SourceMaterial):
        return value
    return SourceMaterial.from_data(value)


def source_year_roc(paper: Any) -> int | None:
    material = source_material(getattr(paper, "source_material", None))
    return material.date.year_roc if material is not None else None
