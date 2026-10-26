"""Reviewed annual candidate groups, independent of reused source numbers."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class NativeGroup:
    category_code: str
    source_label: str
    from_year_roc: int
    through_year_roc: int

    def contains(self, year_roc: int) -> bool:
        return self.from_year_roc <= year_roc <= self.through_year_roc


@dataclass(frozen=True)
class CandidateGroup:
    id: str
    label: str
    kind: str
    member_tracks: tuple[str, ...]
    predecessor_tracks: tuple[str, ...]
    source_groups: tuple[NativeGroup, ...]
    reason: str


@dataclass(frozen=True)
class GroupCatalogue:
    annual_events: tuple[tuple[int, str], ...]
    tracks: tuple[CandidateGroup, ...]


def group_identity_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/tcte/group-identity-v1.json"


def _normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _text(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("TCTE group evidence requires nonempty text")


def _checksum(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("TCTE group evidence requires SHA-256 checksums")


def _year(value: int) -> None:
    if type(value) is not int or not 1 <= value <= 999:
        raise ValueError("Invalid TCTE group evidence year")


def read_group_catalogue(path: Path) -> GroupCatalogue:
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(document, dict)
        or set(document)
        != {
            "schema_version",
            "provider_id",
            "reviewed_at",
            "references",
            "annual_sources",
            "tracks",
        }
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 1
        or document["provider_id"] != "tcte_tve"
    ):
        raise ValueError("Unsupported TCTE group identity evidence")
    date.fromisoformat(document["reviewed_at"])
    references = set()
    for row in document["references"]:
        _text(row["id"])
        _text(row["purpose"])
        _checksum(row["checksum"])
        url = urlsplit(row["url"])
        if (
            url.scheme != "https"
            or url.netloc not in {"www.tcte.edu.tw", "www.ceec.edu.tw"}
            or url.fragment
            or row["id"] in references
            or any(type(page) is not int or page < 1 for page in row["pdf_pages"])
        ):
            raise ValueError("Invalid or duplicate TCTE primary reference")
        references.add(row["id"])
    events = {}
    for row in document["annual_sources"]:
        year = row["year_roc"]
        _year(year)
        _checksum(row["checksum"])
        url = urlsplit(row["source_url"])
        if (
            row["source_exam_id"] != f"tcte-tve-{year}"
            or year in events
            or url.scheme != "https"
            or url.netloc != "web1.tcte.edu.tw"
            or url.path.rstrip("/") != f"/EXAM/{year:03}_4y"
            or url.query
            or url.fragment
        ):
            raise ValueError("Invalid or duplicate TCTE annual source context")
        events[year] = row["source_exam_id"]
    tracks = []
    ids = set()
    labels = set()
    slots = set()
    for row in document["tracks"]:
        for field in ("id", "label", "reason"):
            _text(row[field])
        if (
            re.fullmatch(r"(?:tcte-[a-z0-9]+(?:-[a-z0-9]+)*|common-subject)", row["id"]) is None
            or row["id"] in ids
            or row["label"] in labels
            or row["kind"] not in {"single", "composite", "common"}
        ):
            raise ValueError("Invalid or duplicate TCTE candidate-group meaning")
        ids.add(row["id"])
        labels.add(row["label"])
        groups = []
        for item in row["source_groups"]:
            group = NativeGroup(**item)
            _text(group.category_code)
            _text(group.source_label)
            _year(group.from_year_roc)
            _year(group.through_year_roc)
            if (
                group.from_year_roc > group.through_year_roc
                or group.source_label != _normalized(group.source_label)
                or (
                    row["kind"] != "common"
                    and (
                        re.fullmatch(r"[0-9]{2}", group.category_code) is None
                        or not group.source_label.startswith(group.category_code)
                        or group.source_label == group.category_code
                    )
                )
                or (row["kind"] == "common" and group.category_code != "common")
            ):
                raise ValueError("Invalid TCTE native group label or year range")
            for year in range(group.from_year_roc, group.through_year_roc + 1):
                slot = year, group.category_code, group.source_label
                if year not in events or slot in slots:
                    raise ValueError("Overlapping or unreviewed TCTE native group context")
                slots.add(slot)
            groups.append(group)
        if not groups:
            raise ValueError("TCTE candidate groups require native source contexts")
        tracks.append(
            CandidateGroup(
                row["id"],
                row["label"],
                row["kind"],
                tuple(row["member_tracks"]),
                tuple(row["predecessor_tracks"]),
                tuple(groups),
                row["reason"],
            )
        )
    by_id = {track.id: track for track in tracks}
    for track in tracks:
        for related in (track.member_tracks, track.predecessor_tracks):
            if len(set(related)) != len(related) or any(
                key not in by_id or key == track.id or by_id[key].kind != "single"
                for key in related
            ):
                raise ValueError("Invalid TCTE constituent or predecessor group")
        if (track.kind == "composite") != (len(track.member_tracks) >= 2):
            raise ValueError("TCTE composite groups require their distinct constituent groups")
        if track.kind != "composite" and track.member_tracks:
            raise ValueError("Only TCTE composite groups have constituent groups")
    if (
        not references
        or not events
        or not tracks
        or {track.id for track in tracks if track.kind == "common"} != {"common-subject"}
    ):
        raise ValueError("Incomplete TCTE group evidence")
    return GroupCatalogue(tuple(sorted(events.items())), tuple(tracks))


@lru_cache(maxsize=1)
def group_catalogue() -> GroupCatalogue:
    return read_group_catalogue(group_identity_path(Path(__file__).resolve().parents[3]))


def resolve_group(
    *,
    source_exam_id: str,
    year_roc: int,
    category_code: str,
    subject_code: str,
    subject_name: str,
    catalogue: GroupCatalogue | None = None,
) -> CandidateGroup | None:
    catalogue = catalogue or group_catalogue()
    if (year_roc, source_exam_id) not in catalogue.annual_events:
        return None
    label, _, subject = _normalized(subject_name).partition(" ")
    for track in catalogue.tracks:
        for native in track.source_groups:
            if (
                native.category_code != category_code
                or native.source_label != label
                or not native.contains(year_roc)
            ):
                continue
            if track.kind == "common":
                return track
            expected = {"professional-1": "專業科目(一)", "professional-2": "專業科目(二)"}.get(
                subject_code
            )
            if expected and (subject == expected or subject.startswith(expected + "-")):
                return track
    return None


def validate_group_evidence(repo_root: Path) -> int:
    """Reconcile all declared native contexts against complete current raw and paper state."""
    from app.paths import provider_paths
    from app.state import load_provider_state

    catalogue = read_group_catalogue(group_identity_path(repo_root))
    pages, normalized, _ = load_provider_state(provider_paths(repo_root, "tcte_tve"))
    observed = set()
    for page in pages:
        if (page.year_roc, page.source_exam_id) not in catalogue.annual_events:
            raise ValueError("TCTE raw event lacks reviewed annual evidence")
        for paper in page.papers:
            group = resolve_group(
                source_exam_id=page.source_exam_id,
                year_roc=page.year_roc,
                category_code=paper.category_code,
                subject_code=paper.subject_code,
                subject_name=paper.subject_name_raw,
                catalogue=catalogue,
            )
            if group is None:
                raise ValueError("TCTE raw paper lacks a reviewed native group context")
            observed.add(
                (
                    page.year_roc,
                    paper.category_code,
                    _normalized(paper.subject_name_raw).partition(" ")[0],
                )
            )
    expected = {
        (year, native.category_code, native.source_label)
        for track in catalogue.tracks
        for native in track.source_groups
        for year in range(native.from_year_roc, native.through_year_roc + 1)
    }
    if observed != expected:
        raise ValueError("TCTE native group coverage differs from reviewed annual source facts")
    for normalized_paper in normalized.papers:
        group = resolve_group(
            source_exam_id=normalized_paper.source_exam_id,
            year_roc=normalized_paper.year_roc,
            category_code=normalized_paper.category_code,
            subject_code=normalized_paper.subject_code,
            subject_name=normalized_paper.subject_name_raw,
            catalogue=catalogue,
        )
        if group is None or normalized_paper.track_id != group.id:
            raise ValueError("TCTE retained identity differs from its reviewed native group")
    return len(catalogue.tracks)
