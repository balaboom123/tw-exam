"""Apply generated PDF selections to exact reviewed native bytes.

The operator compiler owns PDF parsing and page selection. Routine acquisition
uses only bounded byte copies and literals, checked against the native parent
and reviewed output digests. Generated plans contain no examination taxonomy.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

MAX_SELECTION_BYTES = 100 * 1024 * 1024
MAX_OPERATIONS = 100_000


@dataclass(frozen=True)
class CopySpan:
    offset: int
    length: int


@dataclass(frozen=True)
class LiteralBytes:
    data: bytes


@dataclass(frozen=True)
class CompiledSelection:
    parent_checksum: str
    output_checksum: str
    output_bytes: int
    operations: tuple[CopySpan | LiteralBytes, ...]

    def apply(self, original: bytes) -> bytes:
        if hashlib.sha256(original).hexdigest() != self.parent_checksum:
            raise ValueError("Native NSYSU document changed; review its new bytes before selection")
        result = bytearray()
        for operation in self.operations:
            if isinstance(operation, CopySpan):
                end = operation.offset + operation.length
                if end > len(original):
                    raise ValueError("Compiled PDF copy range exceeds its native parent")
                result.extend(original[operation.offset : end])
            else:
                result.extend(operation.data)
            if len(result) > self.output_bytes:
                raise ValueError("Compiled PDF exceeds its reviewed size")
        if (
            len(result) != self.output_bytes
            or hashlib.sha256(result).hexdigest() != self.output_checksum
        ):
            raise ValueError("Compiled PDF differs from the reviewed selection")
        if not result.startswith(b"%PDF-"):
            raise ValueError("Compiled PDF lacks its reviewed format signature")
        return bytes(result)


def _checksum(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("Compiled PDF selection requires SHA-256 checksums")
    return value


def read_compiled_selection(
    path: Path,
    *,
    parent_checksum: str,
    parent_bytes: int,
    output_checksum: str,
    output_bytes: int,
) -> CompiledSelection:
    """Validate the complete plan against its separate reviewed source facts."""
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or set(document) != {
        "schema_version",
        "parent_SHA256",
        "output_SHA256",
        "output_bytes",
        "operations",
    }:
        raise ValueError("Invalid compiled PDF selection contract")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ValueError("Unsupported compiled PDF selection version")
    if (
        _checksum(document["parent_SHA256"]) != parent_checksum
        or _checksum(document["output_SHA256"]) != output_checksum
        or type(document["output_bytes"]) is not int
        or document["output_bytes"] != output_bytes
        or not 1 <= output_bytes <= MAX_SELECTION_BYTES
        or type(parent_bytes) is not int
        or parent_bytes < 1
    ):
        raise ValueError("Compiled PDF plan conflicts with reviewed source/output facts")
    rows = document["operations"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_OPERATIONS:
        raise ValueError("Invalid compiled PDF operation count")
    operations: list[CopySpan | LiteralBytes] = []
    produced = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid compiled PDF operation")
        if set(row) == {"copy_from", "length"}:
            offset, length = row["copy_from"], row["length"]
            if (
                type(offset) is not int
                or type(length) is not int
                or offset < 0
                or length < 1
                or offset + length > parent_bytes
            ):
                raise ValueError("Invalid compiled PDF native copy range")
            operation: CopySpan | LiteralBytes = CopySpan(offset, length)
        elif set(row) == {"literal_base64"}:
            encoded = row["literal_base64"]
            if (
                not isinstance(encoded, str)
                or len(encoded) > ((output_bytes - produced + 2) // 3) * 4
            ):
                raise ValueError("Invalid compiled PDF literal size")
            try:
                literal = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as error:
                raise ValueError("Invalid compiled PDF literal encoding") from error
            if not literal:
                raise ValueError("Empty compiled PDF literal")
            length = len(literal)
            operation = LiteralBytes(literal)
        else:
            raise ValueError("Unknown compiled PDF operation")
        produced += length
        if produced > output_bytes:
            raise ValueError("Compiled PDF plan exceeds its declared output size")
        operations.append(operation)
    if produced != output_bytes:
        raise ValueError("Compiled PDF plan has incomplete byte coverage")
    return CompiledSelection(parent_checksum, output_checksum, output_bytes, tuple(operations))


def selection_plan_path(repo_root: Path, output_checksum: str) -> Path:
    return (
        repo_root
        / "data/providers/hce_nsysu/native-selections"
        / f"{_checksum(output_checksum)}.json"
    )
