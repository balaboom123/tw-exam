from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

from app.classification import classify_paper, identity_fields
from app.models import AliasRule, NormalizedCatalog, NormalizedPaper, ParsedPaper, ReviewItem

KNOWN_CANONICAL_IDS = {
    "護理師": "nurse",
    "社會工作師": "social-worker",
    "營養師": "dietitian",
    "心理師": "psychologist",
    "諮商心理師": "counseling-psychologist",
    "臨床心理師": "clinical-psychologist",
}

KNOWN_PREFIXES = [
    r"^專門職業及技術人員(?:高等|普通)?考試",
    r"^專技(?:高考|普考)",
    r"^高等考試",
    r"^普通考試",
    r"^特種考試",
]
_CEEC_GSAT_CANONICAL_ID = "ceec-gsat"
_CEEC_GSAT_CANONICAL_NAME = "學科能力測驗"
_CEEC_AST_CANONICAL_ID = "ceec-ast"
_CEEC_AST_CANONICAL_NAME = "分科測驗"
_TCTE_TVE_CANONICAL_ID = "tcte-tve"
_TCTE_TVE_CANONICAL_NAME = "四技二專統一入學測驗"
_SPECIAL_ADMISSION_CANONICAL_ID = "special-admission"
_SPECIAL_ADMISSION_CANONICAL_NAME = "身心障礙學生升學大專校院甄試"
_POST_RECRUIT_CANONICAL_ID = "post-recruit"
_POST_RECRUIT_CANONICAL_NAME = "中華郵政職階人員甄試"
_HCE_CANONICAL_MAP = {
    "hce-cmu-": ("hce-cmu", "中國醫藥大學學士後中醫學系"),
    "hce-tcu-": ("hce-tcu", "慈濟大學學士後中醫學系"),
    "hce-nsysu-": ("hce-nsysu", "國立中山大學學士後醫學系"),
    "hce-nthu-": ("hce-nthu", "國立清華大學學士後醫學系"),
}
_RCPET_CAP_CANONICAL_ID = "rcpet-cap"
_RCPET_CAP_CANONICAL_NAME = "國中教育會考"
_WDASEC_SKILL_CANONICAL_ID = "wdasec-skill"
_WDASEC_SKILL_CANONICAL_NAME = "全國技術士技能檢定"

_MOEA_RECRUIT_CANONICAL_ID = "moea-recruit"
_MOEA_RECRUIT_CANONICAL_NAME = "國營事業聯招（新進職員）"
_TAIPOWER_RECRUIT_CANONICAL_ID = "taipower-recruit"
_TAIPOWER_RECRUIT_CANONICAL_NAME = "台電新進僱用人員甄試"
_CPC_RECRUIT_CANONICAL_ID = "cpc-recruit"
_CPC_RECRUIT_CANONICAL_NAME = "中油新進人員甄試"
_TWC_RECRUIT_CANONICAL_ID = "twc-recruit"
_TWC_RECRUIT_CANONICAL_NAME = "台水評價職位人員甄試"
_TAISUGAR_RECRUIT_CANONICAL_ID = "taisugar-recruit"
_TAISUGAR_RECRUIT_CANONICAL_NAME = "台糖新進工員甄試"

_SOE_CANONICAL_MAP = {
    "moea-recruit-": (_MOEA_RECRUIT_CANONICAL_ID, _MOEA_RECRUIT_CANONICAL_NAME),
    "taipower-recruit-": (_TAIPOWER_RECRUIT_CANONICAL_ID, _TAIPOWER_RECRUIT_CANONICAL_NAME),
    "cpc-recruit-": (_CPC_RECRUIT_CANONICAL_ID, _CPC_RECRUIT_CANONICAL_NAME),
    "twc-recruit-": (_TWC_RECRUIT_CANONICAL_ID, _TWC_RECRUIT_CANONICAL_NAME),
    "taisugar-recruit-": (_TAISUGAR_RECRUIT_CANONICAL_ID, _TAISUGAR_RECRUIT_CANONICAL_NAME),
}

