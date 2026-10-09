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

from app.hakka_identity import hakka_conflict_reason, hakka_dialect, hakka_level
from app.moex_identity_evidence import resolve_moex_category_identity
from app.source_material import SourceMaterial, material_label
from app.tqc_identity_evidence import resolve_tqc_sample_identity, tqc_identity_catalog

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
    "promotion-police": "警察人員升官等考試",
    "promotion-customs": "關務人員升官等考試",
    "promotion-railway": "鐵路人員升資考試",
    "promotion-highway": "公路人員升資考試",
    "promotion-port": "港務人員升資考試",
    "promotion-postal": "郵政人員升資考試",
    "promotion-telecommunications": "電信人員升資考試",
    "promotion-water-transport": "水運人員升資考試",
    "promotion-aviation": "民航人員升資考試",
    "special-local-government": "地方特考",
    "special-indigenous": "原住民族特考",
    "special-disability": "身心障礙特考",
    "special-customs": "關務特考",
    "special-diplomatic": "外交／國際特考",
    "special-police": "警察人員特考",
    "special-general-police": "一般警察人員特考",
    "special-national-security": "國家安全情報人員特考",
    "special-railway": "鐵路人員特考",
    "special-highway": "公路人員特考",
    "special-port": "港務人員特考",
    "special-judicial": "司法特考",
    "special-coast-guard": "海巡特考",
    "special-immigration": "移民特考",
    "special-other": "其他特種考試",
    "special-aviation": "民航特考",
    "special-maritime": "航海／船員特考",
    "special-investigation": "調查局調查人員特考",
    "eligibility-qualification": "應考資格檢定考試",
    "chinese-medicine-eligibility": "中醫師檢定考試",
    "special-military-transfer": "國軍軍官轉任考試",
    "special-retired-military": "退除役軍人轉任考試",
    "professional-high": "專技高考",
    "professional-ordinary": "專技普考",
    "professional-special": "專技特考",
    "professional-combined": "專技綜合／歷史制度",
    "professional-screening": "專技檢覈／檢覈筆試",
    "professional-ship-radio": "船舶電信人員特考",
    "professional-navigation-special": "航海人員特考",
    "professional-navigation-high": "航海人員高考",
    "professional-navigation-ordinary": "航海人員普考",
    "professional-fishing-special": "漁船船員特考",
    "professional-fishing-screening": "漁船船員檢覈筆試",
    "moex-unknown": "MOEX待審核考試",
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
    "grade-1": "一等",
    "grade-2": "二等",
    "grade-3": "三等",
    "grade-4": "四等",
    "grade-5": "五等",
    "maritime-principal": "正",
    "maritime-assistant": "副",
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
    "transport-senior-3": "高員三級",
    "transport-employee": "員級",
    "transport-associate": "佐級",
    "grade-d": "丁等",
    "qualification-high": "高等檢定",
    "qualification-ordinary": "普通檢定",
    "qualification-professional": "專業檢定",
    "police-commissioned": "警監",
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
    "hakka-elementary": "初級",
    "intermediate-high-intermediate": "中級暨中高級",
    "advanced": "高級",
    "cefr-a1-a2": "A1 基礎級／A2 初級",
    "cefr-b1-b2": "B1 中級／B2 中高級",
    "cefr-c1-c2": "C1 高級／C2 專業級",
    "single": "單一級",
    "class-a": "甲級",
    "class-b": "乙級",
    "class-c": "丙級",
    "radio-general": "通用",
    "radio-special": "特別",
    "radio-limited": "限用",
    "radio-ordinary": "普通",
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
    slug = _ascii_slug(normalized, prefix=prefix)
    # Mixed-script labels must retain all of their meaning. Dropping the
    # Chinese text collapses different occupations to e.g. "pc" or "cnc".
    if re.search(r"[a-zA-Z0-9]", normalized) and any(
        char.isalnum() and not char.isascii() for char in normalized
    ):
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
        return f"{slug}-{digest}"
    return slug


def _display(value: str, fallback: str) -> str:
    normalized = normalize_text(value)
    return normalized or fallback


def _parenthetical_values(text: str) -> list[str]:
    return [normalize_text(value) for value in re.findall(r"[（(]([^）)]*)[）)]", text)]


_STAGE_LABELS = {"stage-1": "第一試", "stage-2": "第二試", "stage-3": "第三試", "pretest": "預試"}
STAGE_IDS = frozenset(_STAGE_LABELS)
# Providers whose public title appends the track label; validate_publication
# derives its bundle-id prefixes from this set.
TRACK_TITLED_PROVIDERS = frozenset(
    {"ceec_gsat", "ceec_ast", "tcte_tve", "wdasec_skill", "tqc_cert"}
)


def _variants(category: str, exam_name: str) -> tuple[tuple[str, str], ...]:
    """Variant ids with the source wording that produced each one."""
    text = normalize_text(f"{category} {exam_name}")
    found: dict[str, str] = {}
    for value in _parenthetical_values(text):
        if re.search(r"一般組", value):
            found.setdefault("general-group", "一般組")
        match = re.search(r"兩岸組\s*([一二三1-3])", value)
        if match:
            number = {"一": "1", "二": "2", "三": "3"}.get(match.group(1), match.group(1))
            found.setdefault(f"cross-strait-group-{number}", f"兩岸組{'一二三'[int(number) - 1]}")
        match = re.search(r"選試\s*(.+)", value)
        if match:
            found.setdefault(
                f"elective-{_slug(match.group(1), prefix='language')}", f"選試{match.group(1)}"
            )
        if any(token in value for token in ("國防部", "退輔會", "海委會")):
            found.setdefault(f"destination-{_slug(value, prefix='destination')}", value)
        if "錄取分發區" in value:
            region = value.replace("錄取分發區", "")
            found.setdefault(f"distribution-{_slug(region, prefix='region')}", value)
    for language in re.findall(r"選試\s*([一-龥A-Za-z]+)", text):
        found.setdefault(f"elective-{_slug(language, prefix='language')}", f"選試{language}")
    return tuple(sorted(found.items()))


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
def _clean_moex_track(category: str, canonical_name: str, series_id: str = "") -> str:
    value = normalize_text(category or canonical_name)
    if "_" in value:
        value = value.split("_")[-1]
    value = re.sub(r"^\d+(?:年|\s+)", "", value)
    if series_id == "special-national-security":
        value = re.sub(r"^(?:國家安全局)?國家安全情報人員(?:考試)?", "", value)
    elif series_id == "special-investigation":
        value = re.sub(r"^(?:法務部調查局調查人員|調查局調查人員|調查人員)(?:考試)?", "", value)
    value = re.sub(r"^(?:專門職業及技術人員|專技)(?:高等|普通|特種)?考試", "", value)
    value = re.sub(r"^(?:高等|普通|初等|特種)考試", "", value)
    value = re.sub(
        r"^(?:高考|普考|初等|初考|特考|地方政府公務人員考試|原住民族考試|身心障礙人員考試|身障特考|關務特考|關務人員考試)",
        "",
        value,
    )
    value = re.sub(r"^(?:一級|二級|三級|三等|四等|五等|1等|2等|3等|4等|5等)考試", "", value)
    value = re.sub(r"(?:類科|科別)$", "", value)
    # Recent official police rows insert 類別 after 人員, sometimes before a
    # group name. It is heading boilerplate; the following group stays intact.
    value = value.replace("人員類別", "人員")
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
    series_id: str = "",
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
    if provider_id == "cpc_recruit" and "博士" in category:
        return "cpc-doctoral", "中油新進博士級人員甄試"
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
        qualification = _moex_maritime_qualification(category, exam_name)
        if qualification is not None:
            return qualification.track_id, qualification.track_label
        professional = _moex_professional_qualification(category, exam_name)
        if professional is not None:
            return _slug(professional.occupation, prefix="track"), professional.occupation
        value = _clean_moex_track(category, canonical_name, series_id)
        return _slug(value, prefix="track"), value
    value = (
        normalize_text(category) or normalize_text(canonical_name) or normalize_text(subject_name)
    )
    return _slug(value or source_exam_id, prefix="track"), value or source_exam_id


