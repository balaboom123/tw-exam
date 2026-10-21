from pathlib import Path

from app.providers.registry import _PROVIDER_FACTORIES
from scripts.validate_docs import _relative_link_errors, _validate_provider_notes


def test_provider_notes_distinguish_supporting_pages_from_duplicate_source_urls(tmp_path):
    from app.providers.registry import _PROVIDER_FACTORIES

    directory = tmp_path / "docs/providers"
    directory.mkdir(parents=True)
    notes = directory / "notes.md"
    inventory = {
        "site_id": "default",
        "providers": [
            {"provider_id": name, "official_source_urls": ["https://example.test/"]}
            for name in _PROVIDER_FACTORIES
        ],
    }
    notes.write_text("Supporting [rules](https://example.test/programme/rules) apply.\n")
    assert _validate_provider_notes(tmp_path, inventory) == []
    notes.write_text("Source [homepage](https://example.test/) repeated here.\n")
    errors = _validate_provider_notes(tmp_path, inventory)
    assert len(errors) == len(_PROVIDER_FACTORIES)
    assert all("duplicates inventory" in error for error in errors)


def test_source_notes_reject_unknown_providers_and_legacy_pages(tmp_path: Path) -> None:
    provider_root = tmp_path / "docs/providers"
    provider_root.mkdir(parents=True)
    notes = provider_root / "notes.md"
    notes.write_text("## `ceec_ast`\n", encoding="utf-8")
    inventory = {
        "site_id": "default",
        "providers": [{"provider_id": name, "official_source_urls": []} for name in _PROVIDER_FACTORIES],
    }
    assert _validate_provider_notes(tmp_path, inventory) == []
    notes.write_text("## `unknown_provider`\n", encoding="utf-8")
    assert any("unregistered provider sections" in error for error in _validate_provider_notes(tmp_path, inventory))
    notes.write_text("## `ceec_ast`\n", encoding="utf-8")
    (provider_root / "ceec_ast.md").write_text("# Old page\n", encoding="utf-8")
    assert any("legacy provider page remains" in error for error in _validate_provider_notes(tmp_path, inventory))


def test_documentation_links_require_the_source_note_anchor(tmp_path: Path) -> None:
    provider_root = tmp_path / "docs/providers"
    provider_root.mkdir(parents=True)
    (provider_root / "notes.md").write_text("## `ceec_ast`\n", encoding="utf-8")
    index = provider_root / "README.md"
    index.write_text("[AST](notes.md#ceec_ast)\n", encoding="utf-8")
    assert _relative_link_errors(tmp_path) == []
    index.write_text("[GSAT](notes.md#ceec_gsat)\n", encoding="utf-8")
    assert any("broken source-judgment anchor" in error for error in _relative_link_errors(tmp_path))
