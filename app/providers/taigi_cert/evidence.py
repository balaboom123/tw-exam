"""Native sample roles and dates, separate from proficiency forms and capture years."""

from __future__ import annotations

import hashlib
import io
import json
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from app.source_material import SourceDate, SourceMaterial

RESOURCE_URL = "https://ttg.moe.edu.tw/tmt/view.php?page=resource"
RULES_URL = "https://ttg.moe.edu.tw/tmt/view.php?page=questionBase"
# This retained storage/discovery partition is not an examination or material date.
CAPTURE_PARTITION_YEAR = 2026
EXCLUDED_LEARNING_RESOURCES = {
    "https://ttg.moe.edu.tw/tmt/src/upload/file/file_109.pdf": "學習地圖",
    "https://ttg.moe.edu.tw/tmt/src/upload/file/file_110.pdf": "就是各門派比較啦",
}


def normalized_label(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


def native_materials_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/taigi/native-materials-v1.json"


def validate_resource_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "ttg.moe.edu.tw"
        or re.fullmatch(r"/tmt/src/upload/file/file_\d+\.(pdf|mp3|zip)", parsed.path) is None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"Unreviewed Taigi resource endpoint: {url}")


@dataclass(frozen=True)
class ArchiveMember:
    native_name: str
    source_url: str
    checksum: str
    bytes: int


@dataclass(frozen=True)
class NativeResource:
    id: str
    current: bool
    form_code: str
    skill: str
    source_url: str
    source_label: str
    checksum: str
    file_type: str
    payload_format: str
    native_heading: str
    published_on: str | None
    revised_on: str | None
    page_count: int | None
    review_basis: str
    archive_members: tuple[ArchiveMember, ...]
    reviewed_at: str
    notes: str

    @property
    def source_exam_id(self) -> str:
        return f"taigi-cert-{self.form_code}-samples"

    @property
    def subject_code(self) -> str:
        return hashlib.sha256(self.source_url.encode()).hexdigest()[:24]

    @property
    def material(self) -> SourceMaterial:
        source_date = (
            SourceDate("publication_year", date.fromisoformat(self.published_on).year)
            if self.published_on is not None
            else SourceDate("undated", None)
        )
        return SourceMaterial("sample", source_date, self.source_url, self.source_label)


def _validate_resource(resource: NativeResource) -> None:
    validate_resource_url(resource.source_url)
    expected_id = (
        "taigi-native-"
        + hashlib.sha256(resource.source_url.encode()).hexdigest()[:20]
        + "-"
        + resource.checksum[:20]
    )
    if (
        resource.id != expected_id
        or type(resource.current) is not bool
        or re.fullmatch(r"[0-9a-f]{64}", resource.checksum) is None
        or resource.form_code not in {"a", "b", "c"}
        or not resource.notes.strip()
        or not resource.source_label.startswith(resource.form_code.upper() + "卷")
        or not resource.source_url.endswith("." + resource.payload_format)
    ):
        raise ValueError("Invalid Taigi native resource context")
    date.fromisoformat(resource.reviewed_at)
    for value in [resource.published_on, resource.revised_on]:
        if value is not None:
            date.fromisoformat(value)
    if resource.revised_on and (
        resource.published_on is None or resource.revised_on < resource.published_on
    ):
        raise ValueError("Taigi native modification precedes publication")
    label = "".join(resource.source_label.split())
    if resource.payload_format == "pdf":
        role = "answer" if label.endswith("範例參考答案") else "question"
        basis = (
            "native_model_answers"
            if role == "answer" and resource.skill in {"口語", "書寫"}
            else "native_answer_table"
            if role == "answer"
            else "native_question_instructions"
        )
        if (
            type(resource.page_count) is not int
            or resource.page_count < 1
            or resource.archive_members
            or resource.skill not in {"聽力", "口語", "閱讀", "書寫"}
            or resource.skill + "測驗範例" not in label
            or not label.endswith("範例參考答案" if role == "answer" else "範例題本")
            or resource.form_code.upper() + "卷" not in resource.native_heading
            or resource.skill + "測驗" not in resource.native_heading
        ):
            raise ValueError("Taigi native PDF contradicts its form/skill/role scope")
    elif resource.payload_format == "mp3":
        role = {"聽力": "listening_audio", "口語": "speaking_audio", "書寫": "dictation_audio"}.get(
            resource.skill, ""
        )
        basis = "official_skill_audio_and_full_MP3_decode"
        if (
            not role
            or not label.endswith(resource.skill + "測驗範例音檔")
            or resource.page_count is not None
            or resource.published_on is not None
            or resource.revised_on is not None
            or resource.archive_members
        ):
            raise ValueError("Taigi audio requires its explicit native skill scope")
    elif resource.payload_format == "zip":
        role, basis = "source_archive", "native_archive_and_exact_member_conservation"
        if (
            resource.skill != "combined"
            or not label.endswith("各測驗範例(全)")
            or resource.page_count is not None
            or resource.published_on is not None
            or resource.revised_on is not None
            or not resource.archive_members
        ):
            raise ValueError("Taigi original archive requires its complete member review")
    else:
        raise ValueError("Unsupported Taigi native payload format")
    if resource.file_type != role or resource.review_basis != basis:
        raise ValueError("Taigi native role contradicts its reviewed evidence")


