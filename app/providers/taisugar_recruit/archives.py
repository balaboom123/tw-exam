"""Safe, version-bound intake of original Taiwan Sugar PDF archive members."""

from __future__ import annotations

import hashlib
import io
import re
import stat
import unicodedata
import zipfile
from urllib.parse import parse_qs, quote, urlsplit, urlunsplit

from app.archive_paths import portable_archive_name

MAX_ARCHIVE_BYTES = 200_000_000
MAX_ARCHIVE_MEMBERS = 1000


def native_member_name(item: zipfile.ZipInfo) -> str:
    """Older company Windows archives store CP950 names without the UTF-8 flag."""
    if item.flag_bits & 0x800:
        return item.filename
    try:
        decoded = item.orig_filename.encode("cp437").decode("cp950")
    except UnicodeError as exc:
        raise ValueError("Unreviewed Taiwan Sugar archive filename encoding") from exc
    # zipfile may already have applied the Unicode Path extra field. Preserve
    # its independent agreement with the legacy filename bytes.
    if item.filename != item.orig_filename and decoded != item.filename:
        raise ValueError("Conflicting Taiwan Sugar archive filename encodings")
    return decoded


def read_pdf_members(data: bytes) -> dict[str, bytes]:
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("Taiwan Sugar original archive exceeds the intake size limit")
    members = {}
    names = set()
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        entries = source.infolist()
        if (
            len(entries) > MAX_ARCHIVE_MEMBERS
            or sum(i.file_size for i in entries) > MAX_ARCHIVE_BYTES
        ):
            raise ValueError("Taiwan Sugar original archive exceeds the expanded intake limit")
        for item in entries:
            native = native_member_name(item)
            name = native.rstrip("/") if item.is_dir() else native
            folded = unicodedata.normalize("NFC", name).casefold()
            kind = stat.S_IFMT(item.external_attr >> 16)
            if (
                not portable_archive_name(name)
                or folded in names
                or kind not in {0, stat.S_IFDIR, stat.S_IFREG}
                or item.flag_bits & 1
            ):
                raise ValueError("Unsafe or duplicate Taiwan Sugar original archive member")
            names.add(folded)
            if item.is_dir():
                continue
            payload = source.read(item)
            if not name.lower().endswith(".pdf") or not payload.startswith(b"%PDF-"):
                raise ValueError(
                    "Taiwan Sugar original archive contains an unreviewed member format"
                )
            members[name] = payload
    if not members:
        raise ValueError("Taiwan Sugar original archive contains no PDF members")
    return members


def member_locator(source_url: str, checksum: str, name: str) -> str:
    return f"{source_url}#archive-sha256={checksum}&member={quote(name, safe='')}"


def parse_member_locator(url: str) -> tuple[str, str, str]:
    parts = urlsplit(url)
    values = parse_qs(parts.fragment, keep_blank_values=True, strict_parsing=True)
    if (
        parts.scheme != "https"
        or parts.netloc != "www.taisugar.com.tw"
        or re.fullmatch(r"/upload/UserFiles/News/\d+/[^/]+\.zip", parts.path, re.IGNORECASE) is None
        or parts.query
        or set(values) != {"archive-sha256", "member"}
        or any(len(v) != 1 for v in values.values())
    ):
        raise ValueError("Invalid Taiwan Sugar native member locator")
    checksum, name = values["archive-sha256"][0], values["member"][0]
    if re.fullmatch(r"[0-9a-f]{64}", checksum) is None or not portable_archive_name(name):
        raise ValueError("Invalid Taiwan Sugar native member version or path")
    return urlunsplit(parts._replace(fragment="")), checksum, name


def member_subject_code(source_url: str, name: str) -> str:
    # The acquisition reference survives a changed parent version. Its locator
    # carries the version checksum, so normal sync retains overwritten bytes.
    key = source_url + "#member=" + quote(name, safe="")
    return "taisugar-member-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
