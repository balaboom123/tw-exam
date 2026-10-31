"""Taipei's shared government attachment transport and filename contract.

Programme, school-level and file-role decisions remain with each source adapter.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from pathlib import PurePosixPath
from urllib.parse import parse_qs, quote, unquote, urlsplit, urlunsplit

from app.providers.http import links


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