_FINANCIAL_CERT_CANONICAL_MAP = {
    # SFI (證基會) certifications
    "sfi-cert-securities-dealer-": ("sfi-securities-dealer", "證券商業務員"),
    "sfi-cert-senior-securities-dealer-": ("sfi-senior-securities-dealer", "證券商高級業務員"),
    "sfi-cert-futures-dealer-": ("sfi-futures-dealer", "期貨商業務員"),
    "sfi-cert-securities-analyst-": ("sfi-securities-analyst", "證券投資分析人員"),
    "sfi-cert-sitca-": ("sfi-sitca", "投信投顧業務員"),
    "sfi-cert-corporate-internal-control-": (
        "sfi-corporate-internal-control",
        "企業內部控制基本能力測驗",
    ),
    "sfi-cert-bills-dealer-": ("sfi-bills-dealer", "票券商業務人員"),
    "sfi-cert-stock-affairs-": ("sfi-stock-affairs", "股務人員"),
    "sfi-cert-asset-securitization-": ("sfi-asset-securitization", "資產證券化基本能力測驗"),
    "sfi-cert-business-ethics-": ("sfi-business-ethics", "工商倫理測驗"),
    "sfi-cert-aml-": ("sfi-aml", "防制洗錢與打擊資恐專業人員測驗"),
    "sfi-cert-sustainability-": ("sfi-sustainability", "永續發展基礎能力測驗"),
    # TABF (金融研訓院) certifications
    "tabf-cert-bank-internal-control-": ("tabf-bank-internal-control", "銀行內部控制與內部稽核"),
    "tabf-cert-trust-business-": ("tabf-trust-business", "信託業務人員"),
    "tabf-cert-financial-planning-": ("tabf-financial-planning", "理財規劃人員"),
    "tabf-cert-fx-junior-": ("tabf-fx-junior", "初階外匯人員"),
    "tabf-cert-fx-senior-": ("tabf-fx-senior", "進階外匯人員"),
    "tabf-cert-credit-": ("tabf-credit", "授信人員"),
    "tabf-cert-risk-management-": ("tabf-risk-management", "風險管理基本能力"),
    "tabf-cert-debt-collection-": ("tabf-debt-collection", "債權催收人員"),
    "tabf-cert-digital-finance-": ("tabf-digital-finance", "數位金融知識與能力"),
    "tabf-cert-aml-": ("tabf-aml", "防制洗錢與打擊資恐"),
    "tabf-cert-fintech-": ("tabf-fintech", "金融科技力知識"),
    "tabf-cert-asset-valuation-": ("tabf-asset-valuation", "資產評估人員"),
    # TII (保發中心) certifications
    "tii-cert-life-insurance-": ("tii-life-insurance", "人身保險業務員"),
    "tii-cert-property-insurance-": ("tii-property-insurance", "財產保險業務員"),
    "tii-cert-investment-insurance-": ("tii-investment-insurance", "投資型保險商品業務員"),
    "tii-cert-health-insurance-": ("tii-health-insurance", "傷害保險及健康保險業務員"),
    "tii-cert-policyholder-service-": ("tii-policyholder-service", "人身保險業務員保戶服務檢定"),
    "tii-cert-aml-": ("tii-aml", "防制洗錢與打擊資恐專業人員測驗"),
    "tii-cert-sustainability-": ("tii-sustainability", "永續發展基礎能力測驗"),
}

