"""Reviewed TOCFL bank units and references, independent of storage years."""

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
from urllib.parse import unquote, urlsplit

from app.source_material import SourceDate, SourceMaterial

DOWNLOAD_URL = "https://tocfl.edu.tw/tocfl/index.php/exam/download"
BANK_URL = "https://tocfl.edu.tw/tocfl/index.php/exam/test/page/1?pressBtn=%28%E9%A1%8C%E5%BA%AB%29"
# Acquisition partition retained for traceability; never an examination/material date.
CAPTURE_PARTITION_YEAR = 2026


def native_materials_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/tocfl/native-materials-v1.json"


def normalized_label(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


def validate_resource_url(url: str) -> None:
    parsed = urlsplit(url)
    path = unquote(parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "tocfl.edu.tw"
        or parsed.query
        or parsed.fragment
        or re.fullmatch(
            r"/tocfl/assets/files/(?:mock_database|vocabulary)/[^/\\]+\.(?:rar|xlsx|xls|pdf|zip)",
            path,
        )
        is None
        or any(ord(char) < 32 for char in path)
    ):
        raise ValueError(f"Unreviewed TOCFL resource endpoint: {url}")


@dataclass(frozen=True)
class NativeBand:
    code: str
    source_label: str
    level_id: str
    level_label: str
    outcomes: tuple[str, ...]


@dataclass(frozen=True)
class NativeResource:
    id: str
    current: bool
    collection: str
    programme: str
    band_code: str | None
    skill: str | None
    unit_label: str | None
    script_variants: tuple[str, ...]
    source_url: str
    source_label: str
    display_label: str
    file_type: str
    payload_format: str
    checksum: str
    bytes: int
    publication_year: int | None
    archive: dict[str, Any] | None
    unavailable_companion_IDs: tuple[str, ...]
    reviewed_at: str
    notes: str

    @property
    def source_exam_id(self) -> str:
        if self.band_code is not None:
            return f"tocfl-cert-{self.band_code}-question-bank"
        return f"{self.programme}-cert-reference"

    @property
    def subject_code(self) -> str:
        return hashlib.sha256(self.source_url.encode()).hexdigest()[:24]

    @property
    def material(self) -> SourceMaterial:
        source_date = (
            SourceDate("publication_year", self.publication_year)
            if self.publication_year is not None
            else SourceDate("undated", None)
        )
        kind = "question_bank" if self.collection == "question_bank" else "reference"
        return SourceMaterial(kind, source_date, self.source_url, self.display_label)

    def category_code(self, script: str) -> str:
        if script not in self.script_variants or self.band_code is None:
            raise ValueError("TOCFL resource has no reviewed paper/script applicability")
        return f"{self.band_code}-{script}"

    def script_for_category(self, category_code: str) -> str | None:
        return next(
            (
                script
                for script in self.script_variants
                if category_code == self.category_code(script)
            ),
            None,
        )


@dataclass(frozen=True)
class NativeScript:
    code: str
    label: str


@dataclass(frozen=True)
class NativeCatalog:
    bands: tuple[NativeBand, ...]
    scripts: tuple[NativeScript, ...]
    resources: tuple[NativeResource, ...]


def read_native_catalog(path: Path) -> NativeCatalog:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        type(payload.get("schema_version")) is not int
        or payload["schema_version"] != 1
        or payload.get("provider_id") != "tocfl_cert"
        or payload.get("listing_urls") != [DOWNLOAD_URL, BANK_URL]
        or not payload.get("resources")
    ):
        raise ValueError("Unsupported TOCFL native evidence")
    bands = tuple(
        NativeBand(**{**row, "outcomes": tuple(row["outcomes"])}) for row in payload["bands"]
    )
    if {b.code for b in bands} != {"novice", "a", "b", "c"} or len(bands) != 4:
        raise ValueError("TOCFL native band coverage is ambiguous")
    for band in bands:
        if not band.level_id or not band.level_label or len(band.outcomes) != 2:
            raise ValueError("TOCFL native band requires its two score outcomes")
    scripts = tuple(NativeScript(**row) for row in payload["script_variants"])
    if {s.code for s in scripts} != {"traditional", "simplified"} or len(scripts) != 2:
        raise ValueError("TOCFL native writing-script variants are ambiguous")
    resources = []
    for row in payload["resources"]:
        fields = dict(row)
        for name in ["script_variants", "unavailable_companion_IDs"]:
            fields[name] = tuple(fields[name])
        resource = NativeResource(**fields)
        validate_resource_url(resource.source_url)
        expected_id = (
            "tocfl-native-"
            + hashlib.sha256(resource.source_url.encode()).hexdigest()[:20]
            + "-"
            + resource.checksum[:20]
        )
        if (
            resource.id != expected_id
            or type(resource.current) is not bool
            or re.fullmatch(r"[0-9a-f]{64}", resource.checksum) is None
            or type(resource.bytes) is not int
            or resource.bytes <= 0
            or not resource.source_url.endswith("." + resource.payload_format)
            or not resource.source_label.strip()
            or not resource.display_label.strip()
            or not resource.notes.strip()
        ):
            raise ValueError("Invalid TOCFL native resource anchor")
        date.fromisoformat(resource.reviewed_at)
        if resource.collection == "reference":
            if (
                resource.programme not in {"tocfl", "cccc"}
                or resource.band_code is not None
                or resource.skill is not None
                or resource.unit_label is not None
                or resource.script_variants
                or resource.file_type != "reference"
                or resource.payload_format not in {"pdf", "xls", "zip"}
                or resource.archive is not None
            ):
                raise ValueError("TOCFL/CCCC vocabulary references cannot become bank papers")
        elif resource.collection in {"question_bank", "bank_instructions"}:
            if (
                resource.programme != "tocfl"
                or resource.band_code not in {b.code for b in bands}
                or not resource.unit_label
                or resource.publication_year is not None
            ):
                raise ValueError("TOCFL rolling bank cannot inherit a filename/update year")
            if resource.collection == "bank_instructions":
                valid = (
                    resource.file_type == "instructions"
                    and resource.skill == "combined"
                    and not resource.script_variants
                    and resource.payload_format == "rar"
                )
            else:
                roles = {
                    "[正體試題]": ("question", ("traditional",)),
                    "[簡體試題]": ("question", ("simplified",)),
                    "[音檔]": ("listening_audio", ("traditional", "simplified")),
                    "[答案]": ("answer", ("traditional", "simplified")),
                    "[聽力腳本]": ("listening_transcript", ("traditional", "simplified")),
                }
                valid = (
                    roles.get(resource.source_label)
                    == (resource.file_type, resource.script_variants)
                    and resource.skill in {"聽力", "閱讀"}
                    and resource.payload_format
                    == ("xlsx" if resource.file_type == "answer" else "rar")
                    and (
                        resource.file_type not in {"listening_audio", "listening_transcript"}
                        or resource.skill == "聽力"
                    )
                )
            if not valid:
                raise ValueError("TOCFL native unit/role/script context conflicts")
            if resource.payload_format == "rar":
                archive = resource.archive
                if (
                    archive is None
                    or type(archive.get("member_count")) is not int
                    or archive["member_count"] <= 0
                    or re.fullmatch(r"[0-9a-f]{64}", archive.get("member_inventory_sha256", ""))
                    is None
                    or sum(archive["formats"].values()) != archive["member_count"]
                    or set(archive["formats"])
                    != {"mp3" if resource.file_type == "listening_audio" else "pdf"}
                ):
                    raise ValueError("TOCFL source RAR requires a complete native member review")
            elif resource.archive is not None:
                raise ValueError("TOCFL workbook is not a question RAR")
        else:
            raise ValueError("Unknown TOCFL resource collection")
        _ = resource.material
        resources.append(resource)
    current = [r for r in resources if r.current]
    if len({(r.source_url, r.checksum) for r in resources}) != len(resources) or len(
        {r.source_url for r in current}
    ) != len(current):
        raise ValueError("Duplicate TOCFL native URL/checksum or current resource")
    units: dict[tuple[str | None, str | None, str | None], list[NativeResource]] = {}
    for resource in current:
        if resource.collection == "question_bank":
            units.setdefault((resource.band_code, resource.skill, resource.unit_label), []).append(
                resource
            )
    for (_band, skill, _unit), members in units.items():
        expected = {
            ("question", ("traditional",)),
            ("question", ("simplified",)),
            ("answer", ("traditional", "simplified")),
        }
        if skill == "聽力":
            expected.update(
                {
                    ("listening_audio", ("traditional", "simplified")),
                    ("listening_transcript", ("traditional", "simplified")),
                }
            )
        actual = {(r.file_type, r.script_variants) for r in members}
        if actual != expected or len(actual) != len(members):
            raise ValueError(
                "TOCFL unit requires both scripts and their complete linked companion packages"
            )
    instruction_bands = [r.band_code for r in current if r.collection == "bank_instructions"]
    if len(instruction_bands) != len(bands) or set(instruction_bands) != {b.code for b in bands}:
        raise ValueError("TOCFL native instruction-band coverage is incomplete")
    return NativeCatalog(bands, scripts, tuple(resources))


