from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from app.paths import legacy_paths, provider_paths, site_paths

Retention = Literal["move", "copy"]
PayloadKind = Literal["raw", "site_bundles", "site_release_assets"]


@dataclass(frozen=True)
class PlannedAction:
    source: Path
    target: Path
    retention: Retention
    payload_kind: PayloadKind
    prune_boundary: Path


@dataclass(frozen=True)
class MigrationReport:
    exit_code: int
    output: str
    conflicts: list[str]


def migrate_legacy_state(
    repo_root: Path,
    *,
    provider_id: str,
    site_id: str,
    mode: str,
) -> MigrationReport:
    actions = _plan_actions(repo_root, provider_id=provider_id, site_id=site_id)
    if mode == "dry-run":
        return _run_dry_run(repo_root, actions)
    if mode == "move":
        return _run_move(repo_root, actions)
    if mode == "verify":
        return _run_verify(repo_root, actions)
    raise ValueError(f"Unsupported migration mode: {mode}")


def _plan_actions(repo_root: Path, *, provider_id: str, site_id: str) -> list[PlannedAction]:
    legacy = legacy_paths(repo_root)
    provider = provider_paths(repo_root, provider_id)
    site = site_paths(repo_root, site_id)
    actions: list[PlannedAction] = []

    actions.extend(
        _plan_directory_moves(
            source_dir=legacy.data_dir / "exams",
            target_dir=provider.exams_dir,
            prune_boundary=legacy.data_dir,
        )
    )
    actions.extend(
        _plan_directory_moves(
            source_dir=legacy.data_dir / "papers",
            target_dir=provider.papers_dir,
            prune_boundary=legacy.data_dir,
        )
    )
    actions.extend(
        _plan_file_action(
            source=legacy.data_dir / "review-queue.json",
            target=provider.review_queue_path,
            retention="move",
            payload_kind="raw",
            prune_boundary=legacy.data_dir,
        )
    )
    actions.extend(
        _plan_file_action(
            source=legacy.data_dir / "sync-failures.json",
            target=provider.sync_failures_path,
            retention="move",
            payload_kind="raw",
            prune_boundary=legacy.data_dir,
        )
    )
    actions.extend(
        _plan_file_action(
            source=legacy.data_dir / "source-manifest.json",
            target=provider.source_manifest_path,
            retention="move",
            payload_kind="raw",
            prune_boundary=legacy.data_dir,
        )
    )
    actions.extend(
        _plan_file_action(
            source=legacy.data_dir / "aliases.json",
            target=provider.aliases_path,
            retention="copy",
            payload_kind="raw",
            prune_boundary=legacy.data_dir,
        )
    )
    actions.extend(
        _plan_file_action(
            source=legacy.bundles_path,
            target=site.bundles_path,
            retention="move",
            payload_kind="site_bundles",
            prune_boundary=legacy.data_dir,
        )
    )
    actions.extend(
        _plan_file_action(
            source=legacy.release_assets_path,
            target=site.release_assets_path,
            retention="move",
            payload_kind="site_release_assets",
            prune_boundary=legacy.data_dir,
        )
    )
    actions.extend(
        _plan_directory_moves(
            source_dir=repo_root / "mirror",
            target_dir=provider.mirror_dir,
            prune_boundary=repo_root / "mirror",
            top_level_filter=lambda candidate: candidate.name.isdigit(),
        )
    )
    actions.extend(
        _plan_bundle_moves(
            source_dir=legacy.bundle_dir,
            target_dir=site.bundle_dir,
            prune_boundary=legacy.bundle_dir,
        )
    )
    return actions


def _plan_directory_moves(
    *,
    source_dir: Path,
    target_dir: Path,
    prune_boundary: Path,
    top_level_filter: Callable[[Path], bool] | None = None,
) -> list[PlannedAction]:
    if not source_dir.exists():
        return []
    actions: list[PlannedAction] = []
    if top_level_filter is None:
        candidates = sorted(source_dir.glob("*.json"))
        for source in candidates:
            actions.append(
                PlannedAction(
                    source=source,
                    target=target_dir / source.name,
                    retention="move",
                    payload_kind="raw",
                    prune_boundary=prune_boundary,
                )
            )
        return actions

    for candidate in sorted(source_dir.iterdir()):
        if not candidate.is_dir() or not top_level_filter(candidate):
            continue
        for source in sorted(path for path in candidate.rglob("*") if path.is_file()):
            actions.append(
                PlannedAction(
                    source=source,
                    target=target_dir / source.relative_to(source_dir),
                    retention="move",
                    payload_kind="raw",
                    prune_boundary=prune_boundary,
                )
            )
    return actions