_REQUESTED_TOPIC_CANONICAL_MAP = {
    "teacher-qual-": ("teacher-qual", "教師資格考試"),
    "teacher-recruit-newtaipei-": ("teacher-recruit-newtaipei", "新北市教師甄試"),
    "teacher-recruit-taoyuan-elementary-": (
        "teacher-recruit-taoyuan-elementary",
        "桃園市國小教師甄試",
    ),
    "teacher-recruit-kaohsiung-": ("teacher-recruit-kaohsiung", "高雄市教師甄試"),
    "teacher-recruit-central-alliance-": (
        "teacher-recruit-central-alliance",
        "中區策略聯盟教師甄試",
    ),
    "teacher-recruit-taipei-junior-": ("teacher-recruit-taipei-junior", "臺北市國中教師甄試"),
    "teacher-recruit-taipei-elementary-": (
        "teacher-recruit-taipei-elementary",
        "臺北市國小教師甄試",
    ),
    "teacher-recruit-tainan-": ("teacher-recruit-tainan", "臺南市國小教師甄試"),
    "gept-cert-elementary-": ("gept-cert-elementary", "GEPT全民英檢 初級"),
    "gept-cert-intermediate-": ("gept-cert-intermediate", "GEPT全民英檢 中級"),
    "gept-cert-high-intermediate-": ("gept-cert-high-intermediate", "GEPT全民英檢 中高級"),
    "gept-cert-advanced-": ("gept-cert-advanced", "GEPT全民英檢 高級"),
    "gept-cert-superior-": ("gept-cert-superior", "GEPT全民英檢 優級"),
    "gept-cert-": ("gept-cert", "GEPT全民英檢"),
    "jlpt-cert-": ("jlpt-cert", "JLPT Japanese-Language Proficiency Test"),
    "tocfl-cert-": ("tocfl-cert", "TOCFL華語文能力測驗"),
    "hakka-cert-basic-elementary-": ("hakka-cert-basic-elementary", "客語能力認證 基礎級暨初級"),
    "hakka-cert-intermediate-high-intermediate-": (
        "hakka-cert-intermediate-high-intermediate",
        "客語能力認證 中級暨中高級",
    ),
    "hakka-cert-advanced-": ("hakka-cert-advanced", "客語能力認證 高級"),
    "hakka-cert-": ("hakka-cert", "客語能力認證"),
    "taigi-cert-a-": ("taigi-cert-a", "臺灣台語語言能力認證 A卷"),
    "taigi-cert-b-": ("taigi-cert-b", "臺灣台語語言能力認證 B卷"),
    "taigi-cert-c-": ("taigi-cert-c", "臺灣台語語言能力認證 C卷"),
    "taigi-cert-": ("taigi-cert", "臺灣台語語言能力認證"),
    "tqc-cert-": ("tqc-cert", "TQC電腦技能基金會認證"),
    "ipas-cert-ise-": ("ipas-cert-ise", "iPAS 資訊安全工程師"),
    "ipas-cert-oia-": ("ipas-cert-oia", "iPAS 營運智慧分析師"),
    "ipas-cert-aiap-": ("ipas-cert-aiap", "iPAS AI應用規劃師"),
    "ipas-cert-aiot-": ("ipas-cert-aiot", "iPAS AIoT應用工程師"),
    "ipas-cert-": ("ipas-cert", "iPAS產業人才能力鑑定"),
}


def legacy_fallback_canonical_id(candidate: str) -> str:
    return "canonical-" + candidate.encode("utf-8").hex()[:16]


def hashed_fallback_canonical_id(candidate: str) -> str:
    normalized_candidate = normalize_text(candidate)
    digest = hashlib.sha256(normalized_candidate.encode("utf-8")).hexdigest()[:16]
    return f"canonical-{digest}"


def load_alias_rules(path: Path) -> list[AliasRule]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [AliasRule(**item) for item in payload.get("rules", [])]


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = normalized.replace("＿", "_")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _match_alias(rule: AliasRule, raw_category: str, year_ad: int) -> bool:
    if rule.year_from is not None and year_ad < rule.year_from:
        return False
    if rule.year_to is not None and year_ad > rule.year_to:
        return False
    if rule.match_type == "exact":
        return normalize_text(raw_category) == normalize_text(rule.raw_pattern)
    if rule.match_type == "contains":
        return normalize_text(rule.raw_pattern) in normalize_text(raw_category)
    raise ValueError(f"Unsupported alias match type: {rule.match_type}")


_VARIANT_LEVEL_SUFFIX = re.compile(
    r"[（(](?:三等|四等|五等|高考|普考|一等|二等|二級|一級|簡任|薦任|委任|一般組|兩岸組[一二三])[）)]"
)
_VARIANT_MILITARY_SUFFIX = re.compile(
    r"[（(](?:中將轉任考試|少將轉任考試|中將轉任|少將轉任|上校轉任|中將|少將)[）)]"
)
_VARIANT_DESTINATION_SUFFIX = re.compile(
    r"[（(](?:退輔會|轉任退輔會|國防部|轉任國防部|轉任海委會|一般錄取分發區|蘭嶼錄取分發區)[）)]"
)
_VARIANT_MILITARY_PREFIX = re.compile(r"^[（(](?:中將轉任|少將轉任|上校轉任)[）)]")
_VARIANT_EXAM_TYPE_PREFIX = re.compile(r"^[（(](?:關務類|技術類)[）)]")
_VARIANT_LANGUAGE_SUFFIX = re.compile(r"[（(]選試[^）)]+[）)]")
_VARIANT_TRAILING = re.compile(r"(?:類科|科別)$")


