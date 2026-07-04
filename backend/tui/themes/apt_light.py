"""
Apt Light — Undergrowth light theme (soft paper + deepened green).
Values come from backend.brand, the Python mirror of web/tokens.css.
"""

from textual.theme import Theme

from backend import brand

apt_light = Theme(
    name="apt-light",
    primary=brand.LIGHT_ACCENT,
    secondary=brand.LIGHT_ACCENT_HOVER,
    accent=brand.LIGHT_INFO,
    foreground=brand.LIGHT_TEXT,
    background=brand.LIGHT_BG,
    success=brand.LIGHT_SUCCESS,
    warning=brand.LIGHT_WARNING,
    error=brand.LIGHT_DANGER,
    surface=brand.LIGHT_SURFACE,
    panel=brand.LIGHT_SURFACE_2,
    boost=brand.LIGHT_SURFACE_3,
    dark=False,
    variables={
        "block-cursor-text-style": "none",
        "input-selection-background": f"{brand.LIGHT_ACCENT} 35%",
        "input-cursor-color": brand.LIGHT_ACCENT,
        "text-muted": brand.LIGHT_TEXT_MUTED,
    },
)
