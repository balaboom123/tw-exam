from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Callable

from app.manifest import SourceManifest
from app.models import SourceExamPage
from app.providers.base import SourceProvider
from app.providers.moex.client import make_result_url, make_year_search_url
from app.sync import retry_network


def _stable_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def hash_exam_codes(exam_codes: list[str]) -> str:
    return _stable_hash(sorted(exam_codes))


def hash_paper_urls(page: SourceExamPage) -> str:
    records = []
    for paper in page.papers:
        for file_type, download_url in sorted(paper.files.items()):
            records.append(
                {
                    "category_code": paper.category_code,
                    "subject_code": paper.subject_code,
                    "subject_name_raw": paper.subject_name_raw,
                    "file_type": file_type,
                    "download_url_source": download_url,
                }
            )
    return _stable_hash(records)


@dataclass
class ProbeResult:
    provider_id: str = ""
    generated_at: str = ""
    changed_years: list[int] = field(default_factory=list)
    changed_exam_codes: list[str] = field(default_factory=list)
    removed_exam_codes: list[str] = field(default_factory=list)
    unchanged_exam_codes: list[str] = field(default_factory=list)
    exam_years: dict[str, int] = field(default_factory=dict)
    should_sync: bool = False
    request_counts: dict[str, int] = field(default_factory=dict)
    updated_manifest: SourceManifest = field(default_factory=SourceManifest)

    def to_output_data(self) -> dict[str, object]:
        return asdict(self)


def _initial_counts() -> dict[str, int]:
    return {
        "year_head_count": 0,
        "year_get_count": 0,
        "exam_head_count": 0,
        "exam_get_count": 0,
        "file_head_count": 0,
        "file_download_count": 0,
    }


def _paper_file_count(page: SourceExamPage) -> int:
    return sum(len(paper.files) for paper in page.papers)


def _probe_url_builder(client: SourceProvider, provider_id: str, attr_name: str, fallback: Callable[..., str]) -> Callable[..., str]:
    builder = getattr(client, attr_name, None)
    if callable(builder):
        return builder
    if provider_id == "moex":
        return fallback
    raise NotImplementedError(f"probe_latest is not supported for provider {provider_id}: missing probe URL model")


def _exam_entry_from_page(page: SourceExamPage, *, result_url: str, head_content_length: int | None, now: str) -> dict[str, object]:
    return {
        "source_exam_id": page.source_exam_id,
        "year_ad": page.year_ad,
        "year_roc": page.year_roc,
        "result_url": result_url,
        "head_content_length": head_content_length,
        "exam_name_hash": _stable_hash(page.exam_name_raw),
        "paper_count": len(page.papers),
        "attachment_count": len(page.attachments),
        "file_link_count": _paper_file_count(page),
        "paper_url_hash": hash_paper_urls(page),
        "last_changed_at": now,
    }


def probe_latest(client: SourceProvider, manifest: SourceManifest, year_window: int, now: str) -> ProbeResult:
    updated = copy.deepcopy(manifest)
    provider_id = manifest.provider_id or getattr(client, "provider_id", "") or "moex"
    if provider_id and not updated.provider_id:
        updated.provider_id = provider_id
    counts = _initial_counts()
    changed_years: list[int] = []
    changed_exam_codes: list[str] = []
    removed_exam_codes: list[str] = []
    unchanged_exam_codes: list[str] = []
    exam_years: dict[str, int] = {}
    year_url_builder = _probe_url_builder(client, provider_id, "build_probe_year_url", make_year_search_url)
    exam_url_builder = _probe_url_builder(client, provider_id, "build_probe_exam_url", make_result_url)

    latest_years = sorted(client.discover_available_years(), reverse=True)[:year_window]
    for year_ad in latest_years:
        year_key = str(year_ad)
        year_roc = year_ad - 1911
        search_url = year_url_builder(year_ad)
        year_head = client.head(search_url)
        counts["year_head_count"] += 1
        existing_year = manifest.years.get(year_key)
        existing_codes = list(existing_year.get("exam_codes", [])) if existing_year else []
        # Older MOEX manifests could record an empty option list when a
        # malformed response had the same content length as a valid page.
        # MOEX archive years are not validly represented by an empty list, so
        # force a guarded rediscovery instead of perpetuating that bad cache.
        year_changed = (
            existing_year is None
            or existing_year.get("head_content_length") != year_head.content_length
            or (provider_id == "moex" and not existing_codes)
        )

        if year_changed:
            exams = retry_network(lambda: client.discover_exams(year_ad))
            counts["year_get_count"] += 1
            current_codes = [exam.code for exam in exams]
            current_hash = hash_exam_codes(current_codes)
            if existing_year is None or existing_year.get("exam_codes_hash") != current_hash or existing_year.get("head_content_length") != year_head.content_length:
                changed_years.append(year_ad)
            updated.years[year_key] = {
                "year_ad": year_ad,
                "year_roc": year_roc,
                "search_url": search_url,
                "head_content_length": year_head.content_length,
                "exam_codes": current_codes,
                "exam_codes_hash": current_hash,
                "last_changed_at": now,
            }
        else:
            current_codes = existing_codes

        current_code_set = set(current_codes)
        for removed_code in sorted(set(existing_codes) - current_code_set):
            removed_exam_codes.append(removed_code)
            updated.exams.pop(removed_code, None)

        for exam_code in current_codes:
            exam_years[exam_code] = year_ad
            result_url = exam_url_builder(exam_code, year_ad)
            exam_head = client.head(result_url)
            counts["exam_head_count"] += 1
            existing_exam = manifest.exams.get(exam_code)
            exam_changed = existing_exam is None or existing_exam.get("head_content_length") != exam_head.content_length
            if exam_changed:
                page = client.fetch_exam_page(exam_code, year_ad)
                counts["exam_get_count"] += 1
                updated.exams[exam_code] = _exam_entry_from_page(
                    page,
                    result_url=result_url,
                    head_content_length=exam_head.content_length,
                    now=now,
                )
                changed_exam_codes.append(exam_code)
            else:
                unchanged_exam_codes.append(exam_code)

    return ProbeResult(
        provider_id=provider_id,
        generated_at=now,
        changed_years=changed_years,
        changed_exam_codes=changed_exam_codes,
        removed_exam_codes=removed_exam_codes,
        unchanged_exam_codes=unchanged_exam_codes,
        exam_years=exam_years,
        should_sync=bool(changed_exam_codes or removed_exam_codes),
        request_counts=counts,
        updated_manifest=updated,
    )
