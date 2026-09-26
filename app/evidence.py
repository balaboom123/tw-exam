"""Resolve repository evidence files and stable code-heading sections."""

import re
from pathlib import Path, PurePosixPath


def markdown_evidence_sections(text: str) -> set[str]:
    """Code-styled level-two headings provide stable section identifiers."""
    return set(re.findall(r"^## `([^`\n]+)`[ \t]*$", text, flags=re.MULTILINE))


def local_evidence_exists(repo_root: Path, reference: str) -> bool:
    filename, separator, section = reference.partition("#")
    logical_path = PurePosixPath(filename)
    if not filename or logical_path.is_absolute() or ".." in logical_path.parts:
        return False
    target = repo_root / filename
    if not target.is_file():
        return False
    if not separator:
        return True
    if target.suffix != ".md" or not section:
        return False
    return section in markdown_evidence_sections(target.read_text(encoding="utf-8"))