def _moex_transport_series(category: str, exam_name: str) -> str | None:
    """Resolve transport recruitment independently of a co-hosted programme.

    Historical listings sometimes retain only the transport grade in the
    category. That identifies railway recruitment only when the event has
    one transport programme. Explicit promotion evidence takes precedence.
    """
    if re.search(r"升資|升等|升官等|晉升士級|(?:員|佐|士)(?:級)?晉", f"{category} {exam_name}"):
        return None
    heading = category.split("_", 1)[0]
    programmes = (
        ("鐵路人員", "special-railway"),
        ("公路人員", "special-highway"),
        ("港務人員", "special-port"),
    )
    explicit = [series for marker, series in programmes if marker in heading]
    if explicit:
        return explicit[0] if len(explicit) == 1 else "moex-unknown"
    if not re.search(r"高員(?:三|3)級|員級|佐級", heading):
        return None
    event_series = [series for marker, series in programmes if marker in exam_name]
    if len(event_series) > 1:
        return "moex-unknown"
    return event_series[0] if event_series else None


@lru_cache(maxsize=8192)
def _moex_eligibility_programme(category: str, exam_name: str) -> str | None:
    """Resolve the historical eligibility tests without consuming cohosted exams."""
    if re.search(r"高等檢定|普通檢定", category):
        return "eligibility-qualification"
    if "中醫師檢定" in category:
        return "chinese-medicine-eligibility"
    if "檢定" not in exam_name:
        return None
    if re.fullmatch(r"中醫師(?:考試)?", category) and "中醫師檢定" in exam_name:
        # An explicit cohosted 中醫師 licensing examination would still need
        # question-header evidence to distinguish it from the eligibility test.
        remainder = exam_name.replace("中醫師檢定", "")
        if "中醫師" not in remainder:
            return "chinese-medicine-eligibility"
    if any(marker in exam_name for marker in ("專門職業及技術人員", "專技", "公務人員")):
        return None
    if re.fullmatch(r"中醫師(?:考試)?", category):
        return "chinese-medicine-eligibility"
    return "eligibility-qualification"


def _moex_native_level_label(series_id: str, level_id: str, fallback: str) -> str:
    if series_id == "civil-high":
        return {"grade-1": "一級", "grade-2": "二級", "grade-3": "三級"}.get(level_id, fallback)
    if series_id == "professional-ship-radio" or series_id.startswith("professional-fishing-"):
        # Native wording includes different radio suffixes and genuinely
        # ungraded fishing technical occupations.
        return fallback
    return _LEVEL_LABELS.get(level_id, fallback)


@lru_cache(maxsize=8192)
def _moex_ship_inspector_programme(category: str, exam_name: str) -> str | None:
    """Resolve FL016883 from its own heading, independently of cohosted exams."""
    category = re.sub(r"\s+", "", normalize_text(category))
    event = normalize_text(exam_name)
    native = re.fullmatch(r"(?:(?P<heading>驗船師(?:高等考試)?)_)?驗船師", category)
    if native is None or "考試" not in event or re.search(r"升資|升等|升官等", event):
        return None
    if native["heading"] == "驗船師高等考試":
        return "professional-high"
    # An exam list can contain several statutory programmes and levels. Stop
    # at a new level heading; a later special exam cannot change this one.
    if re.search(r"高等考試(?:(?!高等暨普通考試|普通考試|特種考試|專技特考).)*驗船師", event):
        return "professional-high"
    if re.search(r"(?:特種考試|專技特考)(?:(?!高等考試|普通考試).)*驗船師", event):
        return "professional-special"
    return None


@dataclass(frozen=True)
class _ProfessionalQualification:
    series_id: str
    level_id: str
    occupation: str
    stage_id: str


