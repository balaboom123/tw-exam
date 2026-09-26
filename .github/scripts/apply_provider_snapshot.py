"""Apply one sync artifact only while its provider baseline is still current."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.providers.registry import get_provider


def apply_snapshot(
    repo_root: Path, snapshot: Path, provider_id: str, base: str,
    *, require_publish_plan: bool = False,
) -> None:
    get_provider(provider_id)
    if not re.fullmatch(r"[0-9a-f]{40}", base):
        raise ValueError("Sync baseline must be a full git commit SHA")
    provider_path = Path("data/providers") / provider_id
    incoming = snapshot / provider_path
    plan = snapshot / ".tmp/site-publish-plan.json"
    if not (incoming / "index.json").is_file():
        raise ValueError(f"Snapshot has no provider index for {provider_id}")
    if any(path.is_symlink() for path in snapshot.rglob("*")):
        raise ValueError("Provider snapshots must not contain symbolic links")
    if require_publish_plan and not plan.is_file():
        raise ValueError("Snapshot has no site publication plan")
    result = subprocess.run(
        ["git", "diff", "--quiet", base, "HEAD", "--", provider_path.as_posix()],
        cwd=repo_root, check=False,
    )
    if result.returncode:
        raise ValueError(
            f"Provider {provider_id} changed after this sync started, or its baseline is unavailable; "
            "rerun the caller against current main"
        )
    destination = repo_root / provider_path
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(incoming, destination)
    output_plan = repo_root / ".tmp/site-publish-plan.json"
    output_plan.parent.mkdir(parents=True, exist_ok=True)
    if plan.is_file():
        shutil.copyfile(plan, output_plan)
    else:
        output_plan.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--require-publish-plan", action="store_true")
    args = parser.parse_args()
    try:
        apply_snapshot(
            args.repo_root, args.snapshot, args.provider, args.base,
            require_publish_plan=args.require_publish_plan,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
