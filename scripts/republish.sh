#!/usr/bin/env bash
# Full local republication in the only safe order: nothing is uploaded until
# the rebuilt archives pass the content audit, and nothing is pruned until
# every hosted archive matches the new inventory. Commit data/ afterwards.
set -euo pipefail

download=()
case $# in
  0)
    ;;
  1)
    if [[ "$1" == "--download" ]]; then
      download=(--download-affected-bundles)
    else
      echo "Usage: scripts/republish.sh [--download]" >&2
      exit 2
    fi
    ;;
  *)
    echo "Usage: scripts/republish.sh [--download]" >&2
    exit 2
    ;;
esac

cd "$(git rev-parse --show-toplevel)"
export GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}"

uv run --frozen python -m app migrate-catalog --repo-root . --site-id default
uv run --frozen python -m app publish-site --repo-root . --site-id default \
  --repository "$GITHUB_REPOSITORY" ${download[@]+"${download[@]}"}
uv run --frozen python -m app audit-files --repo-root . --site-id default \
  --verify-content --output .tmp/file-audit.json
uv run --frozen python -m app audit-catalog --repo-root . --site-id default --strict
uv run --frozen python scripts/validate_publication.py
uv run --frozen python .github/scripts/release_assets.py ensure
uv run --frozen python .github/scripts/release_assets.py upload
uv run --frozen python .github/scripts/release_assets.py prune

git status --short data/
echo "Hosted archives match the working tree. Review the diff, then commit data/ and push."
