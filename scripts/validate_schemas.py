"""Validate persisted JSON artifacts against their versioned contracts."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.site_registry import get_site_config


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
    schemas: dict[str, Draft202012Validator] = {}
    for path in sorted(schema_dir.glob("*.json")):
        schema = _read_json(path)
        Draft202012Validator.check_schema(schema)
        schemas[path.name] = Draft202012Validator(schema)

    site_dir = repo_root / "data" / "sites" / "default"
    bundles = _read_json(site_dir / "bundles.json")
    if not isinstance(bundles, dict) or bundles.get("schema_version") != 2 or not isinstance(bundles.get("bundles"), list):
        raise ValueError("data/sites/default/bundles.json: expected a v2 bundle inventory")
    for index, bundle in enumerate(bundles["bundles"]):
        _validate(schemas["bundle-v2.schema.json"], bundle, f"data/sites/default/bundles.json.bundles[{index}]")

    artifacts = (
        (site_dir / "frontend-bundles.json", "frontend-bundle-feed-v2.schema.json"),
        (site_dir / "release-assets.json", "release-assets-v2.schema.json"),
        (repo_root / "catalog" / "source-inventory.json", "source-inventory.schema.json"),
    )
    for path, schema_name in artifacts:
        _validate(schemas[schema_name], _read_json(path), str(path.relative_to(repo_root)))

    paper_validator = schemas["normalized-paper-v2.schema.json"]
    validated_providers = 0
    providers_without_papers: list[str] = []
    for provider_id in get_site_config("default").provider_ids:
        paper_dir = repo_root / "data" / "providers" / provider_id / "papers"
        year_files = [path for path in paper_dir.glob("*.json") if path.stem.isdigit()]
        if not year_files:
            providers_without_papers.append(provider_id)
            continue
        latest = max(year_files, key=lambda path: int(path.stem))
        papers = _read_json(latest)
        if not isinstance(papers, list):
            raise ValueError(f"{latest.relative_to(repo_root)}: expected a paper array")
        for index, paper in enumerate(papers):
            _validate(paper_validator, paper, f"{latest.relative_to(repo_root)}[{index}]")
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
