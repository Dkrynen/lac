"""Committed Undergrowth brand assets — presence + geometry pin (W3 rebrand)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"


def test_leaf_mark_svg_pins_the_approved_geometry():
    svg = (ASSETS / "leaf-mark.svg").read_text(encoding="utf-8")
    assert 'viewBox="0 0 100 100"' in svg
    assert 'points="50,8 68,20 78,38 80,54 72,70 58,82 50,92 42,82 28,70 20,54 22,38 32,20"' in svg
    assert 'points="50,88 50,24"' in svg          # spine
    assert 'points="50,74 66,74 66,60"' in svg    # branch NE
    assert 'points="50,74 34,74 34,60"' in svg    # branch NW
    assert 'points="50,50 64,50 64,38"' in svg    # branch SE (upper)
    assert 'points="50,50 36,50 36,38"' in svg    # branch SW (upper)
    assert svg.count("#4ADE80") == 10             # 6 stroked elements + 4 via-dots
    assert 'cx="66" cy="60"' in svg
    assert 'cx="34" cy="60"' in svg
    assert 'cx="64" cy="38"' in svg
    assert 'cx="36" cy="38"' in svg


def test_mono_variant_uses_current_color_only():
    svg = (ASSETS / "leaf-mark-mono.svg").read_text(encoding="utf-8")
    assert 'viewBox="0 0 100 100"' in svg
    assert svg.count("currentColor") == 10        # 6 strokes + 4 via-dot fills
    assert "#4ADE80" not in svg


def test_raster_assets_are_committed():
    for rel in (
        "app-icon.ico",
        "favicon.ico",
        "social-preview.png",
        "icons/leaf-mark-256.png",
        "icons/app-icon-256.png",
        "icons/app-icon-16.png",
    ):
        assert (ASSETS / rel).exists(), f"missing committed asset: {rel}"
    assert (ROOT / "web" / "public" / "favicon.ico").exists()
    assert (ROOT / "web" / "public" / "favicon.svg").exists()
    assert (ROOT / "site" / "favicon.svg").exists()