@lru_cache(maxsize=1)
def native_catalog() -> NativeCatalog:
    return read_native_catalog(native_materials_path(Path(__file__).resolve().parents[3]))


def current_resources() -> tuple[NativeResource, ...]:
    return tuple(r for r in native_catalog().resources if r.current)


def native_resource(source_url: str, checksum: str) -> NativeResource | None:
    return next(
        (
            r
            for r in native_catalog().resources
            if r.source_url == source_url and r.checksum == checksum
        ),
        None,
    )


def validate_native_payload(resource: NativeResource, body: bytes) -> None:
    if len(body) != resource.bytes or hashlib.sha256(body).hexdigest() != resource.checksum:
        raise ValueError(
            "TOCFL native resource bytes changed; review this revision before acquisition"
        )
    signatures = {
        "rar": b"Rar!\x1a\x07",
        "pdf": b"%PDF-",
        "xls": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
        "xlsx": b"PK",
        "zip": b"PK",
    }
    if not body.startswith(signatures[resource.payload_format]):
        raise ValueError("TOCFL resource payload contradicts its native format")
    if resource.payload_format in {"zip", "xlsx"}:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            if archive.testzip() is not None:
                raise ValueError("TOCFL native ZIP/workbook failed its CRC check")


def reviewed_native_revisions(
    entries: list[dict[str, Any]], *, repo_root: Path
) -> list[dict[str, Any]]:
    resources = read_native_catalog(native_materials_path(repo_root)).resources
    by_anchor = {(r.source_url, r.checksum): r for r in resources}
    reviewed = []
    for entry in entries:
        record = entry["source_record"]
        resource = by_anchor.get(
            (record.get("download_url_source", ""), record.get("checksum", ""))
        )
        if record.get("provider_id") != "tocfl_cert" or resource is None:
            continue
        reviewed.append(
            {
                "revision_id": entry["id"],
                "native_resource_id": resource.id,
                "programme": resource.programme,
                "collection": resource.collection,
                "band_code": resource.band_code,
                "script_applicability": list(resource.script_variants),
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

    evidence = read_native_catalog(native_materials_path(repo_root))
    by_anchor = {(r.source_url, r.checksum): r for r in evidence.resources}
    provider = provider_paths(repo_root, "tocfl_cert")
    pages, catalog, failures = load_provider_state(provider)
    if failures:
        raise ValueError("TOCFL native projection has unresolved acquisition failures")
    seen = set()
    projections = set()
    verified_paths = set()

    def check(record: dict[str, Any], storage_key: str) -> NativeResource:
        resource = by_anchor.get(
            (record.get("download_url_source", ""), record.get("checksum", ""))
        )
        if record.get("provider_id") != "tocfl_cert" or resource is None:
            raise ValueError("TOCFL source reference lacks exact native URL/checksum evidence")
        parts = PurePosixPath(storage_key).parts
        path = repo_root / "mirror" / storage_key
        if (
            parts[:2] != ("providers", "tocfl_cert")
            or ".." in parts
            or "\\" in storage_key
            or path.is_symlink()
            or not path.resolve().is_relative_to(provider.mirror_dir.resolve())
        ):
            raise ValueError("TOCFL resource locator escapes its provider owner")
        if verify_mirror and storage_key not in verified_paths:
            validate_native_payload(resource, path.read_bytes())
            verified_paths.add(storage_key)
        seen.add((resource.source_url, resource.checksum))
        return resource

    for paper in catalog.papers:
        resource = check(to_plain_data(paper), paper.storage_key)
        script = resource.script_for_category(paper.category_code)
        key = (resource.id, script)
        if (
            resource.collection != "question_bank"
            or script is None
            or key in projections
            or paper.file_type != resource.file_type
            or paper.source_exam_id != resource.source_exam_id
            or paper.year_roc + 1911 != CAPTURE_PARTITION_YEAR
            or paper.subject_code != resource.subject_code
            or paper.subject_name_raw != resource.display_label
            or paper.source_material != resource.material
            or paper.canonical_id != "tocfl-cert"
            or paper.classification_confidence != "high"
            or not paper.storage_key.endswith("." + resource.payload_format)
        ):
            raise ValueError("TOCFL paper contradicts its reviewed band/script/unit/role")
        projections.add(key)
    attachments = set()
    for page in pages:
        if page.year_ad != CAPTURE_PARTITION_YEAR:
            raise ValueError("TOCFL page has an unreviewed acquisition partition")
        for attachment in page.attachments:
            resource = check(
                {**to_plain_data(attachment), "provider_id": page.provider_id},
                attachment.storage_key,
            )
            if (
                resource.collection == "question_bank"
                or resource.id in attachments
                or attachment.file_type != resource.file_type
                or attachment.title != resource.display_label
                or attachment.source_material != resource.material
                or page.source_exam_id != resource.source_exam_id
                or not attachment.storage_key.endswith("." + resource.payload_format)
            ):
                raise ValueError("TOCFL reference/instructions cannot become duplicate bank papers")
            attachments.add(resource.id)
    expected = {
        (r.id, script) for r in evidence.resources if r.current for script in r.script_variants
    }
    expected_attachments = {
        r.id for r in evidence.resources if r.current and r.collection != "question_bank"
    }
    if projections != expected or attachments != expected_attachments:
        raise ValueError("TOCFL native current resource/script coverage is incomplete")
    revisions = load_source_revisions(provider)
    for entry in revisions:
        check(entry["source_record"], entry["blob_storage_key"] or "")
    if seen != by_anchor.keys():
        raise ValueError("TOCFL native anchors lack current or conserved historical coverage")
    return {
        "native_resources": len(evidence.resources),
        "paper_projections": len(projections),
        "reference_attachments": len(attachments),
        "retired_source_references": len(revisions),
        "verified_payload_paths": len(verified_paths),
    }
