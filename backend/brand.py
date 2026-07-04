"""
Undergrowth brand constants — the Python-side source of truth for LAC's palette.

Mirrors web/tokens.css exactly. Consumed by the TUI theme value modules
(backend/tui/themes/) and by the five wordmark/banner strings in
backend/tui/app.py. The full TUI rework (screens, CSS layout, theme
selection) is W2 — this module only supplies color/text constants.

Accent discipline: green appears only where the instrument speaks —
primary actions, focus rings, live/measured numbers, deep-dive affordances,
the TUI prompt/cursor. Never decoration, never large fills.
"""

# --- Dark (default): near-black + green --------------------------------------
BG = "#08090A"
SURFACE = "#0F1210"
SURFACE_2 = "#141917"
SURFACE_3 = "#1B211D"
TEXT = "#E4E8E2"
TEXT_MUTED = "#7C8981"
TEXT_FAINT = "#545C55"

ACCENT = "#4ADE80"          # green — the one accent
ACCENT_HOVER = "#6AEB9C"
ACCENT_PRESSED = "#34C06A"
ACCENT_FG = "#06170D"       # text on accent (dark, not white)

SUCCESS = "#2DD4BF"         # teal — deliberately not the accent green
WARNING = "#D9A84C"         # warm amber, distinct from accent
DANGER = "#E5484D"
INFO = "#6FA8D8"            # desaturated blue

# --- Light (secondary): soft green-tinted paper ------------------------------
LIGHT_BG = "#F6F8F4"
LIGHT_SURFACE = "#FFFFFF"
LIGHT_SURFACE_2 = "#ECF2EA"
LIGHT_SURFACE_3 = "#E0E8DE"
LIGHT_TEXT = "#12201A"
LIGHT_TEXT_MUTED = "#55635B"
LIGHT_TEXT_FAINT = "#8A968C"

LIGHT_ACCENT = "#1FA157"    # green deepened for contrast on paper
LIGHT_ACCENT_HOVER = "#4ADE80"
LIGHT_ACCENT_PRESSED = "#178049"

LIGHT_SUCCESS = "#12876F"
LIGHT_WARNING = "#B27A26"
LIGHT_DANGER = "#D93840"
LIGHT_INFO = "#3E7A9C"

# --- Wordmark degradation (terminals have no SVG) -----------------------------
MARK = "❋"          # ❋ — the vein-leaf degrades to a green flower-glyph
WORDMARK = "lac"
TAGLINE = "local AI, sorted."
