"""Machine-validated source scope and local-state inventory.

The inventory is a reviewed catalog input, not a generated source manifest. It
records what the repository claims to cover and why; validation only proves
that the claim has not drifted from the current local provider state. Missing
live discovery manifests remain visible and can be made fatal with the
explicit strict option.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from app.manifest import load_source_manifest
from app.paths import provider_paths
from app.provider_index import PAPER_SOURCE_EXAM_ID, PAPER_YEAR_ROC, load_provider_index
from app.site_registry import get_site_config
from app.state import load_provider_failures

INVENTORY_SCHEMA_VERSION = 1
INVENTORY_PATH = Path("catalog/source-inventory.json")
_ALLOWED_STATUSES = {"covered", "partial", "blocked", "intentionally_out_of_scope"}
_ALLOWED_AVAILABILITY_MODES = {"observed_range", "current_scope", "unknown"}
_ALLOWED_DISCOVERY_STATUSES = {"present", "missing", "blocked", "not_applicable"}
_ALLOWED_DISCOVERY_COVERAGE = {"complete", "partial", "unknown"}


def _error(path: Path, message: str) -> ValueError:
    return ValueError(f"invalid source inventory {path}: {message}")


def _require_text(value: Any, field: str, *, path: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error(path, f"{field} must be a non-empty string")
    return value


def _require_string_list(
    value: Any,
    field: str,
    *,
    path: Path,
    allow_empty: bool = False,
) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise _error(path, f"{field} must be a non-empty array")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise _error(path, f"{field} must contain non-empty strings")
    if len(value) != len(set(value)):
        raise _error(path, f"{field} must not contain duplicates")
    return list(value)


def _require_years(value: Any, field: str, *, path: Path) -> list[int]:
    if not isinstance(value, list) or any(
        isinstance(item, bool) or not isinstance(item, int) for item in value
    ):
        raise _error(path, f"{field} must be an array of integers")
    if any(item < 1900 or item > 2200 for item in value):
        raise _error(path, f"{field} contains an invalid AD year")
    if value != sorted(set(value)):
        raise _error(path, f"{field} must be sorted and unique")
    return list(value)


def _validate_available_years(value: Any, *, path: Path) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _error(path, "available_years must be an object")
    mode = _require_text(value.get("mode"), "available_years.mode", path=path)
    if mode not in _ALLOWED_AVAILABILITY_MODES:
        raise _error(path, f"unsupported available_years.mode {mode!r}")
    _require_text(value.get("note"), "available_years.note", path=path)
    if mode == "unknown":
        if value.get("start_ad") is not None or value.get("end_ad") is not None:
            raise _error(path, "unknown available years cannot specify start_ad or end_ad")
    else:
        for field in ("start_ad", "end_ad"):
            year = value.get(field)
            if isinstance(year, bool) or not isinstance(year, int) or not 1900 <= year <= 2200:
                raise _error(path, f"available_years.{field} must be an AD year")
        if value["start_ad"] > value["end_ad"]:
            raise _error(path, "available_years.start_ad must not exceed end_ad")
    return dict(value)


def _validate_entry(value: Any, *, path: Path, provider: bool) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _error(path, "every entry must be an object")
    identifier_field = "provider_id" if provider else "source_id"
    _require_text(value.get(identifier_field), identifier_field, path=path)
    urls = _require_string_list(
        value.get("official_source_urls"),
        "official_source_urls",
        path=path,
        allow_empty=not provider,
    )
    if any(not url.startswith("https://") for url in urls):
        raise _error(path, "official_source_urls must use HTTPS")
    _require_text(value.get("exam_category"), "exam_category", path=path)
    status = _require_text(value.get("status"), "status", path=path)
    if status not in _ALLOWED_STATUSES:
        raise _error(path, f"unsupported status {status!r}")
    _require_text(value.get("status_reason"), "status_reason", path=path)
    _validate_available_years(value.get("available_years"), path=path)
    _require_string_list(value.get("evidence"), "evidence", path=path)
    _require_string_list(value.get("restrictions"), "restrictions", path=path, allow_empty=True)
    if not provider:
        return dict(value)

    _require_years(value.get("local_years"), "local_years", path=path)
    local_state = value.get("local_state")
    if not isinstance(local_state, dict):
        raise _error(path, "local_state must be an object")
    for field in ("raw_event_pages", "normalized_paper_records", "sync_failures"):
        count = local_state.get(field)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise _error(path, f"local_state.{field} must be a non-negative integer")
    discovery = value.get("discovery_snapshot")
    if not isinstance(discovery, dict):
        raise _error(path, "discovery_snapshot must be an object")
    _require_text(discovery.get("manifest_path"), "discovery_snapshot.manifest_path", path=path)
    discovery_status = _require_text(
        discovery.get("status"), "discovery_snapshot.status", path=path
    )
    if discovery_status not in _ALLOWED_DISCOVERY_STATUSES:
        raise _error(path, f"unsupported discovery_snapshot.status {discovery_status!r}")
    discovery_coverage = _require_text(
        discovery.get("coverage"), "discovery_snapshot.coverage", path=path
    )
    if discovery_coverage not in _ALLOWED_DISCOVERY_COVERAGE:
        raise _error(path, f"unsupported discovery_snapshot.coverage {discovery_coverage!r}")
    if discovery_status == "blocked":
        if status not in {"partial", "blocked"}:
            raise _error(path, "blocked discovery requires a partial or blocked provider")
        if discovery_coverage != "unknown":
            raise _error(path, "blocked discovery coverage must be unknown")
        expected_ledger = f"catalog/source-coverage/{value['provider_id']}.json"
        if expected_ledger not in value["evidence"]:
            raise _error(
                path, f"blocked discovery requires exact provider coverage ledger {expected_ledger}"
            )
    return dict(value)


def load_source_inventory(repo_root: Path) -> dict[str, Any]:
    path = repo_root / INVENTORY_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _error(path, str(exc)) from exc
    if not isinstance(payload, dict):
        raise _error(path, "top-level value must be an object")
    if payload.get("schema_version") != INVENTORY_SCHEMA_VERSION:
        raise _error(path, f"unsupported schema_version {payload.get('schema_version')!r}")
    _require_text(payload.get("inventory_version"), "inventory_version", path=path)
    _require_text(payload.get("captured_at"), "captured_at", path=path)
    if payload.get("site_id") != "default":
        raise _error(path, "site_id must be default")
    providers = payload.get("providers")
    candidates = payload.get("candidates")
    if not isinstance(providers, list) or not isinstance(candidates, list):
        raise _error(path, "providers and candidates must be arrays")
    provider_entries = [_validate_entry(value, path=path, provider=True) for value in providers]
    candidate_entries = [_validate_entry(value, path=path, provider=False) for value in candidates]
    provider_ids = [entry["provider_id"] for entry in provider_entries]
    if len(provider_ids) != len(set(provider_ids)):
        raise _error(path, "provider IDs must be unique")
    source_ids = [entry["source_id"] for entry in candidate_entries]
    if len(source_ids) != len(set(source_ids)):
        raise _error(path, "candidate source IDs must be unique")
    return payload


def _validate_evidence_paths(repo_root: Path, inventory: dict[str, Any]) -> None:
    for entry in inventory["providers"] + inventory["candidates"]:
        identifier = entry.get("provider_id", entry.get("source_id", "unknown"))
        for evidence in entry["evidence"]:
            if evidence.startswith("https://"):
                continue
            evidence_path = repo_root / evidence
            if not evidence_path.is_file():
                raise ValueError(
                    f"source inventory evidence does not exist for {identifier}: {evidence}"
                )


def _local_observation(repo_root: Path, provider_id: str) -> dict[str, Any]:
    """Use the current index or scan yearly files without loading catalogs."""
    provider = provider_paths(repo_root, provider_id)
    index = load_provider_index(provider)
    if index is not None:
        raw_events = index["raw_events"]
        papers = index["papers"]
        event_ids: set[tuple[str, int]] = {(row[0], row[1]) for row in raw_events}
        event_ids.update((row[PAPER_SOURCE_EXAM_ID], row[PAPER_YEAR_ROC] + 1911) for row in papers)
        return {
            "years": sorted({year for _source_id, year in event_ids}),
            "raw_event_pages": len(raw_events),
            "normalized_paper_records": len(papers),
            "sync_failures": len(load_provider_failures(provider)),
            "event_ids": event_ids,
        }
    years: set[int] = set()
    event_ids = set()
    counts: dict[str, int] = {}
    for label, directory, year_field, offset in (
        ("raw_event_pages", provider.exams_dir, "year_ad", 0),
        ("normalized_paper_records", provider.papers_dir, "year_roc", 1911),
    ):
        count = 0
        for path in sorted(directory.glob("*.json")):
            records = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(records, list):
                raise ValueError(f"provider state is not an array: {path}")
            count += len(records)
            for record in records:
                year = record[year_field] + offset
                years.add(year)
                event_ids.add((record.get("source_exam_id", ""), year))
        counts[label] = count
    return {
        "years": sorted(years),
        **counts,
        "sync_failures": len(load_provider_failures(provider)),
        "event_ids": event_ids,
    }


def provider_ids_from_paths(paths: Iterable[str]) -> list[str]:
    """Return the provider IDs owning the given repository paths, in order."""
    provider_ids: list[str] = []
    for raw_path in paths:
        parts = PurePosixPath(raw_path.strip().strip("/")).parts
        if len(parts) < 3 or parts[0] != "data" or parts[1] != "providers":
            continue
        if parts[2] not in provider_ids:
            provider_ids.append(parts[2])
    return provider_ids


def check_sync_floor(repo_root: Path, provider_ids: Sequence[str]) -> dict[str, Any]:
    """Refuse a sync result that retains fewer records than the reviewed inventory.

    A scheduled sync commits whatever it scraped and pushes straight to main,
    and pushes made with GITHUB_TOKEN start no workflow, so nothing downstream
    ever inspects the result. A source that briefly serves a short listing
    therefore deletes retained events permanently and silently.

    The inventory is the reviewed floor. Growth is ordinary and passes
    untouched; dropping below the recorded event count, paper count, or year
    coverage has to be ratified by editing the inventory in a reviewed change
    rather than by an unattended job.
    """
    inventory = load_source_inventory(repo_root)
    entries = {entry["provider_id"]: entry for entry in inventory["providers"]}
    regressions: list[dict[str, Any]] = []
    checked: list[dict[str, Any]] = []
    for provider_id in provider_ids:
        entry = entries.get(provider_id)
        if entry is None:
            raise ValueError(f"source inventory has no entry for provider: {provider_id}")
        observation = _local_observation(repo_root, provider_id)
        losses: list[str] = []
        for field in ("raw_event_pages", "normalized_paper_records"):
            recorded = entry["local_state"][field]
            if observation[field] < recorded:
                losses.append(f"{field} {recorded} -> {observation[field]}")
        dropped_years = sorted(set(entry["local_years"]) - set(observation["years"]))
        if dropped_years:
            losses.append("years dropped: " + ", ".join(str(year) for year in dropped_years))
        record = {
            "provider_id": provider_id,
            "losses": losses,
            "sync_failures": observation["sync_failures"],
            "raw_event_pages": observation["raw_event_pages"],
            "normalized_paper_records": observation["normalized_paper_records"],
        }
        checked.append(record)
        if losses:
            regressions.append(record)

    if regressions:
        drift_details = "; ".join(
            f"{item['provider_id']}: {', '.join(item['losses'])} "
            f"(sync failures recorded: {item['sync_failures']})"
            for item in regressions
        )
        raise ValueError(
            "sync result drops below the reviewed source inventory floor for "
            f"{len(regressions)} provider(s): {drift_details}"
        )
    return {"providers": checked}


def validate_source_inventory(
    repo_root: Path,
    *,
    site_id: str = "default",
    require_discovery_manifests: bool = False,
) -> dict[str, Any]:
    inventory = load_source_inventory(repo_root)
    _validate_evidence_paths(repo_root, inventory)
    site_config = get_site_config(site_id)
    entries = {entry["provider_id"]: entry for entry in inventory["providers"]}
    expected = set(site_config.provider_ids)
    missing = sorted(expected - set(entries))
    extra = sorted(set(entries) - expected)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing providers: {', '.join(missing)}")
        if extra:
            details.append(f"unknown providers: {', '.join(extra)}")
        raise ValueError("source inventory provider registry mismatch: " + "; ".join(details))

    local_state_drift: list[dict[str, Any]] = []
    local_state_growth: list[dict[str, Any]] = []
    missing_manifests: list[str] = []
    blocked_discoveries: list[str] = []
    not_applicable_manifests: list[str] = []
    incomplete_manifests: list[str] = []
    manifest_event_gaps: list[dict[str, Any]] = []
    manifest_unrepresented_events: list[dict[str, Any]] = []
    for provider_id in site_config.provider_ids:
        entry = entries[provider_id]
        observation = _local_observation(repo_root, provider_id)
        # The inventory is the reviewed floor, in the same sense check_sync_floor
        # uses it: a sync that adds events or papers is ordinary and must pass.
        # Requiring exact equality here meant every successful content-changing
        # sync failed this gate and blocked the deploy, which is what happened
        # on 2026-08-06 when the first sync to complete since 06-29 added one
        # event and 24 papers. Losses still fail; growth is reported instead so
        # the inventory's staleness stays visible without gating the site.
        losses = [
            f"{field} {entry['local_state'][field]} -> {observation[field]}"
            for field in ("raw_event_pages", "normalized_paper_records")
            if observation[field] < entry["local_state"][field]
        ]
        dropped_years = sorted(set(entry["local_years"]) - set(observation["years"]))
        if dropped_years:
            losses.append("years dropped: " + ", ".join(str(year) for year in dropped_years))

        gains = [
            f"{field} {entry['local_state'][field]} -> {observation[field]}"
            for field in ("raw_event_pages", "normalized_paper_records")
            if observation[field] > entry["local_state"][field]
        ]
        added_years = sorted(set(observation["years"]) - set(entry["local_years"]))
        if added_years:
            gains.append("years added: " + ", ".join(str(year) for year in added_years))

        if losses or gains:
            record = {
                "provider_id": provider_id,
                "inventory": {
                    "years": entry["local_years"],
                    **entry["local_state"],
                },
                "actual": {
                    "years": observation["years"],
                    **{
                        field: observation[field]
                        for field in (
                            "raw_event_pages",
                            "normalized_paper_records",
                            "sync_failures",
                        )
                    },
                },
            }
            if losses:
                local_state_drift.append({**record, "losses": losses})
            else:
                local_state_growth.append({**record, "gains": gains})

        discovery = entry["discovery_snapshot"]
        manifest_path = repo_root / discovery["manifest_path"]
        if discovery["status"] == "present":
            if not manifest_path.exists():
                raise ValueError(
                    f"source inventory marks a missing manifest as present: {provider_id}"
                )
            manifest = load_source_manifest(manifest_path, provider_id=provider_id)
            manifest_events = {
                (str(code), int(item.get("year_ad", 0))) for code, item in manifest.exams.items()
            }
            missing_events = sorted(observation["event_ids"] - manifest_events)
            if missing_events:
                manifest_event_gaps.append(
                    {
                        "provider_id": provider_id,
                        "enforced": discovery["coverage"] == "complete",
                        "missing_events": [[code, year] for code, year in missing_events],
                    }
                )
            unrepresented_events = sorted(manifest_events - observation["event_ids"])
            if unrepresented_events:
                manifest_unrepresented_events.append(
                    {
                        "provider_id": provider_id,
                        "events": [[code, year] for code, year in unrepresented_events],
                    }
                )
            if discovery["coverage"] != "complete":
                incomplete_manifests.append(provider_id)
        elif discovery["status"] == "missing":
            if manifest_path.exists():
                raise ValueError(
                    f"source inventory marks an existing manifest as missing: {provider_id}"
                )
            missing_manifests.append(provider_id)
        elif discovery["status"] == "blocked":
            if manifest_path.exists():
                raise ValueError(
                    f"source inventory blocked discovery has a manifest: {provider_id}"
                )
            blocked_discoveries.append(provider_id)
        elif discovery["status"] == "not_applicable":
            if manifest_path.exists():
                raise ValueError(
                    f"source inventory marks an existing manifest as not applicable: {provider_id}"
                )
            not_applicable_manifests.append(provider_id)

    if local_state_drift:
        drift_details = "; ".join(
            f"{item['provider_id']} ({', '.join(item['losses'])})" for item in local_state_drift
        )
        raise ValueError(
            f"source inventory local state regressed for "
            f"{len(local_state_drift)} provider(s): {drift_details}"
        )
    enforced_manifest_event_gaps = [gap for gap in manifest_event_gaps if gap["enforced"]]
    if enforced_manifest_event_gaps:
        raise ValueError(
            "source discovery manifest omits local events for "
            f"{len(enforced_manifest_event_gaps)} provider(s)"
        )
    if require_discovery_manifests and (
        missing_manifests
        or not_applicable_manifests
        or incomplete_manifests
        or manifest_unrepresented_events
    ):
        unresolved = sorted(
            set(missing_manifests)
            | set(not_applicable_manifests)
            | set(incomplete_manifests)
            | {item["provider_id"] for item in manifest_unrepresented_events}
        )
        coverage_gaps = ", ".join(
            f"{item['provider_id']} ({len(item['events'])} unrepresented event(s))"
            for item in manifest_unrepresented_events
        )
        details = []
        if missing_manifests:
            details.append(f"missing manifests: {', '.join(missing_manifests)}")
        if not_applicable_manifests:
            details.append(f"not-applicable manifests: {', '.join(not_applicable_manifests)}")
        if incomplete_manifests:
            details.append(f"incomplete manifests: {', '.join(incomplete_manifests)}")
        if coverage_gaps:
            details.append(f"local source coverage gaps: {coverage_gaps}")
        raise ValueError(
            "complete source discovery remains unresolved for "
            f"{len(unresolved)} provider(s): {'; '.join(details)}"
        )

    return {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "site_id": site_id,
        "provider_count": len(entries),
        "candidate_count": len(inventory["candidates"]),
        "discovery_manifests_present": len(entries)
        - len(missing_manifests)
        - len(blocked_discoveries)
        - len(not_applicable_manifests),
        "discovery_manifests_missing": missing_manifests,
        "discovery_manifests_blocked": blocked_discoveries,
        "discovery_manifests_not_applicable": not_applicable_manifests,
        "discovery_manifests_incomplete": incomplete_manifests,
        "require_discovery_manifests": require_discovery_manifests,
        "local_state_drift": local_state_drift,
        "local_state_growth": local_state_growth,
        "manifest_event_gaps": manifest_event_gaps,
        "manifest_unrepresented_events": manifest_unrepresented_events,
    }
