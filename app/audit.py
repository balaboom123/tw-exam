"""Whole-catalog classification and bundle-disposition audit.

The audit is independent of the network and ZIP creation. It loads every
provider's retained normalized paper record, recomputes identity from raw
evidence, and compares it with the currently published site inventory.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.bundler import _bundle_asset_name, _legacy_asset_names
from app.classification import ExamIdentity, classify_normalized_paper, identity_fields
from app.models import BundleAsset, NormalizedCatalog
from app.normalizer import (
    _derive_canonical,
    _is_legacy_ascii_fixture,
    load_alias_rules,
    renormalize_catalog,
)
from app.paths import provider_paths, site_paths
from app.publication_quarantine import quarantined_provider_ids
from app.publisher import load_site_catalog
from app.release_tags import (
    GITHUB_RELEASE_ASSET_LIMIT,
    RELEASE_SAFETY_TARGET,
    assign_release_tags,
    physical_asset_names,
    strip_ambiguous_legacy_assets,
    validate_release_capacity,
)
from app.site_registry import get_site_config
from app.state import load_provider_state, load_site_bundles


def _jsonify_signature(value: dict[str, Any]) -> dict[str, Any]:
    return {
        **{
            key: item
            for key, item in value.items()
            if key not in {"source_exam_ids", "raw_categories"}
        },
        "source_exam_ids": sorted(value["source_exam_ids"]),
        "raw_categories": sorted(value["raw_categories"]),
    }


def _review_key(item: Any) -> tuple[str, str, str]:
    return (item.provider_id, item.source_exam_id, item.raw_category)


def build_catalog_audit(
    repo_root: Path,
    *,
    site_id: str = "default",
    include_publication_backlog: bool = False,
) -> dict[str, Any]:
    site_config = get_site_config(site_id)
    provider_reports: list[dict[str, Any]] = []
    all_papers: list[Any] = []
    identities_by_paper: dict[int, ExamIdentity] = {}
    all_review_items: list[Any] = []
    for provider_id in site_config.provider_ids:
        provider = provider_paths(repo_root, provider_id)
        raw_pages, catalog, failures = load_provider_state(provider)
        signatures: dict[str, dict[str, Any]] = {}
        confidence_counts: dict[str, int] = defaultdict(int)
        for paper in catalog.papers:
            if not paper.provider_id:
                paper.provider_id = provider_id
            identity = classify_normalized_paper(paper)
            identities_by_paper[id(paper)] = identity
            fields = identity_fields(identity)
            all_papers.append(paper)
            confidence_counts[identity.confidence] += 1
            signature = identity.signature
            signatures.setdefault(
                signature,
                {
                    "signature": signature,
                    "bundle_id": identity.bundle_id,
                    "bundle_name": identity.bundle_name,
                    "confidence": identity.confidence,
                    "reason": identity.reason,
                    "record_count": 0,
                    "source_exam_ids": set(),
                    "raw_categories": set(),
                    "records_needing_v2_rewrite": 0,
                },
            )
            entry = signatures[signature]
            entry["record_count"] += 1
            entry["source_exam_ids"].add(paper.source_exam_id)
            entry["raw_categories"].add(paper.category_raw)
            if any(getattr(paper, field, None) != value for field, value in fields.items()):
                entry["records_needing_v2_rewrite"] += 1
        alias_rules = load_alias_rules(provider.aliases_path)
        current_review_keys = {_review_key(item) for item in catalog.review_queue}
        if all(
            paper.category_raw and paper.canonical_id and paper.canonical_name
            for paper in catalog.papers
        ):
            # Persisted records already have canonical names. Their review
            # status is exactly the identity computed in the first scan, so
            # rebuilding the entire catalog would classify every paper again.
            rebuilt_review_keys = {
                (paper.provider_id, paper.source_exam_id, paper.category_raw)
                for paper in catalog.papers
                if not _is_legacy_ascii_fixture(paper)
                and identities_by_paper[id(paper)].confidence == "review"
            }
        else:
            # Incomplete historical records can acquire a canonical name (and
            # a different identity) during renormalization. Keep that path.
            rebuilt_queue = renormalize_catalog(
                NormalizedCatalog(papers=catalog.papers, review_queue=[]),
                alias_rules,
                collect_reviews=True,
            ).review_queue
            rebuilt_review_keys = {_review_key(item) for item in rebuilt_queue}
        # renormalize_catalog only derives a canonical - and so only raises
        # needs_review - for a paper that has none yet. Every paper here has
        # one, so the rebuild above can only ever reproduce the
        # confidence=="review" rows, never the legacy-canonicalization ones a
        # sync writes while the records are still fresh. Comparing the two
        # populations directly marks each such row stale the moment it is
        # written: that is what failed audit-catalog --strict, and with it the
        # deploy, on 2026-08-06. Deriving the review need separately keeps a
        # legitimately queued row from counting as stale without demanding new
        # rows for the 117 keys this would otherwise raise across MOEX alone.
        derivable_review_keys = set()
        for paper in catalog.papers:
            raw_category = paper.category_raw or paper.exam_name_raw
            if (paper.provider_id, paper.source_exam_id, raw_category) not in current_review_keys:
                continue
            *_unused, needs_review = _derive_canonical(
                paper.source_exam_id,
                raw_category,
                paper.exam_name_raw,
                paper.year_roc + 1911,
                alias_rules,
            )
            if needs_review:
                derivable_review_keys.add((paper.provider_id, paper.source_exam_id, raw_category))
        rebuilt_review_keys |= derivable_review_keys
        all_review_items.extend(catalog.review_queue)
        provider_reports.append(
            {
                "provider_id": provider_id,
                "raw_exam_pages": len(raw_pages),
                "paper_records": len(catalog.papers),
                "distinct_source_exam_ids": len({paper.source_exam_id for paper in catalog.papers}),
                "distinct_identity_signatures": len(signatures),
                "classification_confidence": dict(sorted(confidence_counts.items())),
                "sync_failure_count": len(failures),
                "review_queue_count": len(catalog.review_queue),
                "review_queue_stale_entries": len(current_review_keys - rebuilt_review_keys),
                "review_queue_missing_entries": len(rebuilt_review_keys - current_review_keys),
                "review_queue_stale_keys": [
                    list(key) for key in sorted(current_review_keys - rebuilt_review_keys)
                ],
                "review_queue_missing_keys": [
                    list(key) for key in sorted(rebuilt_review_keys - current_review_keys)
                ],
                "signatures": [
                    _jsonify_signature(value)
                    for value in sorted(signatures.values(), key=lambda item: item["signature"])
                ],
            }
        )

    by_legacy_group: dict[tuple[str, str], dict[str, Any]] = {}
    for paper in all_papers:
        key = (paper.provider_id, paper.canonical_id)
        entry = by_legacy_group.setdefault(
            key,
            {
                "provider_id": paper.provider_id,
                "legacy_canonical_id": paper.canonical_id,
                "legacy_canonical_name": paper.canonical_name,
                "record_count": 0,
                "source_exam_ids": set(),
                "identity_signatures": set(),
                "bundle_ids": set(),
            },
        )
        identity = identities_by_paper[id(paper)]
        entry["record_count"] += 1
        entry["source_exam_ids"].add(paper.source_exam_id)
        entry["identity_signatures"].add(identity.signature)
        entry["bundle_ids"].add(identity.bundle_id)

    mixed_groups = []
    for entry in sorted(
        by_legacy_group.values(),
        key=lambda item: (item["provider_id"], item["legacy_canonical_id"]),
    ):
        if len(entry["identity_signatures"]) <= 1:
            continue
        mixed_groups.append(
            {
                "provider_id": entry["provider_id"],
                "legacy_canonical_id": entry["legacy_canonical_id"],
                "legacy_canonical_name": entry["legacy_canonical_name"],
                "record_count": entry["record_count"],
                "source_exam_ids": sorted(entry["source_exam_ids"]),
                "identity_signatures": sorted(entry["identity_signatures"]),
                "bundle_ids": sorted(entry["bundle_ids"]),
                "disposition": "split",
            }
        )

    current_bundles = load_site_bundles(site_paths(repo_root, site_id))
    papers_by_legacy_id: dict[str, list[Any]] = defaultdict(list)
    for paper in all_papers:
        papers_by_legacy_id[paper.canonical_id].append(paper)
    bundle_dispositions = []
    for bundle in current_bundles:
        bundle_id = bundle.bundle_id or bundle.canonical_id
        matching = [
            paper
            for legacy_id in {bundle.canonical_id, *bundle.legacy_canonical_ids}
            for paper in papers_by_legacy_id.get(legacy_id, [])
        ]
        identities = {identities_by_paper[id(paper)].signature for paper in matching}
        if not matching:
            disposition = "unmapped"
        elif len(identities) > 1:
            disposition = "split"
        elif bundle_id and any(
            identities_by_paper[id(paper)].bundle_id == bundle_id for paper in matching
        ):
            disposition = "keep"
        else:
            disposition = "rename"
        bundle_dispositions.append(
            {
                "current_bundle_id": bundle_id,
                "canonical_name": bundle.canonical_name,
                "record_count": len(matching),
                "identity_signature_count": len(identities),
                "disposition": disposition,
            }
        )

    planned_groups: dict[str, dict[str, Any]] = {}
    for paper in all_papers:
        identity = identities_by_paper[id(paper)]
        group = planned_groups.setdefault(
            identity.bundle_id,
            {"years": set(), "canonical_ids": set(), "provider_ids": set(), "identity": identity},
        )
        group["years"].add(paper.year_roc)
        group["canonical_ids"].add(paper.canonical_id)
        group["provider_ids"].add(paper.provider_id)

    def required_years(paper_group: dict[str, Any]) -> int:
        minimum = site_config.public_min_years
        for canonical_id in paper_group["canonical_ids"]:
            for prefix, prefix_minimum in (
                site_config.public_min_years_by_canonical_prefix or {}
            ).items():
                if canonical_id.startswith(prefix):
                    minimum = min(minimum, prefix_minimum)
        return minimum

    public_planned_groups = [
        group for group in planned_groups.values() if len(group["years"]) >= required_years(group)
    ]
    planned_bundle_count = len(public_planned_groups)
    release_target = min(max(site_config.release_shard_size, 1), RELEASE_SAFETY_TARGET)
    planned_assets = []
    for group in public_planned_groups:
        identity = group["identity"]
        asset_name = _bundle_asset_name(identity.bundle_id, structured=True)
        planned_assets.append(
            BundleAsset(
                canonical_id=identity.bundle_id,
                canonical_name=identity.bundle_name,
                years=sorted(group["years"], reverse=True),
                file_count=0,
                storage_key=f"bundles/{asset_name}",
                asset_name=asset_name,
                bundle_id=identity.bundle_id,
                legacy_asset_names=_legacy_asset_names(
                    identity.bundle_id, identity.bundle_name, asset_name, []
                ),
            )
        )
    planned_assets, _planned_alias_conflicts = strip_ambiguous_legacy_assets(planned_assets)
    planned_tagged = assign_release_tags(
        release_tag_prefix=f"{site_config.release_tag_prefix}-v2",
        existing_bundles=current_bundles,
        bundles=planned_assets,
        max_assets_per_release=release_target,
    )
    validate_release_capacity(planned_tagged)
    planned_release_asset_counts: Counter[str] = Counter()
    for bundle in planned_tagged:
        planned_release_asset_counts[bundle.release_tag] += len(physical_asset_names(bundle))
    planned_release_shards = len(planned_release_asset_counts)
    current_release_counts: Counter[str] = Counter()
    for bundle in current_bundles:
        if bundle.release_tag:
            current_release_counts[bundle.release_tag] += len(physical_asset_names(bundle))
    current_release_capacity_ok = all(
        count <= GITHUB_RELEASE_ASSET_LIMIT for count in current_release_counts.values()
    )
    records_with_identity = sum(
        1 for identity in identities_by_paper.values() if identity.bundle_id
    )
    review_queue_signatures = {
        item.classification_signature for item in all_review_items if item.classification_signature
    }
    review_records = 0
    approved_review_isolated_records = 0
    unapproved_review_records = 0
    for paper in all_papers:
        identity = identities_by_paper[id(paper)]
        if identity.confidence != "review":
            continue
        review_records += 1
        event_marker = f"event-{identity.exam_event_id}" if identity.exam_event_id else ""
        isolated = (
            bool(identity.exam_event_id)
            and identity.exam_event_id == paper.source_exam_id
            and bool(event_marker)
            and event_marker in identity.bundle_id
            and identity.signature in review_queue_signatures
        )
        if isolated:
            approved_review_isolated_records += 1
        else:
            unapproved_review_records += 1
    report = {
        "schema_version": 1,
        "catalog_version": "exam-identity-v2",
        "site_id": site_id,
        "provider_count": len(site_config.provider_ids),
        "providers_with_state": sum(
            1 for report in provider_reports if report["paper_records"] or report["raw_exam_pages"]
        ),
        "paper_records_scanned": len(all_papers),
        "records_with_identity": records_with_identity,
        "records_needing_review": review_records,
        "review_queue_entries": len(all_review_items),
        "review_queue_stale_entries": sum(
            report["review_queue_stale_entries"] for report in provider_reports
        ),
        "review_queue_missing_entries": sum(
            report["review_queue_missing_entries"] for report in provider_reports
        ),
        "review_isolation_policy": "event-specific-review-bundle-v1",
        "approved_review_isolated_records": approved_review_isolated_records,
        "unapproved_review_records": unapproved_review_records,
        "distinct_legacy_groups": len(by_legacy_group),
        "mixed_legacy_groups": mixed_groups,
        "current_bundle_count": len(current_bundles),
        "current_bundle_dispositions": bundle_dispositions,
        "current_bundle_disposition_counts": dict(
            Counter(item["disposition"] for item in bundle_dispositions)
        ),
        "current_release_asset_counts": dict(sorted(current_release_counts.items())),
        "current_release_capacity_ok": current_release_capacity_ok,
        "release_asset_limit": GITHUB_RELEASE_ASSET_LIMIT,
        "release_safety_target": RELEASE_SAFETY_TARGET,
        "planned_bundle_count": planned_bundle_count,
        "planned_release_shards": planned_release_shards,
        "planned_release_shard_target": release_target,
        "planned_release_asset_counts": dict(sorted(planned_release_asset_counts.items())),
        "all_records_covered": records_with_identity == len(all_papers),
        "providers": provider_reports,
    }
    if include_publication_backlog:
        quarantined = quarantined_provider_ids(repo_root, site_id=site_id)
        quarantined_required = sorted(quarantined.intersection(site_config.required_provider_ids))
        if quarantined_required:
            raise ValueError(
                f"Required providers cannot be quarantined for "
                f"site {site_id}: {', '.join(quarantined_required)}"
            )
        for provider_id in site_config.required_provider_ids:
            provider = provider_paths(repo_root, provider_id)
            if not provider.data_dir.exists():
                raise ValueError(
                    f"Missing provider state for {provider_id}: expected {provider.data_dir}"
                )
        if all(
            paper.category_raw and paper.canonical_id and paper.canonical_name
            for paper in all_papers
        ):
            report["publication_backlog"] = _publication_backlog_from_classified(
                site_id=site_id,
                classified_papers=(
                    (paper, identities_by_paper[id(paper)])
                    for paper in all_papers
                    if paper.provider_id not in quarantined
                ),
                current_bundles=current_bundles,
            )
        else:
            # Renormalization may derive a missing canonical name and change
            # its identity. Fall back to the authoritative site load path.
            report["publication_backlog"] = build_publication_backlog(repo_root, site_id=site_id)
    return report


def write_catalog_audit(report: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def audit_exit_code(report: dict[str, Any], *, strict: bool) -> int:
    if not strict:
        return 0
    return (
        1
        if (
            report["unapproved_review_records"]
            or report["review_queue_stale_entries"]
            or report["review_queue_missing_entries"]
            or not report["all_records_covered"]
        )
        else 0
    )


def build_publication_backlog(repo_root: Path, *, site_id: str = "default") -> dict[str, Any]:
    """Return the bundles the site projection implies but has not published.

    This must read exactly the population publish-site reads, which means the
    renormalized site catalog rather than raw provider records. Two things
    stand between the two, and skipping either invents a backlog that is not
    there: alias rules rewrite identities, so a raw record can classify into a
    bundle_id no publication would ever produce, and quarantined providers are
    withheld from the public projection on purpose - counting them reports a
    dozen providers as unpublished when the catalog is behaving as designed.
    """
    # load_site_catalog applies both, and raises if a required provider is
    # quarantined, so this cannot silently under-report either.
    normalized, _failures = load_site_catalog(repo_root, site_id=site_id)
    return _publication_backlog_from_classified(
        site_id=site_id,
        classified_papers=(
            (paper, classify_normalized_paper(paper)) for paper in normalized.papers
        ),
        current_bundles=load_site_bundles(site_paths(repo_root, site_id)),
    )


def _publication_backlog_from_classified(
    *,
    site_id: str,
    classified_papers: Iterable[tuple[Any, ExamIdentity]],
    current_bundles: list[BundleAsset],
) -> dict[str, Any]:
    site_config = get_site_config(site_id)
    groups: dict[str, dict[str, Any]] = {}
    for paper, identity in classified_papers:
        group = groups.setdefault(
            identity.bundle_id,
            {"years": set(), "canonical_ids": set(), "provider_ids": set(), "record_count": 0},
        )
        group["years"].add(paper.year_roc)
        group["canonical_ids"].add(paper.canonical_id)
        group["provider_ids"].add(paper.provider_id)
        group["record_count"] += 1

    def required_years(group: dict[str, Any]) -> int:
        minimum = site_config.public_min_years
        for canonical_id in group["canonical_ids"]:
            for prefix, prefix_minimum in (
                site_config.public_min_years_by_canonical_prefix or {}
            ).items():
                if canonical_id.startswith(prefix):
                    minimum = min(minimum, prefix_minimum)
        return minimum

    published = {bundle.bundle_id or bundle.canonical_id for bundle in current_bundles}
    outstanding = {
        bundle_id: group
        for bundle_id, group in groups.items()
        if len(group["years"]) >= required_years(group) and bundle_id not in published
    }
    by_provider: Counter[str] = Counter()
    for group in outstanding.values():
        for provider_id in group["provider_ids"]:
            by_provider[provider_id] += 1
    return {
        "site_id": site_id,
        "published_bundle_count": len(published),
        "unpublished_bundle_count": len(outstanding),
        "unpublished_record_count": sum(group["record_count"] for group in outstanding.values()),
        "provider_ids": sorted(
            {p for group in outstanding.values() for p in group["provider_ids"]}
        ),
        "unpublished_by_provider": dict(sorted(by_provider.items())),
        "affected_canonical_ids": sorted(outstanding),
        "canonical_aliases": {},
    }


def build_release_plan(
    repo_root: Path, *, site_id: str = "default", release_tag_prefix: str | None = None
) -> dict[str, Any]:
    """Build a read-only physical-asset release plan from current site state."""
    site_config = get_site_config(site_id)
    bundles = load_site_bundles(site_paths(repo_root, site_id))
    bundles, alias_conflicts = strip_ambiguous_legacy_assets(bundles)
    assigned = assign_release_tags(
        release_tag_prefix=release_tag_prefix or f"{site_config.release_tag_prefix}-v2",
        existing_bundles=bundles,
        bundles=bundles,
        max_assets_per_release=min(max(site_config.release_shard_size, 1), RELEASE_SAFETY_TARGET),
    )
    validate_release_capacity(assigned)
    by_tag: dict[str, set[str]] = defaultdict(set)
    for bundle in assigned:
        by_tag.setdefault(bundle.release_tag, set()).update(physical_asset_names(bundle))
    shards = [
        {
            "release_tag": tag,
            "asset_count": len(names),
            "asset_names": sorted(names),
        }
        for tag, names in sorted(by_tag.items())
    ]
    return {
        "schema_version": 2,
        "catalog_version": "exam-identity-v2",
        "site_id": site_id,
        "release_asset_limit": GITHUB_RELEASE_ASSET_LIMIT,
        "safety_target": RELEASE_SAFETY_TARGET,
        "ambiguous_legacy_assets": alias_conflicts,
        "shards": shards,
        "bundles": [
            {
                "bundle_id": bundle.bundle_id or bundle.canonical_id,
                "asset_name": bundle.asset_name,
                "release_tag": bundle.release_tag,
                "legacy_asset_names": bundle.legacy_asset_names,
            }
            for bundle in assigned
        ],
    }


def write_release_plan(plan: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
