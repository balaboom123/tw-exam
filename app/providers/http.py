"""Shared HTTP transport for provider adapters."""

from __future__ import annotations

import math
import random
import re
import ssl
import time
from datetime import datetime, timezone
from email.message import Message
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from pathlib import Path
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode, urljoin, urlparse
from urllib.request import HTTPCookieProcessor, HTTPSHandler, Request, build_opener, urlopen

from app.providers.base import DownloadedFile, ResponseMetadata

_HEADER_CHARSET = re.compile(r"charset=['\"]?\s*([a-zA-Z0-9_-]+)", re.IGNORECASE)
_HTML_CHARSET = re.compile(br"<meta[^>]+charset=['\"]?\s*([a-zA-Z0-9_-]+)", re.IGNORECASE)
_RETRY_STATUSES = {408, 425, 429}


class _Links(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.items: list[tuple[str, str]] = []
        self.href = ""
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.href = dict(attrs).get("href") or ""
            self.parts = []

    def handle_data(self, data: str) -> None:
        if self.href:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.href:
            self.items.append((" ".join(unescape("".join(self.parts)).split()), urljoin(self.base_url, self.href)))
            self.href = ""
            self.parts = []


def links(html: str, base_url: str) -> list[tuple[str, str]]:
    parser = _Links(base_url)
    parser.feed(html)
    return parser.items


def roc(year_ad: int) -> int:
    return year_ad - 1911


def ad(year_roc: int) -> int:
    return year_roc + 1911


def retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return max(0.0, (date - datetime.now(timezone.utc)).total_seconds())


class Http:
    def __init__(
        self,
        provider_id: str,
        *,
        ssl_context: ssl.SSLContext | None = None,
        cookies: bool = False,
        min_interval: float = 0.0,
        max_attempts: int = 3,
        user_agent: str | None = None,
    ) -> None:
        if min_interval < 0 or max_attempts < 1:
            raise ValueError("min_interval must be nonnegative and max_attempts must be positive")
        self.user_agent = user_agent or f"Mozilla/5.0 (compatible; {provider_id.replace('_', '-')}-mirror/1.0)"
        self.ssl_context = ssl_context
        self.min_interval = min_interval
        self.max_attempts = max_attempts
        self._lock = Lock()
        self._next_request = 0.0
        handlers = [HTTPCookieProcessor(CookieJar())] if cookies else []
        if cookies and ssl_context is not None:
            handlers.append(HTTPSHandler(context=ssl_context))
        self._opener = build_opener(*handlers) if cookies else None

    def _pace(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_request - now)
            self._next_request = max(now, self._next_request) + self.min_interval
        if delay:
            time.sleep(delay)

    def _open(self, request: Request, timeout: int):
        if self._opener is not None:
            return self._opener.open(request, timeout=timeout)
        if self.ssl_context is not None:
            return urlopen(request, timeout=timeout, context=self.ssl_context)
        return urlopen(request, timeout=timeout)

    def _request(
        self, url: str, *, method: str = "GET", data: bytes | None = None, timeout: int = 60,
    ) -> tuple[bytes, Message, int]:
        headers = {"User-Agent": self.user_agent}
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        for attempt in range(self.max_attempts):
            self._pace()
            request = Request(url, data=data, headers=headers, method=method)
            try:
                with self._open(request, timeout) as response:
                    return response.read() if method != "HEAD" else b"", response.headers, response.status
            except HTTPError as error:
                if (error.code not in _RETRY_STATUSES and error.code < 500) or attempt + 1 == self.max_attempts:
                    raise
                delay = retry_after_seconds(error.headers.get("Retry-After") if error.headers else None)
                if delay is not None and delay > 120:
                    raise
                error.close()
            except (URLError, TimeoutError):
                if attempt + 1 == self.max_attempts:
                    raise
                delay = None
            time.sleep(delay if delay is not None else min(30.0, 2 ** attempt + random.uniform(0, 0.5)))
        raise AssertionError("unreachable retry state")

    def get_text(self, url: str, *, encoding: str | None = None) -> str:
        raw, headers, _status = self._request(url)
        if encoding is not None:
            return raw.decode(encoding, "replace")
        content_type = headers.get("Content-Type", "")
        header_match = _HEADER_CHARSET.search(content_type)
        html_match = _HTML_CHARSET.search(raw[:2048])
        declared = [header_match.group(1) if header_match else ""]
        declared.append(html_match.group(1).decode("ascii") if html_match else "")
        for charset in (*declared, "utf-8", "big5", "cp950"):
            if not charset:
                continue
            try:
                return raw.decode(charset)
            except (LookupError, UnicodeDecodeError):
                continue
        return raw.decode("utf-8", "replace")

    def post_form(self, url: str, fields: dict[str, str], *, encoding: str | None = None) -> str:
        data = urlencode(fields).encode("ascii")
        raw, headers, _status = self._request(url, method="POST", data=data)
        if encoding is not None:
            return raw.decode(encoding, "replace")
        content_type = headers.get("Content-Type", "")
        header_match = _HEADER_CHARSET.search(content_type)
        charset = header_match.group(1) if header_match else "utf-8"
        try:
            return raw.decode(charset, "replace")
        except LookupError:
            return raw.decode("utf-8", "replace")

    def head(self, url: str) -> ResponseMetadata:
        _raw, headers, status = self._request(url, method="HEAD")
        length = headers.get("Content-Length")
        return ResponseMetadata(
            url=url,
            status=status,
            content_length=int(length) if length else None,
            content_type=headers.get("Content-Type", ""),
            content_disposition=headers.get("Content-Disposition", ""),
            cache_control=headers.get("Cache-Control", ""),
        )

    def download(
        self, url: str, *, filename_fallback: str = "", content_disposition_name: bool = True,
    ) -> DownloadedFile:
        raw, headers, _status = self._request(url, timeout=120)
        name = ""
        if content_disposition_name and (disposition := headers.get("Content-Disposition")):
            message = Message()
            message["Content-Disposition"] = disposition
            name = message.get_filename() or ""
        name = Path(name.replace("\\", "/")).name if name else ""
        if not name:
            name = Path(unquote(urlparse(url).path)).name or filename_fallback
        return DownloadedFile(
            data=raw,
            content_type=headers.get("Content-Type", "application/octet-stream"),
            file_name=name,
        )