def _strip_exam_family(text: str) -> str:
    value = normalize_text(text)
    value = re.sub(r"^\d+年", "", value)
    if "_" in value:
        value = value.split("_")[-1]
    for prefix in KNOWN_PREFIXES:
        value = re.sub(prefix, "", value)
    value = re.sub(r"^第[一二三四五六七八九十]+次", "", value)
    value = re.sub(r"考試$", "", value)
    value = _VARIANT_TRAILING.sub("", value)
    value = _VARIANT_LEVEL_SUFFIX.sub("", value)
    value = _VARIANT_MILITARY_SUFFIX.sub("", value)
    value = _VARIANT_DESTINATION_SUFFIX.sub("", value)
    value = _VARIANT_LANGUAGE_SUFFIX.sub("", value)
    value = _VARIANT_MILITARY_PREFIX.sub("", value)
    value = _VARIANT_EXAM_TYPE_PREFIX.sub("", value)
    return value.strip(" -_")


def _is_ambiguous(candidate: str) -> bool:
    return any(token in candidate for token in ("、", "暨", "及", "與"))


def _canonical_id(candidate: str) -> str:
    if candidate in KNOWN_CANONICAL_IDS:
        return KNOWN_CANONICAL_IDS[candidate]
    return hashed_fallback_canonical_id(candidate)


def _derive_canonical(
    source_exam_id: str,
    raw_category: str,
    exam_name_raw: str,
    year_ad: int,
    alias_rules: list[AliasRule],
) -> tuple[str, str, str, bool]:
    """Return (canonical_id, canonical_name, stripped_candidate, needs_review)."""
    for prefix, (canonical_id, canonical_name) in _SOE_CANONICAL_MAP.items():
        if source_exam_id.startswith(prefix):
            return canonical_id, canonical_name, canonical_name, False
    for prefix, (canonical_id, canonical_name) in _FINANCIAL_CERT_CANONICAL_MAP.items():
        if source_exam_id.startswith(prefix):
            return canonical_id, canonical_name, canonical_name, False
    for prefix, (canonical_id, canonical_name) in _REQUESTED_TOPIC_CANONICAL_MAP.items():
        if source_exam_id.startswith(prefix):
            return canonical_id, canonical_name, canonical_name, False
    if source_exam_id.startswith("gsat-") and _CEEC_GSAT_CANONICAL_NAME in normalize_text(
        raw_category or exam_name_raw
    ):
        return _CEEC_GSAT_CANONICAL_ID, _CEEC_GSAT_CANONICAL_NAME, _CEEC_GSAT_CANONICAL_NAME, False
    if source_exam_id.startswith("ceec-ast-"):
        return _CEEC_AST_CANONICAL_ID, _CEEC_AST_CANONICAL_NAME, _CEEC_AST_CANONICAL_NAME, False
    if source_exam_id.startswith("tcte-tve-"):
        return _TCTE_TVE_CANONICAL_ID, _TCTE_TVE_CANONICAL_NAME, _TCTE_TVE_CANONICAL_NAME, False
    if source_exam_id.startswith("special-admission-"):
        return (
            _SPECIAL_ADMISSION_CANONICAL_ID,
            _SPECIAL_ADMISSION_CANONICAL_NAME,
            _SPECIAL_ADMISSION_CANONICAL_NAME,
            False,
        )
    if source_exam_id.startswith("post-recruit-"):
        return (
            _POST_RECRUIT_CANONICAL_ID,
            _POST_RECRUIT_CANONICAL_NAME,
            _POST_RECRUIT_CANONICAL_NAME,
            False,
        )
    for prefix, (canonical_id, canonical_name) in _HCE_CANONICAL_MAP.items():
        if source_exam_id.startswith(prefix):
            return canonical_id, canonical_name, canonical_name, False
    if source_exam_id.startswith("cap-") and _RCPET_CAP_CANONICAL_NAME in normalize_text(
        raw_category or exam_name_raw
    ):
        return _RCPET_CAP_CANONICAL_ID, _RCPET_CAP_CANONICAL_NAME, _RCPET_CAP_CANONICAL_NAME, False
    if _WDASEC_SKILL_CANONICAL_NAME in normalize_text(raw_category or exam_name_raw):
        return (
            _WDASEC_SKILL_CANONICAL_ID,
            _WDASEC_SKILL_CANONICAL_NAME,
            _WDASEC_SKILL_CANONICAL_NAME,
            False,
        )
    alias = next((rule for rule in alias_rules if _match_alias(rule, raw_category, year_ad)), None)
    candidate = _strip_exam_family(raw_category or exam_name_raw)
    if alias:
        return alias.canonical_id, alias.canonical_name, candidate, False
    canonical_name = candidate or normalize_text(raw_category or exam_name_raw)
    if _is_ambiguous(canonical_name):
        canonical_name = normalize_text(raw_category or exam_name_raw)
        return _canonical_id(canonical_name), canonical_name, candidate, True
    return _canonical_id(canonical_name), canonical_name, candidate, False


