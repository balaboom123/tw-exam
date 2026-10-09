"""Validate persisted JSON artifacts against their versioned contracts."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.site_registry import get_site_config
from app.review_queue import decode_review_queue
from app.moex_identity_evidence import moex_evidence_path, validate_moex_category_evidence
from app.tqc_identity_evidence import tqc_evidence_path, validate_tqc_identity_evidence
from app.paths import provider_paths
from app.provider_index import load_provider_index
from app.source_revisions import load_source_revisions, revision_journal_path


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read {path}: {exc}") from exc


def _validate(validator: Draft202012Validator, payload: Any, label: str) -> None:
    try:
        validator.validate(payload)
    except ValidationError as exc:
        raise ValueError(f"{label}{exc.json_path.removeprefix('$')}: {exc.message}") from exc


def validate_schemas(repo_root: Path = ROOT) -> tuple[int, int, list[str]]:
    schema_dir = repo_root / "schemas"
    documents: dict[str, dict[str, Any]] = {}
    for path in sorted(schema_dir.glob("*.json")):
        schema = _read_json(path)
        Draft202012Validator.check_schema(schema)
        documents[path.name] = schema
    registry = Registry().with_resources(
        (
            schema.get("$id", f"https://tw-exam.invalid/schemas/{name}"),
            Resource.from_contents(schema, default_specification=DRAFT202012),
        )
        for name, schema in documents.items()
    )
    schemas = {
        name: Draft202012Validator(
            schema, registry=registry, format_checker=Draft202012Validator.FORMAT_CHECKER,
        )
        for name, schema in documents.items()
    }

    site_dir = repo_root / "data" / "sites" / "default"
    bundles = _read_json(site_dir / "bundles.json")
    if not isinstance(bundles, dict) or type(bundles.get("schema_version")) is not int or bundles["schema_version"] not in {2, 3} or not isinstance(bundles.get("bundles"), list):
        raise ValueError("data/sites/default/bundles.json: expected a supported bundle inventory")
    for index, bundle in enumerate(bundles["bundles"]):
        version = bundle.get("schema_version")
        if type(version) is not int or version not in {2, 3}:
            raise ValueError(f"Unsupported site bundle record version: {index}")
        if version == 3 and bundles["schema_version"] != 3:
            raise ValueError("Reviewed source material requires site bundle inventory v3")
        _validate(schemas[f"bundle-v{version}.schema.json"], bundle, f"data/sites/default/bundles.json.bundles[{index}]")

    frontend_path = site_dir / "frontend-bundles.json"
    frontend = _read_json(frontend_path)
    version = frontend.get("schema_version")
    if type(version) is not int or version not in {2, 3}:
        raise ValueError("Unsupported frontend feed version")
    _validate(schemas[f"frontend-bundle-feed-v{version}.schema.json"], frontend, str(frontend_path.relative_to(repo_root)))

    artifacts = (
        (site_dir / "release-assets.json", "release-assets-v2.schema.json"),
        (repo_root / "catalog" / "source-inventory.json", "source-inventory.schema.json"),
        (moex_evidence_path(repo_root), "moex-category-identity-v1.schema.json"),
        (tqc_evidence_path(repo_root), "tqc-sample-identity-v1.schema.json"),
    )
    for path, schema_name in artifacts:
        _validate(schemas[schema_name], _read_json(path), str(path.relative_to(repo_root)))
    validate_moex_category_evidence(repo_root)
    validate_tqc_identity_evidence(repo_root)

    validated_providers = 0
    providers_without_papers: list[str] = []
    for provider_id in get_site_config("default").provider_ids:
        provider = provider_paths(repo_root, provider_id)
        index = load_provider_index(provider)
        if index is not None:
            _validate(schemas[f"provider-index-v{index['schema_version']}.schema.json"], index, str(provider.index_path.relative_to(repo_root)))
        revision_path = revision_journal_path(provider)
        if revision_path.exists():
            _validate(schemas["provider-source-revisions-v1.schema.json"], _read_json(revision_path), str(revision_path.relative_to(repo_root)))
            load_source_revisions(provider)
        review_path = repo_root / "data/providers" / provider_id / "review-queue.json"
        if review_path.exists():
            payload = _read_json(review_path)
            _validate(schemas["review-queue-v2.schema.json"], payload, str(review_path.relative_to(repo_root)))
            decode_review_queue(payload, provider_id)
        status = repo_root / "data" / "providers" / provider_id / "sync-status.json"
        if status.is_file():
            _validate(schemas["provider-sync-status-v1.schema.json"], _read_json(status), str(status.relative_to(repo_root)))
        paper_dir = repo_root / "data" / "providers" / provider_id / "papers"
        year_files = [path for path in paper_dir.glob("*.json") if path.stem.isdigit()]
        if not year_files:
            providers_without_papers.append(provider_id)
            continue
        latest = max(year_files, key=lambda path: int(path.stem))
        papers = _read_json(latest)
        if not isinstance(papers, list):
            raise ValueError(f"{latest.relative_to(repo_root)}: expected a paper array")
        for path in year_files:
            records = papers if path == latest else _read_json(path)
            if not isinstance(records, list):
                raise ValueError(f"{path.relative_to(repo_root)}: expected a paper array")
            for index, paper in enumerate(records):
                version = paper.get("schema_version")
                if type(version) is not int or version not in {2, 3}:
                    raise ValueError(f"Unsupported normalized paper version: {path}[{index}]")
                if version == 2 and paper.get("source_material") is not None:
                    raise ValueError(f"Reviewed source material requires normalized paper v3: {path}[{index}]")
                if path == latest or version == 3:
                    _validate(schemas[f"normalized-paper-v{version}.schema.json"], paper, f"{path.relative_to(repo_root)}[{index}]")
        for path in provider.exams_dir.glob("*.json"):
            for page_index, page in enumerate(_read_json(path)):
                for record in [page, *page.get("papers", []), *page.get("attachments", [])]:
                    if record.get("source_material") is not None:
                        _validate(schemas["source-material-v1.schema.json"], record["source_material"], f"{path.relative_to(repo_root)}[{page_index}].source_material")
        validated_providers += 1

    return len(schemas), validated_providers, providers_without_papers


if __name__ == "__main__":
    try:
        schema_count, provider_count, without_papers = validate_schemas()
    except ValueError as exc:
        print(f"schema validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(
        f"validated {schema_count} schemas, site bundles/feed/release assets, source inventory, "
        f"and current paper files for {provider_count} providers"
    )
    if without_papers:
        print(f"registered providers without paper files: {', '.join(without_papers)}")
