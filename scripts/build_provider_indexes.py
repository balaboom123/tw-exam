"""Build or fully verify generated provider indexes from retained source files."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.paths import provider_paths
from app.provider_index import build_provider_index_from_files, load_provider_index, write_provider_index
from app.site_registry import get_site_config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--provider", action="append", default=[])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    provider_ids = args.provider or get_site_config("default").provider_ids
    paper_count = 0
    for provider_id in provider_ids:
        provider = provider_paths(args.repo_root, provider_id)
        rebuilt = build_provider_index_from_files(provider)
        if args.check:
            if load_provider_index(provider) != rebuilt:
                raise ValueError(f"provider index differs from source files: {provider_id}")
        else:
            write_provider_index(provider, rebuilt)
        paper_count += len(rebuilt["papers"])
    action = "verified" if args.check else "built"
    print(f"{action} {len(provider_ids)} provider indexes for {paper_count} papers")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(f"provider index failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