@lru_cache(maxsize=8192)
def _moex_professional_qualification(
    category: str, exam_name: str
) -> _ProfessionalQualification | None:
    """Use a qualification's own programme clause in a shared exam heading.

    Special-exam equivalence is not an administered high/ordinary level.
    The occupational boundary also prevents a cohosted stage from leaking
    into an unrelated qualification.
    """
    event = normalize_text(exam_name)
    if re.search(r"公務人員|升官等|升等|升資|檢定|檢覈", event):
        return None
    native = re.fullmatch(
        r"(?P<occupation>消防設備[師士]|不動產經紀人|不動產估價師|專責報關人員"
        r"|(?:人身|財產)保險(?:代理|經紀)人|(?:一般|海事)保險公證人"
        r"|中醫師|社會工作師|呼吸治療師|語言治療師|聽力師|牙體技術[師生]"
        r"|地政士|食品技師|大地工程技師|驗光[師生])"
        r"(?:(?:考試分階段考試[（(])?第(?P<stage>[一二])階段考試[）)]?"
        r"|[（(](?P<short_stage>[一二])[）)])?",
        normalize_text(category).rsplit("_", 1)[-1],
    )
    if native is None:
        return None
    occupation = native["occupation"]
    if native["short_stage"] is not None and occupation != "大地工程技師":
        return None
    aliases = [occupation]
    if occupation.startswith("消防設備"):
        aliases.extend(("消防設備人員", "消防人員"))
    elif "保險" in occupation:
        aliases.extend(("保險從業", "保險人員", "保險代理人保險經紀人及保險公證人"))
    elif occupation == "專責報關人員":
        aliases.append("報關人員")
    elif occupation.startswith("牙體技術"):
        aliases.append("牙體技術人員")
    elif occupation.startswith("驗光"):
        aliases.append("驗光人員")
    elif occupation in {"食品技師", "大地工程技師"}:
        aliases.append("技師")
    markers = list(
        re.finditer(r"高等[暨、]普通考試|高等考試|普通考試|特種考試|專技[高普特]考", event)
    )
    programmes = set()
    for position, marker in enumerate(markers):
        end = markers[position + 1].start() if position + 1 < len(markers) else len(event)
        clause = event[marker.end() : end]
        if not any(alias in clause for alias in aliases):
            # Some retained headings name only the combined regular programme.
            if not (len(markers) == 1 and not clause.strip() and "專門職業及技術人員" in event):
                continue
        heading = marker[0]
        if heading in {"高等暨普通考試", "高等、普通考試"}:
            # These regulated qualifications have their own regular exam level.
            # This applies only inside the regular programme clause, never to
            # a special examination that states legal high/ordinary equivalence.
            ordinary = (
                occupation
                in {
                    "消防設備士",
                    "不動產經紀人",
                    "專責報關人員",
                    "地政士",
                    "牙體技術生",
                    "驗光生",
                }
                or "保險" in occupation
            )
            programmes.add("professional-ordinary" if ordinary else "professional-high")
        elif heading in {"高等考試", "專技高考"}:
            programmes.add("professional-high")
        elif heading in {"普通考試", "專技普考"}:
            programmes.add("professional-ordinary")
        else:
            programmes.add("professional-special")
    if "專技" in event and f"{occupation}特考" in event:
        programmes.add("professional-special")
    if not programmes:
        return None
    if len(programmes) > 1:
        category_heading = normalize_text(category).rsplit("_", 1)[0]
        if "相當" in category_heading or "專技特考" in category_heading:
            explicit = "professional-special"
        elif re.search(r"高等考試|專技高考|(?:^|_)高(?:等|考)(?:_|$)", category_heading):
            explicit = "professional-high"
        elif re.search(r"普通考試|專技普考|(?:^|_)(?:普通|普考|普)(?:_|$)", category_heading):
            explicit = "professional-ordinary"
        else:
            explicit = None
        if explicit in programmes:
            programmes = {explicit}
    series = next(iter(programmes)) if len(programmes) == 1 else "professional-combined"
    level = {
        "professional-high": "professional-high",
        "professional-ordinary": "professional-ordinary",
        "professional-special": NOT_APPLICABLE,
        "professional-combined": "unknown",
    }[series]
    stage = {"一": "stage-1", "二": "stage-2"}.get(
        native["stage"] or native["short_stage"], NOT_APPLICABLE
    )
    return _ProfessionalQualification(series, level, occupation, stage)


@dataclass(frozen=True)
class _MaritimeQualification:
    series_id: str
    level_id: str
    level_label: str
    track_id: str
    track_label: str
    variants: tuple[tuple[str, str], ...]


@lru_cache(maxsize=8192)
def _moex_ship_radio_category(category: str, exam_name: str) -> _MaritimeQualification | None:
    """Resolve native radio qualifications, independently of cohosted exams.

    FL016859 distinguishes these grades, occupations and military/retake
    subject tables. Legal high/ordinary equivalence is not a native grade.
    Fishing-crew operators belong to their own programme after the 1999
    scope amendment; a shared paper does not merge the two qualifications.
    """
    category = re.sub(r"\s+", "", normalize_text(category))
    if "船舶電信" not in exam_name and not category.startswith("船舶電信人員"):
        return None
    if re.search(r"升資|升等|升官等", exam_name):
        return None
    match = re.fullmatch(
        r"(?:船舶電信人員)?(?P<navy>海軍)?(?P<retake>補考)?"
        r"(?P<grade>通用級|一等|二等|特別級|限用級|普通|限用|通用)"
        r"(?P<role>報務員|話務員|無線電子員|值機員)"
        r"(?:\((?P<suffix>補考|海軍)\))?",
        category,
    )
    if match is None:
        return None
    grade, role = match["grade"], match["role"]
    allowed_grades = {
        "報務員": {"通用級", "一等", "二等", "特別級"},
        "話務員": {"通用級", "限用級"},
        "無線電子員": {"一等", "二等"},
        "值機員": {"普通", "限用", "通用"},
    }
    if grade not in allowed_grades[role]:
        return None
    level_id = {
        "一等": "grade-1",
        "二等": "grade-2",
        "通用級": "radio-general",
        "通用": "radio-general",
        "特別級": "radio-special",
        "限用級": "radio-limited",
        "限用": "radio-limited",
        "普通": "radio-ordinary",
    }[grade]
    track_id = {
        "報務員": "radio-telegraphist",
        "話務員": "radio-telephonist",
        "無線電子員": "radio-electronic-operator",
        "值機員": "radio-operator",
    }[role]
    variants = []
    if match["navy"] or match["suffix"] == "海軍":
        variants.append(("navy-transfer", "海軍轉任"))
    if match["retake"] or match["suffix"] == "補考":
        variants.append(("subject-retake", "補考"))
    return _MaritimeQualification(
        "professional-ship-radio", level_id, grade, track_id, role, tuple(variants)
    )


@lru_cache(maxsize=8192)
def _moex_navigation_category(category: str, exam_name: str) -> _MaritimeQualification | None:
    """Resolve navigation qualifications under FL016913, not event-title order.

    Native ship grades and occupations survive the special/high/ordinary
    programme change. Engine endorsements and naval/retake routes retain
    their separate subject sets. Fishing-crew qualifications are excluded.
    """
    category = re.sub(r"\s+", "", normalize_text(category))
    event = normalize_text(exam_name)
    if "航海人員" not in event or "考試" not in event:
        return None
    if re.search(r"升資|升等|升官等", event):
        return None
    match = re.fullmatch(
        r"(?P<programme>航海人員_?|(?:專技)?高考_|(?:專技)?普考_)?"
        r"(?P<prefix>海軍|補考|加註)?(?P<grade>一等|二等|三等|正|副)"
        r"(?P<role>船長|大副|船副|輪機長|大管輪|管輪|駕駛|司機)"
        r"(?:\((?P<suffix>海軍補考|海軍|補考|加註)\))?",
        category,
    )
    if match is None:
        return None
    grade, role = match["grade"], match["role"]
    if (grade in {"正", "副"}) != (role in {"駕駛", "司機"}):
        return None
    if grade == "三等" and role in {"大副", "大管輪"}:
        return None
    route = f"{match['prefix'] or ''}{match['suffix'] or ''}"
    if "加註" in route and role not in {"輪機長", "大管輪", "管輪"}:
        return None
    programme = match["programme"] or ""
    if "高考" in programme:
        series = "professional-navigation-high"
    elif "普考" in programme:
        series = "professional-navigation-ordinary"
    elif "高等暨普通考試航海人員" in event:
        series = {
            "一等": "professional-navigation-high",
            "二等": "professional-navigation-ordinary",
        }.get(grade, "")
    else:
        series = "professional-navigation-special"
    if series != "professional-navigation-special":
        expected_grade = "一等" if series == "professional-navigation-high" else "二等"
        if not series or grade != expected_grade or role not in {"船副", "管輪"}:
            return None
    level = {
        "一等": "grade-1",
        "二等": "grade-2",
        "三等": "grade-3",
        "正": "maritime-principal",
        "副": "maritime-assistant",
    }[grade]
    track = {
        "船長": "captain",
        "大副": "chief-mate",
        "船副": "deck-officer",
        "輪機長": "chief-engineer",
        "大管輪": "first-engineer",
        "管輪": "engineer",
        "駕駛": "navigation-driver",
        "司機": "navigation-mechanic",
    }[role]
    variants = []
    if "海軍" in route:
        variants.append(("navy-transfer", "海軍轉任"))
    if "補考" in route:
        variants.append(("subject-retake", "補考"))
    if "加註" in route:
        variants.append(("engine-endorsement", "主機加註"))
    if "舊案補考" in event:
        variants.append(("legacy-case-retake", "舊案補考"))
    return _MaritimeQualification(series, level, grade, track, role, tuple(variants))


