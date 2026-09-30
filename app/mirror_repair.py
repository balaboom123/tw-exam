"""Recover URL collisions without deleting retained records or guessing payloads."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from app.models import NormalizedPaper
from app.normalizer import load_alias_rules
from app.paths import provider_paths
from app.providers.base import SourceProvider
from app.providers.registry import get_provider
from app.publisher import write_provider_state
from app.site_registry import get_site_config
from app.state import load_provider_state
from app.storage import MirrorStore
from app.sync import _download_validated, _existing_mirrored


def colliding_mirror_records(papers: list[NormalizedPaper]) -> list[list[NormalizedPaper]]:
    by_path: dict[str, list[NormalizedPaper]] = defaultdict(list)
    for paper in papers:
        if paper.storage_key:
            by_path[paper.storage_key].append(paper)
    return [
        group
        for group in by_path.values()
        if len({paper.download_url_source for paper in group}) > 1
    ]


def repair_mirror_collisions(
    repo_root: Path, *, site_id: str, apply: bool = False
) -> dict[str, Any]:
    report: dict[str, Any] = {"site_id": site_id, "applied": apply, "providers": [], "errors": []}
    store = MirrorStore(repo_root / "mirror")
    for provider_id in get_site_config(site_id).provider_ids:
        paths = provider_paths(repo_root, provider_id)
        pages, catalog, failures = load_provider_state(paths)
        groups = colliding_mirror_records(catalog.papers)
        if not groups:
            continue
        papers = [paper for group in groups for paper in group]
        entry: dict[str, Any] = {
            "provider_id": provider_id,
            "colliding_paths": len(groups),
            "source_records": len(papers),
            "source_urls": len({p.download_url_source for p in papers}),
            "changed_checksums": 0,
            "repaired": False,
        }
        report["providers"].append(entry)
        if not apply:
            continue
        client = get_provider(provider_id)
        unique = {(paper.download_url_source, paper.file_type): paper for paper in papers}

        # MirrorStore is mutable; workers only download. Cache reads and all
        # writes remain serial by preloading already verified repair payloads.
        cache: dict[tuple[str, str], tuple[bytes, str]] = {}
        pending = []
        for key, paper in unique.items():
            digest = hashlib.sha256(key[0].encode()).hexdigest()[:16]
            prefix = f"{Path(paper.storage_key).with_suffix('')}--source-{digest}"
            existing = _existing_mirrored(store, prefix, key[1])
            if existing is None:
                pending.append((key, paper))
            else:
                cache[key] = (existing.path.read_bytes(), existing.path.suffix)

        def download(
            item: tuple[tuple[str, str], NormalizedPaper], *, source_client: SourceProvider = client
        ) -> tuple[bytes, str]:
            (url, role), _paper = item
            return _download_validated(source_client, role, url)

        provider_errors = []
        with ThreadPoolExecutor(
            max_workers=max(1, min(4, getattr(client, "max_concurrency", 4)))
        ) as pool:
            futures = [(item[0], pool.submit(download, item)) for item in pending]
            for key, future in futures:
                try:
                    cache[key] = future.result()
                except Exception as exc:
                    provider_errors.append(
                        {"provider": provider_id, "url": key[0], "error": str(exc)}
                    )
        replacements = {}
        for paper in papers:
            key = (paper.download_url_source, paper.file_type)
            if key not in cache:
                continue
            data, suffix = cache[key]
            digest = hashlib.sha256(paper.download_url_source.encode()).hexdigest()[:16]
            prefix = f"{Path(paper.storage_key).with_suffix('')}--source-{digest}"
            stored = store.write_bytes(f"{prefix}{suffix}", data, overwrite=True)
            entry["changed_checksums"] += stored.checksum != paper.checksum
            paper.storage_key = stored.storage_key
            paper.checksum = stored.checksum
            if paper.download_url_mirror:
                base = paper.download_url_mirror.rsplit("/", 1)[0]
                paper.download_url_mirror = f"{base}/{stored.storage_key.replace('/', '__')}"
            replacements[
                (
                    paper.source_exam_id,
                    paper.year_roc,
                    paper.category_code,
                    paper.subject_code,
                    paper.file_type,
                    paper.download_url_source,
                )
            ] = {
                "storage_key": stored.storage_key,
                "checksum": stored.checksum,
                "asset_name": stored.storage_key.replace("/", "__"),
            }
        store.flush_dedupe_index()
        if provider_errors:
            report["errors"].extend(provider_errors)
            continue  # Preserve this provider's complete state until every URL is verified.
        for page in pages:
            for raw_paper in page.papers:
                for role, url in raw_paper.files.items():
                    raw_key = (
                        page.source_exam_id,
                        page.year_roc,
                        raw_paper.category_code,
                        raw_paper.subject_code,
                        role,
                        url,
                    )
                    if raw_key in replacements:
                        raw_paper.mirror_files[role] = replacements[raw_key]
        write_provider_state(
            paths, pages, catalog, load_alias_rules(paths.aliases_path), failures, None
        )
        entry["repaired"] = True
        print(
            f"Repaired {provider_id}: {len(papers)} references; "
            f"{entry['changed_checksums']} corrected payloads",
            flush=True,
        )
    return report
