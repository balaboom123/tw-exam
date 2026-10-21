"""Native TABF programmes, qualification routes and assessment modules."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Programme:
    code: str
    label: str
    canonical_id: str
    rules_url: str
    grade: str = ""
    variants: tuple[tuple[str, str], ...] = ()

    @property
    def is_aptitude(self) -> bool:
        return self.code.startswith("fit-subject-")

    @property
    def level_id(self) -> str:
        return f"tabf-{self.grade}" if self.grade else "not-applicable"

    @property
    def level_label(self) -> str:
        return {"junior": "初階", "advanced": "進階", "": "不分等級"}[self.grade]

    @property
    def track_id(self) -> str:
        return f"tabf-{self.code}" if self.is_aptitude else self.canonical_id

    @property
    def canonical_label(self) -> str:
        return {
            "tabf-credit": "授信人員專業能力測驗",
            "tabf-bank-internal-control": "銀行內部控制與內部稽核測驗",
            "tabf-trust-business": "信託業業務人員信託業務專業測驗",
            "tabf-fit": "金融人員基礎學科測驗(FIT)",
        }.get(self.canonical_id, self.label)

    @property
    def track_label(self) -> str:
        label = self.label if self.is_aptitude else self.canonical_label
        return label.removeprefix(self.level_label) if self.grade else label


def _rules(code: str) -> str:
    return f"https://www.tabf.org.tw/LicenseExamList.aspx?ExamCode={code}"


PROGRAMMES = (
    Programme("credit-junior", "初階授信人員專業能力測驗", "tabf-credit", _rules("011"), "junior"),
    Programme(
        "credit-advanced", "進階授信人員專業能力測驗", "tabf-credit", _rules("012"), "advanced"
    ),
    Programme("fx-junior", "初階外匯人員專業能力測驗", "tabf-fx-junior", _rules("021"), "junior"),
    Programme(
        "financial-planning", "理財規劃人員專業能力測驗", "tabf-financial-planning", _rules("031")
    ),
    Programme(
        "trust-business", "信託業業務人員信託業務專業測驗", "tabf-trust-business", _rules("041")
    ),
    Programme(
        "bank-internal-control-general",
        "銀行內部控制與內部稽核測驗(一般金融)",
        "tabf-bank-internal-control",
        _rules("051"),
        variants=(("general-finance", "一般金融"),),
    ),
    Programme(
        "bank-internal-control-consumer",
        "銀行內部控制與內部稽核測驗(消費金融)",
        "tabf-bank-internal-control",
        _rules("061"),
        variants=(("consumer-finance", "消費金融"),),
    ),
    Programme(
        "trust-law-only",
        "信託業業務人員信託業務專業測驗【信託法規乙科】",
        "tabf-trust-business",
        _rules("131"),
        variants=(("qualification-trust-law-only", "信託法規乙科"),),
    ),
    Programme(
        "family-trust-advisor",
        "家族信託規劃顧問師資格測驗",
        "tabf-family-trust-advisor",
        _rules("151"),
    ),
    Programme("risk-management", "風險管理基本能力測驗", "tabf-risk-management", _rules("201")),
    Programme(
        "derivatives-sales",
        "衍生性金融商品銷售人員資格測驗",
        "tabf-derivatives-sales",
        _rules("251"),
    ),
    Programme("fintech", "金融科技力知識檢定測驗", "tabf-fintech", _rules("261")),
    Programme("aml", "防制洗錢與打擊資恐專業人員測驗", "tabf-aml", _rules("271")),
    Programme(
        "senior-financial-planning-advisor",
        "高齡金融規劃顧問師資格測驗",
        "tabf-senior-financial-planning-advisor",
        _rules("291"),
    ),
    Programme("sustainability", "永續發展基礎能力測驗", "tabf-sustainability", _rules("381")),
    Programme(
        "fit-subject-i",
        "金融人員基礎學科測驗(FIT)-考科I【會計學+貨幣銀行學】",
        "tabf-fit",
        "https://service.tabf.org.tw/fit/QA.aspx",
    ),
    Programme(
        "fit-subject-ii",
        "金融人員基礎學科測驗(FIT)-考科II【票據法+銀行法】(含施行細則)",
        "tabf-fit",
        "https://service.tabf.org.tw/fit/QA.aspx",
    ),
    Programme(
        "fit-subject-iii",
        "金融人員基礎學科測驗(FIT)-考科III【民法+民事訴訟法+強制執行法】",
        "tabf-fit",
        "https://service.tabf.org.tw/fit/QA.aspx",
    ),
    Programme(
        "fit-subject-iv",
        "金融人員基礎學科測驗(FIT)-考科IV【邏輯推理+資訊科技+創新科技】",
        "tabf-fit",
        "https://service.tabf.org.tw/fit/QA.aspx",
    ),
)


def programme_for_code(code: str) -> Programme:
    for programme in PROGRAMMES:
        if programme.code == code:
            return programme
    raise ValueError(f"Unreviewed TABF programme: {code}")


def normalized_label(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


def programme_for_label(label: str) -> Programme:
    for programme in PROGRAMMES:
        if normalized_label(programme.label) == normalized_label(label):
            return programme
    raise ValueError(f"Unreviewed TABF native category: {label}")


_LEGACY_CANONICALS = {
    "bank-internal-control": ("tabf-bank-internal-control", "銀行內部控制與內部稽核"),
    "trust-business": ("tabf-trust-business", "信託業務人員"),
    "financial-planning": ("tabf-financial-planning", "理財規劃人員"),
    "fx-junior": ("tabf-fx-junior", "初階外匯人員"),
    "fx-senior": ("tabf-fx-senior", "進階外匯人員"),
    "credit": ("tabf-credit", "授信人員"),
    "risk-management": ("tabf-risk-management", "風險管理基本能力"),
    "debt-collection": ("tabf-debt-collection", "債權催收人員"),
    "digital-finance": ("tabf-digital-finance", "數位金融知識與能力"),
    "aml": ("tabf-aml", "防制洗錢與打擊資恐"),
    "fintech": ("tabf-fintech", "金融科技力知識"),
    "asset-valuation": ("tabf-asset-valuation", "資產評估人員"),
}


def canonical_for_event(source_exam_id: str) -> tuple[str, str] | None:
    for programme in PROGRAMMES:
        if re.fullmatch(rf"tabf-cert-{programme.code}-\d{{4}}-phid-\d+", source_exam_id):
            # A module is an independently selectable FIT assessment, not a
            # separately awarded qualification or a candidate performance band.
            return programme.canonical_id, programme.canonical_label
    # Compatibility names do not establish a grade, event year or native role.
    for prefix, canonical in _LEGACY_CANONICALS.items():
        if source_exam_id.startswith(f"tabf-cert-{prefix}-"):
            return canonical
    return None
