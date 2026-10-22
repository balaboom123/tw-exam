from __future__ import annotations

import hashlib
import io
import random
import re
import time
import zipfile
import zlib
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import TypeVar
from urllib.error import HTTPError, URLError
from urllib.parse import unquote

from app.models import (
    AliasRule,
    ExamAttachment,
    NormalizedCatalog,
    ParsedPaper,
    SourceExamPage,
    StoredFile,
    SyncFailure,
)
from app.normalizer import normalize_papers
from app.providers.base import SourceProvider
from app.providers.http import retry_after_seconds
from app.source_material import SourceMaterial
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
    "listening_transcript": (".pdf", ".docx", ".doc", ".rar"),
    "answer": (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ods", ".zip", ".rar"),
    "answer_sheet": (".pdf",),
    "question_cover": (".pdf", ".doc", ".docx"),
    "scoring_guidelines": (".pdf", ".doc", ".docx"),
    "scoring_examples": (".pdf", ".doc", ".docx"),
    "instructions": (".pdf", ".doc", ".docx", ".rar"),
    "corrected_answer": (".pdf", ".doc", ".zip"),
    "answer_clarification": (".pdf",),
    "all_answers": (".pdf",),
    "answer_table": (".html", ".htm"),
    "accessible_bundle": (".zip",),
    "source_archive": (".zip",),
    "source_page": (".html", ".htm"),
    "listening_audio": (".mp3", ".zip", ".rar"),
    "speaking_audio": (".mp3", ".zip", ".rar"),
}
ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
ZIP_EXTENSIONS = frozenset({".zip", ".docx", ".xlsx", ".ods"})
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
    return (
        f"{_provider_storage_prefix(page)}{page.year_roc}/"
        f"{page.source_exam_id}/exam/{attachment.file_type}"
    )


def _mirror_prefix_for_paper(page: SourceExamPage, paper: ParsedPaper, file_type: str) -> str:
    return (
        f"{_provider_storage_prefix(page)}{page.year_roc}/{page.source_exam_id}"
        f"/{paper.category_code}/{paper.subject_code}/{file_type}"
    )


def _strip_bom_prefix(data: bytes) -> bytes:
    return data[3:] if data.startswith(b"\xef\xbb\xbf") else data


def _looks_like_html(data: bytes) -> bool:
    head = _strip_bom_prefix(data[:4096]).lower()
    # Some native pages put a script or comment before their document header.
    # Recognize those prefixes without treating arbitrary embedded strings in
    # JavaScript or binary documents as an HTML document.
    return (
        re.match(
            rb"(?:\s|<!--.*?-->|<script\b[^>]*>.*?</script>)*(?:<!doctype\b|<html\b)",
            head,
            flags=re.DOTALL,
        )
        is not None
    )


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
    if expected_extension in ZIP_EXTENSIONS:
        return any(head.startswith(signature) for signature in ZIP_SIGNATURES)
    if expected_extension == ".xls":
        return head.startswith(DOC_SIGNATURE)
    if expected_extension == ".rar":
        return any(data.startswith(signature) for signature in RAR_SIGNATURES)
    if expected_extension == ".mp3":
        return data.startswith(b"ID3") or any(
            head.startswith(signature) for signature in MP3_FRAME_SYNC_PREFIXES
        )
    if expected_extension in {".jpg", ".jpeg"}:
        return head.startswith(b"\xff\xd8\xff")
    if expected_extension == ".png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if expected_extension in {".html", ".htm"}:
        return _looks_like_html(data)
    return False


def _validate_zip_payload(source: bytes | Path) -> None:
    """Check the container and every entry before accepting or reusing its bytes."""
    archive_source = io.BytesIO(source) if isinstance(source, bytes) else source
    try:
        with zipfile.ZipFile(archive_source) as archive:
            bad_entry = archive.testzip()
            if bad_entry is not None:
                raise RuntimeError(f"CRC mismatch in ZIP entry {bad_entry!r}")
    except (
        zipfile.BadZipFile,
        OSError,
        RuntimeError,
        ValueError,
        EOFError,
        NotImplementedError,
        zlib.error,
    ) as exc:
        raise RuntimeError(f"Invalid ZIP container: {exc}") from exc