@lru_cache(maxsize=8192)
def _moex_fishing_crew_category(category: str, exam_name: str) -> _MaritimeQualification | None:
    """Preserve fishing qualifications under FL016872 and FL016890.

    Some cohosted event titles omit fishing entirely. Native category wording
    identifies the programme; legal equivalence does not replace its grades.
    """
    category = re.sub(r"\s+", "", normalize_text(category))
    event = normalize_text(exam_name)
    if "考試" not in event or re.search(r"升資|升等|升官等", event):
        return None
    old = re.fullmatch(
        r"(?P<prefix>補考)?(?P<grade>一級|二級|三級|四級)(?P<role>漁航員|輪機員)"
        r"(?:\((?P<suffix>檢覈筆試|檢覈|補考)\))?",
        category,
    )
    technical = re.fullmatch(
        r"(?P<prefix>補考)?(?P<role>製造主任技術員|冷凍長技術員)(?:\((?P<suffix>補考)\))?",
        category,
    )
    series = "professional-fishing-special"
    variants: tuple[tuple[str, str], ...] = ()
    if old is not None:
        grade, role = old["grade"], old["role"]
        if old["suffix"] in {"檢覈", "檢覈筆試"}:
            if grade != "一級" or old["prefix"]:
                return None
            series = "professional-fishing-screening"
        if old["prefix"] or old["suffix"] == "補考":
            variants = (("subject-retake", "補考"),)
        level = {
            "一級": "maritime-rank-1",
            "二級": "maritime-rank-2",
            "三級": "maritime-rank-3",
            "四級": "maritime-rank-4",
        }[grade]
        track = "fishing-navigator" if role == "漁航員" else "fishing-engineer"
    elif technical is not None:
        role = technical["role"]
        level, grade = NOT_APPLICABLE, "未分級"
        track = "manufacturing-officer" if role == "製造主任技術員" else "refrigeration-chief"
        if technical["prefix"] or technical["suffix"]:
            variants = (("subject-retake", "補考"),)
    else:
        # The ROC90 reform explicitly prefixes these categories with 漁船船員.
        # An unprefixed ship officer or radio operator can belong to a different
        # cohosted programme and must never be consumed by this rule.
        native = re.fullmatch(r"漁船船員_?(?P<qualification>.+)", category)
        if native is None:
            return None
        qualification = native["qualification"]
        officer = re.fullmatch(
            r"(?P<grade>一等|二等|三等)(?P<role>船長|船副|輪機長|大管輪|管輪)", qualification
        )
        if officer is not None:
            grade, role = officer["grade"], officer["role"]
            allowed = {
                "一等": {"船長", "船副", "輪機長", "大管輪", "管輪"},
                "二等": {"船長", "船副", "輪機長"},
                "三等": {"船長", "船副"},
            }
            if role not in allowed[grade]:
                return None
            level = {"一等": "grade-1", "二等": "grade-2", "三等": "grade-3"}[grade]
            track = {
                "船長": "captain",
                "船副": "deck-officer",
                "輪機長": "chief-engineer",
                "大管輪": "first-engineer",
                "管輪": "engineer",
            }[role]
        else:
            radio = {
                "無線電子員": (NOT_APPLICABLE, "未分級", "radio-electronic-operator", "無線電子員"),
                "普通值機員": ("radio-ordinary", "普通", "radio-operator", "值機員"),
                "限用值機員": ("radio-limited", "限用", "radio-operator", "值機員"),
                "一級話務員": ("maritime-rank-1", "一級", "radio-telephonist", "話務員"),
                "二級話務員": ("maritime-rank-2", "二級", "radio-telephonist", "話務員"),
            }.get(qualification)
            if radio is None:
                return None
            level, grade, track, role = radio
    return _MaritimeQualification(series, level, grade, track, role, variants)


@lru_cache(maxsize=8192)
def _moex_maritime_qualification(category: str, exam_name: str) -> _MaritimeQualification | None:
    return (
        _moex_ship_radio_category(category, exam_name)
        or _moex_navigation_category(category, exam_name)
        or _moex_fishing_crew_category(category, exam_name)
    )


