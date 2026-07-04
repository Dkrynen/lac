"""
Apt Dark — Undergrowth dark theme (near-black + green).
Values come from backend.brand, the Python mirror of web/tokens.css.

Module filename and Theme `name="apt-dark"` are kept intentionally
(Global Constraints — .apt/apt.jsonc already persists "apt-dark" as a
saved user choice; only the color VALUES changed).
"""

from textual.theme import Theme

from backend import brand

apt_dark = Theme(
    name="apt-dark",
    primary=brand.ACCENT,
    secondary=brand.ACCENT_HOVER,
    accent=brand.INFO,
    foreground=brand.TEXT,
    background=brand.BG,
    success=brand.SUCCESS,
    warning=brand.WARNING,
    error=brand.DANGER,
    surface=brand.SURFACE,
    panel=brand.SURFACE_2,
    boost=brand.SURFACE_3,
    dark=True,
    variables={
        "block-cursor-text-style": "none",
        "input-selection-background": f"{brand.ACCENT} 35%",
        "input-cursor-color": brand.ACCENT,
        "text-muted": brand.TEXT_MUTED,
    },
)
