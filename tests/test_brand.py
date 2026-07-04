"""LAC brand constants + TUI theme consistency (W3 rebrand)."""

import re

from backend import brand
from backend.tui.themes.apt_dark import apt_dark
from backend.tui.themes.apt_light import apt_light

HEX = re.compile(r"^#[0-9A-F]{6}$")

# The retired Iris palette (dark + light) must be gone from shipped themes.
IRIS = {"#6E7BF2", "#8B96F5", "#5A67E0", "#4F52D6", "#4F6BED", "#7C3AED", "#38BDF8", "#0284C7"}

DARK_NAMES = [
    "BG", "SURFACE", "SURFACE_2", "SURFACE_3", "TEXT", "TEXT_MUTED",
    "TEXT_FAINT", "ACCENT", "ACCENT_HOVER", "ACCENT_PRESSED", "ACCENT_FG",
    "SUCCESS", "WARNING", "DANGER", "INFO",
]
LIGHT_NAMES = [
    "LIGHT_BG", "LIGHT_SURFACE", "LIGHT_SURFACE_2", "LIGHT_SURFACE_3",
    "LIGHT_TEXT", "LIGHT_TEXT_MUTED", "LIGHT_TEXT_FAINT", "LIGHT_ACCENT",
    "LIGHT_ACCENT_HOVER", "LIGHT_ACCENT_PRESSED", "LIGHT_SUCCESS",
    "LIGHT_WARNING", "LIGHT_DANGER", "LIGHT_INFO",
]


def test_brand_exposes_wellformed_hex_tokens():
    for name in DARK_NAMES + LIGHT_NAMES:
        value = getattr(brand, name)
        assert HEX.match(value), f"{name}={value!r} is not #RRGGBB"


def test_green_is_the_one_accent():
    assert brand.ACCENT == "#4ADE80"
    assert brand.ACCENT_FG == "#06170D"       # dark text on accent, not white
    assert brand.LIGHT_ACCENT == "#1FA157"    # deepened for paper contrast


def test_tui_wordmark_degradation():
    assert brand.MARK == "❋"     # ❋ — the vein-leaf, in a terminal
    assert brand.WORDMARK == "lac"
    assert brand.TAGLINE == "local AI, sorted."


def test_dark_theme_wears_undergrowth():
    assert apt_dark.primary == brand.ACCENT
    assert apt_dark.background == brand.BG
    assert apt_dark.surface == brand.SURFACE
    assert apt_dark.panel == brand.SURFACE_2
    assert apt_dark.foreground == brand.TEXT
    assert apt_dark.success == brand.SUCCESS
    assert apt_dark.warning == brand.WARNING
    assert apt_dark.error == brand.DANGER
    assert apt_dark.variables["input-cursor-color"] == brand.ACCENT


def test_light_theme_wears_undergrowth():
    assert apt_light.primary == brand.LIGHT_ACCENT
    assert apt_light.background == brand.LIGHT_BG
    assert apt_light.foreground == brand.LIGHT_TEXT
    assert apt_light.variables["input-cursor-color"] == brand.LIGHT_ACCENT


def test_no_iris_left_in_shipping_themes():
    for theme in (apt_dark, apt_light):
        values = [
            theme.primary, theme.secondary, theme.accent, theme.foreground,
            theme.background, theme.surface, theme.panel, theme.boost,
            theme.success, theme.warning, theme.error,
        ]
        values += list(theme.variables.values())
        for v in values:
            assert str(v).upper() not in IRIS, f"iris leaked: {v}"
