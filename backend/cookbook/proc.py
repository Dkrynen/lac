"""Central subprocess wrapper.

Two jobs:
1. On Windows, always hide the console window (CREATE_NO_WINDOW + a hidden
   STARTUPINFO). On a windowed PyInstaller exe, a raw subprocess call pops a
   console; routing every shell-out through here kills the terminal-flash bug.
2. Track the PIDs we spawn so kill logic (server.clear_port) can prove a target
   is ours before ever terminating it — LAC must never kill a foreign process.
"""
import os
import subprocess
import threading

_IS_WINDOWS = os.name == "nt"

# PIDs of processes THIS process launched. Consulted by kill logic.
_spawned_pids: set[int] = set()
_lock = threading.Lock()


def _win_kwargs() -> dict:
    if not _IS_WINDOWS:
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": si}


def run(cmd, **kwargs):
    """subprocess.run with the console window always hidden on Windows."""
    merged = {**_win_kwargs(), **kwargs}
    return subprocess.run(cmd, **merged)


def popen(cmd, **kwargs):
    """subprocess.Popen with the console hidden; records the child PID as ours."""
    merged = {**_win_kwargs(), **kwargs}
    p = subprocess.Popen(cmd, **merged)
    register_spawned(p.pid)
    return p


def run_interactive(cmd, **kwargs):
    """subprocess.run for an INTERACTIVE child (e.g. a terminal TUI like OpenCode):
    inherits the parent console and stdio so the user can interact. Deliberately does
    NOT hide the window -- the opposite of run()/popen(), which suppress it."""
    return subprocess.run(cmd, **kwargs)


def register_spawned(pid) -> None:
    with _lock:
        _spawned_pids.add(int(pid))


def is_ours(pid) -> bool:
    with _lock:
        return int(pid) in _spawned_pids
