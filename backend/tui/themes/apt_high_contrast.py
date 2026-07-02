"""
Apt High Contrast theme — maximum readability for accessibility.
"""

from textual.theme import Theme

apt_high_contrast = Theme(
    name="apt-high-contrast",
    primary="#FFFFFF",
    secondary="#FFB347",
    accent="#00BFFF",
    foreground="#FFFFFF",
    background="#000000",
    success="#00FF00",
    warning="#FFD700",
    error="#FF4444",
    surface="#1A1A1A",
    panel="#2A2A2A",
    boost="#3A3A3A",
    dark=True,
    variables={
        "block-cursor-text-style": "bold",
        "input-selection-background": "#ffffff 50%",
        "input-cursor-color": "#FFFFFF",
        "footer-key-foreground": "#FFD700",
        "text-muted": "#CCCCCC",
    },
)
