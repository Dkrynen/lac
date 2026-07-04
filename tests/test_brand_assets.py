"""Committed Undergrowth brand assets — presence + geometry pin (W3 rebrand)."""

from pathlib import Path

from PIL import Image

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
    """Every committed raster must exist AND decode to its exact expected
    pixel geometry — existence alone would let a corrupted or wrong-size
    regenerated asset pass silently (per assets/generate_icons.py's own
    render_mark/render_app_icon/render_social_preview sizing)."""

    # PNGs are square, one file per requested size — icons/{leaf-mark,app-icon}-N.png -> (N, N).
    png_expected_sizes: dict[str, tuple[int, int]] = {
        f"icons/leaf-mark-{n}.png": (n, n) for n in (16, 32, 48, 64, 128, 256, 512)
    }
    png_expected_sizes.update(
        {f"icons/app-icon-{n}.png": (n, n) for n in (16, 32, 48, 64, 128, 256, 512)}
    )
    png_expected_sizes["social-preview.png"] = (1280, 640)  # render_social_preview()

    for rel, expected_size in png_expected_sizes.items():
        path = ASSETS / rel
        assert path.exists(), f"missing committed asset: {rel}"
        with Image.open(path) as img:
            assert img.size == expected_size, (
                f"{rel}: expected {expected_size}, got {img.size}"
            )

    # .ico files embed multiple resolutions (Pillow's default `Image.open()` only
    # surfaces the largest frame's `.size`). This venv's Pillow (12.3.0) exposes
    # the full embedded-size set via both `img.info["sizes"]` and `img.ico.sizes()`
    # (verified interactively) — assert it matches exactly what
    # assets/generate_icons.py passes as `sizes=[...]` when writing each .ico.
    ico_expected_sizes = {
        "app-icon.ico": {(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)},
        "favicon.ico": {(16, 16), (32, 32), (48, 48)},
    }
    for rel, expected_sizes in ico_expected_sizes.items():
        path = ASSETS / rel
        assert path.exists(), f"missing committed asset: {rel}"
        with Image.open(path) as img:
            assert img.size[0] > 0 and img.size[1] > 0, f"{rel}: opened with zero size"
            assert img.ico.sizes() == expected_sizes, (
                f"{rel}: expected embedded sizes {expected_sizes}, got {img.ico.sizes()}"
            )

    assert (ROOT / "web" / "public" / "favicon.ico").exists()
    assert (ROOT / "web" / "public" / "favicon.svg").exists()
    assert (ROOT / "site" / "favicon.svg").exists()
