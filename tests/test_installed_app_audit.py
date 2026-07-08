from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "installed_app_audit.py"


def _load_audit():
    spec = importlib.util.spec_from_file_location("installed_app_audit", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _args(**overrides):
    defaults = {
        "app_url": "http://lac.local",
        "edge": "",
        "timeout": 30,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_text_content_strips_scripts_styles_and_tags():
    audit = _load_audit()

    text = audit.text_content("<div>Dashboard</div><style>bad</style><script>bad</script>")

    assert text == "Dashboard"


def test_installed_app_audit_report_success(monkeypatch):
    audit = _load_audit()

    monkeypatch.setattr(audit, "check_pages", lambda args: [{"ok": True, "path": "/"}])
    monkeypatch.setattr(audit, "check_api", lambda args: [{"ok": True, "name": "version"}])
    monkeypatch.setattr(audit, "request_json", lambda base_url, path, timeout=30: (
        200,
        {
            "app_dir": audit.EXPECTED_APP_DIR,
            "models_are_bundled": False,
            "model_weight_files_in_app": [],
        },
    ))

    report = audit.build_report(_args())

    assert report["ok"] is True
    assert report["installed_app_ok"] is True
    assert report["no_bundled_weights"] is True


def test_installed_app_audit_fails_on_page_failure(monkeypatch):
    audit = _load_audit()

    monkeypatch.setattr(audit, "check_pages", lambda args: [{"ok": False, "path": "/browse"}])
    monkeypatch.setattr(audit, "check_api", lambda args: [{"ok": True, "name": "version"}])
    monkeypatch.setattr(audit, "request_json", lambda base_url, path, timeout=30: (
        200,
        {
            "app_dir": audit.EXPECTED_APP_DIR,
            "models_are_bundled": False,
            "model_weight_files_in_app": [],
        },
    ))

    report = audit.build_report(_args())

    assert report["ok"] is False
    assert report["failed_pages"][0]["path"] == "/browse"