def _detected_zip_extension(data: bytes) -> str:
    """Identify an unnamed ZIP without guessing from a role's format order."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())
        if "word/document.xml" in names:
            return ".docx"
        if "xl/workbook.xml" in names:
            return ".xlsx"
        if "mimetype" in names and "content.xml" in names:
            mime_info = archive.getinfo("mimetype")
            if mime_info.file_size < 256 and archive.read(mime_info).strip() == (
                b"application/vnd.oasis.opendocument.spreadsheet"
            ):
                return ".ods"
    return ".zip"


def _validated_extension(file_type: str, data: bytes, content_type: str, file_name: str) -> str:
    expected_extensions = _expected_extensions(file_type)
    resolved_extension = _extension_for(content_type, file_name).lower()
    if _looks_like_html(data) and file_type not in {"answer_table", "source_page"}:
        joined_extensions = (
            " or ".join(expected_extensions) if expected_extensions else resolved_extension
        )
        raise RuntimeError(
            f"Downloaded HTML placeholder instead of {joined_extensions} for {file_type}"
        )
    if expected_extensions:
        if _matches_expected_binary(data, ".zip"):
            _validate_zip_payload(data)
            extension = resolved_extension
            if extension not in ZIP_EXTENSIONS or extension not in expected_extensions:
                extension = _detected_zip_extension(data)
            if extension in expected_extensions:
                return extension
            raise RuntimeError(f"Downloaded {extension} container is not supported for {file_type}")
        # Prefer a supported source filename/MIME extension, then detect other
        # supported formats. A matching signature must still pass content checks.
        candidates = dict.fromkeys((resolved_extension, *expected_extensions))
        for extension in candidates:
            if extension not in expected_extensions or not _matches_expected_binary(
                data, extension
            ):
                continue
            if extension in ZIP_EXTENSIONS:
                _validate_zip_payload(data)
            return extension
        joined_extensions = " or ".join(expected_extensions)
        raise RuntimeError(
            f"Downloaded file does not match expected {joined_extensions} payload for {file_type}"
        )
    if resolved_extension in ZIP_EXTENSIONS or _matches_expected_binary(data, ".zip"):
        _validate_zip_payload(data)
    return resolved_extension


def _is_valid_stored_file(path: Path, file_type: str) -> bool:
    expected_extensions = _expected_extensions(file_type)
    actual_extension = path.suffix.lower()
    if not expected_extensions or actual_extension not in expected_extensions:
        return False
    with path.open("rb") as stream:
        if not _matches_expected_binary(stream.read(4096), actual_extension):
            return False
    if actual_extension in ZIP_EXTENSIONS:
        try:
            _validate_zip_payload(path)
        except RuntimeError:
            return False
    return True


Result = TypeVar("Result")


def retry_network(operation: Callable[[], Result], attempts: int = 3) -> Result:
    """Retry transient source requests, respecting a server's Retry-After."""
    for attempt in range(attempts):
        try:
            return operation()
        except OSError as exc:
            if isinstance(exc, HTTPError) and exc.code not in {408, 425, 429} and exc.code < 500:
                raise
            if attempt + 1 == attempts:
                raise
            delay = 2**attempt + random.uniform(0.0, 0.25)
            if isinstance(exc, HTTPError):
                retry_after = retry_after_seconds(
                    exc.headers.get("Retry-After") if exc.headers else None
                )
                if retry_after is not None:
                    delay = max(delay, retry_after)
            if delay > 120:
                raise
            time.sleep(delay)
    raise AssertionError("network retry loop did not return or raise")


