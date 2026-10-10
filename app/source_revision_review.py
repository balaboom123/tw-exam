"""Reviewed meaning of retained references, separate from immutable source evidence.

A legacy journal can contain a document incorrectly acquired as a paper or
audio. Its original record and recovery key remain evidence of that acquisition;
an exact reviewed fact supplies a non-paper disposition without inventing a new
examination identity or adding the retired material to current publication.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any, cast

from app.source_material import SourceMaterial

ROOT = Path(__file__).resolve().parents[1]


def reference_revision_evidence_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/source-revisions/reference-material-v1.json"


def _facts(repo_root: Path) -> list[dict[str, Any]]:
    path = reference_revision_evidence_path(repo_root)
    if not path.exists():
        return []  # Earlier/data-only recovery roots contain no reviewed judgments.
    document = json.loads(path.read_text())
    if type(document.get("schema_version")) is not int or document["schema_version"] != 1:
        raise ValueError("Unsupported reference revision evidence version")
    return cast(list[dict[str, Any]], document["facts"])


def _matches(entry: dict[str, Any], fact: dict[str, Any]) -> bool:
    record = entry["source_record"]
    return (
        entry["id"] == fact["revision_id"]
        and entry["record_type"] == "paper"
        and all(record.get(field) == value for field, value in fact["source_record"].items())
    )


def reviewed_reference_revisions(
    entries: list[dict[str, Any]], *, repo_root: Path = ROOT
) -> list[dict[str, Any]]:
    """Return independent judgments; never mutate or normalize the old records."""
    facts = _facts(repo_root)
    reviewed = []
    for entry in entries:
        for fact in facts:
            if _matches(entry, fact):
                material = SourceMaterial.from_data(fact["source_material"])
                if material.kind != "reference" or material.needs_review:
                    raise ValueError("Reference revision requires resolved reference facts")
                reviewed.append(
                    {
                        "revision_id": entry["id"],
                        "evidence_id": fact["id"],
                        "disposition": "non_paper_reference",
                        "source_material": fact["source_material"],
                        "native_members": [member["name"] for member in fact["native_members"]],
                        "reason": fact["reason"],
                    }
                )
                break
    return reviewed


def validate_reference_revision_evidence(repo_root: Path, *, verify_mirror: bool = False) -> int:
    """Require the exact immutable source anchor and optionally its native PDFs."""
    from app.paths import provider_paths
    from app.providers.registry import _PROVIDER_FACTORIES
    from app.source_revisions import load_source_revisions

    if not reference_revision_evidence_path(repo_root).is_file():
        raise ValueError("Missing reference revision evidence")
    facts = _facts(repo_root)
    ids = [fact["id"] for fact in facts]
    revisions = [fact["revision_id"] for fact in facts]
    if len(set(ids)) != len(ids) or len(set(revisions)) != len(revisions):
        raise ValueError("Duplicate reference revision evidence")
    journals = {}
    for fact in facts:
        names = [member["name"] for member in fact["native_members"]]
        if len(names) != len(set(names)):
            raise ValueError(f"{fact['id']}: duplicate native reference member")
        provider_id = fact["source_record"]["provider_id"]
        if provider_id not in _PROVIDER_FACTORIES:
            raise ValueError(f"{fact['id']}: unknown reference revision provider")
        if provider_id not in journals:
            journals[provider_id] = load_source_revisions(provider_paths(repo_root, provider_id))
        matches = [entry for entry in journals[provider_id] if _matches(entry, fact)]
        if len(matches) != 1:
            raise ValueError(f"{fact['id']}: reference revision anchor is missing or changed")
        reviewed_reference_revisions(matches, repo_root=repo_root)
        if verify_mirror:
            entry = matches[0]
            path = repo_root / "mirror" / entry["blob_storage_key"]
            with path.open("rb") as stream:
                checksum = hashlib.file_digest(stream, "sha256").hexdigest()
            if checksum != fact["source_record"]["checksum"]:
                raise ValueError(f"{fact['id']}: reference revision mirror differs")
            expected = {member["name"]: member["checksum"] for member in fact["native_members"]}
            with zipfile.ZipFile(path, metadata_encoding=fact["filename_encoding"]) as archive:
                names = archive.namelist()
                if len(names) != len(set(names)) or set(names) != expected.keys():
                    raise ValueError(f"{fact['id']}: native reference members differ")
                for name, member_checksum in expected.items():
                    data = archive.read(name)
                    if (
                        not data.startswith(b"%PDF-")
                        or hashlib.sha256(data).hexdigest() != member_checksum
                    ):
                        raise ValueError(f"{fact['id']}: native reference PDF differs")
    return len(facts)
