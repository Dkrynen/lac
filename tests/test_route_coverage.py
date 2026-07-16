from __future__ import annotations

import importlib.util
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
APP_TSX = ROOT / "web" / "src" / "App.tsx"
SIDEBAR_TSX = ROOT / "web" / "src" / "components" / "sidebar.tsx"
AUDIT_SCRIPT = ROOT / "scripts" / "installed_app_audit.py"


def _load_audit():
    spec = importlib.util.spec_from_file_location("installed_app_audit", AUDIT_SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def route_paths() -> set[str]:
    text = APP_TSX.read_text(encoding="utf-8")
    return {
        match.group(1)
        for match in re.finditer(r'<Route\s+path="([^"]+)"', text)
        if match.group(1) != "*"
    }


def sidebar_paths() -> set[str]:
    text = SIDEBAR_TSX.read_text(encoding="utf-8")
    return set(re.findall(r'to:\s*"([^"]+)"', text))


def test_installed_app_audit_covers_all_first_class_routes():
    audit = _load_audit()
    audited = {path for path, _expected in audit.PAGE_ROUTES}

    assert route_paths() <= audited


def test_installed_app_audit_requires_the_cloud_activity_heading():
    audit = _load_audit()

    assert ("/cloud", "Cloud Activity") in audit.PAGE_ROUTES


def test_sidebar_links_are_real_routes():
    assert sidebar_paths() <= route_paths()