def _existing_mirrored(mirror_store: MirrorStore, prefix: str, file_type: str) -> StoredFile | None:
    legacy_prefix = prefix
    if prefix.startswith("providers/"):
        _, _, legacy_prefix = prefix.split("/", 2)
    for candidate in dict.fromkeys((prefix, legacy_prefix)):
        stored = mirror_store.find_existing(candidate)
        if stored is not None and _is_valid_stored_file(stored.path, file_type):
            break
    else:
        return None
    if (
        stored is not None
        and legacy_prefix != prefix
        and stored.storage_key.startswith(legacy_prefix)
    ):
        promoted_storage_key = f"{prefix}{stored.path.suffix.lower()}"
        stored = mirror_store.write_bytes(
            promoted_storage_key, stored.path.read_bytes(), overwrite=True
        )
    return stored


def _download_validated(client: SourceProvider, file_type: str, url: str) -> tuple[bytes, str]:
    downloaded = retry_network(lambda: client.download_file(url))
    extension = _validated_extension(
        file_type, downloaded.data, downloaded.content_type, downloaded.file_name
    )
    return downloaded.data, extension


@dataclass(frozen=True)
class _RetainedSourceFile:
    storage_key: str
    checksum: str
    context: tuple[str, str, SourceMaterial | None]


@dataclass(frozen=True)
class _MirrorRequest:
    prefix: str
    file_type: str
    url: str
    paper_code: str
    attachment: ExamAttachment | None = None
    paper: ParsedPaper | None = None
    retained: _RetainedSourceFile | None = None


def _retained_source_files(
    pages: list[SourceExamPage],
) -> dict[tuple[str, str], _RetainedSourceFile]:
    """Connect cached bytes to the source URL that supplied them."""
    retained = {}
    for page in pages:
        for attachment in page.attachments:
            if attachment.checksum:
                retained[
                    (
                        _mirror_prefix_for_attachment(page, attachment),
                        attachment.download_url_source,
                    )
                ] = _RetainedSourceFile(
                    attachment.storage_key,
                    attachment.checksum,
                    (attachment.title, "", attachment.source_material or page.source_material),
                )
        for paper in page.papers:
            for file_type, url in paper.files.items():
                checksum = paper.mirror_files.get(file_type, {}).get("checksum", "")
                if checksum:
                    retained[(_mirror_prefix_for_paper(page, paper, file_type), url)] = (
                        _RetainedSourceFile(
                            paper.mirror_files[file_type].get("storage_key", ""),
                            checksum,
                            (
                                paper.category_raw,
                                paper.subject_name_raw,
                                paper.source_material or page.source_material,
                            ),
                        )
                    )
    return retained


def _request_context(
    request: _MirrorRequest,
    page: SourceExamPage,
) -> tuple[str, str, SourceMaterial | None]:
    if request.attachment is not None:
        item = request.attachment
        return item.title, "", item.source_material or page.source_material
    assert request.paper is not None
    paper = request.paper
    return paper.category_raw, paper.subject_name_raw, paper.source_material or page.source_material


def _retained_after_network_failure(
    request: _MirrorRequest,
    mirror_store: MirrorStore,
) -> StoredFile | None:
    retained = request.retained
    if retained is None or not retained.storage_key:
        return None
    key = Path(retained.storage_key)
    if key.is_absolute() or ".." in key.parts:
        return None
    owner = Path(request.prefix).parts[:2]
    if owner[0] == "providers" and key.parts[:2] != owner:
        return None
    path = mirror_store.root / key
    try:
        with path.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != retained.checksum:
                return None
        if _expected_extensions(request.file_type) and not _is_valid_stored_file(
            path, request.file_type
        ):
            return None
        return StoredFile(retained.storage_key, path, retained.checksum, False, path.stat().st_size)
    except OSError:
        return None


def _assign_mirrored_file(
    request: _MirrorRequest,
    stored: StoredFile,
    mirror_base_url: str,
    mirror_metadata: dict[tuple[str, str, str], dict[str, str]],
) -> None:
    asset_name = _asset_name_for(stored.storage_key)
    if request.attachment is not None:
        item = request.attachment
        item.storage_key = stored.storage_key
        item.asset_name = asset_name
        item.checksum = stored.checksum
        item.download_url_mirror = (
            f"{mirror_base_url.rstrip('/')}/{asset_name}" if mirror_base_url else ""
        )
        return
    assert request.paper is not None
    paper = request.paper
    metadata = {
        "storage_key": stored.storage_key,
        "asset_name": asset_name,
        "checksum": stored.checksum,
    }
    paper.mirror_files[request.file_type] = metadata
    mirror_metadata[(paper.category_code, paper.subject_code, request.file_type)] = metadata