def read_native_resources(path: Path) -> tuple[NativeResource, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        type(payload.get("schema_version")) is not int
        or payload["schema_version"] != 1
        or payload.get("provider_id") != "taigi_cert"
        or payload.get("evidence_url") != RESOURCE_URL
        or payload.get("rules_url") != RULES_URL
        or not payload.get("resources")
    ):
        raise ValueError("Unsupported Taigi native resource evidence")
    resources = []
    for row in payload["resources"]:
        fields = dict(row)
        fields["archive_members"] = tuple(ArchiveMember(**m) for m in fields["archive_members"])
        resource = NativeResource(**fields)
        _validate_resource(resource)
        resources.append(resource)
    by_anchor = {(r.source_url, r.checksum): r for r in resources}
    current = [r for r in resources if r.current]
    if (
        len(by_anchor) != len(resources)
        or len({r.id for r in resources}) != len(resources)
        or len({r.source_url for r in current}) != len(current)
        or not current
    ):
        raise ValueError("Duplicate Taigi native resource anchor")
    for resource in resources:
        names = set()
        for member in resource.archive_members:
            name = PurePosixPath(member.native_name)
            linked = by_anchor.get((member.source_url, member.checksum))
            if (
                member.native_name in names
                or name.is_absolute()
                or ".." in name.parts
                or any(c in member.native_name for c in '\\:*?"<>|')
                or linked is None
                or linked.form_code != resource.form_code
                or linked.file_type == "source_archive"
                or linked.checksum != member.checksum
                or type(member.bytes) is not int
                or member.bytes <= 0
            ):
                raise ValueError("Taigi archive member lacks its exact owned native file")
            names.add(member.native_name)
    return tuple(resources)


@lru_cache(maxsize=1)
def native_resources() -> tuple[NativeResource, ...]:
    return read_native_resources(native_materials_path(Path(__file__).resolve().parents[3]))


def current_resources() -> tuple[NativeResource, ...]:
    return tuple(resource for resource in native_resources() if resource.current)


def native_resource(url: str, checksum: str) -> NativeResource | None:
    return next(
        (r for r in native_resources() if (r.source_url, r.checksum) == (url, checksum)), None
    )


def validate_native_payload(resource: NativeResource, body: bytes) -> None:
    actual = hashlib.sha256(body).hexdigest()
    if actual != resource.checksum:
        raise ValueError(
            f"Taigi replacement bytes require native material review: "
            f"{resource.source_url} SHA256={actual}"
        )
    if resource.payload_format == "pdf" and not body.startswith(b"%PDF-"):
        raise ValueError("Taigi native PDF signature is invalid")
    if resource.payload_format == "mp3" and not (
        body.startswith(b"ID3") or (len(body) > 1 and body[0] == 255 and body[1] & 224 == 224)
    ):
        raise ValueError("Taigi native MP3 signature is invalid")
    if resource.payload_format == "zip":
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            entries = [member for member in archive.infolist() if not member.is_dir()]
            expected = {m.native_name: m for m in resource.archive_members}
            if len(entries) != len(expected) or {m.filename for m in entries} != expected.keys():
                raise ValueError("Taigi native archive member coverage changed")
            for entry in entries:
                fact = expected[entry.filename]
                data = archive.read(entry)
                if len(data) != fact.bytes or hashlib.sha256(data).hexdigest() != fact.checksum:
                    raise ValueError("Taigi archive differs from its individual native payload")


