from pathlib import Path

import pytest

from app.evidence import local_evidence_exists
from app.source_inventory import _validate_evidence_paths


def test_local_evidence_requires_the_named_section(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("# Source judgment\n\n## `ceec_ast`\n\nOfficial AST archive.\n", encoding="utf-8")
    (tmp_path / "evidence.json").write_text("{}", encoding="utf-8")
    assert local_evidence_exists(tmp_path, "evidence.json")
    assert local_evidence_exists(tmp_path, "notes.md")
    assert local_evidence_exists(tmp_path, "notes.md#ceec_ast")
    for reference in ("notes.md#ceec_gsat", "notes.md#", "evidence.json#ceec_ast", "missing.md", ".", "../notes.md", "/notes.md"):
        assert not local_evidence_exists(tmp_path, reference), reference


def test_inventory_rejects_a_missing_section_without_rejecting_external_evidence(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("## `ceec_gsat`\n", encoding="utf-8")
    inventory = {
        "providers": [{"provider_id": "ceec_ast", "evidence": ["notes.md#ceec_ast"]}],
        "candidates": [{"source_id": "candidate", "evidence": ["https://official.example/archive"]}],
    }
    with pytest.raises(ValueError, match="source inventory evidence does not exist for ceec_ast"):
        _validate_evidence_paths(tmp_path, inventory)
    (tmp_path / "notes.md").write_text("## `ceec_ast`\n", encoding="utf-8")
    _validate_evidence_paths(tmp_path, inventory)
