# /// script
# dependencies = ["fonttools[woff]>=4.59"]
# ///
"""Build small Traditional Chinese WOFF2 fonts from the locally installed OFL Noto CJK faces.

Run with `uv run scripts/build_frontend_fonts.py` after changing public names or UI copy.
The source TTCs come from the `fonts-noto-cjk` package; pass --font-dir for another installation.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import logging
import string
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
SITE_FEED = ROOT / "data/sites/default/frontend-bundles.json"
FONT_OUTPUT = FRONTEND / "src/assets/fonts"
FONT_SOURCES = (
    ("NotoSansCJK-Regular.ttc", "exam-sans-tc-regular.woff2", "Exam Sans TC", "Regular"),
    ("NotoSansCJK-Bold.ttc", "exam-sans-tc-bold.woff2", "Exam Sans TC", "Bold"),
    ("NotoSerifCJK-Bold.ttc", "exam-serif-tc-bold.woff2", "Exam Serif TC", "Bold"),
)


def public_characters() -> set[int]:
    feed = json.loads(SITE_FEED.read_text(encoding="utf-8"))
    catalog_texts = []
    for item in feed["bundles"]:
        catalog_texts.append(item["name"])
        catalog_texts.extend(item.get("subjectLabels", []))
    ui_texts = []
    for source in FRONTEND.glob("*.html"):
        ui_texts.append(source.read_text(encoding="utf-8"))
    for source in (FRONTEND / "src").rglob("*"):
        if source.suffix in {".tsx", ".ts", ".css"}:
            ui_texts.append(source.read_text(encoding="utf-8"))
    ui_text = "".join(ui_texts)
    catalog_text = "".join(catalog_texts)
    common_cjk = {
        character for character, _ in Counter(
            character for character in ui_text + catalog_text
            if "\u3400" <= character <= "\u9fff"
        ).most_common(500)
    }
    # Every static UI character remains covered; rare future catalog names use
    # the platform font until the subset is regenerated with updated data.
    return {ord(character) for character in ui_text + string.printable + "".join(common_cjk)}


def set_subset_names(font: TTFont, family: str, style: str) -> None:
    names = font["name"]
    postscript = family.replace(" ", "") + "-" + style
    replacements = {
        1: family, 3: f"{postscript};tw-exam-subset", 4: f"{family} {style}",
        6: postscript, 16: family, 17: style,
    }
    for record in list(names.names):
        if record.nameID in replacements:
            names.setName(replacements[record.nameID], record.nameID,
                          record.platformID, record.platEncID, record.langID)


def build_face(source: Path, target: Path, family: str, style: str, characters: set[int]) -> None:
    font = TTFont(source, fontNumber=3)
    options = subset.Options()
    options.notdef_glyph = True
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(unicodes=characters)
    subsetter.subset(font)
    set_subset_names(font, family, style)
    font.flavor = "woff2"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    font.save(temporary)
    temporary.replace(target)
    font.close()
    print(f"{target.relative_to(ROOT)}: {target.stat().st_size:,} bytes")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font-dir", type=Path,
                        default=Path("/usr/share/fonts/opentype/noto"))
    parser.add_argument("--output-dir", type=Path, default=FONT_OUTPUT)
    args = parser.parse_args()
    logging.getLogger("fontTools.subset").setLevel(logging.WARNING)
    characters = public_characters()
    print(f"Subsetting for {len(characters)} current UI and public-catalog characters")
    for source_name, target_name, family, style in FONT_SOURCES:
        source = args.font_dir / source_name
        if not source.is_file():
            parser.error(f"missing source font: {source}")
        build_face(source, args.output_dir / target_name, family, style, characters)


if __name__ == "__main__":
    main()
