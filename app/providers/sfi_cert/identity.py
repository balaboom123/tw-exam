"""SFI programme names, independent of mutable download-slot numerals."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


def label_key(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


@dataclass(frozen=True)
class Programme:
    slug: str
    label: str
    listing_labels: tuple[str, ...]
    native_names: tuple[str, ...]
    variants: tuple[tuple[str, str], ...] = ()

    @property
    def canonical_id(self) -> str:
        return f"sfi-{self.slug}"


PROGRAMMES = (
    Programme(
        "securities-analyst", "證券投資分析人員", ("證券投資分析人員",), ("證券投資分析人員",)
    ),
    Programme(
        "senior-securities-dealer", "證券商高級業務員", ("證券商高級業務員",), ("證券商高級業務員",)
    ),
    Programme("securities-dealer", "證券商業務員", ("證券商業務員",), ("證券商業務員",)),
    Programme(
        "securities-law-practice",
        "證券交易相關法規與實務乙科",
        ("證券交易相關法規與實務乙科",),
        ("證券交易相關法規與實務乙科",),
    ),
    Programme("futures-dealer", "期貨商業務員", ("期貨商業務員",), ("期貨商業務員", "期貨業務員")),
    Programme(
        "futures-trust-sales",
        "期貨信託基金銷售機構銷售人員",
        ("期貨信託基金銷售機構銷售人員",),
        ("期貨信託基金銷售機構銷售人員",),
    ),
    Programme("futures-analyst", "期貨交易分析人員", ("期貨交易分析人員",), ("期貨交易分析人員",)),
    Programme(
        "sitca",
        "投信投顧業務員",
        ("投信投顧業務員(一科、二科及三科)",),
        ("投信投顧業務員", "投信投顧業務人員"),
        (("subject-route-collection", "一科、二科及三科"),),
    ),
    Programme(
        "sitca-law",
        "投信投顧相關法規乙科",
        ("投信投顧相關法規乙科",),
        ("投信投顧相關法規乙科", "投信投顧相關法規(含自律規範)乙科"),
    ),
    Programme(
        "corporate-internal-control",
        "企業內部控制基本能力測驗",
        ("企業內部控制",),
        ("企業內部控制基本能力",),
    ),
    Programme(
        "aml",
        "防制洗錢與打擊資恐專業人員測驗",
        ("防制洗錢與打擊資恐專業人員",),
        ("防制洗錢與打擊資恐專業人員",),
    ),
    Programme(
        "sustainability", "永續發展基礎能力測驗", ("永續發展基礎能力測驗",), ("永續發展基礎能力",)
    ),
)

# Keep existing compatibility lookups for historical callers. These entries do
# not establish that the current paper listing contains these programmes.
LEGACY_CANONICAL_NAMES = {
    "bills-dealer": "票券商業務人員",
    "stock-affairs": "股務人員",
    "asset-securitization": "資產證券化基本能力測驗",
    "business-ethics": "工商倫理測驗",
}


def programme_for_slug(slug: str) -> Programme | None:
    return next((programme for programme in PROGRAMMES if programme.slug == slug), None)


def programme_for_listing(label: str) -> Programme | None:
    return next(
        (
            programme
            for programme in PROGRAMMES
            if any(label_key(label) == label_key(alias) for alias in programme.listing_labels)
        ),
        None,
    )


def canonical_for_event(source_exam_id: str) -> tuple[str, str] | None:
    match = re.fullmatch(r"sfi-cert-(.+)-\d{4}-\d+(?:-kaohsiung)?", source_exam_id)
    if match is None:
        return None
    slug = match.group(1)
    programme = programme_for_slug(slug)
    if programme is not None:
        return programme.canonical_id, programme.label
    if slug in LEGACY_CANONICAL_NAMES:
        return f"sfi-{slug}", LEGACY_CANONICAL_NAMES[slug]
    return None