def _plan_bundle_moves(
    *, source_dir: Path, target_dir: Path, prune_boundary: Path
) -> list[PlannedAction]:
    if not source_dir.exists():
        return []
    actions: list[PlannedAction] = []
    for source in sorted(source_dir.iterdir()):
        if not source.is_file():
            continue
        actions.append(
            PlannedAction(
                source=source,
                target=target_dir / source.name,
                retention="move",
                payload_kind="raw",
                prune_boundary=prune_boundary,
            )
        )
    return actions


def _plan_file_action(
    *,
    source: Path,
    target: Path,
    retention: Retention,
    payload_kind: PayloadKind,
    prune_boundary: Path,
) -> list[PlannedAction]:
    if not source.exists():
        return []
    return [
        PlannedAction(
            source=source,
            target=target,
            retention=retention,
            payload_kind=payload_kind,
            prune_boundary=prune_boundary,
        )
    ]


def _run_dry_run(repo_root: Path, actions: list[PlannedAction]) -> MigrationReport:
    lines = ["Legacy state promotion dry-run:"]
    if not actions:
        lines.append("No legacy state found to promote.")
    for action in actions:
        operation = "COPY" if action.retention == "copy" else "MOVE"
        lines.append(
            f"{operation} {_display_path(repo_root, action.source)} "
            f"-> {_display_path(repo_root, action.target)}"
        )
    return MigrationReport(exit_code=0, output="\n".join(lines), conflicts=[])


def _run_move(repo_root: Path, actions: list[PlannedAction]) -> MigrationReport:
    lines = ["Legacy state promotion move:"]
    conflicts: list[str] = []
    for action in actions:
        if not action.source.exists():
            continue
        if action.target.exists():
            if not _targets_match(action, site_id=_site_id_from_target(action.target)):
                conflicts.append(
                    _conflict_message(
                        repo_root, action, "target already exists with different content"
                    )
                )
                continue
            if action.retention == "move":
                action.source.unlink()
                _prune_empty_parents(action.source.parent, action.prune_boundary)
                lines.append(f"Removed legacy duplicate {_display_path(repo_root, action.source)}")
            else:
                lines.append(f"Verified copied input {_display_path(repo_root, action.source)}")
            continue

        action.target.parent.mkdir(parents=True, exist_ok=True)
        if action.payload_kind == "raw" and action.retention == "move":
            action.source.replace(action.target)
            _prune_empty_parents(action.source.parent, action.prune_boundary)
            lines.append(
                f"Moved {_display_path(repo_root, action.source)} "
                f"-> {_display_path(repo_root, action.target)}"
            )
            continue

        if action.payload_kind == "raw":
            action.target.write_bytes(action.source.read_bytes())
        else:
            _write_json(
                action.target,
                _expected_json_payload(action, site_id=_site_id_from_target(action.target)),
            )
        if action.retention == "move":
            action.source.unlink()
            _prune_empty_parents(action.source.parent, action.prune_boundary)
            lines.append(
                f"Moved {_display_path(repo_root, action.source)} "
                f"-> {_display_path(repo_root, action.target)}"
            )
        else:
            lines.append(
                f"Copied {_display_path(repo_root, action.source)} "
                f"-> {_display_path(repo_root, action.target)}"
            )

    if conflicts:
        lines.extend(f"Conflict: {message}" for message in conflicts)
        lines.append(f"Move blocked by {len(conflicts)} conflict(s).")
        return MigrationReport(exit_code=1, output="\n".join(lines), conflicts=conflicts)

    lines.append("Move completed.")
    return MigrationReport(exit_code=0, output="\n".join(lines), conflicts=[])


