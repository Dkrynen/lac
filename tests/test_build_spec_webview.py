import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_build_spec_collects_webview():
    text = (ROOT / "build.spec").read_text(encoding="utf-8")
    assert 'collect_all("webview")' in text


def test_pywebview_importable():
    assert importlib.util.find_spec("webview") is not None
