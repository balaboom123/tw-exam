from __future__ import annotations

"""Event-level archive coverage audit.

This audit is intentionally separate from bundle construction.  It compares
retained raw and normalized state with the site inventory, verifies local
mirror references, and can optionally compare source discovery with local
events.  It never downloads source files or writes provider state.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Iterable

from app.bundler import public_bundle_ids_from_indexes, resolve_mirror_storage_path
from app.coverage_exceptions import (
    event_exception_for,
    failure_exception_for,
    load_coverage_exceptions,
)
from app.paths import provider_paths, site_paths
from app.provider_index import (
    PAPER_CODE,
    PAPER_FILE_TYPE,
    PAPER_SOURCE_EXAM_ID,
    PAPER_STORAGE_KEY,
    PAPER_YEAR_ROC,
    build_provider_index,
    load_provider_index,
    paper_index_bundle_id,
    paper_index_canonical_id,
)
from app.providers.registry import get_provider
from app.publication_quarantine import quarantined_provider_ids
from app.site_registry import get_site_config
from app.state import load_provider_failures, load_provider_state, load_site_bundles


def _event_key(source_exam_id: str, year_ad: int) -> tuple[str, int]:
    return source_exam_id, year_ad


def _published_bundle_index(bundles: list[Any]) -> dict[str, dict[str, set[int]]]:
    index: dict[str, dict[str, set[int]]] = defaultdict(dict)
    for bundle in bundles:
        bundle_id = bundle.bundle_id or bundle.canonical_id
        for candidate in {bundle_id, bundle.canonical_id, *bundle.legacy_canonical_ids}:
            if candidate:
                index[candidate].setdefault(bundle_id, set()).update(bundle.years)
    return index


def _failure_status(failures: list[Any]) -> str | None:
    stages = {failure.stage for failure in failures}
    if "bundle" in stages:
        return "bundle_repair_needed"
    if stages:
        return "sync_failure_recorded"
    return None


@dataclass(slots=True)
class _EventPapers:
    count: int = 0
    bundle_ids: set[str] = field(default_factory=set)
    missing_mirror_files: set[str] = field(default_factory=set)


def _probe_provider(
    provider_id: str,
    local_event_keys: set[tuple[str, int]],
    *,
    client: Any | None,
) -> dict[str, Any]:
    try:
        source_client = client or get_provider(provider_id)
        years = sorted(set(source_client.discover_available_years()), reverse=True)
    except Exception as exc:
        return {
            "status": "error",
            "available_years": [],
            "source_event_count": 0,
            "source_only_events": [],
            "year_errors": [],
            "error": str(exc),
        }

    source_events: list[dict[str, Any]] = []
    year_errors: list[dict[str, Any]] = []
    for year_ad in years:
        try:
            exams = source_client.discover_exams(year_ad)
        except Exception as exc:
            year_errors.append({"year_ad": year_ad, "error": str(exc)})
            continue
        for exam in exams:
            source_events.append(
                {
                    "source_exam_id": exam.code,
                    "year_ad": exam.year_ad,
                    "year_roc": exam.year_roc,
                    "label": exam.label,
                }
            )
    deduped: dict[tuple[str, int], dict[str, Any]] = {}
    for event in source_events:
        deduped[_event_key(event["source_exam_id"], event["year_ad"])] = event
    source_only_events = [
        event
        for key, event in sorted(deduped.items(), key=lambda item: (-item[0][1], item[0][0]))
        if key not in local_event_keys
    ]
    return {
        "status": "partial" if year_errors else "ok",
        "available_years": years,
        "source_event_count": len(deduped),
        "source_only_events": source_only_events,
        "year_errors": year_errors,
    }


def build_history_coverage_audit(
    repo_root: Path,
    *,
    site_id: str = "default",
    provider_ids: Iterable[str] | None = None,
    probe_sources: bool = False,
    clients: dict[str, Any] | None = None,
    check_mirror: bool = True,
) -> dict[str, Any]:
    """Build a read-only per-event coverage report.

    ``source_only_events`` are authoritative discovery gaps only when the
    optional source probe succeeds.  An upstream outage is reported as a
    probe error, never misclassified as missing historical material. Source-only
    events and probe errors both count toward ``parser_gap`` in strict reports.

    ``check_mirror`` distinguishes two conditions that must not be conflated: a
    mirror that is missing a file it should hold is a ``download_gap``, whereas
    an absent mirror tree means the download dimension is simply unverifiable.
    The mirror is gitignored operational state, so a checkout without it cannot
    make any claim about download completeness.  Callers that lack the tree pass
    ``check_mirror=False``; the report then records ``mirror_checked: false`` so
    a reduced-scope run can never be read as a full audit.
    """
    site_config = get_site_config(site_id)
    selected_provider_ids = tuple(provider_ids or site_config.provider_ids)
    published_index = _published_bundle_index(load_site_bundles(site_paths(repo_root, site_id)))
    provider_reports: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    total_parser_gaps = 0
    all_indexes: list[dict[str, Any]] = []

    for provider_id in selected_provider_ids:
        provider = provider_paths(repo_root, provider_id)
        index = load_provider_index(provider)
        if index is None:
            raw_pages, catalog, failures = load_provider_state(provider)
            index = build_provider_index(provider, raw_pages, catalog.papers)
        else:
            failures = load_provider_failures(provider)
        coverage_exceptions = load_coverage_exceptions(repo_root, provider_id)
        all_indexes.append(index)
        raw_by_event = {
            _event_key(row[0], row[1]): row[2]
            for row in index["raw_events"]
        }
        papers_by_event: dict[tuple[str, int], _EventPapers] = defaultdict(_EventPapers)
        for row in index["papers"]:
            event = papers_by_event[_event_key(row[PAPER_SOURCE_EXAM_ID], row[PAPER_YEAR_ROC] + 1911)]
            event.count += 1
            event.bundle_ids.add(
                paper_index_bundle_id(index, row) or paper_index_canonical_id(index, row)
            )
            if check_mirror and resolve_mirror_storage_path(repo_root / "mirror", row[PAPER_STORAGE_KEY], provider_id) is None:
                event.missing_mirror_files.add(
                    row[PAPER_STORAGE_KEY] or f"{row[PAPER_CODE]}:{row[PAPER_FILE_TYPE]}"
                )
        failures_by_event: dict[tuple[str, int], list[Any]] = defaultdict(list)
        for failure in failures:
            failures_by_event[_event_key(failure.source_exam_id, failure.year_roc + 1911)].append(failure)

        event_keys = set(raw_by_event) | set(papers_by_event) | set(failures_by_event)
        events: list[dict[str, Any]] = []
        matched_exception_keys: set[tuple[str, str, int, str, str]] = set()
        for source_exam_id, year_ad in sorted(event_keys, key=lambda item: (-item[1], item[0])):
            event_key = _event_key(source_exam_id, year_ad)
            raw_has_material = raw_by_event.get(event_key, False)
            papers = papers_by_event[event_key]
            event_failures = failures_by_event[event_key]
            event_exception = event_exception_for(coverage_exceptions, source_exam_id, year_ad)
            if event_exception is not None:
                matched_exception_keys.add(event_exception.key)
            matched_file_exceptions = []
            for failure in event_failures:
                exception = failure_exception_for(provider_id, failure, coverage_exceptions)
                if exception is not None:
                    matched_file_exceptions.append(exception)
                    matched_exception_keys.add(exception.key)
            missing_mirror_files = sorted(papers.missing_mirror_files)
            required_bundle_ids = papers.bundle_ids
            published_bundle_ids = sorted(
                {
                    published_bundle_id
                    for bundle_id in required_bundle_ids
                    for published_bundle_id, published_years in published_index.get(bundle_id, {}).items()
                    if year_ad - 1911 in published_years
                }
            )
            unpublished_bundle_ids = sorted(
                bundle_id
                for bundle_id in required_bundle_ids
                if not any(
                    year_ad - 1911 in published_years
                    for published_years in published_index.get(bundle_id, {}).values()
                )
            )
            failure_status = _failure_status(event_failures)
            has_current_material = bool(papers.count or event_failures or raw_has_material)
            if event_exception is not None:
                # An event exception is valid only for a retained event with no
                # currently materialized records.  It must not hide new data or
                # a changed failure state.
                status = "coverage_exception_conflict" if has_current_material else event_exception.status
            elif missing_mirror_files:
                status = "download_gap"
            elif event_failures and len(matched_file_exceptions) == len(event_failures):
                status = "partially_blocked" if has_current_material else "blocked"
            elif failure_status is not None:
                status = failure_status
            elif not papers.count and event_key in raw_by_event:
                # A source page that lists no papers has nothing to normalize, so
                # calling that a normalization gap is a false positive. It failed
                # the strict audit and blocked every deploy on 2026-08-09, when
                # special_admission discovered 116學年度身心障礙學生升學大專校院甄試
                # - announced, not yet held, no papers to publish.
                #
                # has_current_material is exactly the discriminator: at this
                # branch there are no papers and no failures, so it is true only
                # when the raw page itself carried papers or attachments. A page
                # that did list papers and still normalized none is a real gap
                # and stays one.
                status = "normalization_gap" if has_current_material else "awaiting_papers"
            elif papers.count and unpublished_bundle_ids:
                status = "normalized_not_published"
            elif papers.count:
                status = "published_complete"
            else:
                status = "failure_only"
            status_counts[status] += 1
            events.append(
                {
                    "source_exam_id": source_exam_id,
                    "year_ad": year_ad,
                    "year_roc": year_ad - 1911,
                    "raw_page_present": event_key in raw_by_event,
                    "normalized_paper_records": papers.count,
                    "missing_mirror_files": missing_mirror_files,
                    "published_bundle_ids": published_bundle_ids,
                    "unpublished_bundle_ids": unpublished_bundle_ids,
                    "failure_count": len(event_failures),
                    "failure_stages": sorted({failure.stage for failure in event_failures}),
                    "coverage_exception": event_exception.as_dict() if event_exception else None,
                    "matched_file_coverage_exceptions": [exception.as_dict() for exception in matched_file_exceptions],
                    "status": status,
                }
            )

        orphan_coverage_exceptions = [
            exception.as_dict()
            for exception in coverage_exceptions
            if exception.key not in matched_exception_keys
        ]
        if orphan_coverage_exceptions:
            status_counts["coverage_exception_orphan"] += len(orphan_coverage_exceptions)

        source_probe = {"status": "not_requested", "available_years": [], "source_event_count": 0, "source_only_events": [], "year_errors": []}
        if probe_sources:
            source_probe = _probe_provider(
                provider_id,
                event_keys,
                client=(clients or {}).get(provider_id),
            )
            total_parser_gaps += len(source_probe["source_only_events"])
            total_parser_gaps += len(source_probe["year_errors"])
            if source_probe["status"] == "error":
                total_parser_gaps += 1
        provider_reports.append(
            {
                "provider_id": provider_id,
                "raw_exam_pages": len(index["raw_events"]),
                "normalized_paper_records": len(index["papers"]),
                "sync_failure_count": len(failures),
                "coverage_exception_count": len(coverage_exceptions),
                "orphan_coverage_exceptions": orphan_coverage_exceptions,
                "events": events,
                "source_probe": source_probe,
            }
        )

    public_ids = public_bundle_ids_from_indexes(
        all_indexes,
        min_years=site_config.public_min_years,
        min_years_by_canonical_prefix=site_config.public_min_years_by_canonical_prefix,
    )
    quarantined = quarantined_provider_ids(repo_root, site_id=site_id)
    for provider_report in provider_reports:
        for event in provider_report["events"]:
            if event["status"] != "normalized_not_published":
                continue
            # A quarantined provider is withheld on purpose and with recorded
            # evidence, so it is not an accidental publication gap. It keeps a
            # status of its own rather than joining the min-years bucket, so
            # the withheld volume stays visible in the summary.
            if provider_report["provider_id"] in quarantined:
                event["status"] = "withheld_by_quarantine"
                status_counts["normalized_not_published"] -= 1
                status_counts["withheld_by_quarantine"] += 1
                continue
            unpublished_ids = set(event["unpublished_bundle_ids"])
            policy_eligible_ids = sorted(unpublished_ids & public_ids)
            policy_excluded_ids = sorted(unpublished_ids - public_ids)
            event["policy_eligible_bundle_ids"] = policy_eligible_ids
            event["policy_excluded_bundle_ids"] = policy_excluded_ids
            if policy_excluded_ids and not policy_eligible_ids:
                event["status"] = "excluded_by_publication_policy"
                status_counts["normalized_not_published"] -= 1
                status_counts["excluded_by_publication_policy"] += 1

    summary = dict(sorted(status_counts.items()))
    summary["parser_gap"] = total_parser_gaps
    return {
        "schema_version": 1,
        "site_id": site_id,
        "probe_sources": probe_sources,
        "mirror_checked": check_mirror,
        "provider_count": len(provider_reports),
        "summary": summary,
        "providers": provider_reports,
    }


def write_history_coverage_audit(report: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def history_audit_exit_code(report: dict[str, Any], *, strict: bool) -> int:
    if not strict:
        return 0
    summary = report["summary"]
    return int(
        any(
            summary.get(status, 0)
            for status in (
                "download_gap",
                "sync_failure_recorded",
                "normalization_gap",
                "normalized_not_published",
                "bundle_repair_needed",
                "coverage_exception_conflict",
                "coverage_exception_orphan",
                "parser_gap",
            )
        )
    )
