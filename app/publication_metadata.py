"""Small, deterministic metadata projections for public bundle discovery."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

MAX_SEARCH_ALIASES = 24
MAX_TAIPOWER_SEARCH_ALIASES = 64
MAX_SUBJECT_LABELS = 6
MAX_LABEL_LENGTH = 120

_GENERIC_SUBJECT_BUNDLE_PREFIXES = (
    "wdasec-skill-",
    "ceec-gsat-",
    "ceec-ast-",
    "tcte-tve-",
)

_TAIPOWER_SUBJECT_PREFIX = re.compile(
    r"^\d{2,3}(?:年度|年(?:\d{1,2}月)?)新進"
    r"(?:養成班試題(?:科目|解答|試題)[AB]|僱用人員甄試(?:科目)?(?:試題|解答|答案))"
    r"_+(?P<subject>.+)$"
)
_TAIPOWER_INLINE_SUBJECTS = {
    "104企業管理概論、法律常識題目": "企業管理概論、法律常識",
    "104企業管理概論、法律常識答案": "企業管理概論、法律常識",
}


def _field(paper: Any, name: str) -> str:
    if isinstance(paper, Mapping):
        value = paper.get(name, "")
    else:
        value = getattr(paper, name, "")
    return value if isinstance(value, str) else ""


def file_subject_label(value: str) -> str:
    """Remove file-role suffixes without discarding subject/group distinctions."""
    value = re.sub(r"[\r\n\t]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" -—–")
    if not value:
        return ""
    value = re.sub(
        r"\s+(?:試題內容|答題卷|封面|選擇(?:\(填\)|（填）)?題答案|非選擇題評分(?:原則|標準|說明)|答案|解答)$",
        "",
        value,
    )
    return value.strip(" -—–")


def publication_subject_label(value: str, *, provider_id: str) -> str:
    """Project a subject label while retaining the original acquisition wording."""
    if provider_id == "taipower_recruit":
        match = _TAIPOWER_SUBJECT_PREFIX.fullmatch(value)
        if match:
            value = match.group("subject")
            value = re.sub(r"\.pdf$", "", value, flags=re.IGNORECASE)
        else:
            value = _TAIPOWER_INLINE_SUBJECTS.get(value, value)
    return file_subject_label(value)


def _clean_label(value: str) -> str:
    value = file_subject_label(value)
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
    providers: set[str] = set()
    for paper in papers:
        provider_id = _field(paper, "provider_id")
        providers.add(provider_id)
        raw_subject = publication_subject_label(
            _field(paper, "subject_name_raw"), provider_id=provider_id
        )
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
    # Taipower publishes a whole programme with many shared subjects. Keep its
    # historical subject vocabulary searchable before event labels and codes.
    alias_limit = (
        MAX_TAIPOWER_SEARCH_ALIASES if providers == {"taipower_recruit"} else MAX_SEARCH_ALIASES
    )
    search_aliases = _unique([*labels, *aliases], limit=alias_limit)
    if bundle_id.startswith(_GENERIC_SUBJECT_BUNDLE_PREFIXES):
        return search_aliases, subject_labels
    return search_aliases, []
