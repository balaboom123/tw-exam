"""Successful event sync receipts and their site-owned public projection."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.models import NormalizedPaper, SourceExamPage, SyncFailure
from app.paths import ProviderPaths, provider_paths
from app.source_inventory import load_source_inventory


def _timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid sync timestamp: {value!r}") from exc
    if parsed.utcoffset() is None:
        raise ValueError(f"Sync timestamp must include a timezone: {value!r}")
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_sync_status(provider: ProviderPaths) -> dict[str, str]:
    path = provider.sync_status_path
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != 1
        or payload.get("provider_id") != provider.provider_id
        or not isinstance(payload.get("events"), dict)
    ):
        raise ValueError(f"Invalid provider sync status: {path}")
    events = payload["events"]
    if any(not isinstance(event, str) or not event for event in events):
        raise ValueError(f"Invalid event ID in provider sync status: {path}")
    return {event: _timestamp(timestamp) for event, timestamp in events.items()}


def record_successful_sync(
    provider: ProviderPaths,
    pages: Sequence[SourceExamPage],
    failures: Sequence[SyncFailure],
    *,
    synced_at: str | None = None,
) -> None:
    failed = {failure.source_exam_id for failure in failures}
    successful = {page.source_exam_id for page in pages if page.source_exam_id not in failed}
    if not successful:
        return
    events = load_sync_status(provider)
    timestamp = _timestamp(synced_at or datetime.now(UTC).isoformat())
    events.update({event: timestamp for event in successful})
    provider.sync_status_path.parent.mkdir(parents=True, exist_ok=True)
    provider.sync_status_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provider_id": provider.provider_id,
                "events": events,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def add_frontend_provenance(
    repo_root: Path, papers: Iterable[NormalizedPaper], frontend_bundles: list[dict[str, Any]]
) -> None:
    events_by_bundle: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for paper in papers:
        events_by_bundle[paper.bundle_id or paper.canonical_id].add(
            (paper.provider_id, paper.source_exam_id)
        )
    metadata = project_provenance(repo_root, events_by_bundle)
    for bundle in frontend_bundles:
        bundle.update(metadata.get(bundle["id"], {}))


def project_provenance(
    repo_root: Path, events_by_bundle: Mapping[str, set[tuple[str, str]]]
) -> dict[str, dict[str, Any]]:
    # Legacy local fixtures and isolated publication sandboxes may have no
    # reviewed inventory; do not invent attribution or sync dates for them.
    if not (repo_root / "catalog/source-inventory.json").is_file():
        return {}
    entries = {
        entry["provider_id"]: entry for entry in load_source_inventory(repo_root)["providers"]
    }
    providers = {provider for events in events_by_bundle.values() for provider, _ in events}
    receipts = {
        provider: load_sync_status(provider_paths(repo_root, provider)) for provider in providers
    }
    result: dict[str, dict[str, Any]] = {}
    for bundle_id, events in events_by_bundle.items():
        metadata: dict[str, Any] = {}
        sources = []
        for provider in sorted({provider for provider, _ in events}):
            if provider not in entries:
                raise ValueError(f"Missing reviewed source inventory entry for provider {provider}")
            entry = entries[provider]
            url = entry["official_source_urls"][0]
            sources.append({"name": entry.get("source_name") or urlsplit(url).hostname, "url": url})
        if sources:
            metadata["sources"] = sources
        timestamps = [
            receipts[provider][event] for provider, event in events if event in receipts[provider]
        ]
        if timestamps:
            metadata["updated"] = max(timestamps)
        result[bundle_id] = metadata
    return result
