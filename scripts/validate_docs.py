from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.cli import build_parser
from app.evidence import local_evidence_exists, markdown_evidence_sections
from app.providers.registry import _PROVIDER_FACTORIES
from app.site_registry import get_site_config
from scripts.render_docs import expected_outputs


REQUIRED_PATHS = [
    "AGENTS.md",
    "docs/architecture.md",
    "docs/concepts.md",
    "docs/reference/exam-identity.md",
    "docs/reference/contracts.md",
    "docs/contributing/add-a-provider.md",
    "docs/operations/commands.md",
    "docs/providers/README.md",
    "docs/providers/rejected-sources.md",
    "docs/providers/notes.md",
    "docs/archive/README.md",
]
FORBIDDEN_LIVE_DIRS = ["docs/developer", "docs/operator", "docs/superpowers"]
INLINE_LINK = re.compile(r"!?\[[^\]]*\]\((?P<target><[^>]+>|[^)\s]+)(?:\s+[^)]*)?\)")
REFERENCE_LINK = re.compile(r"^\s*\[[^\]]+\]:\s*(?P<target>\S+)", flags=re.MULTILINE)


def _load_inventory(repo_root: Path) -> dict[str, object]:
    return json.loads((repo_root / "catalog" / "source-inventory.json").read_text(encoding="utf-8"))


def _cli_commands() -> set[str]:
    parser = build_parser()
    subparsers_action = next(action for action in parser._actions if action.dest == "command")
    return set(subparsers_action.choices)


def _validate_provider_notes(repo_root: Path, inventory: dict[str, object]) -> list[str]:
    errors: list[str] = []
    inventory_ids = {entry["provider_id"] for entry in inventory["providers"]}
    registry_ids = set(_PROVIDER_FACTORIES)
    site_ids = set(get_site_config(str(inventory["site_id"])).provider_ids)
    for label, actual in (("runtime registry", registry_ids), ("site registry", site_ids)):
        missing = sorted(inventory_ids - actual)
        extra = sorted(actual - inventory_ids)
        if missing or extra:
            errors.append(f"inventory/{label} mismatch: missing={missing} extra={extra}")
    provider_root = repo_root / "docs" / "providers"
    for path in provider_root.glob("*.md"):
        if path.name not in {"README.md", "rejected-sources.md", "notes.md"}:
            errors.append(f"legacy provider page remains: {path.relative_to(repo_root)}")
    notes_path = provider_root / "notes.md"
    if not notes_path.is_file():
        return errors
    text = notes_path.read_text(encoding="utf-8")
    unknown = sorted(markdown_evidence_sections(text) - inventory_ids)
    if unknown:
        errors.append(f"docs/providers/notes.md: unregistered provider sections: {unknown}")
    for provider in inventory["providers"]:
        if any(url in text for url in provider["official_source_urls"]):
            errors.append(f"docs/providers/notes.md: official URL for {provider['provider_id']} duplicates inventory")
    return errors


def _relative_link_errors(repo_root: Path) -> list[str]:
    errors: list[str] = []
    markdown_paths = list((repo_root / "docs").rglob("*.md")) + [repo_root / "README.md", repo_root / "AGENTS.md"]
    for path in markdown_paths:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        targets = [match.group("target") for match in INLINE_LINK.finditer(text)]
        targets.extend(match.group("target") for match in REFERENCE_LINK.finditer(text))
        for raw_target in targets:
            target = raw_target.strip("<>")
            if target.startswith(("http://", "https://", "mailto:", "data:", "#")):
                continue
            target_path = unquote(target.split("#", 1)[0])
            if not target_path:
                continue
            resolved = (repo_root / target_path.lstrip("/")) if target_path.startswith("/") else (path.parent / target_path)
            if not resolved.exists():
                errors.append(f"{path.relative_to(repo_root)}: broken relative link {raw_target!r}")
            elif "#" in target and os.path.relpath(resolved, repo_root) == "docs/providers/notes.md":
                reference = f"docs/providers/notes.md#{target.split('#', 1)[1]}"
                if not local_evidence_exists(repo_root, reference):
                    errors.append(f"{path.relative_to(repo_root)}: broken source-judgment anchor {raw_target!r}")
    return errors