def reviewed_native_revisions(
    entries: list[dict[str, Any]], *, repo_root: Path
) -> list[dict[str, Any]]:
    resources = {
        (r.source_url, r.checksum): r
        for r in read_native_resources(native_materials_path(repo_root))
    }
    reviewed = []
    for entry in entries:
        record = entry["source_record"]
        resource = resources.get((record.get("download_url_source"), record.get("checksum")))
        if record.get("provider_id") != "taigi_cert" or resource is None:
            continue
        reviewed.append(
            {
                "revision_id": entry["id"],
                "native_resource_id": resource.id,
                "form_code": resource.form_code,
                "file_type": resource.file_type,
                "source_material": {
                    "kind": resource.material.kind,
                    "date": {
                        "basis": resource.material.date.basis,
                        "year_ad": resource.material.date.year_ad,
                    },
                },
                "original_source_record_unchanged": True,
            }
        )
    return reviewed


def validate_native_resources(repo_root: Path, *, verify_mirror: bool = False) -> dict[str, int]:
    from app.models import to_plain_data
    from app.paths import provider_paths
    from app.source_revisions import load_source_revisions
    from app.state import load_provider_state

    resources = read_native_resources(native_materials_path(repo_root))
    by_anchor = {(r.source_url, r.checksum): r for r in resources}
    provider = provider_paths(repo_root, "taigi_cert")
    pages, catalog, _failures = load_provider_state(provider)
    seen = set()
    verified_paths = set()
    validated_formats = set()

    def check(record: dict[str, Any], storage_key: str) -> NativeResource:
        anchor = (record.get("download_url_source", ""), record.get("checksum", ""))
        resource = by_anchor.get(anchor)
        if record.get("provider_id") != "taigi_cert" or resource is None:
            raise ValueError("Taigi source reference lacks exact native evidence")
        parts = PurePosixPath(storage_key).parts
        path = repo_root / "mirror" / storage_key
        if (
            parts[:2] != ("providers", "taigi_cert")
            or ".." in parts
            or "\\" in storage_key
            or not storage_key.endswith("." + resource.payload_format)
            or path.is_symlink()
            or not path.resolve().is_relative_to(provider.mirror_dir.resolve())
        ):
            raise ValueError("Taigi native locator escapes its provider owner")
        if verify_mirror and storage_key not in verified_paths:
            body = path.read_bytes()
            if hashlib.sha256(body).hexdigest() != resource.checksum:
                raise ValueError("Taigi native payload checksum changed")
            if resource.checksum not in validated_formats:
                validate_native_payload(resource, body)
                validated_formats.add(resource.checksum)
            verified_paths.add(storage_key)
        seen.add(anchor)
        return resource

    current_seen = set()
    for paper in catalog.papers:
        resource = check(to_plain_data(paper), paper.storage_key)
        if resource.id in current_seen:
            raise ValueError("Duplicate Taigi native current resource projection")
        current_seen.add(resource.id)
        if (
            resource.file_type == "source_archive"
            or paper.file_type != resource.file_type
            or paper.source_exam_id != resource.source_exam_id
            or paper.year_roc + 1911 != CAPTURE_PARTITION_YEAR
            or paper.category_code != resource.form_code
            or paper.canonical_id != "taigi-cert-" + resource.form_code
            or paper.subject_code != resource.subject_code
            or paper.subject_name_raw != resource.source_label
            or paper.source_material != resource.material
            or paper.classification_confidence != "high"
        ):
            raise ValueError("Taigi paper contradicts its native form/skill/role/date")
    attachments = 0
    for page in pages:
        for attachment in page.attachments:
            record = {**to_plain_data(attachment), "provider_id": page.provider_id}
            resource = check(record, attachment.storage_key)
            if resource.id in current_seen:
                raise ValueError("Duplicate Taigi native current archive attachment")
            current_seen.add(resource.id)
            if (
                resource.file_type != "source_archive"
                or attachment.file_type != resource.file_type
                or attachment.title != resource.source_label
                or attachment.source_material != resource.material
                or page.source_exam_id != resource.source_exam_id
                or page.year_ad != CAPTURE_PARTITION_YEAR
            ):
                raise ValueError("Taigi original archive contradicts its native resource context")
            attachments += 1
    revisions = load_source_revisions(provider)
    unverified_references = 0
    for entry in revisions:
        if entry["blob_storage_key"] is None:
            # Preserve metadata-only observations without treating them as
            # checksum-backed native material or satisfying anchor coverage.
            unverified_references += 1
            continue
        check(entry["source_record"], entry["blob_storage_key"])
    if seen != by_anchor.keys():
        raise ValueError("Taigi native resources lack current or conserved historical coverage")
    return {
        "native_resources": len(resources),
        "native_unique_payloads": len({r.checksum for r in resources}),
        "paper_projections": len(catalog.papers),
        "original_archive_attachments": attachments,
        "retired_source_references": len(revisions),
        "unverified_source_references": unverified_references,
        "verified_payload_paths": len(verified_paths),
    }