@lru_cache(maxsize=8192)
def _moex_level(category: str, exam_name: str, canonical_name: str) -> tuple[str, str, str, str]:
    cat = normalize_text(category)
    event = normalize_text(exam_name)
    professional = f"{cat} {event}"
    inspector = _moex_ship_inspector_programme(cat, event)
    if inspector is not None:
        level = "professional-high" if inspector == "professional-high" else NOT_APPLICABLE
        return level, _LEVEL_LABELS[level], "high", f"native ship-inspector programme: {cat}"
    qualification = _moex_maritime_qualification(cat, event)
    if qualification is not None:
        if qualification.series_id.startswith("professional-fishing-"):
            programme = "fishing-crew"
        elif qualification.series_id == "professional-ship-radio":
            programme = "ship-radio"
        else:
            programme = "navigation"
        return (
            qualification.level_id,
            qualification.level_label,
            "high",
            f"native {programme} qualification: {cat}",
        )
    eligibility = _moex_eligibility_programme(cat, event)
    if eligibility == "chinese-medicine-eligibility":
        return (
            NOT_APPLICABLE,
            _LEVEL_LABELS[NOT_APPLICABLE],
            "high",
            "ungraded Chinese-medicine eligibility test",
        )
    if eligibility == "eligibility-qualification":
        for pattern, level_id in (
            (r"^高等(?:檢定)?(?:_|考試|$)", "qualification-high"),
            (r"^普通(?:檢定)?(?:_|考試|$)", "qualification-ordinary"),
        ):
            if re.search(pattern, cat):
                return (
                    level_id,
                    _LEVEL_LABELS[level_id],
                    "high",
                    f"explicit eligibility-test level: {cat}",
                )
        return (
            "unknown",
            _LEVEL_LABELS["unknown"],
            "review",
            f"eligibility-test level missing from category: {cat}",
        )
    professional_qualification = _moex_professional_qualification(cat, event)
    if professional_qualification is not None:
        level = professional_qualification.level_id
        confidence = "review" if level == "unknown" else "high"
        return (
            level,
            _LEVEL_LABELS[level],
            confidence,
            f"qualification-specific professional programme: {cat}",
        )
    legacy_level = re.match(r"^(高等|普通|普)_", cat)
    if legacy_level is not None:
        is_professional = "專門職業及技術人員" in event or "專技" in event
        is_civil = "公務人員" in event
        if is_professional and not is_civil:
            level_id = "professional-high" if legacy_level[1] == "高等" else "professional-ordinary"
            return (
                level_id,
                _LEVEL_LABELS[level_id],
                "high",
                f"native professional level in category: {cat}",
            )
        if is_civil and not is_professional and legacy_level[1] in {"普通", "普"}:
            return (
                "ordinary",
                _LEVEL_LABELS["ordinary"],
                "high",
                f"native civil ordinary level in category: {cat}",
            )
        return (
            "unknown",
            _LEVEL_LABELS["unknown"],
            "review",
            f"legacy level requires programme evidence: {cat}; {event}",
        )
    if _moex_transport_series(cat, event) is not None:
        for pattern, level_id in (
            (r"高員(?:三|3)級", "transport-senior-3"),
            (r"員級", "transport-employee"),
            (r"佐級", "transport-associate"),
        ):
            if re.search(pattern, cat.split("_", 1)[0]):
                return (
                    level_id,
                    _LEVEL_LABELS[level_id],
                    "high",
                    f"explicit transport recruitment grade: {cat}",
                )
        return (
            "unknown",
            _LEVEL_LABELS["unknown"],
            "review",
            f"transport recruitment category has no official grade: {cat}",
        )
    explicit_patterns = (
        (r"高等暨普通|高等、普通", "combined", "合併／制度待審核"),
        (r"員級\s*晉高員|員級高員|員晉高員", "promotion-employee-to-senior", "員級晉高員級"),
        (r"佐級\s*晉員|佐晉員", "promotion-associate-to-employee", "佐級晉員級"),
        (r"士級\s*晉佐|士晉佐", "promotion-worker-to-associate", "士級晉佐級"),
        (r"高員級|高級員", "promotion-senior-rank", "高員級"),
        (r"一級(?:漁航員|輪機員|船員)", "maritime-rank-1", "一級船員"),
        (r"二級(?:漁航員|輪機員|船員)", "maritime-rank-2", "二級船員"),
        (r"三級(?:漁航員|輪機員|船員)", "maritime-rank-3", "三級船員"),
        (r"警監", "police-commissioned", "警監"),
        (r"警正", "police-senior", "警正"),
        (r"警佐", "police-associate", "警佐"),
        (r"員級", "promotion-employee-rank", "員級"),
        (r"佐級", "promotion-associate-rank", "佐級"),
        (
            r"第[一二三四五六七八九十百]+職等|第\d+職等|[一二三四五六七八九十百]+職等|\d+職等",
            "promotion-official-rank",
            "升等／職等",
        ),
        (
            r"高考?\s*一級|高等一級|^[一1]級(?=考試|_|$)|[（(]一級[）)]"
            r"|一級考試|一等考試|一等_|^一等",
            "grade-1",
            "一等",
        ),
        (r"高等檢定", "qualification-high", "高等檢定"),
        (r"普通檢定", "qualification-ordinary", "普通檢定"),
        (
            r"高考?\s*二級|高等二級|^[二2]級(?=考試|_|$)|[（(]二級[）)]"
            r"|二級考試|二等考試|二等_",
            "grade-2",
            "二等",
        ),
        (
            r"高考?\s*三級|^高[三3]_|高3|^[三3]級(?=考試|_|$)|[（(]三級[）)]"
            r"|三級考試|三等考試|三等_|司法三等|3等|三等",
            "grade-3",
            "三等",
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
    civil_high = re.search(
        r"公務人員高等考試[一二三]級(?:考試)?(?:(?:暨|及|、)[一二三]級(?:考試)?)*", event
    )
    if civil_high is not None:
        native_grades = set(re.findall(r"([一二三])級", civil_high[0]))
        if re.search(r"[（(]高考[）)]", cat) and native_grades == {"三"}:
            return "grade-3", "三級", "high", f"native civil high category marker: {cat}"
        if len(native_grades) > 1 or "普通考試" in event:
            return (
                "unknown",
                _LEVEL_LABELS["unknown"],
                "review",
                f"combined civil high event has no category grade: {cat}",
            )
    if "中醫師檢定" in event and any(
        marker in event for marker in ("專技", "專門職業及技術人員", "檢覈")
    ):
        return (
            "unknown",
            _LEVEL_LABELS["unknown"],
            "review",
            f"cohosted eligibility and professional event needs category evidence: {cat}",
        )
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
        (r"公務人員初等考試", "elementary", "初等／初考"),
        (r"公務人員高等考試一級", "grade-1", "一等"),
        (r"公務人員高等考試二級", "grade-2", "二等"),
        (r"公務人員高等考試三級", "grade-3", "三等"),
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


def _taigi_form(text: str) -> str | None:
    # A/B/C are paper forms covering pairs of official proficiency levels.
    # Conflicting form markers cannot select one of those bands.
    matches = re.findall(
        r"(?<![A-Za-z0-9])([ABC])\s*卷|卷\s*([ABC])(?![A-Za-z0-9])", text, re.IGNORECASE
    )
    forms = {first.upper() or second.upper() for first, second in matches}
    return next(iter(forms)) if len(forms) == 1 else None


def _non_moex_level(
    provider_id: str, category: str, canonical_id: str, subject_name: str
) -> tuple[str, str, str, str]:
    text = normalize_text(f"{category} {subject_name}")
    if provider_id == "cpc_recruit" and "博士" in category:
        return (
            NOT_APPLICABLE,
            _LEVEL_LABELS[NOT_APPLICABLE],
            "high",
            "CPC doctoral recruitment: doctorate is an eligibility requirement, not an exam grade",
        )
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
        return (
            "unknown",
            _LEVEL_LABELS["unknown"],
            "review",
            "Hakka grade requires the download label",
        )
    if provider_id == "taigi_cert":
        form = _taigi_form(text)
        if form is not None:
            level_id = {"A": "cefr-a1-a2", "B": "cefr-b1-b2", "C": "cefr-c1-c2"}[form]
            return (
                level_id,
                _LEVEL_LABELS[level_id],
                "high",
                f"official Taiwanese proficiency band for {form}卷",
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
    if provider_id in {"gept_cert", "jlpt_cert", "wdasec_skill", "taigi_cert", "tqc_cert"}:
        # These programmes have official levels. An absent or unsupported
        # marker is missing evidence, not proof that the dimension is absent.
        return (
            "unknown",
            _LEVEL_LABELS["unknown"],
            "review",
            f"{provider_id}: missing official level evidence in record",
        )
    return (
        NOT_APPLICABLE,
        _LEVEL_LABELS[NOT_APPLICABLE],
        "medium",
        "provider policy declares no level dimension",
    )


_TRANSPORT_PROMOTION_SERIES = (
    ("鐵路", "promotion-railway"),
    ("公路", "promotion-highway"),
    ("港務", "promotion-port"),
    ("郵政", "promotion-postal"),
    ("電信", "promotion-telecommunications"),
    ("水運", "promotion-water-transport"),
    ("民航", "promotion-aviation"),
)


def _moex_promotion_series(category: str, exam_name: str, level_id: str) -> str | None:
    """Resolve promotion purpose before interpreting an occupation as recruitment.

    Each transport sector has its own paper table. A rank transition alone
    cannot identify the sector in a shared event. Civil and customs promotion
    likewise share rank labels, so an unmarked co-hosted category stays review.
    """
    if not re.search(r"升官等|升等|升資|晉升士級", f"{category} {exam_name}"):
        return None
    heading = category.rsplit("_", 1)[0] if "_" in category else category
    if "公務人員升官等" in heading or re.search(r"公務(?:簡|薦|委)任", heading):
        return "civil-promotion"
    if re.search(r"警察人員升官等|警察(?:人員)?警(?:監|正|佐)", heading):
        return "promotion-police"
    if level_id.startswith("police-"):
        return "promotion-police" if "警察" in exam_name else "moex-unknown"

    if level_id.startswith("promotion-") and level_id != "promotion-official-rank":
        # Older listings put the sector after the rank, alongside the track:
        # 員級晉高員級_電信人員業務類報務. Keep that source evidence too.
        explicit = [
            series
            for marker, series in _TRANSPORT_PROMOTION_SERIES
            if f"{marker}人員" in category
            or f"交通事業{marker}" in heading
            or re.search(rf"{marker}\s*(?:業務類|技術類|總局|士級|佐級|員級|高員級)", heading)
            or re.search(rf"(?:[-－]|[（(])(?:臺灣)?{marker}(?:總局|公司)?[）)]?$", category)
        ]
        if re.search(r"[（(](?:基隆|臺中|台中|高雄|花蓮)港[）)]$", category):
            explicit.append("promotion-port")
        explicit = list(dict.fromkeys(explicit))
        if explicit:
            return explicit[0] if len(explicit) == 1 else "moex-unknown"
        candidates = [
            series for marker, series in _TRANSPORT_PROMOTION_SERIES if marker in exam_name
        ]
        if candidates:
            return candidates[0] if len(candidates) == 1 else "moex-unknown"

    if "關務" in category:
        return "promotion-customs"
    civil_ranks = {"recommended-rank", "delegated-rank", "appointed-rank"}
    if level_id in civil_ranks:
        if "關務" in exam_name:
            if "公務人員" in exam_name:
                return "moex-unknown"
            return "promotion-customs"
        if "公務人員" in exam_name:
            return "civil-promotion"
    if "警察人員升官等" in exam_name and not any(
        marker in exam_name for marker in ("公務人員", "交通事業", "升資")
    ):
        return "promotion-police"
    if "警察人員升官等" in exam_name and any(
        marker in category for marker in ("警察人員", "消防人員", "海岸巡防人員")
    ):
        return "promotion-police"
    return None


def _moex_series(
    category: str, exam_name: str, level_id: str, canonical_id: str
) -> tuple[str, str, str, str]:
    cat = normalize_text(category)
    event = normalize_text(exam_name)
    inspector = _moex_ship_inspector_programme(cat, event)
    if inspector is not None:
        return "professional", "professional-exam", inspector, _SERIES_LABELS[inspector]
    qualification = _moex_maritime_qualification(cat, event)
    if qualification is not None:
        series = qualification.series_id
        return "professional", "professional-exam", series, _SERIES_LABELS[series]
    eligibility = _moex_eligibility_programme(cat, event)
    if eligibility is not None:
        return "qualification", "exam-eligibility", eligibility, _SERIES_LABELS[eligibility]
    professional = _moex_professional_qualification(cat, event)
    if professional is not None:
        series = professional.series_id
        return "professional", "professional-exam", series, _SERIES_LABELS[series]
    promotion_series = _moex_promotion_series(cat, event, level_id)
    if promotion_series is not None:
        return (
            "civil-service",
            "civil-service-exam" if promotion_series == "moex-unknown" else "civil-promotion",
            promotion_series,
            _SERIES_LABELS[promotion_series],
        )
    transport_series = _moex_transport_series(cat, event)
    if transport_series is not None:
        return (
            "civil-service",
            "civil-service-exam",
            transport_series,
            _SERIES_LABELS[transport_series],
        )
    category_first = (
        ("原住民族", "special-indigenous", "原住民族特考"),
        ("原住民", "special-indigenous", "原住民族特考"),
        ("身心障礙", "special-disability", "身心障礙特考"),
        ("身障", "special-disability", "身心障礙特考"),
        ("關務", "special-customs", "關務特考"),
        ("外交", "special-diplomatic", "外交／國際特考"),
        ("國際經濟商務", "special-diplomatic", "外交／國際特考"),
        # The specific programme must precede its substring. The two police
        # programmes have different eligibility rules and paper sets.
        ("一般警察", "special-general-police", _SERIES_LABELS["special-general-police"]),
        ("警察", "special-police", _SERIES_LABELS["special-police"]),
        ("國家安全情報", "special-national-security", _SERIES_LABELS["special-national-security"]),
        ("調查人員", "special-investigation", _SERIES_LABELS["special-investigation"]),
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
            if marker == "司法" and not re.search(
                r"司法官|司法事務官|司法(?:人員)?(?:特考|考試|[一二三四五甲乙丙丁]等|_)|^司法$", cat
            ):
                # 司法行政 is an occupation in civil high examinations.
                # Occupation wording cannot select the judicial programme.
                continue
            if marker == "警察" and "一般警察" in event:
                # Some historical rows put the programme between the grade
                # and occupation, such as 三等考試_警察特考_行政警察人員.
                heading = cat.rsplit("_", 1)[0] if "_" in cat else cat
                if not re.search(r"警察(?:人員)?(?:考試|特考|[二三四]等)|特種警察", heading):
                    # An occupation such as 行政警察 occurs in both programmes.
                    # It cannot stand in for an official programme heading.
                    continue
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
    if "一般警察" in event and "警察" in event.replace("一般警察", ""):
        # A shared source event is not enough to assign an unmarked category
        # to either police programme. Preserve it in event-specific review.
        return "civil-service", "civil-service-exam", "moex-unknown", _SERIES_LABELS["moex-unknown"]
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
        ("一般警察", "special-general-police", _SERIES_LABELS["special-general-police"]),
        ("特種考試警察", "special-police", _SERIES_LABELS["special-police"]),
        ("司法人員", "special-judicial", "司法特考"),
        ("調查局調查人員", "special-investigation", _SERIES_LABELS["special-investigation"]),
        ("國家安全情報", "special-national-security", _SERIES_LABELS["special-national-security"]),
        ("專門職業及技術人員", "professional-combined", "專技綜合／歷史制度"),
        ("檢覈", "professional-screening", "專技檢覈／檢覈筆試"),
        ("檢核", "professional-screening", "專技檢覈／檢覈筆試"),
        ("公務人員高等考試", "civil-high", _SERIES_LABELS["civil-high"]),
        ("特種考試", "special-other", "其他特種考試"),
    )
    for marker, series_id, label in event_rules:
        if marker in event:
            if series_id == "civil-high" and level_id in {"ordinary", "elementary"}:
                continue
            if series_id == "special-national-security" and any(
                other in event for other in ("任用資格", "軍法官", "政風人員")
            ):
                return (
                    "civil-service",
                    "civil-service-exam",
                    "moex-unknown",
                    _SERIES_LABELS["moex-unknown"],
                )
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
    if provider_id == "tqc_cert":
        return tqc_identity_catalog().programme
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
    category_code: str = "",
    source_material: SourceMaterial | None = None,
    source_checksum: str = "",
) -> ExamIdentity:
    provider_id = normalize_text(provider_id) or "unknown-provider"
    category = normalize_text(category_raw)
    exam_name = normalize_text(exam_name_raw)
    if provider_id == "moex":
        level_id, level_label, confidence, reason = _moex_level(category, exam_name, canonical_name)
        domain_id, family_id, series_id, series_label = _moex_series(
            category, exam_name, level_id, canonical_id
        )
        reviewed = resolve_moex_category_identity(
            source_exam_id, year_ad, category_code, category_raw, exam_name_raw
        )
        if reviewed is not None:
            if reviewed.series_id not in _SERIES_LABELS:
                raise ValueError(f"Invalid MOEX programme in {reviewed.fact_id}")
            if reviewed.level_id not in _LEVEL_LABELS:
                raise ValueError(f"Invalid MOEX native grade in {reviewed.fact_id}")
            if reviewed.series_id in {"eligibility-qualification", "chinese-medicine-eligibility"}:
                domain_id, family_id = "qualification", "exam-eligibility"
            elif reviewed.series_id in {
                "civil-high",
                "civil-ordinary",
            } or reviewed.series_id.startswith("special-"):
                domain_id, family_id = "civil-service", "civil-service-exam"
            elif reviewed.series_id in {
                "professional-high",
                "professional-ordinary",
                "professional-special",
                "professional-navigation-special",
                "professional-navigation-high",
                "professional-navigation-ordinary",
            }:
                domain_id, family_id = "professional", "professional-exam"
            else:
                raise ValueError(f"Unsupported MOEX reviewed programme in {reviewed.fact_id}")
            series_id, series_label = reviewed.series_id, _SERIES_LABELS[reviewed.series_id]
            level_id, level_label = reviewed.level_id, _LEVEL_LABELS[reviewed.level_id]
            confidence = "high"
            reason = f"reviewed official question headers: {reviewed.fact_id}"
            for review in reviewed.subject_reviews:
                if review.subject_code == subject_code:
                    confidence = "review"
                    reason = f"{review.reason}; {reason}"
        if series_id in {series for _marker, series in _TRANSPORT_PROMOTION_SERIES}:
            # Historical listings sometimes abbreviate the destination rank.
            # The transport-promotion rules and retained official headers use
            # the complete transition, not an additional examination grade.
            transition = {
                "promotion-senior-rank": "promotion-employee-to-senior",
                "promotion-employee-rank": "promotion-associate-to-employee",
                "promotion-associate-rank": "promotion-worker-to-associate",
            }.get(level_id)
            if transition is not None:
                level_id = transition
                level_label = _LEVEL_LABELS[level_id]
                reason = f"official transport promotion destination rank: {category}"
        if (
            series_id
            in {"special-general-police", "special-national-security", "special-investigation"}
            and level_id == NOT_APPLICABLE
        ):
            level_id = "unknown"
            level_label = _LEVEL_LABELS[level_id]
            confidence = "review"
            reason = f"{series_label}: missing official grade evidence in record"
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
        series_id,
    )
    variant_pairs = _variants(category, exam_name)
    if provider_id == "hakka_cert":
        level_id, level_label, reason = hakka_level(
            subject_name_raw, source_material, source_checksum, subject_code
        )
        dialect = hakka_dialect(subject_name_raw, category_code)
        conflict = hakka_conflict_reason(subject_name_raw, source_checksum, subject_code)
        if dialect is None or conflict:
            variant_pairs = (*variant_pairs, ("dialect-unknown", "腔調待確認"))
            confidence = "review"
            dialect_reason = (
                conflict or "Hakka source dialect is missing, unsupported or conflicting"
            )
            reason = f"{dialect_reason}; {reason}"
        else:
            variant_pairs = (*variant_pairs, (f"dialect-{dialect[0]}", dialect[1]))
            confidence = "high"
    if provider_id == "tqc_cert":
        sample = (
            resolve_tqc_sample_identity(subject_name_raw, source_checksum)
            if source_material is not None and source_material.kind == "sample"
            else None
        )
        if sample is None:
            track_id = _slug(subject_name_raw, prefix="tqc")
            track_label = normalize_text(subject_name_raw) or "待審核科目"
            reason = "TQC sample title or checksum lacks reviewed native grade/content evidence"
            if (
                source_material is not None
                and source_material.kind == "sample"
                and re.fullmatch(r"[0-9a-f]{64}", source_checksum)
            ):
                variant_pairs = (
                    *variant_pairs,
                    (f"unreviewed-payload-{source_checksum[:16]}", "待審核內容版本"),
                )
        else:
            track_id, track_label = sample.track_id, sample.track_label
            level_id, level_label = sample.level_id, sample.level_label
            confidence = "high"
            reason = f"reviewed TQC native sample: {sample.fact_id}; {sample.reason}"
            variant_pairs = (*variant_pairs, *sample.variants)
    if provider_id == "moex":
        qualification = _moex_maritime_qualification(category, exam_name)
        if qualification is not None:
            variant_pairs = (*variant_pairs, *qualification.variants)
    if provider_id == "moex" and series_id == "civil-high" and year_ad < 1996:
        # The 1996 reform replaced a two-level civil high system with three
        # levels. Equal native numerals across that change are not equal exams.
        variant_pairs = (*variant_pairs, ("civil-high-pre-1996", "85年改制前"))
    if provider_id == "taigi_cert":
        form = _taigi_form(normalize_text(f"{category} {subject_name_raw}"))
        if form is not None:
            variant_pairs = (*variant_pairs, (f"paper-form-{form.lower()}", f"{form}卷"))
    if source_material is not None and source_material.kind != "administered":
        variant_pairs = (
            *variant_pairs,
            (
                f"material-{source_material.kind.replace('_', '-')}",
                material_label(source_material.kind),
            ),
        )
    variants = tuple(variant_id for variant_id, _label in variant_pairs)
    stage_id = _stage_id(category, exam_name)
    if provider_id == "cpc_recruit" and track_id == "cpc-doctoral" and stage_id == NOT_APPLICABLE:
        # The static doctoral archive supplies the initial written test. Its
        # official brochures distinguish this from the later interview and
        # research-presentation stage.
        stage_id = "stage-1"
    if provider_id == "moex":
        professional = _moex_professional_qualification(category, exam_name)
        if professional is not None:
            stage_id = professional.stage_id
    if not source_exam_id:
        confidence = "review"
        reason = f"missing source exam event id; {reason}"
    if track_id.endswith("unknown"):
        confidence = "review"
        reason = f"track cannot be resolved; {reason}"
    if level_id == "unknown":
        confidence = "review"
    if source_material is not None:
        reason = (
            f"source material: {source_material.kind}; "
            f"date basis: {source_material.date.basis}; {reason}"
        )
        if source_material.needs_review:
            confidence = "review"
            reason = f"{source_material.review_reason}; {reason}"
    if provider_id == "moex" and series_id == "moex-unknown":
        confidence = "review"
        reason = f"official programme cannot be resolved from category and event; {reason}"
    parts = [provider_id, series_id, level_id, track_id, *variants]
    if stage_id != NOT_APPLICABLE:
        parts.append(stage_id)
    if confidence == "review" and source_exam_id:
        parts.append(f"event-{_ascii_slug(source_exam_id, prefix='event')}")
    bundle_id = "-".join(_ascii_slug(part, prefix="concept") for part in parts if part)
    if provider_id == "moex":
        track_display = _display(track_label, canonical_name)
        level_label = _moex_native_level_label(series_id, level_id, level_label)
        name_parts = [series_label]
        if level_label != series_label:
            name_parts.append(level_label)
        name_parts.append(track_display)
        bundle_name = "｜".join(name_parts)
        # The track text often already carries the wording (e.g. a region), and
        # two patterns can match one phrase; keep only the longest wording.
        candidates = [label for _variant_id, label in variant_pairs if label not in bundle_name]
        labels = [
            label
            for label in dict.fromkeys(candidates)
            if not any(label != other and label in other for other in candidates)
        ]
        if labels:
            bundle_name += f"｜{'、'.join(labels)}"
        if stage_id != NOT_APPLICABLE:
            stage_label = _STAGE_LABELS[stage_id]
            if professional is not None and professional.occupation == "大地工程技師":
                stage_label = {"stage-1": "第一階段", "stage-2": "第二階段"}[stage_id]
            bundle_name += f"｜{stage_label}"
    else:
        bundle_name = _display(canonical_name, track_label)
        if provider_id in {"tqc_cert", "hakka_cert"}:
            bundle_name = series_label
        if provider_id == "cpc_recruit" and track_id == "cpc-doctoral":
            # Keep the old canonical compatibility key, while the structured
            # identity and title distinguish this specific hiring programme.
            bundle_name = track_label
            stage_label = "初試（筆試）" if stage_id == "stage-1" else _STAGE_LABELS[stage_id]
            bundle_name += f"｜{stage_label}"
        if level_id not in {NOT_APPLICABLE, "unknown"}:
            bundle_name = f"{bundle_name}｜{level_label}"
        # These providers publish separate subject/occupation tracks under a
        # shared legacy canonical name. Surface the identity discriminator.
        if provider_id in TRACK_TITLED_PROVIDERS:
            label = track_label
            if provider_id in {"ceec_gsat", "ceec_ast"}:
                label = re.sub(r"^(?:分科測驗|學科能力測驗)\s*[-－]\s*", "", label)
            if provider_id == "wdasec_skill":
                label = re.sub(r"\s*(?:甲級|乙級|丙級|單一級)\s*$", "", label)
            if label and label != bundle_name:
                bundle_name = f"{bundle_name}｜{label}"
        if provider_id == "taigi_cert":
            for _variant, form_label in variant_pairs:
                if form_label not in bundle_name:
                    bundle_name += f"｜{form_label}"
        if provider_id in {"tqc_cert", "hakka_cert"}:
            for _variant_id, label in variant_pairs:
                if label not in bundle_name:
                    bundle_name += f"｜{label}"
    if source_material is not None and source_material.kind != "administered":
        label = material_label(source_material.kind)
        if label not in bundle_name:
            bundle_name += f"｜{label}"
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
    category_code: str,
    subject_code: str = "",
) -> ExamIdentity:
    # Most MOEX papers share an event/category identity. Only explicitly
    # reviewed conflicting subjects add their native locator to this cache.
    return _classify_paper_uncached(
        provider_id="moex",
        source_exam_id=source_exam_id,
        year_ad=year_ad,
        category_raw=category_raw,
        exam_name_raw=exam_name_raw,
        canonical_id=canonical_id,
        canonical_name=canonical_name,
        category_code=category_code,
        subject_code=subject_code,
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
    category_code: str = "",
    source_material: SourceMaterial | None = None,
    source_checksum: str = "",
) -> ExamIdentity:
    if provider_id == "moex" and source_material is None:
        reviewed = resolve_moex_category_identity(
            source_exam_id, year_ad, category_code, category_raw, exam_name_raw
        )
        return _classify_moex_record(
            source_exam_id,
            year_ad,
            category_raw,
            exam_name_raw,
            canonical_id,
            canonical_name,
            category_code,
            subject_code if reviewed is not None and reviewed.subject_reviews else "",
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
        category_code=category_code,
        source_material=source_material,
        source_checksum=source_checksum,
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
        category_code=getattr(paper, "category_code", ""),
        source_material=getattr(paper, "source_material", None),
        source_checksum=getattr(paper, "checksum", ""),
    )


def public_facets(identity: ExamIdentity) -> dict[str, str]:
    if identity.domain_id == "civil-service":
        exam_class = "公職考試"
        exam_subclass = "公職／公務人員"
    elif identity.domain_id == "professional":
        exam_class = "專技人員考試"
        exam_subclass = identity.series_label
    elif identity.domain_id == "qualification":
        exam_class = "應考資格檢定"
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
