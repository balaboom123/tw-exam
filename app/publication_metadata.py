"""Small, deterministic metadata projections for public bundle discovery."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

MAX_SEARCH_ALIASES = 24
MAX_SUBJECT_LABELS = 6
MAX_LABEL_LENGTH = 120

_GENERIC_SUBJECT_BUNDLE_PREFIXES = (
    "wdasec-skill-",
    "ceec-gsat-",
    "ceec-ast-",
    "tcte-tve-",
)


def _field(paper: Any, name: str) -> str:
    if isinstance(paper, Mapping):
        value = paper.get(name, "")
    else:
        value = getattr(paper, name, "")
    return value if isinstance(value, str) else ""


def _clean_label(value: str) -> str:
    value = re.sub(r"[\r\n\t]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" -—–")
    if not value:
        return ""
    value = re.sub(
        r"\s+(?:試題內容|答題卷|選擇(?:\(填\)|（填）)?題答案|非選擇題評分原則|答案|解答)$",
        "",
        value,
    )
    value = re.sub(r"\s+專業科目\s*[（(][一二三123]+[）)](?:-[^ ]+)?$", "", value)
    value = re.sub(r"\s+(?:學科|術科)$", "", value)
    value = re.sub(r"\s+(?:甲級|乙級|丙級|單一級)(?:\s+(?:學科|術科))?$", "", value)
    return value.strip(" -—–")[:MAX_LABEL_LENGTH].rstrip()


def _unique(values: Iterable[str], *, limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
        if len(result) >= limit:
            break
    return result


def derive_public_metadata(
    papers: Iterable[Any],
    *,
    bundle_id: str,
    canonical_name: str,
) -> tuple[list[str], list[str]]:
    """Return ``(search_aliases, subject_labels)`` for one logical bundle."""

    labels: list[str] = []
    aliases: list[str] = []
    for paper in papers:
        raw_subject = _field(paper, "subject_name_raw")
        for chunk in re.split(r"[；;\n]+", raw_subject):
            label = _clean_label(chunk)
            if label and label != canonical_name and not label.isdigit():
                labels.append(label)
                aliases.append(label)

        for field_name in ("category_raw", "exam_name_raw"):
            label = _clean_label(_field(paper, field_name))
            if label and label != canonical_name:
                aliases.append(label)

        for field_name in ("category_code", "subject_code"):
            code = _field(paper, field_name).strip()
            if code:
                aliases.append(code)

    subject_labels = _unique(labels, limit=MAX_SUBJECT_LABELS)
    search_aliases = _unique([*labels, *aliases], limit=MAX_SEARCH_ALIASES)
    if bundle_id.startswith(_GENERIC_SUBJECT_BUNDLE_PREFIXES):
        return search_aliases, subject_labels
    return search_aliases, []
