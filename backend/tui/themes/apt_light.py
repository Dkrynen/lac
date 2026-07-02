"""
Apt Light theme — light variation of Apt's design.
"""

from textual.theme import Theme

apt_light = Theme(
    name="apt-light",
    primary="#4F6BED",
    secondary="#7C3AED",
    accent="#0284C7",
    foreground="#1E293B",
    background="#F8FAFC",
    success="#16A34A",
    warning="#CA8A04",
    error="#DC2626",
    surface="#F1F5F9",
    panel="#E2E8F0",
    boost="#CBD5E1",
    dark=False,
    variables={
        "block-cursor-text-style": "none",
        "input-selection-background": "#4f6bed 35%",
        "input-cursor-color": "#4F6BED",
        "text-muted": "#64748B",
    },
)