def _live_markdown(repo_root: Path) -> list[Path]:
    return [path for path in (repo_root / "docs").rglob("*.md") if "archive" not in path.relative_to(repo_root / "docs").parts]


def _documentation_hygiene_errors(repo_root: Path, inventory: dict[str, object]) -> list[str]:
    errors: list[str] = []
    commands = _cli_commands()
    for path in _live_markdown(repo_root) + [repo_root / "README.md", repo_root / "AGENTS.md"]:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if "\\n\\n" in text:
            errors.append(f"{path.relative_to(repo_root)}: contains literal \\n\\n")
        headings = re.findall(r"^#{1,6}\s+(.+?)\s*$", text, flags=re.MULTILINE)
        duplicates = sorted(heading for heading, count in Counter(headings).items() if count > 1)
        if duplicates:
            errors.append(f"{path.relative_to(repo_root)}: duplicate headings {duplicates}")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if re.search(r"(?<!uv run )python3? -m app\b", line):
                errors.append(f"{path.relative_to(repo_root)}:{line_number}: use 'uv run python -m app'")
            if "cmd /c" in line.lower() or line.rstrip().endswith("^"):
                errors.append(f"{path.relative_to(repo_root)}:{line_number}: Windows command syntax is not allowed")
            match = re.search(r"(?:uv run )?python3? -m app\s+([a-z0-9-]+)", line)
            if match and match.group(1) not in commands:
                errors.append(f"{path.relative_to(repo_root)}:{line_number}: unknown app subcommand {match.group(1)!r}")
    for forbidden in FORBIDDEN_LIVE_DIRS:
        if (repo_root / forbidden).exists():
            errors.append(f"legacy live documentation directory still exists: {forbidden}")
    for required in REQUIRED_PATHS:
        if not (repo_root / required).is_file():
            errors.append(f"required documentation is missing: {required}")
    for entry in [*inventory["providers"], *inventory["candidates"]]:
        for evidence in entry["evidence"]:
            if evidence.startswith(("http://", "https://")):
                continue
            if evidence.startswith("docs/archive/"):
                errors.append(f"{entry.get('provider_id', entry.get('source_id'))}: archive path used as inventory evidence: {evidence}")
            if not local_evidence_exists(repo_root, evidence):
                errors.append(f"{entry.get('provider_id', entry.get('source_id'))}: missing inventory evidence: {evidence}")
    return errors


def _freshness_errors(repo_root: Path) -> list[str]:
    errors: list[str] = []
    try:
        outputs = expected_outputs(repo_root)
    except ValueError as exc:
        return [f"cannot render documentation: {exc}"]
    for path, expected in outputs.items():
        if not path.exists():
            errors.append(f"missing generated documentation: {path.relative_to(repo_root)}")
        elif path.read_text(encoding="utf-8") != expected:
            errors.append(f"stale generated documentation: {path.relative_to(repo_root)}")
    return errors


def validate(repo_root: Path) -> list[str]:
    inventory = _load_inventory(repo_root)
    errors: list[str] = []
    errors.extend(_validate_provider_notes(repo_root, inventory))
    errors.extend(_documentation_hygiene_errors(repo_root, inventory))
    errors.extend(_relative_link_errors(repo_root))
    errors.extend(_freshness_errors(repo_root))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate maintained documentation against executable repository truth.")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true", help="Explicitly select non-mutating validation mode.")
    args = parser.parse_args()
    errors = validate(args.repo_root)
    if errors:
        for error in errors:
            print(f"docs validation failed: {error}", file=sys.stderr)
        return 1
    print("documentation validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
