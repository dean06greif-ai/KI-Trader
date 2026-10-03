"""Erzeugt alle App-Icons aus einer Logo-Variante in frontend/public/brand/.

Aufruf: python3 scripts/make_icons.py monogram|candles|bidask  (pip install cairosvg pillow)
"""
import io
import re
import shutil
import sys
from pathlib import Path

import cairosvg
from PIL import Image

PUB = Path(__file__).resolve().parents[1] / "frontend" / "public"


def png(svg: bytes, size: int) -> Image.Image:
    return Image.open(io.BytesIO(cairosvg.svg2png(bytestring=svg, output_width=size, output_height=size)))


def maskable(svg: str) -> bytes:
    # Vollflächiger Hintergrund, Motiv in der 80-%-Sicherheitszone (Android-Masken)
    inner = re.sub(r"<rect[^>]*rx=\"14\"[^>]*/>", "", svg, count=1)
    body = inner[inner.index(">") + 1:inner.rindex("</svg>")]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="#0B0F14"/>'
            f'<g transform="translate(6.4 6.4) scale(0.8)">{body}</g></svg>').encode()


def main(variant: str):
    src = PUB / "brand" / f"logo-{variant}.svg"
    svg = src.read_text()
    shutil.copy(src, PUB / "favicon.svg")
    b = svg.encode()
    png(b, 180).save(PUB / "apple-touch-icon.png")
    png(b, 192).save(PUB / "icon-192.png")
    png(b, 512).save(PUB / "icon-512.png")
    png(maskable(svg), 512).save(PUB / "icon-maskable-512.png")
    png(b, 256).save(PUB / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    print(f"Icons aus {src.name} erzeugt")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "monogram")
