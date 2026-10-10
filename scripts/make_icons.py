"""Website-Icons (Tab, Handy, PWA) aus einer SVG-Quelle erzeugen.

Motiv (10/2026): Kurs-Icon wie im Header (Achsen + steigende Kurslinie mit
Pfeil); die Kurslinie bildet dezent ein schräges „M“, darunter die Signatur
„MM“ (MarketMaker). Aufruf: python scripts/make_icons.py  (braucht cairosvg + Pillow,
nur lokal – nicht Teil des Deploys).
"""
import io
from pathlib import Path

import cairosvg
from PIL import Image

PUBLIC = Path(__file__).resolve().parents[1] / "frontend" / "public"

GLYPH = """
  <path d="M13 11 V51 H54" fill="none" stroke="#19E57A" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M14 43 L22 27 L28.5 36 L36 23 L40 30 L52 18" fill="none" stroke="url(#mmLine)" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M44 18 H52 V26" fill="none" stroke="#3DFF9A" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
  <g transform="translate(33 39.5) skewX(-14)" fill="none" stroke="#19E57A" stroke-opacity="0.9" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <path d="M0 7 V0 L3 4.2 L6 0 V7"/>
    <path d="M8.5 7 V0 L11.5 4.2 L14.5 0 V7"/>
  </g>"""

DEFS = """<defs>
    <linearGradient id="mmLine" x1="14" y1="44" x2="52" y2="16" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#00C46A"/><stop offset="1" stop-color="#3DFF9A"/>
    </linearGradient>
  </defs>"""


def svg(rounded: bool = True, scale: float = 1.0) -> str:
    bg = '<rect width="64" height="64" rx="14" fill="#111214"/>' if rounded \
        else '<rect width="64" height="64" fill="#111214"/>'
    off = 32 * (1 - scale)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">\n  {DEFS}\n  {bg}\n'
            f'  <g transform="translate({off:g} {off:g}) scale({scale:g})">{GLYPH}\n  </g>\n</svg>\n')


def png(src: str, size: int) -> Image.Image:
    raw = cairosvg.svg2png(bytestring=src.encode(), output_width=size, output_height=size)
    return Image.open(io.BytesIO(raw)).convert("RGBA")


def main():
    main_svg = svg()
    (PUBLIC / "favicon.svg").write_text(main_svg, encoding="utf-8")
    png(main_svg, 192).save(PUBLIC / "icon-192.png")
    png(main_svg, 512).save(PUBLIC / "icon-512.png")
    png(svg(rounded=False), 180).save(PUBLIC / "apple-touch-icon.png")
    png(svg(rounded=False, scale=0.78), 512).save(PUBLIC / "icon-maskable-512.png")
    png(main_svg, 256).save(PUBLIC / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    print("Icons geschrieben nach", PUBLIC)


if __name__ == "__main__":
    main()
