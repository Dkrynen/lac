import os
import subprocess
import pytest
from backend.cookbook import proc


@pytest.fixture(autouse=True)
def _clear_registry():
    proc._spawned_pids.clear()
    yield
    proc._spawned_pids.clear()


def test_win_kwargs_empty_off_windows(monkeypatch):
    monkeypatch.setattr(proc, "_IS_WINDOWS", False)
    assert proc._win_kwargs() == {}


@pytest.mark.skipif(os.name != "nt", reason="Windows console-hiding flags")
def test_win_kwargs_hides_console_on_windows():
    kw = proc._win_kwargs()
    assert kw["creationflags"] & subprocess.CREATE_NO_WINDOW
    assert kw["startupinfo"].wShowWindow == subprocess.SW_HIDE
    assert kw["startupinfo"].dwFlags & subprocess.STARTF_USESHOWWINDOW


def test_popen_records_pid_as_ours(monkeypatch):
    class FakeProc:
        pid = 4242
    monkeypatch.setattr(proc.subprocess, "Popen", lambda *a, **k: FakeProc())
    p = proc.popen(["anything"])
    assert p.pid == 4242
    assert proc.is_ours(4242)
    assert not proc.is_ours(9999)


def test_register_and_is_ours_coerce_int():
    proc.register_spawned("777")
    assert proc.is_ours(777)
    assert proc.is_ours("777")
