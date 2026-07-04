"""
Generate LAC's raster brand assets from the vein-leaf geometry.

One-time generator; all outputs are COMMITTED. Re-run only when the mark
changes. Pillow is a dev-only tool (build.spec excludes PIL from the exe;
it is NOT a runtime dependency and must not enter requirements.txt).

Usage (from repo root):
    .venv\\Scripts\\python.exe -m pip install pillow
    .venv\\Scripts\\python.exe assets/generate_icons.py
"""
from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
ICONS = ASSETS / "icons"

# --- Vein-leaf geometry (design box 0..100) ----------------------------------
# MUST stay in sync with assets/leaf-mark.svg (the canonical vector).
LEAF_OUTLINE = [
    (50, 8), (68, 20), (78, 38), (80, 54), (72, 70), (58, 82),
    (50, 92), (42, 82), (28, 70), (20, 54), (22, 38), (32, 20),
]
SPINE = [(50, 88), (50, 24)]
BRANCHES = [
    [(50, 74), (66, 74), (66, 60)],
    [(50, 74), (34, 74), (34, 60)],
    [(50, 50), (64, 50), (64, 38)],
    [(50, 50), (36, 50), (36, 38)],
]
DOTS = [(66, 60), (34, 60), (64, 38), (36, 38)]
OUTLINE_WIDTH = 3.0
VEIN_WIDTH = 2.5
DOT_RADIUS = 2.5
GLYPH_CENTER_Y = 50.0  # leaf spans y 8..92 — midpoint used to vertically center

ACCENT = (0x4A, 0xDE, 0x80, 255)
PLATE = (0x08, 0x09, 0x0A, 255)  # --bg, near-black

SS = 8  # supersampling factor: render big, downscale with LANCZOS


def _draw_line(draw: ImageDraw.ImageDraw, points, ox: float, oy: float, scale: float,
                width: float, closed: bool = False) -> None:
    """Draw a polyline (optionally closed) with round caps/joins, in ACCENT."""

    def pt(p):
        return (ox + p[0] * scale, oy + p[1] * scale)

    pts = [pt(p) for p in points]
    if closed:
        pts = pts + [pts[0]]
    w = width * scale
    draw.line(pts, fill=ACCENT, width=max(1, round(w)), joint="curve")
    for x, y in pts:
        r = w / 2
        draw.ellipse([x - r, y - r, x + r, y + r], fill=ACCENT)


def _draw_mark(draw: ImageDraw.ImageDraw, ox: float, oy: float, scale: float) -> None:
    """Draw the vein-leaf mark; design box top-left at (ox, oy), scaled by `scale`."""
    _draw_line(draw, LEAF_OUTLINE, ox, oy, scale, OUTLINE_WIDTH, closed=True)
    _draw_line(draw, SPINE, ox, oy, scale, VEIN_WIDTH)
    for branch in BRANCHES:
        _draw_line(draw, branch, ox, oy, scale, VEIN_WIDTH)

    def pt(p):
        return (ox + p[0] * scale, oy + p[1] * scale)

    r = DOT_RADIUS * scale
    for dot in DOTS:
        x, y = pt(dot)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=ACCENT)


def render_mark(size: int) -> Image.Image:
    """Transparent-background mark, glyph vertically centred on the canvas."""
    big = size * SS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    scale = big / 100.0
    oy = (50.0 - GLYPH_CENTER_Y) * scale
    _draw_mark(ImageDraw.Draw(img), 0.0, oy, scale)
    return img.resize((size, size), Image.LANCZOS)


def render_app_icon(size: int) -> Image.Image:
    """Dark rounded plate + mark at 78% — Windows/app icon."""
    big = size * SS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, big - 1, big - 1], radius=big * 0.22, fill=PLATE)
    scale = big * 0.78 / 100.0
    ox = (big - 100.0 * scale) / 2
    oy = (big - 100.0 * scale) / 2 + (50.0 - GLYPH_CENTER_Y) * scale
    _draw_mark(d, ox, oy, scale)
    return img.resize((size, size), Image.LANCZOS)


def render_social_preview() -> Image.Image:
    """1280x640 GitHub social card: plate + big mark, no text.

    Text is deliberately omitted: GitHub overlays the repo name on the card,
    and rendering the wordmark would need a bundled font. Mark only.
    """
    w, h = 1280 * 2, 640 * 2
    img = Image.new("RGBA", (w, h), PLATE)
    d = ImageDraw.Draw(img)
    scale = (h * 0.62) / 100.0
    ox = (w - 100.0 * scale) / 2
    oy = (h - 100.0 * scale) / 2 + (50.0 - GLYPH_CENTER_Y) * scale
    _draw_mark(d, ox, oy, scale)
    return img.resize((1280, 640), Image.LANCZOS).convert("RGB")


def main() -> None:
    ICONS.mkdir(parents=True, exist_ok=True)

    for size in (16, 32, 48, 64, 128, 256, 512):
        render_mark(size).save(ICONS / f"leaf-mark-{size}.png")
        render_app_icon(size).save(ICONS / f"app-icon-{size}.png")

    base = render_app_icon(256)
    base.save(
        ASSETS / "app-icon.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    base.save(
        ASSETS / "favicon.ico",
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48)],
    )

    render_social_preview().save(ASSETS / "social-preview.png")

    # Copies consumed by the web app (Vite serves web/public at /) and the site
    (ROOT / "web" / "public").mkdir(exist_ok=True)
    shutil.copyfile(ASSETS / "favicon.ico", ROOT / "web" / "public" / "favicon.ico")
    shutil.copyfile(ASSETS / "leaf-mark.svg", ROOT / "web" / "public" / "favicon.svg")
    shutil.copyfile(ASSETS / "leaf-mark.svg", ROOT / "site" / "favicon.svg")

    print(f"brand assets written under {ASSETS}")


if __name__ == "__main__":
    main()
