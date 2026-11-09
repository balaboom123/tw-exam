"""Taipei's shared government attachment transport and filename contract.

Programme, school-level and file-role decisions remain with each source adapter.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import parse_qs, quote, unquote, urlsplit, urlunsplit

from app.providers.http import links


@dataclass(frozen=True)
class PaperDownload:
    subject_name: str
    subject_code: str
    file_type: str
    url: str


def group_paper_files(
    downloads: Iterable[PaperDownload],
) -> dict[tuple[str, str], dict[str, str]]:
    """Collapse repeated links; competing native files require source review."""
    grouped: dict[tuple[str, str], dict[str, str]] = {}
    for download in downloads:
        key = (download.subject_code, download.subject_name)
        files = grouped.setdefault(key, {})
        previous = files.get(download.file_type)
        if previous is not None and previous != download.url:
            raise ValueError(
                f"Competing Taipei downloads for {download.subject_name}: {download.file_type}"
            )
        files[download.file_type] = download.url
    return grouped


def request_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            quote(parts.path, safe="/%"),
            quote(parts.query, safe="=&%"),
            parts.fragment,
        )
    )


def download_name(url: str) -> str:
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    for key in ("n", "u"):
        encoded = query.get(key, [""])[0]
        if encoded:
            encoded = unquote(encoded)
            padding = "=" * (-len(encoded) % 4)
            decoded = base64.b64decode(encoded + padding).decode("utf-8", "replace")
            return PurePosixPath(decoded).name
    return PurePosixPath(unquote(parts.path)).name


def official_pdf_downloads(html: str, *, page_url: str) -> Iterator[tuple[str, str]]:
    for _label, url in links(html, page_url):
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.netloc.casefold() != "www-ws.gov.taipei"
            or parts.path.casefold() != "/download.ashx"
        ):
            continue
        name = download_name(url)
        if name.lower().endswith(".pdf"):
            yield name, url