def restore_catalog_files(
    client: SourceProvider,
    mirror_store: MirrorStore,
    catalog: NormalizedCatalog,
) -> list[SyncFailure]:
    """Restore retained files without changing their recorded identity or bytes."""
    failures: list[SyncFailure] = []
    for paper in catalog.papers:
        try:
            path = mirror_store.root / paper.storage_key
            if path.is_file() and _is_valid_stored_file(path, paper.file_type):
                if (
                    not paper.checksum
                    or hashlib.sha256(path.read_bytes()).hexdigest() == paper.checksum
                ):
                    continue
            downloaded = retry_network(partial(client.download_file, paper.download_url_source))
            extension = _validated_extension(
                paper.file_type,
                downloaded.data,
                downloaded.content_type,
                downloaded.file_name,
            )
            if extension != path.suffix.lower():
                raise RuntimeError(
                    "Retained file format changed; refresh its source exam before publication"
                )
            if paper.checksum and hashlib.sha256(downloaded.data).hexdigest() != paper.checksum:
                raise RuntimeError(
                    "Retained file checksum changed; refresh its source exam before publication"
                )
            mirror_store.write_bytes(paper.storage_key, downloaded.data, overwrite=True)
        except Exception as exc:
            failures.append(
                SyncFailure(
                    stage="download",
                    source_exam_id=paper.source_exam_id,
                    year_roc=paper.year_roc,
                    paper_code=paper.paper_code,
                    file_type=paper.file_type,
                    url=paper.download_url_source,
                    message=f"Failed to restore retained file: {exc}",
                )
            )
    return failures


