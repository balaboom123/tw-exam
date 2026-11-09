"""Read reviewed native PDF members without extracting upstream ZIP paths.

The provider preserves the complete original ZIP separately. Its member locator
binds a native path to the exact parent revision; recruitment meaning is owned
by the annual evidence, not inferred by this archive reader.
"""

from __future__ import annotations

import hashlib
import io
import re
import stat
import unicodedata
import zipfile
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, urlsplit

from app.archive_paths import portable_archive_name
from app.providers.base import UnreviewedSourceFile

MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_MEMBER_BYTES = 20 * 1024 * 1024
MAX_EXPANDED_BYTES = 100 * 1024 * 1024
MAX_MEMBERS = 200


@dataclass(frozen=True)
class NativeZipMember:
    path: str
    size: int
    checksum: str


@dataclass(frozen=True)
class NativeZip:
    archive_id: str
    source_url: str
    size: int
    checksum: str
    members: tuple[NativeZipMember, ...]

    def locator(self, member: NativeZipMember) -> str:
        if member not in self.members:
            raise ValueError("Unreviewed Kaohsiung native ZIP member")
        return (
            f"{self.source_url}#archive-sha256={self.checksum}&member={quote(member.path, safe='')}"
        )


def attachment_notice(url: str) -> str:
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    if (
        parts.scheme != "https"
        or parts.netloc != "bulletin.kh.edu.tw"
        or parts.path != "/attachment.php"
        or parts.fragment
        or set(query) != {"b_sn", "a_sn"}
        or len(query["b_sn"]) != 1
        or len(query["a_sn"]) != 1
        or re.fullmatch(r"B[0-9]{9}", query["b_sn"][0]) is None
        or not query["a_sn"][0].isdigit()
        or parts.query != f"b_sn={query['b_sn'][0]}&a_sn={query['a_sn'][0]}"
    ):
        raise ValueError("Invalid Kaohsiung official attachment URL")
    return query["b_sn"][0]


def validate_zip_contract(archive: NativeZip) -> None:
    attachment_notice(archive.source_url)
    if (
        re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", archive.archive_id) is None
        or type(archive.size) is not int
        or not 0 < archive.size <= MAX_ARCHIVE_BYTES
        or re.fullmatch(r"[0-9a-f]{64}", archive.checksum) is None
        or not isinstance(archive.members, tuple)
        or not 0 < len(archive.members) <= MAX_MEMBERS
    ):
        raise ValueError("Invalid Kaohsiung native ZIP revision or budget")
    names = set()
    for member in archive.members:
        name = unicodedata.normalize("NFC", member.path).casefold()
        if (
            not portable_archive_name(member.path)
            or not member.path.lower().endswith(".pdf")
            or name in names
            or type(member.size) is not int
            or not 0 < member.size <= MAX_MEMBER_BYTES
            or re.fullmatch(r"[0-9a-f]{64}", member.checksum) is None
        ):
            raise ValueError("Unsafe or competing Kaohsiung native PDF member")
        names.add(name)
    if sum(member.size for member in archive.members) > MAX_EXPANDED_BYTES:
        raise ValueError("Kaohsiung native ZIP exceeds its expanded byte budget")


def verified_zip_members(archive: NativeZip, data: bytes) -> dict[str, bytes]:
    """Verify the complete parent and member population before exposing any PDF."""
    validate_zip_contract(archive)
    if len(data) != archive.size or hashlib.sha256(data).hexdigest() != archive.checksum:
        # A changed upstream ZIP is held for review; the previous state is retained.
        raise UnreviewedSourceFile(
            "Kaohsiung native ZIP changed; retain previous state for review",
            url=archive.source_url,
        )
    expected = {member.path: member for member in archive.members}
    payloads = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as source:
            entries = source.infolist()
            if len(entries) != len(expected) or {entry.filename for entry in entries} != set(
                expected
            ):
                raise UnreviewedSourceFile(
                    "Kaohsiung native ZIP member population changed", url=archive.source_url
                )
            for entry in entries:
                member = expected[entry.filename]
                mode = entry.external_attr >> 16
                if (
                    entry.is_dir()
                    or entry.flag_bits & 1
                    or stat.S_IFMT(mode) not in {0, stat.S_IFREG}
                    or entry.file_size != member.size
                ):
                    raise ValueError("Unsafe Kaohsiung native ZIP entry or declared size")
                with source.open(entry) as stream:
                    payload = stream.read(member.size + 1)
                if (
                    len(payload) != member.size
                    or not payload.startswith(b"%PDF-")
                    or hashlib.sha256(payload).hexdigest() != member.checksum
                ):
                    raise ValueError("Kaohsiung native PDF differs from its reviewed parent")
                payloads[member.path] = payload
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as exc:
        raise ValueError("Unreadable Kaohsiung native ZIP") from exc
    return payloads