def _run_verify(repo_root: Path, actions: list[PlannedAction]) -> MigrationReport:
    lines = ["Legacy state promotion verify:"]
    conflicts: list[str] = []
    pending: list[str] = []
    for action in actions:
        source_exists = action.source.exists()
        target_exists = action.target.exists()
        if not source_exists and not target_exists:
            continue
        if source_exists and not target_exists:
            pending.append(
                f"Pending promotion: {_display_path(repo_root, action.source)} "
                f"-> {_display_path(repo_root, action.target)}"
            )
            continue
        if not target_exists:
            continue
        if not source_exists:
            continue
        if not _targets_match(action, site_id=_site_id_from_target(action.target)):
            conflicts.append(
                _conflict_message(repo_root, action, "target differs from legacy source")
            )
            continue
        if action.retention == "move":
            pending.append(
                f"Pending legacy cleanup: {_display_path(repo_root, action.source)} "
                f"-> {_display_path(repo_root, action.target)}"
            )

    if conflicts:
        lines.extend(f"Conflict: {message}" for message in conflicts)
    if pending:
        lines.extend(pending)
    if conflicts or pending:
        lines.append(
            f"Verification failed with {len(conflicts)} "
            f"conflict(s) and {len(pending)} pending promotion(s)."
        )
        return MigrationReport(exit_code=1, output="\n".join(lines), conflicts=conflicts)

    lines.append("Verification passed.")
    return MigrationReport(exit_code=0, output="\n".join(lines), conflicts=[])


def _targets_match(action: PlannedAction, *, site_id: str) -> bool:
    if action.payload_kind == "raw":
        return _files_match(action.source, action.target)
    try:
        expected = _expected_json_payload(action, site_id=site_id)
        actual: object = json.loads(action.target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return actual == expected


def _expected_json_payload(action: PlannedAction, *, site_id: str) -> dict[str, Any]:
    source_payload = json.loads(action.source.read_text(encoding="utf-8"))
    if action.payload_kind == "site_bundles":
        bundles = (
            source_payload.get("bundles", source_payload)
            if isinstance(source_payload, dict)
            else source_payload
        )
        return {
            "schema_version": 1,
            "site_id": site_id,
            "bundles": [_rewrite_storage_key_entry(bundle, site_id=site_id) for bundle in bundles],
        }
    if action.payload_kind == "site_release_assets":
        assets = (
            source_payload.get("assets", source_payload)
            if isinstance(source_payload, dict)
            else source_payload
        )
        return {
            "schema_version": 1,
            "site_id": site_id,
            "assets": [_rewrite_storage_key_entry(asset, site_id=site_id) for asset in assets],
        }
    raise ValueError(f"Unsupported payload kind for JSON rewrite: {action.payload_kind}")


def _rewrite_storage_key_entry(entry: dict[str, Any], *, site_id: str) -> dict[str, Any]:
    rewritten = dict(entry)
    storage_key = rewritten.get("storage_key")
    if isinstance(storage_key, str):
        rewritten["storage_key"] = _rewrite_storage_key(storage_key, site_id=site_id)
    return rewritten


def _rewrite_storage_key(storage_key: str, *, site_id: str) -> str:
    scoped_prefix = f"bundles/sites/{site_id}/"
    if storage_key.startswith(scoped_prefix):
        return storage_key
    if storage_key.startswith("bundles/sites/"):
        return storage_key
    if storage_key.startswith("bundles/"):
        return scoped_prefix + storage_key.removeprefix("bundles/")
    return storage_key


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _files_match(source: Path, target: Path) -> bool:
    if source.suffix == ".json" and target.suffix == ".json":
        try:
            source_payload: object = json.loads(source.read_text(encoding="utf-8"))
            target_payload: object = json.loads(target.read_text(encoding="utf-8"))
            return source_payload == target_payload
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False
    if source.stat().st_size != target.stat().st_size:
        return False
    return _sha256(source) == _sha256(target)


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prune_empty_parents(path: Path, boundary: Path) -> None:
    current = path
    while current != boundary and current.exists():
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _display_path(repo_root: Path, path: Path) -> str:
    return path.relative_to(repo_root).as_posix()


def _conflict_message(repo_root: Path, action: PlannedAction, reason: str) -> str:
    return (
        f"{_display_path(repo_root, action.source)} -> "
        f"{_display_path(repo_root, action.target)} ({reason})"
    )


def _site_id_from_target(target: Path) -> str:
    parts = target.parts
    try:
        sites_index = parts.index("sites")
    except ValueError:
        return "default"
    if sites_index + 1 >= len(parts):
        return "default"
    return parts[sites_index + 1]
