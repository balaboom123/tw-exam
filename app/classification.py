"""Evidence-backed, provider-neutral exam identity classification.

The legacy normalizer intentionally keeps ``canonical_id`` and
``canonical_name`` for URL compatibility.  This module owns the v2 dimensions
used for publication grouping.  A classifier result is deterministic and
explainable: every dimension is derived from provider, source event, raw
category, and content fields, and ambiguous records are isolated into an
exam-event-specific review bundle instead of being silently merged.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any

IDENTITY_SCHEMA_VERSION = 2
CATALOG_VERSION = "exam-identity-v2"
BUNDLE_POLICY_ID = "default-bundle-policy-v2"
NOT_APPLICABLE = "not-applicable"


@dataclass(frozen=True)
class ExamIdentity:
    provider_id: str
    domain_id: str
    exam_family_id: str
    exam_series_id: str
    level_id: str
    track_id: str
    variant_ids: tuple[str, ...]
    stage_id: str
    exam_event_id: str
    bundle_id: str
    bundle_name: str
    confidence: str
    reason: str
    series_label: str
    level_label: str
    track_label: str

    @property
    def signature(self) -> str:
        variants = ",".join(self.variant_ids) or NOT_APPLICABLE
        return "|".join(
            (
                self.provider_id,
                self.domain_id,
                self.exam_family_id,
                self.exam_series_id,
                self.level_id,
                self.track_id,
                variants,
                self.stage_id,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["variant_ids"] = list(self.variant_ids)
        payload["classification_signature"] = self.signature
        return payload


_TRACK_ALIASES = {
    "一般行政": "general-administration",
    "民政": "civil-affairs",
    "戶政": "household-registration",
    "地政": "land-administration",
    "人事行政": "personnel-administration",
    "教育行政": "education-administration",
    "文化行政": "cultural-administration",
    "財稅行政": "tax-administration",
    "會計": "accounting",
    "審計": "audit",
    "資訊處理": "information-processing",
    "電子工程": "electronic-engineering",
    "機械工程": "mechanical-engineering",
    "土木工程": "civil-engineering",
    "護理師": "nurse",
    "營養師": "dietitian",
    "社會工作師": "social-worker",
    "心理師": "psychologist",
    "律師": "lawyer",
    "會計師": "accountant",
    "專利師": "patent-attorney",
}

_SERIES_LABELS = {
    "civil-high": "高等考試",
    "civil-ordinary": "普通考試",
    "civil-elementary": "初等考試",
    "civil-promotion": "升官等／升等考試",
    "special-local-government": "地方特考",
    "special-indigenous": "原住民族特考",
    "special-disability": "身心障礙特考",
    "special-customs": "關務特考",
    "special-diplomatic": "外交／國際特考",
    "special-police": "警察／一般警察特考",
    "special-judicial": "司法特考",
    "special-coast-guard": "海巡特考",
    "special-immigration": "移民特考",
    "special-other": "其他特種考試",
    "special-aviation": "民航特考",
    "special-maritime": "航海／船員特考",
    "special-investigation": "調查／情報特考",
    "civil-qualification": "公務人員檢定考試",
    "professional-qualification": "專技檢定考試",
    "special-military-transfer": "國軍軍官轉任考試",
    "special-retired-military": "退除役軍人轉任考試",
    "professional-high": "專技高考",
    "professional-ordinary": "專技普考",
    "professional-special": "專技特考",
    "professional-combined": "專技綜合／歷史制度",
    "professional-screening": "專技檢覈／檢覈筆試",
    "teacher-qualification": "教師資格考試",
    "teacher-recruitment": "教師甄試",
    "language-gept": "全民英檢",
    "language-jlpt": "日本語能力試驗",
    "language-tocfl": "華語文能力測驗",
    "language-hakka": "客語能力認證",
    "language-taigi": "臺灣台語語言能力認證",
    "admission-gsat": "學科能力測驗",
    "admission-ast": "分科測驗",
    "admission-tcte": "四技二專統一入學測驗",
    "admission-cap": "國中教育會考",
    "admission-special": "身心障礙學生升學甄試",
    "skill-certification": "技術士技能檢定",
    "employment-recruitment": "就業／國營事業甄試",
    "postal-recruitment": "中華郵政職階人員甄試",
    "financial-certification": "金融證照／能力測驗",
    "professional-certification": "專業能力證照",
}

_LEVEL_LABELS = {
    "grade-1": "一等／高考一級",
    "grade-2": "二等／高考二級",
    "grade-3": "三等／高考三級",
    "grade-4": "四等",
    "grade-5": "五等",
    "ordinary": "普通／普考",
    "elementary": "初等／初考",
    "recommended-rank": "薦任",
    "delegated-rank": "委任",
    "appointed-rank": "簡任",
    "grade-a": "甲等",
    "grade-b": "乙等",
    "grade-c": "丙等",
    "promotion-worker-to-associate": "士級晉佐級",
    "promotion-worker-rank": "士級",
    "promotion-associate-to-employee": "佐級晉員級",
    "promotion-employee-to-senior": "員級晉高員級",
    "promotion-official-rank": "升等／職等",
    "promotion-associate-rank": "佐級",
    "promotion-employee-rank": "員級",
    "promotion-senior-rank": "高員級",
    "grade-d": "丁等",
    "qualification-high": "高等檢定",
    "qualification-ordinary": "普通檢定",
    "qualification-professional": "專業檢定",
    "police-commissioned": "警監／警正",
    "police-senior": "警正",
    "police-associate": "警佐",
    "professional-high": "專技高考",
    "professional-ordinary": "專技普考",
    "professional-special": "專技特考",
    "combined": "合併／制度待審核",
    "n1": "N1",
    "n2": "N2",
    "n3": "N3",
    "n4": "N4",
    "n5": "N5",
    "basic-elementary": "基礎級暨初級",
    "intermediate-high-intermediate": "中級暨中高級",
    "advanced": "高級",
    "paper-a": "A卷",
    "paper-b": "B卷",
    "paper-c": "C卷",
    "single": "單一級",
    "class-a": "甲級",
    "class-b": "乙級",
    "class-c": "丙級",
    NOT_APPLICABLE: "不分級",
    "unknown": "待審核等級",
}


def normalize_text(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = text.replace("＿", "_").replace("－", "-")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _ascii_slug(value: str, *, prefix: str) -> str:
    text = normalize_text(value).strip(" -_/")
    if not text:
        return f"{prefix}-unknown"
    lowered = text.lower()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    if lowered:
        return lowered
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _slug(value: str, *, prefix: str = "concept") -> str:
    normalized = normalize_text(value)
    if normalized in _TRACK_ALIASES:
        return _TRACK_ALIASES[normalized]
    return _ascii_slug(normalized, prefix=prefix)


def _display(value: str, fallback: str) -> str:
    normalized = normalize_text(value)
    return normalized or fallback


def _parenthetical_values(text: str) -> list[str]:
    return [normalize_text(value) for value in re.findall(r"[（(]([^）)]*)[）)]", text)]


def _variant_ids(category: str, exam_name: str) -> tuple[str, ...]:
    text = normalize_text(f"{category} {exam_name}")
    values: list[str] = []
    for value in _parenthetical_values(text):
        if re.search(r"一般組", value):
            values.append("general-group")
        match = re.search(r"兩岸組\s*([一二三1-3])", value)
        if match:
            number = {"一": "1", "二": "2", "三": "3"}.get(match.group(1), match.group(1))
            values.append(f"cross-strait-group-{number}")
        match = re.search(r"選試\s*(.+)", value)
        if match:
            values.append(f"elective-{_slug(match.group(1), prefix='language')}")
        if any(token in value for token in ("國防部", "退輔會", "海委會")):
            values.append(f"destination-{_slug(value, prefix='destination')}")
        if "錄取分發區" in value:
            values.append(f"distribution-{_slug(value.replace('錄取分發區', ''), prefix='region')}")
    direct_language = re.findall(r"選試\s*([一-龥A-Za-z]+)", text)
    values.extend(f"elective-{_slug(language, prefix='language')}" for language in direct_language)
    return tuple(dict.fromkeys(sorted(values)))


def _stage_id(category: str, exam_name: str) -> str:
    text = normalize_text(f"{category} {exam_name}")
    if re.search(r"第一試|一試|初試", text):
        return "stage-1"
    if re.search(r"第二試|二試|複試", text):
        return "stage-2"
    if re.search(r"第三試|三試", text):
        return "stage-3"
    if "預試" in text:
        return "pretest"
    return NOT_APPLICABLE


@lru_cache(maxsize=8192)
def _clean_moex_track(category: str, canonical_name: str) -> str:
    value = normalize_text(category or canonical_name)
    if "_" in value:
        value = value.split("_")[-1]
    value = re.sub(r"^\d+(?:年|\s+)", "", value)
    value = re.sub(r"^(?:專門職業及技術人員|專技)(?:高等|普通|特種)?考試", "", value)
    value = re.sub(r"^(?:高等|普通|初等|特種)考試", "", value)
    value = re.sub(
        r"^(?:高考|普考|初等|初考|特考|地方政府公務人員考試|原住民族考試|身心障礙人員考試|身障特考|關務特考|關務人員考試)",
        "",
        value,
    )
    value = re.sub(r"^(?:一級|二級|三級|三等|四等|五等|1等|2等|3等|4等|5等)考試", "", value)
    value = re.sub(r"(?:類科|科別)$", "", value)
    value = re.sub(
        r"[（(](?:三等|四等|五等|高考|普考|初考|一般組|兩岸組[一二三]|高員級|高級員|員級|佐級|八職等|十二職等)[）)]",
        "",
        value,
    )
    value = re.sub(
        r"[（(](?:選試[^）)]*|國防部|退輔會|轉任[^）)]*|一般錄取分發區|蘭嶼錄取分發區)[）)]",
        "",
        value,
    )
    value = re.sub(
        r"^(?:身障|原住民族|地方政府|關務|退除役特考|警察特考|外交人員考試)[^_]*_", "", value
    )
    value = value.strip(" -_/")
    return value or normalize_text(canonical_name)


def _track_details(
    provider_id: str,
    category: str,
    canonical_id: str,
    canonical_name: str,
    subject_name: str,
    subject_code: str,
    source_exam_id: str,
    exam_name: str,
) -> tuple[str, str]:
    if provider_id == "wdasec_skill":
        value = normalize_text(subject_name) or normalize_text(subject_code)
        value = re.sub(r"\s*(?:甲級|乙級|丙級|單一級|學科|術科)\s*$", "", value)
        return _slug(value, prefix="skill"), value
    if provider_id == "tcte_tve":
        match = re.search(r"(\d{2}[^ ]*(?:群|類))", normalize_text(subject_name))
        if match:
            value = match.group(1)
            group_code = value[:2]
            return f"tcte-group-{group_code}", value
        return "common-subject", "共同科目"
    if provider_id in {"ceec_gsat", "ceec_ast"}:
        value = normalize_text(exam_name).split("－", 1)[-1]
        value = re.sub(r"^\d+(?:學年度|年度)?\s*", "", value)
        if provider_id == "ceec_ast" and not re.search(
            r"分科測驗\s*[-－]", normalize_text(exam_name)
        ):
            subject = normalize_text(subject_name)
            if subject:
                value = f"分科測驗-{subject}"
        value = value or normalize_text(subject_name)
        return _slug(value, prefix="subject"), value
    if provider_id == "rcpet_cap":
        return "cap", "國中教育會考"
    if provider_id == "gept_cert":
        return "gept", "GEPT全民英檢"
    if provider_id == "jlpt_cert":
        return "jlpt", "JLPT"
    if provider_id == "tocfl_cert":
        return "tocfl", "TOCFL"
    if provider_id == "hakka_cert":
        return "hakka", "客語能力認證"
    if provider_id == "taigi_cert":
        return "taigi", "臺灣台語語言能力認證"
    if provider_id == "ipas_cert":
        return _slug(canonical_id, prefix="ipas"), _display(canonical_name, canonical_id)
    if provider_id in {"sfi_cert", "tabf_cert", "tii_cert"}:
        return _slug(canonical_id, prefix="cert"), _display(canonical_name, canonical_id)
    if provider_id.startswith("teacher_recruit"):
        return _slug(canonical_id, prefix="teacher"), _display(canonical_name, canonical_id)
    if provider_id == "teacher_qual":
        return "teacher-qualification", "教師資格考試"
    if provider_id in {
        "moea_recruit",
        "taipower_recruit",
        "cpc_recruit",
        "twc_recruit",
        "taisugar_recruit",
    }:
        return _slug(canonical_id, prefix="recruit"), _display(canonical_name, canonical_id)
    if provider_id == "post_recruit":
        return "postal-recruitment", "中華郵政職階人員甄試"
    if provider_id == "special_admission":
        return "special-admission", "身心障礙學生升學甄試"
    if provider_id in {"hce_cmu", "hce_tcu", "hce_nsysu", "hce_nthu"}:
        return _slug(canonical_id, prefix="hce"), _display(canonical_name, canonical_id)
    if provider_id == "moex":
        value = _clean_moex_track(category, canonical_name)
        return _slug(value, prefix="track"), value
    value = (
        normalize_text(category) or normalize_text(canonical_name) or normalize_text(subject_name)
    )
    return _slug(value or source_exam_id, prefix="track"), value or source_exam_id


@lru_cache(maxsize=8192)
def _moex_level(category: str, exam_name: str, canonical_name: str) -> tuple[str, str, str, str]:
    cat = normalize_text(category)
    event = normalize_text(exam_name)
    professional = f"{cat} {event}"
    explicit_patterns = (
        (r"高等暨普通|高等、普通", "combined", "合併／制度待審核"),
        (r"高員三級|高員3級", "promotion-employee-to-senior", "員級晉高員級"),
        (r"員級晉高員|員級高員|員晉高員", "promotion-employee-to-senior", "員級晉高員級"),
        (r"佐級晉員|佐晉員", "promotion-associate-to-employee", "佐級晉員級"),
        (r"士級晉佐|士晉佐", "promotion-worker-to-associate", "士級晉佐級"),
        (r"高員級|高級員", "promotion-senior-rank", "高員級"),
        (r"一級(?:漁航員|輪機員|船員)", "maritime-rank-1", "一級船員"),
        (r"二級(?:漁航員|輪機員|船員)", "maritime-rank-2", "二級船員"),
        (r"三級(?:漁航員|輪機員|船員)", "maritime-rank-3", "三級船員"),
        (r"警監", "police-commissioned", "警監／警正"),
        (r"警正", "police-senior", "警正"),
        (r"警佐", "police-associate", "警佐"),
        (r"員級", "promotion-employee-rank", "員級"),
        (r"佐級", "promotion-associate-rank", "佐級"),
        (
            r"第[一二三四五六七八九十百]+職等|第\d+職等|[一二三四五六七八九十百]+職等|\d+職等",
            "promotion-official-rank",
            "升等／職等",
        ),
        (r"高考?\s*一級|高等一級|一級考試|一等考試|一等_|^一等", "grade-1", "一等／高考一級"),
        (r"高等檢定", "qualification-high", "高等檢定"),
        (r"高等_", "grade-3", "三等／高考三級"),
        (r"普通檢定", "qualification-ordinary", "普通檢定"),
        (r"普通_", "ordinary", "普通／普考"),
        (r"中醫師檢定|中醫師考試", "qualification-professional", "專業檢定"),
        (r"中醫師考試", "qualification-professional", "專業檢定"),
        (r"高考?\s*二級|高等二級|二級考試|二等考試|二等_", "grade-2", "二等／高考二級"),
        (
            r"高考?\s*三級|高3|三級考試|三等考試|三等_|司法三等|3等|三等",
            "grade-3",
            "三等／高考三級",
        ),
        (r"二等考試|二等_|2等|二等", "grade-2", "二等"),
        (r"四等考試|四等_|4等|四等", "grade-4", "四等"),
        (r"五等考試|五等_|5等|五等", "grade-5", "五等"),
        (r"普通考試|普考|普通_", "ordinary", "普通／普考"),
        (r"初等考試|初等_|初考|初等", "elementary", "初等／初考"),
        (r"薦任升等|公務薦任|薦任", "recommended-rank", "薦任"),
        (r"委任升等|公務委任|委任", "delegated-rank", "委任"),
        (r"簡任升等|公務簡任|簡任", "appointed-rank", "簡任"),
        (r"甲等", "grade-a", "甲等"),
        (r"乙等", "grade-b", "乙等"),
        (r"丙等", "grade-c", "丙等"),
        (r"丁等", "grade-d", "丁等"),
    )
    for pattern, level_id, label in explicit_patterns:
        if re.search(pattern, cat):
            return level_id, label, "high", f"explicit category marker: {cat}"
    if re.search(r"(?:^|[（(])(?:相當)?高考(?:[_＿]|\s)", cat) or "相當高考" in cat:
        return (
            "professional-high",
            "專技高考",
            "high",
            f"historical professional high category marker: {cat}",
        )
    professional_patterns = (
        (r"專技高考|專門職業及技術人員高等(?:考試|技師考試)", "professional-high", "專技高考"),
        (r"專技普考|專門職業及技術人員普通考試", "professional-ordinary", "專技普考"),
        (r"專技特考|專門職業及技術人員特種考試", "professional-special", "專技特考"),
    )
    for pattern, level_id, label in professional_patterns:
        if re.search(pattern, cat):
            return level_id, label, "high", f"explicit professional category marker: {cat}"
    event_patterns = (
        (r"晉升士級", "promotion-worker-rank", "士級"),
        (r"專技(?:人員)?檢覈|檢覈筆試|檢覈", "professional-screening", "專技檢覈"),
        (
            (
                r"專門職業及技術人員.*特種考試|特種考試.*(?:中醫師|心理師|營養師|護理師|驗船師|引水人|技師|建築師|醫事|牙體|聽力師|語言治"
                r"療師|消防設備|土地登記專業代理人|不動產經紀人|專責報關|保險)|專責報關.*特考|保險從業.*特考|航海人員.*驗船師|驗船師.*考試"
            ),
            "professional-special",
            "專技特考",
        ),
        (r"專門職業及技術人員高等(?:考試|技師考試)|專技高考", "professional-high", "專技高考"),
        (r"專門職業及技術人員普通(?:考試|技師考試)|專技普考", "professional-ordinary", "專技普考"),
        (
            r"專門職業及技術人員.*高等暨普通|專門職業及技術人員.*高等、普通",
            "combined",
            "合併／制度待審核",
        ),
        (r"中醫師檢定", "qualification-professional", "專業檢定"),
        (r"檢定考試|檢定", "qualification-ordinary", "普通檢定"),
        (r"公務人員初等考試", "elementary", "初等／初考"),
        (r"公務人員高等考試一級", "grade-1", "一等／高考一級"),
        (r"公務人員高等考試二級", "grade-2", "二等／高考二級"),
        (r"公務人員高等考試三級", "grade-3", "三等／高考三級"),
        (r"公務人員普通考試", "ordinary", "普通／普考"),
    )
    matching = [
        (level_id, label, pattern)
        for pattern, level_id, label in event_patterns
        if re.search(pattern, event)
    ]
    if len(matching) == 1:
        level_id, label, _ = matching[0]
        return level_id, label, "medium", f"source event marker: {event}"
    if "升官等" in event or "升等" in event or "升資" in event:
        return (
            "unknown",
            _LEVEL_LABELS["unknown"],
            "review",
            f"promotion level missing from category: {cat or canonical_name}",
        )
    if any(
        marker in event
        for marker in ("外交領事人員", "國際新聞人員", "民航人員", "調查局調查人員", "國家安全局")
    ):
        return (
            NOT_APPLICABLE,
            _LEVEL_LABELS[NOT_APPLICABLE],
            "medium",
            "official special series has no level marker in the source category",
        )
    if "專門職業及技術人員" in professional and ("高等暨普通" in event or "高等、普通" in event):
        return (
            "combined",
            _LEVEL_LABELS["combined"],
            "medium",
            "source event officially combines professional levels without a category marker",
        )
    # A small set of stable professional aliases is intentionally treated as
    # an ungraded qualification when synthetic/legacy rows omit the official
    # event wording. Real ambiguous MOEX rows still remain review-isolated.
    if normalize_text(canonical_name).lower() in {
        "nurse",
        "doctor",
        "護理師",
        "醫師",
        "中醫師",
        "牙醫師",
        "藥師",
        "獸醫師",
    }:
        return (
            NOT_APPLICABLE,
            _LEVEL_LABELS[NOT_APPLICABLE],
            "medium",
            "known professional qualification has no separate level marker",
        )
    return (
        "unknown",
        _LEVEL_LABELS["unknown"],
        "review",
        f"no authoritative level marker in category/event: {cat or event or canonical_name}",
    )


def _non_moex_level(
    provider_id: str, category: str, canonical_id: str, subject_name: str
) -> tuple[str, str, str, str]:
    text = normalize_text(f"{category} {subject_name}")
    if provider_id == "gept_cert":
        for marker, level_id in (
            ("初級", "elementary"),
            ("中高級", "high-intermediate"),
            ("中級", "intermediate"),
            ("高級", "advanced"),
            ("優級", "superior"),
        ):
            if marker in text:
                return level_id, marker, "high", f"GEPT official level marker: {marker}"
    if provider_id == "jlpt_cert":
        match = re.search(r"N([1-5])", text, re.IGNORECASE)
        if match:
            level_id = f"n{match.group(1)}"
            return (
                level_id,
                level_id.upper(),
                "high",
                f"JLPT official level marker: N{match.group(1)}",
            )
    if provider_id == "hakka_cert":
        for level_id in ("basic-elementary", "intermediate-high-intermediate", "advanced"):
            if level_id in canonical_id or level_id.replace("-", "") in text:
                return (
                    level_id,
                    _LEVEL_LABELS[level_id],
                    "high",
                    f"Hakka provider mapping: {level_id}",
                )
    if provider_id == "taigi_cert":
        match = re.search(r"(?:卷|[-_])([ABC])\b", text, re.IGNORECASE)
        if match:
            level_id = f"paper-{match.group(1).lower()}"
            return (
                level_id,
                _LEVEL_LABELS[level_id],
                "high",
                f"Taiwanese language paper marker: {match.group(1).upper()}卷",
            )
    if provider_id == "wdasec_skill":
        for marker, level_id in (
            ("甲級", "class-a"),
            ("乙級", "class-b"),
            ("丙級", "class-c"),
            ("單一級", "single"),
        ):
            if marker in text:
                return level_id, marker, "high", f"skill certification level marker: {marker}"
        return (
            NOT_APPLICABLE,
            _LEVEL_LABELS[NOT_APPLICABLE],
            "medium",
            "skill provider has no level marker in record",
        )
    return (
        NOT_APPLICABLE,
        _LEVEL_LABELS[NOT_APPLICABLE],
        "medium",
        "provider policy declares no level dimension",
    )


def _moex_series(
    category: str, exam_name: str, level_id: str, canonical_id: str
) -> tuple[str, str, str, str]:
    cat = normalize_text(category)
    event = normalize_text(exam_name)
    category_first = (
        ("原住民族", "special-indigenous", "原住民族特考"),
        ("原住民", "special-indigenous", "原住民族特考"),
        ("身心障礙", "special-disability", "身心障礙特考"),
        ("身障", "special-disability", "身心障礙特考"),
        ("關務", "special-customs", "關務特考"),
        ("外交", "special-diplomatic", "外交／國際特考"),
        ("國際經濟商務", "special-diplomatic", "外交／國際特考"),
        ("警察", "special-police", "警察／一般警察特考"),
        ("一般警察", "special-police", "警察／一般警察特考"),
        ("司法", "special-judicial", "司法特考"),
        ("海岸巡防", "special-coast-guard", "海巡特考"),
        ("海巡", "special-coast-guard", "海巡特考"),
        ("移民", "special-immigration", "移民特考"),
        ("退除役", "special-retired-military", "退除役軍人轉任考試"),
        ("軍官轉任", "special-military-transfer", "國軍軍官轉任考試"),
        ("上校轉任", "special-military-transfer", "國軍軍官轉任考試"),
    )
    for marker, series_id, label in category_first:
        if marker in cat:
            return "civil-service", "civil-service-exam", series_id, label
    if (
        "升官等" in cat
        or "升等" in cat
        or "升資" in cat
        or "升官等" in event
        or "升等" in event
        or "升資" in event
        or "晉升士級" in event
    ):
        return (
            "civil-service",
            "civil-promotion",
            "civil-promotion",
            _SERIES_LABELS["civil-promotion"],
        )
    event_rules = (
        ("原住民族", "special-indigenous", "原住民族特考"),
        ("原住民", "special-indigenous", "原住民族特考"),
        ("身心障礙", "special-disability", "身心障礙特考"),
        ("地方特考", "special-local-government", "地方特考"),
        ("地方政府", "special-local-government", "地方特考"),
        ("地方公務人員", "special-local-government", "地方特考"),
        ("基層公務人員", "special-local-government", "地方特考"),
        ("臺灣省", "special-local-government", "地方特考"),
        ("台灣省", "special-local-government", "地方特考"),
        ("福建省", "special-local-government", "地方特考"),
        ("關務", "special-customs", "關務特考"),
        ("外交領事", "special-diplomatic", "外交／國際特考"),
        ("民航人員", "special-aviation", "民航特考"),
        ("驗船師", "professional-special", "專技特考"),
        ("專責報關", "professional-special", "專技特考"),
        ("保險從業", "professional-special", "專技特考"),
        ("中醫師考試", "professional-combined", "專技綜合／歷史制度"),
        ("航海人員", "special-maritime", "航海／船員特考"),
        ("特種考試警察", "special-police", "警察／一般警察特考"),
        ("司法人員", "special-judicial", "司法特考"),
        ("調查局調查人員", "special-investigation", "調查／情報特考"),
        ("專門職業及技術人員", "professional-combined", "專技綜合／歷史制度"),
        ("檢覈", "professional-screening", "專技檢覈／檢覈筆試"),
        ("檢核", "professional-screening", "專技檢覈／檢覈筆試"),
        ("中醫師檢定", "professional-qualification", "專技檢定考試"),
        ("檢定", "civil-qualification", "公務人員檢定考試"),
        ("特種考試", "special-other", "其他特種考試"),
    )
    for marker, series_id, label in event_rules:
        if marker in event:
            if series_id == "professional-combined" and level_id.startswith("professional"):
                series_id = level_id
                label = _SERIES_LABELS.get(series_id, _SERIES_LABELS["professional-combined"])
            return (
                "professional" if series_id.startswith("professional") else "civil-service",
                "professional-exam"
                if series_id.startswith("professional")
                else "civil-service-exam",
                series_id,
                label,
            )
    if level_id == "elementary":
        return (
            "civil-service",
            "civil-service-exam",
            "civil-elementary",
            _SERIES_LABELS["civil-elementary"],
        )
    if level_id == "ordinary":
        return (
            "civil-service",
            "civil-service-exam",
            "civil-ordinary",
            _SERIES_LABELS["civil-ordinary"],
        )
    if level_id == "grade-1" or level_id == "grade-2" or level_id == "grade-3":
        return "civil-service", "civil-service-exam", "civil-high", _SERIES_LABELS["civil-high"]
    if level_id.startswith("professional"):
        return (
            "professional",
            "professional-exam",
            "professional-combined",
            _SERIES_LABELS["professional-combined"],
        )
    if canonical_id in {
        "nurse",
        "doctor",
        "dietitian",
        "social-worker",
        "psychologist",
        "counseling-psychologist",
        "clinical-psychologist",
    }:
        return (
            "professional",
            "professional-exam",
            "professional-combined",
            _SERIES_LABELS["professional-combined"],
        )
    return "civil-service", "civil-service-exam", "moex-unknown", "MOEX待審核考試"


def _provider_series(provider_id: str, canonical_id: str) -> tuple[str, str, str, str]:
    if provider_id == "ceec_gsat":
        return (
            "admissions",
            "university-admission",
            "admission-gsat",
            _SERIES_LABELS["admission-gsat"],
        )
    if provider_id == "ceec_ast":
        return (
            "admissions",
            "university-admission",
            "admission-ast",
            _SERIES_LABELS["admission-ast"],
        )
    if provider_id == "tcte_tve":
        return (
            "admissions",
            "technical-admission",
            "admission-tcte",
            _SERIES_LABELS["admission-tcte"],
        )
    if provider_id == "rcpet_cap":
        return "admissions", "secondary-admission", "admission-cap", _SERIES_LABELS["admission-cap"]
    if provider_id == "special_admission":
        return (
            "admissions",
            "special-admission",
            "admission-special",
            _SERIES_LABELS["admission-special"],
        )
    if provider_id == "gept_cert":
        return (
            "certification",
            "language-certification",
            "language-gept",
            _SERIES_LABELS["language-gept"],
        )
    if provider_id == "jlpt_cert":
        return (
            "certification",
            "language-certification",
            "language-jlpt",
            _SERIES_LABELS["language-jlpt"],
        )
    if provider_id == "tocfl_cert":
        return (
            "certification",
            "language-certification",
            "language-tocfl",
            _SERIES_LABELS["language-tocfl"],
        )
    if provider_id == "hakka_cert":
        return (
            "certification",
            "language-certification",
            "language-hakka",
            _SERIES_LABELS["language-hakka"],
        )
    if provider_id == "taigi_cert":
        return (
            "certification",
            "language-certification",
            "language-taigi",
            _SERIES_LABELS["language-taigi"],
        )
    if provider_id == "wdasec_skill":
        return (
            "certification",
            "skill-certification",
            "skill-certification",
            _SERIES_LABELS["skill-certification"],
        )
    if provider_id in {"sfi_cert", "tabf_cert", "tii_cert"}:
        return (
            "certification",
            "financial-certification",
            "financial-certification",
            _SERIES_LABELS["financial-certification"],
        )
    if provider_id == "ipas_cert":
        return (
            "certification",
            "professional-certification",
            "professional-certification",
            _SERIES_LABELS["professional-certification"],
        )
    if provider_id == "teacher_qual":
        return (
            "teacher",
            "teacher-exam",
            "teacher-qualification",
            _SERIES_LABELS["teacher-qualification"],
        )
    if provider_id.startswith("teacher_recruit"):
        return (
            "teacher",
            "teacher-exam",
            "teacher-recruitment",
            _SERIES_LABELS["teacher-recruitment"],
        )
    if provider_id == "post_recruit":
        return (
            "employment",
            "employment-exam",
            "postal-recruitment",
            _SERIES_LABELS["postal-recruitment"],
        )
    if provider_id in {
        "moea_recruit",
        "taipower_recruit",
        "cpc_recruit",
        "twc_recruit",
        "taisugar_recruit",
    }:
        return (
            "employment",
            "employment-exam",
            "employment-recruitment",
            _SERIES_LABELS["employment-recruitment"],
        )
    if provider_id.startswith("hce_"):
        return (
            "admissions",
            "university-admission",
            "post-baccalaureate-medical",
            "學士後醫學／中醫",
        )
    return (
        "other",
        "provider-exam",
        _slug(canonical_id, prefix="series"),
        _display(canonical_id, "待審核考試"),
    )


def _classify_paper_uncached(
    *,
    provider_id: str,
    source_exam_id: str,
    year_ad: int,
    category_raw: str,
    exam_name_raw: str,
    canonical_id: str,
    canonical_name: str,
    subject_name_raw: str = "",
    subject_code: str = "",
) -> ExamIdentity:
    provider_id = normalize_text(provider_id) or "unknown-provider"
    category = normalize_text(category_raw)
    exam_name = normalize_text(exam_name_raw)
    if provider_id == "moex":
        level_id, level_label, confidence, reason = _moex_level(category, exam_name, canonical_name)
        domain_id, family_id, series_id, series_label = _moex_series(
            category, exam_name, level_id, canonical_id
        )
    else:
        level_id, level_label, confidence, reason = _non_moex_level(
            provider_id, category, canonical_id, subject_name_raw
        )
        domain_id, family_id, series_id, series_label = _provider_series(provider_id, canonical_id)
    track_id, track_label = _track_details(
        provider_id,
        category,
        canonical_id,
        canonical_name,
        subject_name_raw,
        subject_code,
        source_exam_id,
        exam_name,
    )
    variants = _variant_ids(category, exam_name)
    stage_id = _stage_id(category, exam_name)
    if not source_exam_id:
        confidence = "review"
        reason = f"missing source exam event id; {reason}"
    if track_id.endswith("unknown"):
        confidence = "review"
        reason = f"track cannot be resolved; {reason}"
    if level_id == "unknown":
        confidence = "review"
    if provider_id == "moex" and series_id == "moex-unknown":
        confidence = "review"
    parts = [provider_id, series_id, level_id, track_id, *variants]
    if stage_id != NOT_APPLICABLE:
        parts.append(stage_id)
    if confidence == "review" and source_exam_id:
        parts.append(f"event-{_ascii_slug(source_exam_id, prefix='event')}")
    bundle_id = "-".join(_ascii_slug(part, prefix="concept") for part in parts if part)
    if provider_id == "moex":
        track_display = _display(track_label, canonical_name)
        level_display = _LEVEL_LABELS.get(level_id, level_label)
        bundle_name = f"{series_label}｜{level_display}｜{track_display}"
        if variants:
            bundle_name += f"｜{'、'.join(variants)}"
        if stage_id != NOT_APPLICABLE:
            bundle_name += f"｜{stage_id}"
    else:
        bundle_name = _display(canonical_name, track_label)
        if level_id not in {NOT_APPLICABLE, "unknown"}:
            bundle_name = f"{bundle_name}｜{level_label}"
    return ExamIdentity(
        provider_id=provider_id,
        domain_id=domain_id,
        exam_family_id=family_id,
        exam_series_id=series_id,
        level_id=level_id,
        track_id=track_id,
        variant_ids=variants,
        stage_id=stage_id,
        exam_event_id=source_exam_id,
        bundle_id=bundle_id,
        bundle_name=bundle_name,
        confidence=confidence,
        reason=reason,
        series_label=series_label,
        level_label=level_label,
        track_label=track_label,
    )


def identity_fields(identity: ExamIdentity) -> dict[str, Any]:
    facets = public_facets(identity)
    return {
        "schema_version": IDENTITY_SCHEMA_VERSION,
        "catalog_version": CATALOG_VERSION,
        "domain_id": identity.domain_id,
        "exam_family_id": identity.exam_family_id,
        "exam_series_id": identity.exam_series_id,
        "level_id": identity.level_id,
        "track_id": identity.track_id,
        "variant_ids": list(identity.variant_ids),
        "stage_id": identity.stage_id,
        "exam_event_id": identity.exam_event_id,
        "bundle_id": identity.bundle_id,
        "bundle_name": identity.bundle_name,
        "bundle_policy_id": BUNDLE_POLICY_ID,
        "classification_confidence": identity.confidence,
        "classification_reason": identity.reason,
        "exam_class": facets["exam_class"],
        "exam_subclass": facets["exam_subclass"],
    }


@lru_cache(maxsize=8192)
def _classify_moex_record(
    source_exam_id: str,
    year_ad: int,
    category_raw: str,
    exam_name_raw: str,
    canonical_id: str,
    canonical_name: str,
) -> ExamIdentity:
    # MOEX classification depends on the event and category, not the paper's
    # subject. Hundreds of papers can therefore share one immutable identity.
    return _classify_paper_uncached(
        provider_id="moex",
        source_exam_id=source_exam_id,
        year_ad=year_ad,
        category_raw=category_raw,
        exam_name_raw=exam_name_raw,
        canonical_id=canonical_id,
        canonical_name=canonical_name,
    )


def classify_paper(
    *,
    provider_id: str,
    source_exam_id: str,
    year_ad: int,
    category_raw: str,
    exam_name_raw: str,
    canonical_id: str,
    canonical_name: str,
    subject_name_raw: str = "",
    subject_code: str = "",
) -> ExamIdentity:
    if provider_id == "moex":
        return _classify_moex_record(
            source_exam_id,
            year_ad,
            category_raw,
            exam_name_raw,
            canonical_id,
            canonical_name,
        )
    return _classify_paper_uncached(
        provider_id=provider_id,
        source_exam_id=source_exam_id,
        year_ad=year_ad,
        category_raw=category_raw,
        exam_name_raw=exam_name_raw,
        canonical_id=canonical_id,
        canonical_name=canonical_name,
        subject_name_raw=subject_name_raw,
        subject_code=subject_code,
    )


def classify_normalized_paper(paper: Any) -> ExamIdentity:
    return classify_paper(
        provider_id=getattr(paper, "provider_id", ""),
        source_exam_id=getattr(paper, "source_exam_id", ""),
        year_ad=int(getattr(paper, "year_roc", 0) or 0) + 1911,
        category_raw=getattr(paper, "category_raw", ""),
        exam_name_raw=getattr(paper, "exam_name_raw", ""),
        canonical_id=getattr(paper, "canonical_id", ""),
        canonical_name=getattr(paper, "canonical_name", ""),
        subject_name_raw=getattr(paper, "subject_name_raw", ""),
        subject_code=getattr(paper, "subject_code", ""),
    )


def public_facets(identity: ExamIdentity) -> dict[str, str]:
    if identity.domain_id == "civil-service":
        exam_class = "公職考試"
        exam_subclass = "公職／公務人員"
    elif identity.domain_id == "professional":
        exam_class = "專技人員考試"
        exam_subclass = identity.series_label
    elif identity.domain_id == "admissions":
        exam_class = "升學測驗"
        exam_subclass = identity.series_label
    elif identity.domain_id == "teacher":
        exam_class = "教師考試"
        exam_subclass = identity.series_label
    elif identity.domain_id == "employment":
        exam_class = "國營／就業甄試"
        exam_subclass = identity.series_label
    elif identity.domain_id == "certification":
        exam_class = "證照／檢定"
        exam_subclass = identity.series_label
    else:
        exam_class = "其他考試"
        exam_subclass = identity.series_label
    return {"exam_class": exam_class, "exam_subclass": exam_subclass}
