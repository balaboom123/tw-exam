"""Private proposal: reviewed NTHU annual rules and source-file applicability.

This module is confined to the isolated migration rehearsal; production does
not import it. Native file storage remains separate from admissions grouping.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import date
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from app.models import SourceExamPage, file_type_label
from app.providers.hce_archive import HCE_CONFIGS
from app.source_material import SourceDate, SourceMaterial


@dataclass(frozen=True)
class NativePaper:
    subject_code: str
    subject_name_raw: str
    file_type: str
    source_url: str
    checksum: str

    def material(self, year_roc: int) -> SourceMaterial:
        return SourceMaterial(
            "administered",
            SourceDate("exam_year", year_roc + 1911),
            self.source_url,
            f"{year_roc}學年度國立清華大學學士後醫學系｜{self.subject_name_raw}｜{file_type_label(self.file_type)}",
        )


@dataclass(frozen=True)
class AdmissionGroup:
    category_code: str
    track_id: str
    label: str
    subjects: tuple[str, ...]

    def accepts(self, subject_code: str) -> bool:
        return subject_code == "all" or subject_code in self.subjects


@dataclass(frozen=True)
class AnnualExam:
    year_roc: int
    source_exam_id: str
    written_test_date: date
    stage_id: str
    rule_url: str
    rule_checksum: str
    groups: tuple[AdmissionGroup, ...]
    files: tuple[NativePaper, ...]


def annual_papers_path(repo_root: Path) -> Path:
    return repo_root / "catalog/mappings/nthu/annual-papers-v1.json"


def _official_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "adms.site.nthu.edu.tw" or parsed.fragment:
        raise ValueError("NTHU annual evidence requires an official source URL")


def _checksum(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("NTHU annual evidence requires SHA-256 checksums")


def read_annual_exams(path: Path) -> tuple[AnnualExam, ...]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        set(document) != {"schema_version", "provider_id", "events"}
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 1
        or document["provider_id"] != "hce_nthu"
    ):
        raise ValueError("Unsupported NTHU annual evidence")
    subjects = set(HCE_CONFIGS["hce_nthu"].subject_slugs.values())
    years = set()
    exams = []
    group_meanings: dict[str, tuple[str, str]] = {}
    for row in document["events"]:
        year = row["year_roc"]
        if type(year) is not int or not 1 <= year <= 999 or year in years:
            raise ValueError("Invalid or duplicate NTHU annual context")
        years.add(year)
        written = date.fromisoformat(row["written_test_date"])
        date.fromisoformat(row["reviewed_at"])
        if (
            written.year != year + 1911
            or row["source_exam_id"] != f"hce-nthu-{year}"
            or row["stage_id"] != "stage-1"
        ):
            raise ValueError("NTHU stage/date evidence conflicts with its annual context")
        _official_url(row["rule_url"])
        _checksum(row["rule_checksum"])
        if not row["rule_printed_pages"] or any(
            type(p) is not int or p < 1 for p in row["rule_printed_pages"]
        ):
            raise ValueError("NTHU rules require native page evidence")
        groups = []
        categories = set()
        tracks = set()
        for group in row["groups"]:
            code, track = group["category_code"], group["track_id"]
            if (
                not re.fullmatch(r"post-bacc-medicine(?:-[a-z0-9-]+)?", code)
                or not re.fullmatch(r"hce-nthu(?:-[a-z0-9-]+)?", track)
                or code in categories
                or track in tracks
            ):
                raise ValueError("Invalid or duplicate NTHU admissions group")
            selected = group["subjects"]
            if (
                not selected
                or len(set(selected)) != len(selected)
                or not set(selected) <= subjects
                or not isinstance(group["label"], str)
            ):
                raise ValueError("Invalid NTHU group subject applicability")
            categories.add(code)
            tracks.add(track)
            meaning = track, group["label"]
            if code in group_meanings and group_meanings[code] != meaning:
                raise ValueError("NTHU category changes its reviewed group identity")
            group_meanings[code] = meaning
            groups.append(AdmissionGroup(code, track, group["label"], tuple(selected)))
        if not groups or (len(groups) > 1 and any(not g.label for g in groups)):
            raise ValueError("NTHU groups require distinct native labels")
        files = []
        urls = set()
        contexts = set()
        for item in row["files"]:
            paper = NativePaper(**item)
            _official_url(paper.source_url)
            _checksum(paper.checksum)
            context = paper.source_url, paper.checksum
            if (
                context in contexts
                or not paper.subject_name_raw
                or paper.file_type not in {"question", "all_answers", "answer_clarification"}
                or paper.subject_code not in subjects | {"all"}
                or (paper.file_type == "all_answers") != (paper.subject_code == "all")
                or not any(g.accepts(paper.subject_code) for g in groups)
            ):
                raise ValueError("Invalid or conflicting NTHU native file applicability")
            urls.add(paper.source_url)
            contexts.add(context)
            files.append(paper)
        if not files:
            raise ValueError("NTHU annual context requires native file evidence")
        required_subjects = {subject for group in groups for subject in group.subjects}
        question_subjects = {paper.subject_code for paper in files if paper.file_type == "question"}
        if required_subjects != question_subjects:
            raise ValueError("NTHU annual group lacks complete native question coverage")
        exams.append(
            AnnualExam(
                year,
                row["source_exam_id"],
                written,
                row["stage_id"],
                row["rule_url"],
                row["rule_checksum"],
                tuple(groups),
                tuple(files),
            )
        )
    if not exams:
        raise ValueError("NTHU annual evidence cannot be empty")
    return tuple(exams)


@lru_cache(maxsize=1)
def annual_exams() -> tuple[AnnualExam, ...]:
    return read_annual_exams(annual_papers_path(Path(__file__).resolve().parents[3]))


def _annual_exam(source_exam_id: str, year_roc: int) -> AnnualExam | None:
    return next(
        (
            e
            for e in annual_exams()
            if e.source_exam_id == source_exam_id and e.year_roc == year_roc
        ),
        None,
    )


def project_admission_groups(page: SourceExamPage) -> SourceExamPage:
    """Assign native group references without duplicating source payloads."""
    if page.provider_id != "hce_nthu" or page.year_ad != page.year_roc + 1911:
        raise ValueError("Invalid NTHU source event context")
    config = HCE_CONFIGS["hce_nthu"]
    annual = _annual_exam(page.source_exam_id, page.year_roc)
    projected = []
    for paper in page.papers:
        if paper.category_code != config.category_code or paper.source_category_code:
            raise ValueError("NTHU projection requires native acquisition categories")
        for role, url in paper.files.items():
            native = (
                next(
                    (
                        f
                        for f in annual.files
                        if f.source_url == url
                        and f.file_type == role
                        and f.subject_code == paper.subject_code
                        and f.subject_name_raw == paper.subject_name_raw
                    ),
                    None,
                )
                if annual
                else None
            )
            material = (
                native.material(page.year_roc)
                if native
                else SourceMaterial(
                    "administered",
                    SourceDate("unknown", None),
                    url,
                    paper.subject_name_raw,
                    "NTHU native paper lacks reviewed annual source, role and date evidence",
                )
            )
            groups = [g for g in annual.groups if g.accepts(paper.subject_code)] if annual else []
            if not groups:
                projected.append(replace(paper, files={role: url}, source_material=material))
                continue
            for group in groups:
                projected.append(
                    replace(
                        paper,
                        category_code=group.category_code,
                        source_category_code=config.category_code,
                        files={role: url},
                        mirror_files={role: dict(paper.mirror_files[role])}
                        if role in paper.mirror_files
                        else {},
                        source_material=material,
                    )
                )
    return replace(page, papers=projected)


def resolve_identity(
    *,
    source_exam_id: str,
    year_ad: int,
    category_code: str,
    category_raw: str,
    subject_code: str,
    source_material: SourceMaterial | None,
    checksum: str,
) -> tuple[AnnualExam, AdmissionGroup] | None:
    annual = _annual_exam(source_exam_id, year_ad - 1911)
    if (
        annual is None
        or source_material is None
        or category_raw != HCE_CONFIGS["hce_nthu"].category_name
    ):
        return None
    native = next(
        (
            f
            for f in annual.files
            if f.subject_code == subject_code
            and f.checksum == checksum
            and f.material(annual.year_roc) == source_material
        ),
        None,
    )
    group = next(
        (g for g in annual.groups if g.category_code == category_code and g.accepts(subject_code)),
        None,
    )
    return (annual, group) if native is not None and group is not None else None


def validate_annual_papers(repo_root: Path, *, verify_mirror: bool = False) -> int:
    """Require each reviewed native version to have a retained source anchor."""
    import hashlib

    from app.paths import provider_paths
    from app.source_revisions import load_source_revisions
    from app.state import load_provider_state

    provider = provider_paths(repo_root, "hce_nthu")
    _, catalog, _ = load_provider_state(provider)
    anchors = [
        (
            p.source_exam_id,
            p.year_roc,
            p.subject_code,
            p.file_type,
            p.download_url_source,
            p.checksum,
            p.storage_key,
        )
        for p in catalog.papers
    ]
    for revision in load_source_revisions(provider):
        row = revision["source_record"]
        if revision["record_type"] == "paper":
            anchors.append(
                (
                    row["source_exam_id"],
                    row["year_roc"],
                    row["subject_code"],
                    row["file_type"],
                    row["download_url_source"],
                    row["checksum"],
                    revision["blob_storage_key"],
                )
            )
    count = 0
    for annual in read_annual_exams(annual_papers_path(repo_root)):
        for paper in annual.files:
            key = (
                annual.source_exam_id,
                annual.year_roc,
                paper.subject_code,
                paper.file_type,
                paper.source_url,
                paper.checksum,
            )
            matches = [a[-1] for a in anchors if a[:-1] == key]
            if not matches:
                raise ValueError("NTHU native evidence lacks a retained source anchor")
            if verify_mirror:
                for storage_key in set(matches):
                    with (repo_root / "mirror" / storage_key).open("rb") as stream:
                        if hashlib.file_digest(stream, "sha256").hexdigest() != paper.checksum:
                            raise ValueError("NTHU retained native checksum mismatch")
            count += 1
    return count