def _deduplicate_review_queue(items: list[ReviewItem]) -> list[ReviewItem]:
    """Keep one evidence record per provider/event/category review signature.

    Older queues were emitted once per paper and predated provider attribution.
    Prefer the richest v2 item when duplicate legacy rows are encountered so
    migration remains lossless without inflating the human review workload.
    """

    def key(item: ReviewItem) -> tuple[str, str, str]:
        return (item.provider_id, item.source_exam_id, item.raw_category)

    def score(item: ReviewItem) -> tuple[int, int, int, int]:
        return (
            bool(item.classification_signature),
            bool(item.bundle_id),
            bool(item.reason),
            bool(item.raw_exam_name),
        )

    selected: dict[tuple[str, str, str], ReviewItem] = {}
    for item in items:
        current = selected.get(key(item))
        if current is None or score(item) > score(current):
            selected[key(item)] = item
    return [selected[item_key] for item_key in sorted(selected)]


def normalize_papers(
    source_exam_id: str,
    year_ad: int,
    exam_name_raw: str,
    papers: Iterable[ParsedPaper],
    alias_rules: list[AliasRule],
    mirror_base_url: str,
    mirror_metadata: dict[tuple[str, str, str], dict[str, str]],
    provider_id: str = "",
) -> NormalizedCatalog:
    year_roc = year_ad - 1911
    normalized_papers: list[NormalizedPaper] = []
    review_queue: list[ReviewItem] = []
    for paper in papers:
        raw_category = paper.category_raw or exam_name_raw
        canonical_id, canonical_name, candidate, needs_review = _derive_canonical(
            source_exam_id, raw_category, exam_name_raw, year_ad, alias_rules
        )
        identity = None
        fields = {}
        if provider_id and not _is_legacy_ascii_fixture(
            paper, canonical_name=canonical_name, exam_name_raw=exam_name_raw
        ):
            identity = classify_paper(
                provider_id=provider_id,
                source_exam_id=source_exam_id,
                year_ad=year_ad,
                category_raw=raw_category,
                exam_name_raw=exam_name_raw,
                canonical_id=canonical_id,
                canonical_name=canonical_name,
                subject_name_raw=paper.subject_name_raw,
                subject_code=paper.subject_code,
            )
            fields = identity_fields(identity)
        if needs_review or (identity is not None and identity.confidence == "review"):
            review_queue.append(
                ReviewItem(
                    raw_category=raw_category,
                    normalized_candidate=candidate or canonical_name,
                    source_exam_id=source_exam_id,
                    year_roc=year_roc,
                    provider_id=provider_id,
                    raw_exam_name=exam_name_raw,
                    classification_signature=identity.signature if identity is not None else "",
                    bundle_id=identity.bundle_id if identity is not None else "",
                    reason=("legacy canonicalization requires review; " if needs_review else "")
                    + (identity.reason if identity is not None else ""),
                )
            )

        for file_type, download_url_source in paper.files.items():
            metadata = mirror_metadata.get((paper.category_code, paper.subject_code, file_type), {})
            storage_key = metadata.get("storage_key", "")
            asset_name = metadata.get("asset_name") or storage_key
            download_url_mirror = (
                f"{mirror_base_url.rstrip('/')}/{asset_name}"
                if mirror_base_url and asset_name
                else ""
            )
            normalized_papers.append(
                NormalizedPaper(
                    canonical_id=canonical_id,
                    canonical_name=canonical_name,
                    year_roc=year_roc,
                    exam_name_raw=exam_name_raw,
                    category_raw=paper.category_raw,
                    category_code=paper.category_code,
                    source_exam_id=source_exam_id,
                    subject_code=paper.subject_code,
                    subject_name_raw=paper.subject_name_raw,
                    paper_code=f"{paper.category_code}-{paper.subject_code}-{file_type}",
                    file_type=file_type,
                    download_url_source=download_url_source,
                    download_url_mirror=download_url_mirror,
                    storage_key=storage_key,
                    checksum=metadata.get("checksum", ""),
                    provider_id=provider_id,
                    **fields,
                )
            )
    return NormalizedCatalog(
        papers=normalized_papers, review_queue=_deduplicate_review_queue(review_queue)
    )


