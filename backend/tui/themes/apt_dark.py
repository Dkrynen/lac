"""
Apt Dark theme — dark theme matching the current Apt TUI design.
Uses Textual's Theme API.
"""

from textual.theme import Theme

apt_dark = Theme(
    name="apt-dark",
    primary="#6E7BF2",
    secondary="#8B96F5",
    accent="#38BDF8",
    foreground="#ECECEE",
    background="#09090B",
    success="#3DD68C",
    warning="#F5A524",
    error="#F6465D",
    surface="#0F0F13",
    panel="#15151A",
    boost="#1B1B22",
    dark=True,
    variables={
        "block-cursor-text-style": "none",
        "input-selection-background": "#6E7BF2 35%",
        "input-cursor-color": "#6E7BF2",
        "text-muted": "#A1A1AA",
    },
)
