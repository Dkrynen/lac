from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "public_readiness_gate.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("public_readiness_gate", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _args(**overrides):
    defaults = {
        "repo_root": ROOT,
        "lac_pro_root": ROOT.parent / "lac-pro",
        "app_url": "http://lac.local",
        "edge": "",
        "installed_exe": r"C:\Program Files (x86)\LAC\lac.exe",
        "model": "qwen2.5:0.5b",
        "import_repo_id": "org/model-GGUF",
        "import_quant": "Q4_K_M",
        "import_filename": "model-Q4_K_M.gguf",
        "include_live_import": False,
        "include_launch_smoke": False,
        "allow_existing_launch": False,
        "skip_source": False,
        "skip_web_build": False,
        "skip_guards": False,
        "skip_installed": False,
        "skip_live": False,
        "skip_public": True,
        "strict_public_match": False,
        "allow_missing_lac_pro": False,
        "timeout": 120,
        "source_timeout": 300,
        "web_timeout": 240,
        "installed_timeout": 180,
        "live_timeout": 180,
        "import_timeout": 900,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_public_readiness_gate_builds_default_lanes(monkeypatch):
    gate = _load_gate()
    calls = []

    def fake_run_command(name, command, cwd, timeout, lane):
        calls.append((lane, name, command, cwd))
        return {
            "lane": lane,
            "name": name,
            "ok": True,
            "returncode": 0,
            "cwd": str(cwd),
            "command": command,
            "stdout_tail": "",
            "stderr_tail": "",
        }

    monkeypatch.setattr(gate, "run_command", fake_run_command)
    monkeypatch.setattr(gate, "check_lac_pro_remote", lambda args: {
        "lane": "guards",
        "name": "lac_pro_remote_guard",
        "ok": True,
        "stdout_tail": "",
        "stderr_tail": "",
    })

    report = gate.build_report(_args())

    assert report["ok"] is True
    assert set(report["lanes"]) == {"source", "guards", "installed", "live"}
    assert any(name == "python_tests_non_live" for _, name, _, _ in calls)
    assert any(name == "web_typecheck" for _, name, _, _ in calls)
    assert any(name == "release_readiness" for _, name, _, _ in calls)
    assert any(name == "runtime_smoke" for _, name, _, _ in calls)
    assert not any(name == "live_import_stress" for _, name, _, _ in calls)


def test_public_readiness_gate_includes_live_import_when_requested(monkeypatch):
    gate = _load_gate()
    names = []

    def fake_run_command(name, command, cwd, timeout, lane):
        names.append(name)
        return {
            "lane": lane,
            "name": name,
            "ok": True,
            "returncode": 0,
            "cwd": str(cwd),
            "command": command,
            "stdout_tail": "",
            "stderr_tail": "",
        }

    monkeypatch.setattr(gate, "run_command", fake_run_command)
    monkeypatch.setattr(gate, "check_lac_pro_remote", lambda args: {
        "lane": "guards",
        "name": "lac_pro_remote_guard",
        "ok": True,
        "stdout_tail": "",
        "stderr_tail": "",
    })

    report = gate.build_report(_args(include_live_import=True))

    assert report["ok"] is True
    assert "live_import_stress" in names


def test_public_readiness_gate_includes_launch_and_strict_public_flags(monkeypatch):
    gate = _load_gate()
    commands = {}

    def fake_run_command(name, command, cwd, timeout, lane):
        commands[name] = command
        return {
            "lane": lane,
            "name": name,
            "ok": True,
            "returncode": 0,
            "cwd": str(cwd),
            "command": command,
            "stdout_tail": "",
            "stderr_tail": "",
        }

    monkeypatch.setattr(gate, "run_command", fake_run_command)
    monkeypatch.setattr(gate, "check_lac_pro_remote", lambda args: {
        "lane": "guards",
        "name": "lac_pro_remote_guard",
        "ok": True,
        "stdout_tail": "",
        "stderr_tail": "",
    })

    report = gate.build_report(_args(
        include_launch_smoke=True,
        allow_existing_launch=True,
        skip_public=False,
        strict_public_match=True,
    ))

    assert report["ok"] is True
    assert "--strict-public-match" in commands["release_readiness"]
    assert "installed_launch_smoke" in commands
    assert "--allow-existing" in commands["installed_launch_smoke"]


def test_public_readiness_gate_fails_on_lac_pro_remote(monkeypatch):
    gate = _load_gate()

    class Proc:
        returncode = 0
        stdout = "origin https://example.invalid/repo.git\n"
        stderr = ""

    monkeypatch.setattr(gate.subprocess, "run", lambda *a, **kw: Proc())

    row = gate.check_lac_pro_remote(_args(lac_pro_root=ROOT))

    assert row["ok"] is False
    assert "local-only" in row["stderr_tail"]


def test_public_readiness_gate_reports_failed_check(monkeypatch):
    gate = _load_gate()

    def fake_run_command(name, command, cwd, timeout, lane):
        return {
            "lane": lane,
            "name": name,
            "ok": name != "runtime_smoke",
            "returncode": 1 if name == "runtime_smoke" else 0,
            "cwd": str(cwd),
            "command": command,
            "stdout_tail": "",
            "stderr_tail": "boom" if name == "runtime_smoke" else "",
        }

    monkeypatch.setattr(gate, "run_command", fake_run_command)
    monkeypatch.setattr(gate, "check_lac_pro_remote", lambda args: {
        "lane": "guards",
        "name": "lac_pro_remote_guard",
        "ok": True,
        "stdout_tail": "",
        "stderr_tail": "",
    })

    report = gate.build_report(_args())

    assert report["ok"] is False
    assert report["failed"][0]["name"] == "runtime_smoke"