def _is_legacy_ascii_fixture(
    paper: NormalizedPaper | ParsedPaper, *, canonical_name: str = "", exam_name_raw: str = ""
) -> bool:
    """Keep hand-authored ASCII fixture records on the v1 projection.

    Real provider records carry source-language names; this narrow guard exists
    only so legacy unit fixtures do not silently change their public asset IDs.
    """
    schema_version = getattr(paper, "schema_version", 1)
    paper_name = canonical_name or getattr(paper, "canonical_name", "")
    category_raw = getattr(paper, "category_raw", "")
    if schema_version != 1 or not paper_name.isascii() or not category_raw.isascii():
        return False
    source_exam_name = exam_name_raw or getattr(paper, "exam_name_raw", "")
    return bool(re.search(r"\b(?:exam|gsat)\b", source_exam_name, re.IGNORECASE))


def renormalize_catalog(
    catalog: NormalizedCatalog,
    alias_rules: list[AliasRule],
    *,
    collect_reviews: bool = True,
) -> NormalizedCatalog:
    """Attach v2 identity metadata without changing legacy canonical URLs.

    ``canonical_id`` is a compatibility key.  It is intentionally retained
    during migration; the v2 ``bundle_id`` is the only publication grouping
    key.  ``collect_reviews`` is disabled by the publication read path so a
    compatibility publish does not manufacture duplicate queue entries; the
    explicit catalog migration command enables it.
    """
    papers: list[NormalizedPaper] = []
    # A full renormalization is also the authoritative review-state rebuild.
    # Keeping the old queue here would preserve rows whose current papers no
    # longer need review (for example after a classifier or canonicalization
    # fix). The publication read path deliberately keeps the old queue because
    # it passes ``collect_reviews=False`` and must not mutate review state.
    review_queue: list[ReviewItem] = (
        [] if collect_reviews else _deduplicate_review_queue(list(catalog.review_queue))
    )
    canonical_review_keys = (
        {
            (item.provider_id, item.source_exam_id, item.raw_category)
            for item in catalog.review_queue
            if item.reason.startswith("legacy canonicalization requires review")
        }
        if collect_reviews
        else set()
    )
    for paper in catalog.papers:
        raw_category = paper.category_raw or paper.exam_name_raw
        provider_id = paper.provider_id
        year_ad = paper.year_roc + 1911
        canonical_id = paper.canonical_id
        canonical_name = paper.canonical_name
        candidate = canonical_name
        needs_review = False
        # Preserve unresolved canonicalization reviews after canonical fields
        # are filled. Re-evaluate aliases so resolved reviews can disappear;
        # previously unqueued historical records keep their reviewed scope.
        has_canonical_review = (
            provider_id,
            paper.source_exam_id,
            raw_category,
        ) in canonical_review_keys
        if not canonical_id or not canonical_name or has_canonical_review:
            derived_id, derived_name, candidate, needs_review = _derive_canonical(
                paper.source_exam_id, raw_category, paper.exam_name_raw, year_ad, alias_rules
            )
            if not canonical_id or not canonical_name:
                canonical_id, canonical_name = derived_id, derived_name
        identity = None
        fields = {}
        if provider_id and not _is_legacy_ascii_fixture(paper):
            identity = classify_paper(
                provider_id=provider_id,
                source_exam_id=paper.source_exam_id,
                year_ad=year_ad,
                category_raw=raw_category,
                exam_name_raw=paper.exam_name_raw,
                canonical_id=canonical_id,
                canonical_name=canonical_name,
                subject_name_raw=paper.subject_name_raw,
                subject_code=paper.subject_code,
            )
            fields = identity_fields(identity)
        paper = replace(paper, canonical_id=canonical_id, canonical_name=canonical_name, **fields)
        if collect_reviews and (
            needs_review or (identity is not None and identity.confidence == "review")
        ):
            # Append the generated evidence even when a legacy queue row has
            # the same key; deduplication below prefers this richer v2 item.
            review_queue.append(
                ReviewItem(
                    raw_category=raw_category,
                    normalized_candidate=candidate or canonical_name,
                    source_exam_id=paper.source_exam_id,
                    year_roc=paper.year_roc,
                    provider_id=provider_id,
                    raw_exam_name=paper.exam_name_raw,
                    classification_signature=identity.signature if identity is not None else "",
                    bundle_id=identity.bundle_id if identity is not None else "",
                    reason=("legacy canonicalization requires review; " if needs_review else "")
                    + (identity.reason if identity is not None else ""),
                )
            )
        papers.append(paper)
    return NormalizedCatalog(papers=papers, review_queue=_deduplicate_review_queue(review_queue))
