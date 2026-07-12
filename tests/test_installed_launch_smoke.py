from __future__ import annotations

import argparse
import ctypes
import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


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
        "audit_process_timeout": 240,
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


def test_run_audit_uses_explicit_overall_timeout(monkeypatch):
    launch = _load_launch()
    captured = {}

    class Proc:
        pid = 4321
        returncode = 0

        def communicate(self, timeout):
            captured["timeout"] = timeout
            return '{"ok": true}', ""

    monkeypatch.setattr(launch.subprocess, "Popen", lambda *args, **kwargs: Proc())

    result = launch.run_audit(_args(audit_timeout=45, audit_process_timeout=240))

    assert result["ok"] is True
    assert captured["timeout"] == 240


def test_run_audit_reports_overall_timeout_without_traceback(monkeypatch):
    launch = _load_launch()
    terminated = []

    class Proc:
        pid = 4321
        returncode = None
        calls = 0

        def communicate(self, timeout):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(["audit"], timeout)
            self.returncode = -9
            return "partial", "timed out"

    monkeypatch.setattr(launch.subprocess, "Popen", lambda *args, **kwargs: Proc())
    monkeypatch.setattr(
        launch,
        "_terminate_process_tree",
        lambda proc, guard=None: terminated.append(proc.pid),
    )

    result = launch.run_audit(_args(audit_process_timeout=240))

    assert result["ok"] is False
    assert result["returncode"] is None
    assert result["error"] == "installed app audit exceeded 240 seconds"
    assert terminated == [4321]


def test_run_audit_closes_process_tree_guard_after_success(monkeypatch):
    launch = _load_launch()
    closed = []

    class Proc:
        pid = 4321
        returncode = 0

        def communicate(self, timeout):
            return '{"ok": true}', ""

    class Guard:
        def close(self):
            closed.append("closed")

    monkeypatch.setattr(launch.subprocess, "Popen", lambda *args, **kwargs: Proc())
    monkeypatch.setattr(launch, "_process_tree_guard", lambda proc: Guard())

    result = launch.run_audit(_args())

    assert result["ok"] is True
    assert closed == ["closed"]


def test_terminate_process_tree_attempts_taskkill_after_parent_exit(monkeypatch):
    launch = _load_launch()
    commands = []

    class Proc:
        pid = 4321
        returncode = 0

        def poll(self):
            return 0

        def wait(self, timeout):
            return 0

        def kill(self):
            raise AssertionError("an exited direct process must not be killed again")

    monkeypatch.setattr(launch.os, "name", "nt")
    monkeypatch.setattr(
        launch.subprocess,
        "run",
        lambda command, **kwargs: commands.append(command),
    )

    launch._terminate_process_tree(Proc())

    assert commands == [["taskkill", "/PID", "4321", "/T", "/F"]]


def _windows_pid_exists(pid: int) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object integration")
def test_process_tree_guard_kills_child_after_parent_has_exited():
    launch = _load_launch()
    parent_code = (
        "import subprocess,sys; "
        "sys.stdin.readline(); "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        "print(child.pid, flush=True)"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=launch.creation_flags(),
    )
    guard = launch._process_tree_guard(parent)
    if guard is None:
        parent.kill()
        parent.wait(timeout=10)
        pytest.skip("Windows Job Object assignment unavailable")

    child_pid = 0
    try:
        assert parent.stdin is not None
        assert parent.stdout is not None
        parent.stdin.write("go\n")
        parent.stdin.flush()
        child_pid = int(parent.stdout.readline().strip())
        parent.wait(timeout=10)
        assert parent.returncode == 0
        assert _windows_pid_exists(child_pid)

        guard.close()
        deadline = time.monotonic() + 10
        while _windows_pid_exists(child_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _windows_pid_exists(child_pid)
    finally:
        guard.close()
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=10)
        if child_pid and _windows_pid_exists(child_pid):
            subprocess.run(
                ["taskkill", "/PID", str(child_pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                creationflags=launch.creation_flags(),
            )
