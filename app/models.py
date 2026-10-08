from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.source_material import MaterialSummary, SourceMaterial, material_summary, source_material


@dataclass
class ExamOption:
    code: str
    year_ad: int
    year_roc: int
    label: str


@dataclass
class SearchPageData:
    available_years: list[int]
    exams: list[ExamOption]


@dataclass
class ExamAttachment:
    title: str
    file_type: str
    download_url_source: str
    storage_key: str = ""
    asset_name: str = ""
    checksum: str = ""
    download_url_mirror: str = ""
    source_material: SourceMaterial | None = None

    def __post_init__(self) -> None:
        self.source_material = source_material(self.source_material)


@dataclass
class ParsedPaper:
    category_raw: str
    category_code: str
    subject_code: str
    subject_name_raw: str
    files: dict[str, str]
    mirror_files: dict[str, dict[str, str]] = field(default_factory=dict)
    source_material: SourceMaterial | None = None

    def __post_init__(self) -> None:
        self.source_material = source_material(self.source_material)


@dataclass
class SourceExamPage:
    source_exam_id: str
    year_ad: int
    year_roc: int
    exam_name_raw: str
    attachments: list[ExamAttachment]
    papers: list[ParsedPaper]
    provider_id: str = ""
    source_material: SourceMaterial | None = None

    def __post_init__(self) -> None:
        self.source_material = source_material(self.source_material)


@dataclass
class AliasRule:
    match_type: str
    raw_pattern: str
    canonical_id: str
    canonical_name: str
    year_from: int | None = None
    year_to: int | None = None


@dataclass
class ReviewItem:
    raw_category: str
    normalized_candidate: str
    source_exam_id: str
    year_roc: int
    provider_id: str = ""
    raw_exam_name: str = ""
    classification_signature: str = ""
    bundle_id: str = ""
    reason: str = ""

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


@dataclass
class NormalizedPaper:
    canonical_id: str
    canonical_name: str
    year_roc: int
    exam_name_raw: str
    category_raw: str
    subject_name_raw: str
    paper_code: str
    file_type: str
    download_url_source: str
    category_code: str = ""
    source_exam_id: str = ""
    subject_code: str = ""
    download_url_mirror: str = ""
    download_url_bundle: str = ""
    storage_key: str = ""
    checksum: str = ""
    provider_id: str = ""

    # Versioned exam identity fields. The legacy canonical fields above remain
    # for URL and migration compatibility; publication grouping uses bundle_id.
    schema_version: int = 1
    catalog_version: str = ""
    domain_id: str = ""
    exam_family_id: str = ""
    exam_series_id: str = ""
    level_id: str = ""
    track_id: str = ""
    variant_ids: list[str] = field(default_factory=list)
    stage_id: str = ""
    exam_event_id: str = ""
    bundle_id: str = ""
    bundle_name: str = ""
    bundle_policy_id: str = "default-bundle-policy-v2"
    classification_confidence: str = ""
    classification_reason: str = ""
    exam_class: str = ""
    exam_subclass: str = ""
    source_material: SourceMaterial | None = None

    def __post_init__(self) -> None:
        self.source_material = source_material(self.source_material)
        if type(self.schema_version) is not int or self.schema_version not in {1, 2, 3}:
            raise ValueError("Unsupported normalized paper version")
        if (self.schema_version == 3) != (self.source_material is not None):
            raise ValueError("Reviewed source material requires normalized paper v3")


@dataclass
class NormalizedCatalog:
    papers: list[NormalizedPaper]
    review_queue: list[ReviewItem]


@dataclass
class BundleAsset:
    canonical_id: str
    canonical_name: str
    years: list[int]
    file_count: int
    storage_key: str
    asset_name: str
    release_tag: str = ""
    download_url: str = ""
    checksum: str = ""
    legacy_asset_names: list[str] = field(default_factory=list)
    schema_version: int = 1
    bundle_id: str = ""
    catalog_version: str = ""
    domain_id: str = ""
    exam_family_id: str = ""
    exam_series_id: str = ""
    level_id: str = ""
    track_id: str = ""
    variant_ids: list[str] = field(default_factory=list)
    stage_id: str = ""
    bundle_policy_id: str = "default-bundle-policy-v2"
    classification_confidence: str = ""
    classification_reason: str = ""
    exam_class: str = ""
    exam_subclass: str = ""
    search_aliases: list[str] = field(default_factory=list)
    subject_labels: list[str] = field(default_factory=list)
    legacy_canonical_ids: list[str] = field(default_factory=list)
    # A logical exam identity may need multiple downloadable ZIP parts when
    # one archive would exceed GitHub's per-asset byte limit. The identity
    # remains `bundle_id`; these fields describe the physical projection.
    part_index: int = 1
    part_count: int = 1
    part_label: str = ""
    source_material: MaterialSummary | None = None

    def __post_init__(self) -> None:
        self.source_material = material_summary(self.source_material)
        if type(self.schema_version) is not int or self.schema_version not in {1, 2, 3}:
            raise ValueError("Unsupported bundle version")
        if (self.schema_version == 3) != (self.source_material is not None):
            raise ValueError("Reviewed source material requires bundle v3")
        if self.source_material is not None and self.years != self.source_material.years:
            raise ValueError("Bundle years differ from reviewed material dates")


@dataclass
class SyncFailure:
    stage: str
    source_exam_id: str
    year_roc: int
    paper_code: str
    file_type: str
    url: str
    message: str

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


@dataclass
class BundleBuildResult:
    bundles: list[BundleAsset]
    failures: list[SyncFailure]


@dataclass
class StoredFile:
    storage_key: str
    path: Path
    checksum: str
    created: bool
    size: int


FILE_TYPE_LABELS = {
    "question_alt": "\u8a66\u984c\uff08\u4e8c\uff09",
    "answer_sheet": "\u7b54\u984c\u5377",
    "question_cover": "試題封面",
    "scoring_guidelines": "評分原則",
    "question": "試題",
    "question_answer": "試題與答案",
    "answer": "答案",
    "corrected_answer": "更正答案",
    "all_answers": "全部答案",
    "answer_table": "答案表",
    "accessible_bundle": "無障礙題本",
    "listening_audio": "聽力音檔",
    "listening_transcript": "聽力逐字稿",
}


def file_type_label(file_type: str) -> str:
    if file_type.startswith("question_page_"):
        page = file_type.removeprefix("question_page_").lstrip("0") or "0"
        return f"試題第{page}頁"
    return FILE_TYPE_LABELS.get(file_type, file_type or "file")


def to_plain_data(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return to_plain_data(asdict(value))
    if isinstance(value, dict):
        return {
            key: to_plain_data(item)
            for key, item in value.items()
            if key != "source_material" or item is not None
        }
    if isinstance(value, (list, tuple)):
        return [to_plain_data(item) for item in value]
    return value
