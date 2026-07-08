from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "installed_launch_smoke.py"


def _load_launch():
    spec = importlib.util.spec_from_file_location("installed_launch_smoke", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _args(**overrides):
    defaults = {
        "exe": str(ROOT / "fake-lac.exe"),
        "repo_root": ROOT,
        "app_url": "http://lac.local",
        "edge": "",
        "launch_timeout": 5,
        "audit_timeout": 5,
        "skip_audit": False,
        "allow_existing": False,
        "keep_running": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_launch_smoke_blocks_preexisting_without_flag(monkeypatch):
    launch = _load_launch()
    monkeypatch.setattr(launch, "probe_app", lambda base_url, timeout=5: {"version": "2.6.4"})

    report = launch.build_report(_args())

    assert report["ok"] is False
    assert "already responds" in report["error"]


def test_launch_smoke_allows_existing_for_audit(monkeypatch):
    launch = _load_launch()
    monkeypatch.setattr(launch, "probe_app", lambda base_url, timeout=5: {"version": "2.6.4"})
    monkeypatch.setattr(launch, "run_audit", lambda args: {"ok": True})

    report = launch.build_report(_args(allow_existing=True))

    assert report["ok"] is True
    assert report["started"] is False
    assert report["audit"]["ok"] is True


def test_launch_smoke_reports_missing_exe(monkeypatch):
    launch = _load_launch()
    monkeypatch.setattr(launch, "probe_app", lambda base_url, timeout=5: None)

    report = launch.build_report(_args(exe=str(ROOT / "missing.exe")))

    assert report["ok"] is False
    assert report["error"] == "installed executable not found"


def test_launch_smoke_starts_audits_and_terminates(monkeypatch, tmp_path):
    launch = _load_launch()
    exe = tmp_path / "lac.exe"
    exe.write_text("", encoding="utf-8")
    probes = iter([None, {"version": "2.6.4"}])

    class Proc:
        pid = 123
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 0

        def wait(self, timeout=10):
            return self.returncode

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(launch, "probe_app", lambda base_url, timeout=5: next(probes))
    monkeypatch.setattr(launch.subprocess, "Popen", lambda *a, **kw: Proc())
    monkeypatch.setattr(launch, "run_audit", lambda args: {"ok": True})

    report = launch.build_report(_args(exe=str(exe)))

    assert report["ok"] is True
    assert report["started"] is True
    assert report["pid"] == 123
    assert report["shutdown"]["terminated"] is True