def sync_exam_pages(
    client: SourceProvider,
    exam_codes: list[tuple[str, int]],
    mirror_store: MirrorStore,
    alias_rules: list[AliasRule],
    mirror_base_url: str,
    download_attachments: bool = True,
    refresh_files: bool = False,
    previous_pages: list[SourceExamPage] | None = None,
) -> tuple[list[SourceExamPage], NormalizedCatalog, list[SyncFailure]]:
    refresh_files = refresh_files or bool(getattr(client, "refresh_files_on_sync", False))
    download_attachments = download_attachments or bool(
        getattr(client, "download_attachments_on_sync", False)
    )
    raw_pages: list[SourceExamPage] = []
    normalized_papers = []
    review_queue = []
    failures: list[SyncFailure] = []
    retained_sources = _retained_source_files(previous_pages or [])

    for exam_code, year_ad in exam_codes:
        try:
            page = retry_network(partial(client.fetch_exam_page, exam_code, year_ad))
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
        requests: list[_MirrorRequest] = []
        if download_attachments:
            for attachment in page.attachments:
                requests.append(
                    _MirrorRequest(
                        prefix=_mirror_prefix_for_attachment(page, attachment),
                        file_type=attachment.file_type,
                        url=attachment.download_url_source,
                        paper_code=f"exam-{attachment.file_type}",
                        attachment=attachment,
                    )
                )

        for paper in page.papers:
            for file_type, download_url in paper.files.items():
                requests.append(
                    _MirrorRequest(
                        prefix=_mirror_prefix_for_paper(page, paper, file_type),
                        file_type=file_type,
                        url=download_url,
                        paper_code=f"{paper.category_code}-{paper.subject_code}-{file_type}",
                        paper=paper,
                    )
                )

        # Some official pages reuse subject codes for different source URLs.
        # Never let one URL's cached payload satisfy another URL in that group.
        urls_by_prefix: dict[str, set[str]] = {}
        expected_checksums: dict[str, str] = {}
        for request in requests:
            urls_by_prefix.setdefault(request.prefix, set()).add(request.url)
        for index, request in enumerate(requests):
            previous = retained_sources.get((request.prefix, request.url))
            checksum = previous.checksum if previous is not None else ""
            reviewed_checksum = getattr(client, "reviewed_file_checksum", None)
            if callable(reviewed_checksum):
                checksum = reviewed_checksum(request.url) or checksum
            prefix_path = mirror_store.root / request.prefix
            previously_split = any(prefix_path.parent.glob(f"{prefix_path.name}--source-*"))
            if len(urls_by_prefix[request.prefix]) > 1 or previously_split:
                digest = hashlib.sha256(request.url.encode("utf-8")).hexdigest()[:16]
                request = replace(request, prefix=f"{request.prefix}--source-{digest}")
            if previous is not None and previous.context == _request_context(request, page):
                request = replace(request, retained=previous)
            requests[index] = request
            expected_checksums[requests[index].prefix] = checksum

        max_workers = max(1, min(4, int(getattr(client, "max_concurrency", 4))))
        stored_by_prefix: dict[str, StoredFile] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for start in range(0, len(requests), max_workers):
                batch: list[tuple[_MirrorRequest, StoredFile | Future[tuple[bytes, str]]]] = []
                pending_by_prefix: dict[str, Future[tuple[bytes, str]]] = {}
                for request in requests[start : start + max_workers]:
                    try:
                        stored = stored_by_prefix.get(request.prefix)
                        expected_checksum = expected_checksums[request.prefix]
                        if stored is None and not refresh_files and expected_checksum:
                            stored = _existing_mirrored(
                                mirror_store, request.prefix, request.file_type
                            )
                            if stored is not None and stored.checksum != expected_checksum:
                                stored = None
                        if stored is not None:
                            stored_by_prefix[request.prefix] = stored
                            batch.append((request, stored))
                        else:
                            pending = pending_by_prefix.get(request.prefix)
                            if pending is None:
                                pending = executor.submit(
                                    _download_validated,
                                    client,
                                    request.file_type,
                                    request.url,
                                )
                                pending_by_prefix[request.prefix] = pending
                            batch.append((request, pending))
                    except Exception as exc:
                        failures.append(
                            SyncFailure(
                                stage="download",
                                source_exam_id=page.source_exam_id,
                                year_roc=page.year_roc,
                                paper_code=request.paper_code,
                                file_type=request.file_type,
                                url=request.url,
                                message=str(exc),
                            )
                        )

                # Only fetches run on workers. All MirrorStore access stays on
                # this thread because its dedupe index is mutable state.
                for request, result in batch:
                    try:
                        stored = stored_by_prefix.get(request.prefix)
                        if stored is None:
                            assert isinstance(result, Future)
                            data, extension = result.result()
                            stored = mirror_store.write_bytes(
                                f"{request.prefix}{extension}",
                                data,
                                overwrite=True,
                            )
                            mirror_store.delete_matching_except(request.prefix, stored.storage_key)
                            stored_by_prefix[request.prefix] = stored
                        _assign_mirrored_file(request, stored, mirror_base_url, mirror_metadata)
                    except Exception as exc:
                        # A failed refresh does not invalidate the exact earlier
                        # bytes for an unchanged source context. Keep the failure
                        # visible, and never use this path for invalid new content.
                        if isinstance(exc, (URLError, TimeoutError, ConnectionError)):
                            retained_file = _retained_after_network_failure(request, mirror_store)
                            if retained_file is not None:
                                _assign_mirrored_file(
                                    request, retained_file, mirror_base_url, mirror_metadata
                                )
                        failures.append(
                            SyncFailure(
                                stage="download",
                                source_exam_id=page.source_exam_id,
                                year_roc=page.year_roc,
                                paper_code=request.paper_code,
                                file_type=request.file_type,
                                url=request.url,
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
                source_material=paper.source_material,
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
            source_material=page.source_material,
        )
        if provider_id:
            for normalized_paper in normalized.papers:
                normalized_paper.provider_id = provider_id
        raw_pages.append(page)
        normalized_papers.extend(normalized.papers)
        review_queue.extend(normalized.review_queue)

    mirror_store.flush_dedupe_index()
    return (
        raw_pages,
        NormalizedCatalog(papers=normalized_papers, review_queue=review_queue),
        failures,
    )
