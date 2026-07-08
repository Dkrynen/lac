from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "release_readiness.py"


def _load_release_readiness():
    spec = importlib.util.spec_from_file_location("release_readiness", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_build_workflow_stamps_inno_version_and_uploads_checksum():
    text = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    assert "-replace '1\\.0\\.0'" not in text
    assert "#define MyAppVersion" in text
    assert "SHA256SUMS.txt" in text
    assert "windows-build/SHA256SUMS.txt" in text


def test_sha256_file_reports_uppercase_digest(tmp_path):
    rr = _load_release_readiness()
    artifact = tmp_path / "LAC-Setup-test.exe"
    artifact.write_bytes(b"lac")
    assert rr.sha256_file(artifact) == "A40D4E73CD6A4DDC99F4C6A425196629C82CE3EAC00E3740EE94811EB629A93A"


def test_check_running_app_reports_version_debug_and_pro_plugin(monkeypatch):
    rr = _load_release_readiness()

    def fake_read_json(url, timeout=15):
        if url.endswith("/api/system/version"):
            return {"version": rr.APP_VERSION, "app_name": "LAC"}
        if url.endswith("/api/plugins"):
            return [{"name": "pro", "version": "0.1.0", "ok": True, "error": None}]
        raise AssertionError(url)

    def fake_read_bytes(url, timeout=15):
        assert url.endswith("/api/system/debug-bundle")
        return 200, {"content-disposition": 'attachment; filename="lac-debug.json"'}, b'{"app":{"version":"x"}}'

    monkeypatch.setattr(rr, "read_json", fake_read_json)
    monkeypatch.setattr(rr, "read_bytes", fake_read_bytes)

    result = rr.check_running_app("http://lac.local")
    assert result["ok"] is True
    assert result["debug_bundle"]["attachment"] is True
    assert result["pro_plugin"]["name"] == "pro"


def test_public_release_reports_local_size_mismatch(monkeypatch):
    rr = _load_release_readiness()

    monkeypatch.setattr(
        rr,
        "read_json",
        lambda url, timeout=20: {
            "tag_name": "v2.6.2",
            "html_url": "https://example.test/release",
            "assets": [
                {
                    "name": "LAC-Setup-2.6.2-windows-x64.exe",
                    "size": 10,
                    "browser_download_url": "https://example.test/LAC-Setup.exe",
                }
            ],
        },
    )

    result = rr.check_public_release({"size_bytes": 11})
    assert result["ok"] is True
    assert result["asset_name"] == "LAC-Setup-2.6.2-windows-x64.exe"
    assert result["local_matches_published_size"] is False
