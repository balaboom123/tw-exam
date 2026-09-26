# /// script
# dependencies = ["pillow>=10"]
# ///
"""Render the static tw-exam social share card from local Noto CJK fonts."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "frontend/public/og-card.png"
SERIF = "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc"
SANS = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"


def main() -> None:
    image = Image.new("RGB", (1200, 630), "#f7f5ec")
    draw = ImageDraw.Draw(image)
    seal = "#a8432b"
    ink = "#2e302d"
    muted = "#64635e"
    draw.rectangle((0, 0, 18, 630), fill=seal)
    draw.rounded_rectangle((92, 105, 230, 243), radius=8, fill=seal)
    draw.text((119, 111), "試", font=ImageFont.truetype(SERIF, 91, index=3), fill="#f7f5ec")
    draw.text((264, 121), "tw-exam", font=ImageFont.truetype(SANS, 43, index=3), fill=seal)
    draw.line((92, 284, 1108, 284), fill="#d4cfc3", width=2)
    draw.text((92, 321), "台灣歷屆試題庫", font=ImageFont.truetype(SERIF, 72, index=3), fill=ink)
    draw.text((94, 437), "官方來源  ·  分類整理  ·  ZIP 下載", font=ImageFont.truetype(SANS, 32, index=3), fill=muted)
    draw.line((92, 558, 1108, 558), fill="#d4cfc3", width=2)
    draw.text((94, 574), "balaboom123.github.io/tw-exam", font=ImageFont.truetype(SANS, 20, index=3), fill=muted)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUT, optimize=True)
    print(f"{OUT.relative_to(ROOT)}: {OUT.stat().st_size:,} bytes, {image.width}×{image.height}")


if __name__ == "__main__":
    main()
