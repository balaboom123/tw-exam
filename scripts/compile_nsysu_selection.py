"""Compile a reviewed native PDF and reviewed selection into a byte plan.

This operator tool does not parse PDFs, choose pages, or approve identities.
Both inputs must match the separate reviewed annual evidence before any
provider-owned generated file is written. Routine sync only applies the plan.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BLOCK_BYTES = 64


def compile_selection(original: bytes, selected: bytes) -> dict[str, object]:
    from app.providers.hce_nsysu.selection_plan import MAX_SELECTION_BYTES

    if not original.startswith(b"%PDF-") or not selected.startswith(b"%PDF-"):
        raise ValueError("Both reviewed inputs must have PDF signatures")
    if not 1 <= len(original) <= MAX_SELECTION_BYTES:
        raise ValueError("Native PDF exceeds the compiler size bound")
    if not 1 <= len(selected) <= MAX_SELECTION_BYTES:
        raise ValueError("Selected PDF exceeds the compiler size bound")
    index = {}
    for offset in range(0, len(original) - BLOCK_BYTES + 1, BLOCK_BYTES):
        index.setdefault(original[offset : offset + BLOCK_BYTES], offset)
    operations = []
    literal = bytearray()

    def flush_literal() -> None:
        if literal:
            operations.append({"literal_base64": base64.b64encode(literal).decode("ascii")})
            literal.clear()

    cursor = 0
    while cursor < len(selected):
        offset = index.get(selected[cursor : cursor + BLOCK_BYTES])
        if offset is None:
            literal.append(selected[cursor])
            cursor += 1
            continue
        flush_literal()
        length = BLOCK_BYTES
        limit = min(len(original) - offset, len(selected) - cursor)
        while (
            length + 4096 <= limit
            and original[offset + length : offset + length + 4096]
            == selected[cursor + length : cursor + length + 4096]
        ):
            length += 4096
        while length < limit and original[offset + length] == selected[cursor + length]:
            length += 1
        operations.append({"copy_from": offset, "length": length})
        cursor += length
    flush_literal()
    return {
        "schema_version": 1,
        "parent_SHA256": hashlib.sha256(original).hexdigest(),
        "output_SHA256": hashlib.sha256(selected).hexdigest(),
        "output_bytes": len(selected),
        "operations": operations,
    }


def main() -> None:
    from app.providers.hce_nsysu.evidence import native_selections_path, read_annual_exams
    from app.providers.hce_nsysu.selection_plan import read_compiled_selection, selection_plan_path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--selected", type=Path, required=True)
    args = parser.parse_args()
    original = args.parent.read_bytes()
    selected = args.selected.read_bytes()
    parent_checksum = hashlib.sha256(original).hexdigest()
    output_checksum = hashlib.sha256(selected).hexdigest()
    matches = [
        (document, selection)
        for annual in read_annual_exams(native_selections_path(ROOT))
        for document in annual.documents
        if document.checksum == parent_checksum and document.size == len(original)
        for selection in document.selections
        if selection.checksum == output_checksum and selection.size == len(selected)
    ]
    if len(matches) != 1:
        raise ValueError("Inputs do not identify one separately reviewed native PDF selection")
    document, selection = matches[0]
    plan = compile_selection(original, selected)
    encoded = (json.dumps(plan, sort_keys=True, separators=(",", ":")) + "\n").encode()
    target = selection_plan_path(ROOT, selection.checksum)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".json") as temporary:
        temporary.write(encoded)
        temporary.flush()
        reconstructed = read_compiled_selection(
            Path(temporary.name),
            parent_checksum=document.checksum,
            parent_bytes=document.size,
            output_checksum=selection.checksum,
            output_bytes=selection.size,
        ).apply(original)
        if reconstructed != selected:
            raise ValueError("Compiled selection failed byte-for-byte verification")
        if target.exists():
            if target.read_bytes() != encoded:
                raise ValueError("Existing plan differs; preserve it and investigate the compiler")
        else:
            # Linking the verified temporary file publishes all bytes at once
            # and cannot replace a concurrently restored plan.
            os.link(temporary.name, target)
    print(json.dumps({"plan": str(target.relative_to(ROOT)), "output_SHA256": output_checksum}))


if __name__ == "__main__":
    main()
