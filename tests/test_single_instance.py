import sys
import types
import pytest
from backend import desktop


def test_acquire_true_off_windows(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    assert desktop.acquire_single_instance() is True


def _fake_ctypes(last_error):
    fake = types.SimpleNamespace()
    k32 = types.SimpleNamespace()
    k32.CreateMutexW = lambda *a: 12345
    k32.GetLastError = lambda: last_error
    fake.windll = types.SimpleNamespace(kernel32=k32)
    fake.wintypes = types.SimpleNamespace(BOOL=lambda v: v)
    return fake


def test_acquire_true_first_instance(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "ctypes", _fake_ctypes(0))
    monkeypatch.setitem(sys.modules, "ctypes.wintypes", _fake_ctypes(0).wintypes)
    assert desktop.acquire_single_instance() is True


def test_acquire_false_when_already_running(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    ERROR_ALREADY_EXISTS = 183
    monkeypatch.setitem(sys.modules, "ctypes", _fake_ctypes(ERROR_ALREADY_EXISTS))
    monkeypatch.setitem(sys.modules, "ctypes.wintypes", _fake_ctypes(ERROR_ALREADY_EXISTS).wintypes)
    assert desktop.acquire_single_instance() is False


def test_focus_existing_never_raises(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    desktop.focus_existing_window()  # no exception off-Windows
