"""Manage downloadable bundle assets on GitHub releases.

Shared by the publication workflows so the release logic lives in one place.
Requires the gh CLI with GH_TOKEN set.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

RELEASE_TAG = os.environ.get("RELEASE_TAG") or os.environ.get("MOEX_RELEASE_TAG") or ""
SITE_ID = os.environ.get("SITE_ID", "default")
RELEASE_ASSETS_PATH = Path("data") / "sites" / SITE_ID / "release-assets.json"
UPLOAD_BATCH_SIZE = 50
GITHUB_RELEASE_ASSET_LIMIT = 1000
RELEASE_SAFETY_TARGET = 900
GITHUB_RELEASE_ASSET_BYTE_LIMIT = 2_147_483_648
HASH_CHUNK_BYTES = 1024 * 1024


def _local_assets() -> list[dict]:
    payload = json.loads(RELEASE_ASSETS_PATH.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return payload.get("assets", payload)
    return payload


def _asset_release_tag(asset: dict) -> str:
    release_tag = str(asset.get("release_tag") or RELEASE_TAG).strip()
    if not release_tag:
        raise ValueError("release asset entry is missing release_tag and no fallback release tag is configured")
    return release_tag


def _group_assets_by_release_tag(assets: list[dict] | None = None) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for asset in assets or _local_assets():
        grouped[_asset_release_tag(asset)].append(asset)
    return dict(grouped)


def _asset_zip_names(asset: dict, *, include_legacy: bool = True) -> list[str]:
    names = [asset["asset_name"]]
    if include_legacy:
        # Legacy alias names stay published on the release when already present so old download URLs keep working.
        names.extend(asset.get("legacy_asset_names", []))
    return [name for name in dict.fromkeys(names) if name and name.endswith(".zip")]


def _desired_zip_names(release_tag: str | None = None) -> set[str]:
    if release_tag is None:
        return {name for asset in _local_assets() for name in _asset_zip_names(asset, include_legacy=False)}
    return {
        name
        for asset in _group_assets_by_release_tag().get(release_tag, [])
        for name in _asset_zip_names(asset, include_legacy=False)
    }


def _repository_slug() -> str:
    # Actions exports GITHUB_REPOSITORY; the placeholder form resolves from the
    # checked-out remote everywhere else.
    return os.environ.get("GITHUB_REPOSITORY") or ":owner/:repo"


def _release_zip_digests(release_tag: str, *, allow_missing: bool = False) -> dict[str, str]:
    """Map each published ZIP asset to its sha256 digest.

    The digest is what makes a stale upload visible. Asset names are derived
    from bundle identity and stay stable across rebuilds, so a name that is
    already present says nothing about whether the bytes behind it are current.
    An asset GitHub reports no digest for is returned as an empty string, which
    every caller treats as "cannot be shown to match" rather than as current.
    """
    repository = _repository_slug()
    try:
        release_id = subprocess.check_output(
            ["gh", "api", f"repos/{repository}/releases/tags/{release_tag}", "--jq", ".id"],
            text=True,
            encoding="utf-8",
            stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError:
        if allow_missing:
            return {}
        raise
    raw_payload = subprocess.check_output(
        [
            "gh", "api", "--paginate",
            f"repos/{repository}/releases/{release_id}/assets?per_page=100",
            "--jq", '.[] | [.name, (.digest // "")] | @tsv',
        ],
        text=True,
        encoding="utf-8",
    )
    digests: dict[str, str] = {}
    for line in raw_payload.splitlines():
        name, _, digest = line.partition("\t")
        if name.endswith(".zip"):
            digests[name] = digest.removeprefix("sha256:")
    return digests


def _release_zip_names(release_tag: str, *, allow_missing: bool = False) -> list[str]:
    return sorted(_release_zip_digests(release_tag, allow_missing=allow_missing))


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure() -> int:
    for release_tag in sorted(_group_assets_by_release_tag()):
        view = subprocess.run(
            ["gh", "release", "view", release_tag],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if view.returncode != 0:
            subprocess.run(
                [
                    "gh", "release", "create", release_tag,
                    "--title", f"Downloadable exam bundles ({release_tag})",
                    "--notes", "Human-friendly exam bundles with compatibility aliases",
                ],
                check=True,
            )
    return 0


def coverage() -> int:
    bootstrap_required = False
    stale_required = False
    total_expected = 0
    total_release = 0
    total_stale = 0
    for release_tag in sorted(_group_assets_by_release_tag()):
        expected = _desired_zip_names(release_tag)
        release_digests = _release_zip_digests(release_tag, allow_missing=True)
        current = set(release_digests)
        published = {
            name
            for asset in _group_assets_by_release_tag().get(release_tag, [])
            for name in _asset_zip_names(asset, include_legacy=True)
        }
        # A published asset is only current when its bytes hash to the checksum
        # the site catalog hands users to verify the download against.
        stale = sorted(
            name
            for asset in _group_assets_by_release_tag().get(release_tag, [])
            if asset.get("checksum")
            for name in _asset_zip_names(asset, include_legacy=False)
            if name in release_digests and release_digests[name] != asset["checksum"]
        )
        total_expected += len(expected)
        total_release += len(current)
        total_stale += len(stale)
        if len(expected) > GITHUB_RELEASE_ASSET_LIMIT or len(current) > GITHUB_RELEASE_ASSET_LIMIT:
            raise ValueError(
                f"release {release_tag} exceeds GitHub's {GITHUB_RELEASE_ASSET_LIMIT}-asset limit "
                f"(expected={len(expected)}, current={len(current)})"
            )
        if len(expected) > RELEASE_SAFETY_TARGET:
            print(f"warning: release {release_tag} exceeds the {RELEASE_SAFETY_TARGET}-asset safety target")
        missing = expected - current
        unexpected = current - published
        # Missing or unexpected names mean the release inventory itself is
        # wrong, which only a bootstrap can rebuild. Stale bytes are repaired
        # by the ordinary upload step, so they are reported separately.
        release_bootstrap_required = bool(missing or unexpected)
        bootstrap_required = bootstrap_required or release_bootstrap_required
        stale_required = stale_required or bool(stale)
        print(
            f"release_tag: {release_tag}, expected zips: {len(expected)}, release zips: {len(current)}, "
            f"stale zips: {len(stale)}, bootstrap_required: {release_bootstrap_required}"
        )
        if release_bootstrap_required:
            for name in sorted(missing):
                print(f"missing from release {release_tag}: {name}")
            for name in sorted(unexpected):
                print(f"unexpected in release {release_tag}: {name}")
        for name in stale:
            print(f"stale bytes in release {release_tag}: {name}")
    print(
        f"total expected zips: {total_expected}, total release zips: {total_release}, "
        f"total stale zips: {total_stale}, bootstrap_required: {bootstrap_required}, "
        f"stale_required: {stale_required}"
    )
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(f"bootstrap_required={str(bootstrap_required).lower()}\n")
            handle.write(f"stale_required={str(stale_required).lower()}\n")
    return 0


def upload() -> int:
    missing = []
    drifted = []
    for release_tag, assets in sorted(_group_assets_by_release_tag().items()):
        release_digests = _release_zip_digests(release_tag, allow_missing=True)
        remote_names = set(release_digests)
        if len(remote_names) > GITHUB_RELEASE_ASSET_LIMIT:
            print(
                f"release {release_tag} already has {len(remote_names)} ZIP assets; refusing to upload beyond "
                f"the {GITHUB_RELEASE_ASSET_LIMIT}-asset limit",
                file=sys.stderr,
            )
            return 1
        upload_specs: list[str] = []
        for asset in assets:
            local_path = Path(asset["storage_key"])
            zip_names = _asset_zip_names(asset, include_legacy=False)
            if local_path.exists() and local_path.stat().st_size >= GITHUB_RELEASE_ASSET_BYTE_LIMIT:
                print(
                    f"release asset {local_path} is {local_path.stat().st_size} bytes; "
                    f"GitHub requires assets smaller than {GITHUB_RELEASE_ASSET_BYTE_LIMIT} bytes",
                    file=sys.stderr,
                )
                return 1
            if not local_path.exists():
                if any(name not in remote_names for name in zip_names):
                    missing.append(str(local_path))
                continue
            recorded_checksum = asset.get("checksum", "")
            # Asset names are identity-derived and survive a rebuild unchanged,
            # so presence on the release proves nothing about the bytes. Compare
            # against the checksum the catalog publishes instead, falling back
            # to presence for legacy entries that carry no checksum at all.
            if recorded_checksum:
                outdated = [name for name in zip_names if release_digests.get(name) != recorded_checksum]
            else:
                outdated = [name for name in zip_names if name not in release_digests]
            if not outdated:
                continue
            local_digest = _file_digest(local_path)
            if recorded_checksum and local_digest != recorded_checksum:
                # Publishing here would serve bytes that fail the checksum the
                # site hands users. The catalog has to be regenerated first.
                drifted.append(f"{local_path}: catalog records {recorded_checksum}, file hashes {local_digest}")
                continue
            for name in outdated:
                spec = f"{local_path}#{name}"
                upload_specs.append(spec)
                remote_names.add(name)
        if len(remote_names) > GITHUB_RELEASE_ASSET_LIMIT:
            print(
                f"release {release_tag} would exceed {GITHUB_RELEASE_ASSET_LIMIT} ZIP assets after upload",
                file=sys.stderr,
            )
            return 1
        for start in range(0, len(upload_specs), UPLOAD_BATCH_SIZE):
            batch = upload_specs[start:start + UPLOAD_BATCH_SIZE]
            subprocess.run(["gh", "release", "upload", release_tag, *batch, "--clobber"], check=True)
    if missing:
        print("Missing expected bundle files before upload:\n" + "\n".join(missing), file=sys.stderr)
        return 1
    if drifted:
        print(
            "Bundle files disagree with the checksums the site catalog publishes; "
            "regenerate the catalog before uploading:\n" + "\n".join(drifted),
            file=sys.stderr,
        )
        return 1
    return 0


def prune() -> int:
    for release_tag in sorted(_group_assets_by_release_tag()):
        desired = {
            name
            for asset in _group_assets_by_release_tag().get(release_tag, [])
            for name in _asset_zip_names(asset, include_legacy=True)
        }
        for name in _release_zip_names(release_tag, allow_missing=True):
            if name not in desired:
                subprocess.run(["gh", "release", "delete-asset", release_tag, name, "--yes"], check=True)
    return 0


def primary_only_check() -> int:
    """Fail closed unless every active Release contains only current primary ZIPs."""
    grouped = _group_assets_by_release_tag()
    if not grouped:
        print("No site Release inventory to verify", file=sys.stderr)
        return 1
    failed = False
    for tag, assets in sorted(grouped.items()):
        remote = _release_zip_digests(tag)
        expected = {asset["asset_name"] for asset in assets}
        unexpected = sorted(remote.keys() - expected)
        invalid = sorted(
            asset["asset_name"] for asset in assets
            if not asset.get("checksum") or remote.get(asset["asset_name"]) != asset["checksum"]
        )
        print(f"{tag}: primary ZIPs={len(expected)}, unexpected ZIPs={len(unexpected)}, "
              f"missing or unverifiable primaries={len(invalid)}")
        failed = failed or bool(unexpected or invalid)
        for name in unexpected:
            print(f"unexpected hosted ZIP: {tag}/{name}", file=sys.stderr)
        for name in invalid:
            print(f"missing or unverifiable primary: {tag}/{name}", file=sys.stderr)
    return int(failed)


COMMANDS = {
    "ensure": ensure, "coverage": coverage, "upload": upload, "prune": prune,
    "primary-only-check": primary_only_check,
}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        print(f"usage: release_assets.py {{{'|'.join(COMMANDS)}}}", file=sys.stderr)
        return 2
    return COMMANDS[sys.argv[1]]()


if __name__ == "__main__":
    raise SystemExit(main())
