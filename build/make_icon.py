"""Generate the app icon (assets/icon.ico + assets/icon.png) with Pillow — no design
tool needed, fully reproducible:

    python build/make_icon.py

Design: a light photo card on a dark rounded tile (the viewer's own #181818
chrome), a simple landscape on the card, and a big gold star badge over the
bottom-right corner — "pick the good photos". The star is what survives at 16px.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"

# Palette
TILE = (24, 24, 24, 255)          # app chrome
TILE_EDGE = (60, 60, 60, 255)
CARD = (245, 243, 238, 255)       # photo paper
SKY = (95, 160, 220, 255)
HILL_FAR = (70, 120, 90, 255)
HILL_NEAR = (44, 88, 66, 255)
SUN = (255, 205, 90, 255)
STAR = (255, 196, 40, 255)
STAR_EDGE = (150, 100, 10, 255)


def _star_points(cx: float, cy: float, r_outer: float, r_inner: float) -> list[tuple[float, float]]:
    pts = []
    for i in range(10):
        r = r_outer if i % 2 == 0 else r_inner
        a = -math.pi / 2 + i * math.pi / 5
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def render(size: int = 1024) -> Image.Image:
    """Draw at a large size (antialiasing comes from downscaling)."""
    s = size
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    # Dark rounded tile
    pad = s * 0.04
    d.rounded_rectangle((pad, pad, s - pad, s - pad), radius=s * 0.20, fill=TILE, outline=TILE_EDGE, width=int(s * 0.012))

    # Photo card (slightly inset, small radius)
    c0, c1 = s * 0.17, s * 0.83
    d.rounded_rectangle((c0, c0, c1, c1), radius=s * 0.04, fill=CARD)

    # Picture area inside the card
    p0x, p0y, p1x, p1y = s * 0.22, s * 0.22, s * 0.78, s * 0.70
    d.rectangle((p0x, p0y, p1x, p1y), fill=SKY)
    # Sun
    sr = s * 0.055
    d.ellipse((p1x - s * 0.19 - sr, p0y + s * 0.09 - sr, p1x - s * 0.19 + sr, p0y + s * 0.09 + sr), fill=SUN)
    # Hills (two overlapping triangles), clipped to the picture area
    hills = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    hd = ImageDraw.Draw(hills)
    hd.polygon([(p0x, p1y), (p0x + s * 0.20, p0y + s * 0.16), (p0x + s * 0.42, p1y)], fill=HILL_FAR)
    hd.polygon([(p0x + s * 0.22, p1y), (p0x + s * 0.40, p0y + s * 0.24), (p1x, p1y)], fill=HILL_NEAR)
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rectangle((p0x, p0y, p1x, p1y), fill=255)
    im.paste(hills, (0, 0), Image.composite(hills.split()[3], Image.new("L", (s, s), 0), mask))

    # Gold star badge over the bottom-right corner, with a dark rim so it reads on the card
    cx, cy = s * 0.70, s * 0.72
    r_out, r_in = s * 0.24, s * 0.105
    d.polygon(_star_points(cx, cy, r_out + s * 0.03, r_in + s * 0.013), fill=TILE)
    d.polygon(_star_points(cx, cy, r_out, r_in), fill=STAR, outline=STAR_EDGE, width=int(s * 0.008))
    return im


def make_icon(out_dir: Path = ASSETS_DIR) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    big = render(1024)
    png = out_dir / "icon.png"
    big.resize((256, 256), Image.LANCZOS).save(png, "PNG")
    ico = out_dir / "icon.ico"
    # Pillow builds every size from the base image; start from 256 so the small
    # sizes are downscaled from an already-clean render.
    base = big.resize((256, 256), Image.LANCZOS)
    base.save(ico, format="ICO", sizes=[(n, n) for n in ICO_SIZES])
    return ico, png


if __name__ == "__main__":
    ico_path, png_path = make_icon()
    print(ico_path)
    print(png_path)
    sys.exit(0)
