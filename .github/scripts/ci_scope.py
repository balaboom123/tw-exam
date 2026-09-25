#!/usr/bin/env python3
"""Decide whether a CI change needs the retained-catalog gates."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess


CATALOG_PREFIXES = ("app/", "catalog/", "data/", "schemas/", "scripts/", "tests/", ".github/scripts/")
CATALOG_FILES = {".github/workflows/ci.yml", "pyproject.toml", "uv.lock", "pytest.ini"}


def requires_catalog_gates(paths: list[str]) -> bool:
    return any(path in CATALOG_FILES or path.startswith(CATALOG_PREFIXES) for path in paths)


def changed_paths(event: str, base: str) -> list[str] | None:
    if event == "workflow_dispatch" or not base or set(base) == {"0"}:
        return None
    comparison = f"{base}...HEAD" if event == "pull_request" else base
    args = ["git", "diff", "--name-only", "--no-renames", comparison]
    if event != "pull_request":
        args.append("HEAD")
    try:
        result = subprocess.run(args, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.splitlines()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True)
    parser.add_argument("--base", default="")
    args = parser.parse_args()
    paths = changed_paths(args.event, args.base)
    needed = paths is None or requires_catalog_gates(paths)
    output = f"catalog={'true' if needed else 'false'}\n"
    print(output, end="")
    if output_file := os.environ.get("GITHUB_OUTPUT"):
        with Path(output_file).open("a", encoding="utf-8") as stream:
            stream.write(output)


if __name__ == "__main__":
    main()
