from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import unquote

from app.models import AliasRule, ExamAttachment, NormalizedCatalog, ParsedPaper, SourceExamPage, StoredFile, SyncFailure
from app.normalizer import normalize_papers
from app.providers.base import SourceProvider
from app.storage import MirrorStore

EXTENSION_OVERRIDES = {
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "application/x-rar-compressed": ".rar",
    "application/vnd.rar": ".rar",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}
EXPECTED_EXTENSIONS = {
    "question": (".pdf", ".doc", ".zip", ".rar", ".docx", ".xls", ".xlsx", ".ods"),
    "question_answer": (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ods", ".zip", ".rar"),
    "question_alt": (".pdf", ".docx", ".doc", ".xls", ".xlsx", ".ods", ".zip", ".rar"),
    "answer": (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ods", ".zip", ".rar"),
    "answer_sheet": (".pdf",),
    "corrected_answer": (".pdf", ".doc", ".zip"),
    "all_answers": (".pdf",),
    "answer_table": (".html", ".htm"),
    "accessible_bundle": (".zip",),
    "listening_audio": (".mp3", ".zip", ".rar"),
}
ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
DOC_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
RAR_SIGNATURES = (b"Rar!\x1a\x07\x00", b"Rar!\x1a\x07\x01\x00")
MP3_FRAME_SYNC_PREFIXES = (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")


def _extension_for(content_type: str, file_name: str) -> str:
    suffix = Path(unquote(file_name)).suffix
    if suffix:
        return suffix
    return EXTENSION_OVERRIDES.get(content_type.split(";")[0].strip(), ".bin")


def _asset_name_for(storage_key: str) -> str:
    return storage_key.replace("/", "__")


def _provider_storage_prefix(page: SourceExamPage) -> str:
    return f"providers/{page.provider_id}/" if page.provider_id else ""


def _mirror_prefix_for_attachment(page: SourceExamPage, attachment: ExamAttachment) -> str:
    return f"{_provider_storage_prefix(page)}{page.year_roc}/{page.source_exam_id}/exam/{attachment.file_type}"


def _mirror_prefix_for_paper(page: SourceExamPage, paper: ParsedPaper, file_type: str) -> str:
    return f"{_provider_storage_prefix(page)}{page.year_roc}/{page.source_exam_id}/{paper.category_code}/{paper.subject_code}/{file_type}"


def _strip_bom_prefix(data: bytes) -> bytes:
    return data[3:] if data.startswith(b"\xef\xbb\xbf") else data


def _looks_like_html(data: bytes) -> bool:
    head = _strip_bom_prefix(data[:256]).lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html") or head.startswith(b"<!doctype")


def _expected_extensions(file_type: str) -> tuple[str, ...]:
    if re.fullmatch(r"question_page_\d{2}", file_type):
        return (".jpg", ".jpeg", ".png")
    return EXPECTED_EXTENSIONS.get(file_type, ())


def _matches_expected_binary(data: bytes, expected_extension: str) -> bool:
    head = _strip_bom_prefix(data[:8])
    if expected_extension == ".pdf":
        return head.startswith(b"%PDF")
    if expected_extension == ".doc":
        return head.startswith(DOC_SIGNATURE)
    if expected_extension in {".zip", ".docx", ".xlsx", ".ods"}:
        return any(head.startswith(signature) for signature in ZIP_SIGNATURES)
    if expected_extension == ".xls":
        return head.startswith(DOC_SIGNATURE)
    if expected_extension == ".rar":
        return any(data.startswith(signature) for signature in RAR_SIGNATURES)
    if expected_extension == ".mp3":
        return data.startswith(b"ID3") or any(head.startswith(signature) for signature in MP3_FRAME_SYNC_PREFIXES)
    if expected_extension in {".jpg", ".jpeg"}:
        return head.startswith(b"\xff\xd8\xff")
    if expected_extension == ".png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if expected_extension in {".html", ".htm"}:
        return _looks_like_html(data)
    return False


def _validated_extension(file_type: str, data: bytes, content_type: str, file_name: str) -> str:
    expected_extensions = _expected_extensions(file_type)
    resolved_extension = _extension_for(content_type, file_name).lower()
    if _looks_like_html(data) and file_type != "answer_table":
        joined_extensions = " or ".join(expected_extensions) if expected_extensions else resolved_extension
        raise RuntimeError(f"Downloaded HTML placeholder instead of {joined_extensions} for {file_type}")
    if expected_extensions:
        if resolved_extension in expected_extensions and _matches_expected_binary(data, resolved_extension):
            return resolved_extension
        for expected_extension in expected_extensions:
            if _matches_expected_binary(data, expected_extension):
                return expected_extension
        joined_extensions = " or ".join(expected_extensions)
        raise RuntimeError(f"Downloaded file does not match expected {joined_extensions} payload for {file_type}")
    return resolved_extension


def _is_valid_stored_file(path: Path, file_type: str) -> bool:
    expected_extensions = _expected_extensions(file_type)
    actual_extension = path.suffix.lower()
    if not expected_extensions or actual_extension not in expected_extensions:
        return False
    return _matches_expected_binary(path.read_bytes()[:8], actual_extension)


def _retry_network(operation, attempts: int = 3):
    for attempt in range(attempts):
        try:
            return operation()
        except OSError as exc:
            if isinstance(exc, HTTPError) and exc.code not in {408, 425, 429} and exc.code < 500:
                raise
            if attempt + 1 == attempts:
                raise
            time.sleep(2**attempt)
    raise AssertionError("network retry loop did not return or raise")


def _ensure_mirrored(client: SourceProvider, mirror_store: MirrorStore, prefix: str, file_type: str, download_url: str) -> StoredFile:
    legacy_prefix = prefix
    stored = mirror_store.find_existing(prefix)
    if prefix.startswith("providers/"):
        _, _, legacy_prefix = prefix.split("/", 2)
    if stored is not None and not _is_valid_stored_file(stored.path, file_type):
        stored = None
        if legacy_prefix != prefix:
            stored = mirror_store.find_existing(legacy_prefix)
    if stored is None and legacy_prefix != prefix:
        stored = mirror_store.find_existing(legacy_prefix)
    if stored is not None and not _is_valid_stored_file(stored.path, file_type):
        stored = None
    if stored is None:
        downloaded = _retry_network(lambda: client.download_file(download_url))
        extension = _validated_extension(file_type, downloaded.data, downloaded.content_type, downloaded.file_name)
        stored = mirror_store.write_bytes(f"{prefix}{extension}", downloaded.data, overwrite=True)
        mirror_store.delete_matching_except(prefix, stored.storage_key)
    elif legacy_prefix != prefix and stored.storage_key.startswith(legacy_prefix):
        promoted_storage_key = f"{prefix}{stored.path.suffix.lower()}"
        promoted = mirror_store.write_bytes(promoted_storage_key, stored.path.read_bytes(), overwrite=False)
        stored = StoredFile(
            storage_key=promoted.storage_key,
            path=promoted.path,
            checksum=hashlib.sha256(stored.path.read_bytes()).hexdigest(),
            created=promoted.created,
            size=promoted.size,
        )
    return stored


def restore_catalog_files(
    client: SourceProvider, mirror_store: MirrorStore, catalog: NormalizedCatalog,
) -> list[SyncFailure]:
    """Restore retained files without changing their recorded identity or bytes."""
    failures: list[SyncFailure] = []
    for paper in catalog.papers:
        try:
            path = mirror_store.root / paper.storage_key
            if path.is_file() and _is_valid_stored_file(path, paper.file_type):
                if not paper.checksum or hashlib.sha256(path.read_bytes()).hexdigest() == paper.checksum:
                    continue
            downloaded = _retry_network(lambda: client.download_file(paper.download_url_source))
            extension = _validated_extension(
                paper.file_type, downloaded.data, downloaded.content_type, downloaded.file_name,
            )
            if extension != path.suffix.lower():
                raise RuntimeError("Retained file format changed; refresh its source exam before publication")
            if paper.checksum and hashlib.sha256(downloaded.data).hexdigest() != paper.checksum:
                raise RuntimeError("Retained file checksum changed; refresh its source exam before publication")
            mirror_store.write_bytes(paper.storage_key, downloaded.data, overwrite=True)
        except Exception as exc:
            failures.append(SyncFailure(
                stage="download", source_exam_id=paper.source_exam_id, year_roc=paper.year_roc,
                paper_code=paper.paper_code, file_type=paper.file_type,
                url=paper.download_url_source, message=f"Failed to restore retained file: {exc}",
            ))
    return failures


def sync_exam_pages(
    client: SourceProvider,
    exam_codes: list[tuple[str, int]],
    mirror_store: MirrorStore,
    alias_rules: list[AliasRule],
    mirror_base_url: str,
    download_attachments: bool = True,
) -> tuple[list[SourceExamPage], NormalizedCatalog, list[SyncFailure]]:
    raw_pages: list[SourceExamPage] = []
    normalized_papers = []
    review_queue = []
    failures: list[SyncFailure] = []

    for exam_code, year_ad in exam_codes:
        try:
            page = _retry_network(lambda: client.fetch_exam_page(exam_code, year_ad))
        except Exception as exc:
            failures.append(
                SyncFailure(
                    stage="fetch",
                    source_exam_id=exam_code,
                    year_roc=year_ad - 1911,
                    paper_code="",
                    file_type="",
                    url="",
                    message=f"Failed to fetch exam page: {exc}",
                )
            )
            continue
        provider_id = page.provider_id or getattr(client, "provider_id", "")
        if provider_id and not page.provider_id:
            page.provider_id = provider_id
        mirror_metadata: dict[tuple[str, str, str], dict[str, str]] = {}

        if download_attachments:
            for attachment in page.attachments:
                try:
                    stored = _ensure_mirrored(
                        client,
                        mirror_store,
                        _mirror_prefix_for_attachment(page, attachment),
                        attachment.file_type,
                        attachment.download_url_source,
                    )
                    attachment.storage_key = stored.storage_key
                    attachment.asset_name = _asset_name_for(stored.storage_key)
                    attachment.checksum = stored.checksum
                    attachment.download_url_mirror = f"{mirror_base_url.rstrip('/')}/{attachment.asset_name}" if mirror_base_url else ""
                except Exception as exc:
                    failures.append(
                        SyncFailure(
                            stage="download",
                            source_exam_id=page.source_exam_id,
                            year_roc=page.year_roc,
                            paper_code=f"exam-{attachment.file_type}",
                            file_type=attachment.file_type,
                            url=attachment.download_url_source,
                            message=str(exc),
                        )
                    )

        for paper in page.papers:
            for file_type, download_url in paper.files.items():
                try:
                    stored = _ensure_mirrored(client, mirror_store, _mirror_prefix_for_paper(page, paper, file_type), file_type, download_url)
                    paper.mirror_files[file_type] = {
                        "storage_key": stored.storage_key,
                        "asset_name": _asset_name_for(stored.storage_key),
                        "checksum": stored.checksum,
                    }
                    mirror_metadata[(paper.category_code, paper.subject_code, file_type)] = paper.mirror_files[file_type]
                except Exception as exc:
                    failures.append(
                        SyncFailure(
                            stage="download",
                            source_exam_id=page.source_exam_id,
                            year_roc=page.year_roc,
                            paper_code=f"{paper.category_code}-{paper.subject_code}-{file_type}",
                            file_type=file_type,
                            url=download_url,
                            message=str(exc),
                        )
                    )

        normalized_input_papers = [
            ParsedPaper(
                category_raw=paper.category_raw,
                category_code=paper.category_code,
                subject_code=paper.subject_code,
                subject_name_raw=paper.subject_name_raw,
                files={file_type: paper.files[file_type] for file_type in paper.mirror_files},
                mirror_files=paper.mirror_files,
            )
            for paper in page.papers
            if paper.mirror_files
        ]

        normalized = normalize_papers(
            source_exam_id=page.source_exam_id,
            year_ad=page.year_ad,
            exam_name_raw=page.exam_name_raw,
            papers=normalized_input_papers,
            alias_rules=alias_rules,
            mirror_base_url=mirror_base_url,
            mirror_metadata=mirror_metadata,
            provider_id=provider_id,
        )
        if provider_id:
            for normalized_paper in normalized.papers:
                normalized_paper.provider_id = provider_id
        raw_pages.append(page)
        normalized_papers.extend(normalized.papers)
        review_queue.extend(normalized.review_queue)

    mirror_store.flush_dedupe_index()
    return raw_pages, NormalizedCatalog(papers=normalized_papers, review_queue=review_queue), failures
