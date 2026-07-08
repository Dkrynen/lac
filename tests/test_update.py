from __future__ import annotations

import json

from backend.update import (
    UpdateMode,
    _parse_version,
    check_update,
    detect_install_method,
    is_newer,
    select_release_download_url,
)


def test_parse_version():
    assert _parse_version("2.2.0") == (2, 2, 0)
    assert _parse_version("v2.3.1") == (2, 3, 1)
    assert _parse_version("2.10") == (2, 10, 0)
    assert _parse_version("2.3.1-rc1") == (2, 3, 1)


def test_is_newer():
    assert is_newer("2.3.0", "2.2.0")
    assert is_newer("3.0.0", "2.9.9")
    assert not is_newer("2.2.0", "2.2.0")
    assert not is_newer("2.1.0", "2.2.0")


def test_update_mode_parse():
    assert UpdateMode.parse("enable") is UpdateMode.ENABLE
    assert UpdateMode.parse("disable") is UpdateMode.DISABLE
    assert UpdateMode.parse("check-only") is UpdateMode.CHECK_ONLY
    assert UpdateMode.parse("garbage") is UpdateMode.CHECK_ONLY
    assert UpdateMode.parse(True) is UpdateMode.ENABLE


def test_detect_install_method_returns_string():
    m = detect_install_method()
    assert m in ("pip", "uv", "pyinstaller", "source")


class _FakeResp:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._payload


def test_check_update_detects_newer(monkeypatch):
    payload = {"tag_name": "v99.0.0", "html_url": "http://x", "body": "changelog", "assets": [{"browser_download_url": "http://x/exe"}]}
    monkeypatch.setattr("backend.update.urllib.request.urlopen", lambda req, timeout=10: _FakeResp(payload))
    info = check_update()
    assert info is not None
    assert info["latest_version"] == "99.0.0"
    assert info["changelog"] == "changelog"
    assert info["install_method"] in ("pip", "uv", "pyinstaller", "source")
    assert info["download_url"] == "http://x/exe"


def test_select_release_download_url_prefers_windows_installer():
    payload = {
        "assets": [
            {"name": "notes.txt", "browser_download_url": "http://x/notes.txt"},
            {"name": "LAC-Setup-2.6.2-windows-x64.exe", "browser_download_url": "http://x/setup.exe"},
            {"name": "source.zip", "browser_download_url": "http://x/source.zip"},
        ]
    }
    assert select_release_download_url(payload, "http://x/releases") == "http://x/setup.exe"


def test_select_release_download_url_falls_back_without_assets():
    assert select_release_download_url({"assets": []}, "http://x/releases") == "http://x/releases"


def test_check_update_none_when_up_to_date(monkeypatch):
    from backend import update as u

    payload = {"tag_name": "v0.0.1", "html_url": "http://x", "body": "", "assets": []}
    monkeypatch.setattr("backend.update.urllib.request.urlopen", lambda req, timeout=10: _FakeResp(payload))
    assert check_update() is None


def test_check_update_handles_network_error(monkeypatch):
    def boom(req, timeout=10):
        raise OSError("offline")
    monkeypatch.setattr("backend.update.urllib.request.urlopen", boom)
    assert check_update() is None
