from __future__ import annotations

import argparse
import ctypes
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EXE = r"C:\Program Files (x86)\LAC\lac.exe"
DEFAULT_APP_URL = "http://127.0.0.1:5050"
DEFAULT_EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def read_json(url: str, timeout: int = 5) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "LAC-installed-launch-smoke/1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def probe_app(base_url: str, timeout: int = 5) -> dict[str, Any] | None:
    try:
        return read_json(base_url.rstrip("/") + "/api/system/version", timeout)
    except Exception:  # noqa: BLE001 - probe intentionally treats any miss as not running
        return None


def wait_for_app(base_url: str, deadline: float) -> dict[str, Any] | None:
    while time.monotonic() < deadline:
        payload = probe_app(base_url, timeout=2)
        if payload:
            return payload
        time.sleep(0.5)
    return None


class _WindowsProcessTreeGuard:
    """Keep a subprocess tree in a kill-on-close Windows Job Object."""

    def __init__(self, proc: subprocess.Popen[Any]) -> None:
        from ctypes import wintypes

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._kernel32 = kernel32
        self._handle = handle
        try:
            info = ExtendedLimitInformation()
            info.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(
                handle,
                9,  # JobObjectExtendedLimitInformation
                ctypes.byref(info),
                ctypes.sizeof(info),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            process_handle = wintypes.HANDLE(int(getattr(proc, "_handle")))
            if not kernel32.AssignProcessToJobObject(handle, process_handle):
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        handle = getattr(self, "_handle", None)
        if handle:
            self._handle = None
            self._kernel32.CloseHandle(handle)


def _process_tree_guard(
    proc: subprocess.Popen[Any],
) -> _WindowsProcessTreeGuard | None:
    if os.name != "nt":
        return None
    try:
        return _WindowsProcessTreeGuard(proc)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _terminate_process_tree(
    proc: subprocess.Popen[Any],
    guard: _WindowsProcessTreeGuard | None = None,
) -> None:
    """Terminate the audit and every browser descendant it created."""

    if guard is not None:
        guard.close()
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
                creationflags=creation_flags(),
            )
        except Exception:  # noqa: BLE001 - direct child kill remains the fallback
            pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    if proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


def creation_flags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_audit(args: argparse.Namespace) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(Path(args.repo_root) / "scripts" / "installed_app_audit.py"),
        "--app-url",
        args.app_url,
        "--timeout",
        str(args.audit_timeout),
    ]
    if args.edge:
        cmd.extend(["--edge", args.edge])
    overall_timeout = max(1, int(args.audit_process_timeout))
    popen_kwargs: dict[str, Any] = {
        "cwd": str(args.repo_root),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = creation_flags()
    else:
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **popen_kwargs)
    guard = _process_tree_guard(proc)
    try:
        try:
            stdout, stderr = proc.communicate(timeout=overall_timeout)
        except subprocess.TimeoutExpired:
            _terminate_process_tree(proc, guard)
            try:
                stdout, stderr = proc.communicate(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                stdout, stderr = "", ""
            return {
                "ok": False,
                "returncode": None,
                "command": cmd,
                "stdout_tail": str(stdout or "")[-3000:],
                "stderr_tail": str(stderr or "")[-3000:],
                "report": None,
                "error": f"installed app audit exceeded {overall_timeout} seconds",
            }
    finally:
        if guard is not None:
            guard.close()
    parsed: dict[str, Any] | None = None
    try:
        parsed = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        parsed = None
    return {
        "ok": proc.returncode == 0 and bool(parsed and parsed.get("ok")),
        "returncode": proc.returncode,
        "command": cmd,
        "stdout_tail": (stdout or "")[-3000:],
        "stderr_tail": (stderr or "")[-3000:],
        "report": parsed,
    }


def terminate_process(proc: subprocess.Popen[Any], timeout: int = 10) -> dict[str, Any]:
    if proc.poll() is not None:
        return {"terminated": True, "returncode": proc.returncode, "already_exited": True}
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
        return {"terminated": True, "returncode": proc.returncode, "already_exited": False}
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=timeout)
        return {"terminated": True, "returncode": proc.returncode, "already_exited": False, "killed": True}


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    exe = Path(args.exe)
    existing = probe_app(args.app_url, timeout=2)
    if existing and not args.allow_existing:
        return {
            "ok": False,
            "exe": str(exe),
            "app_url": args.app_url,
            "preexisting_app": existing,
            "error": "app already responds; stop it or pass --allow-existing for a non-launch audit",
        }
    if existing and args.allow_existing:
        audit = None if args.skip_audit else run_audit(args)
        return {
            "ok": bool(audit is None or audit.get("ok")),
            "exe": str(exe),
            "app_url": args.app_url,
            "started": False,
            "preexisting_app": existing,
            "audit": audit,
        }
    if not exe.exists():
        return {"ok": False, "exe": str(exe), "app_url": args.app_url, "error": "installed executable not found"}

    proc = subprocess.Popen(
        [str(exe)],
        cwd=str(exe.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags(),
    )
    launched = wait_for_app(args.app_url, time.monotonic() + args.launch_timeout)
    audit = None
    shutdown: dict[str, Any] | None = None
    try:
        if launched and not args.skip_audit:
            audit = run_audit(args)
    finally:
        if not args.keep_running:
            shutdown = terminate_process(proc)

    return {
        "ok": bool(launched and (audit is None or audit.get("ok")) and (args.keep_running or shutdown and shutdown.get("terminated"))),
        "exe": str(exe),
        "app_url": args.app_url,
        "started": True,
        "pid": proc.pid,
        "version": launched,
        "audit": audit,
        "shutdown": shutdown,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Start the installed LAC app, wait for the API, audit it, then shut it down.")
    p.add_argument("--exe", default=DEFAULT_EXE)
    p.add_argument("--repo-root", type=Path, default=ROOT)
    p.add_argument("--app-url", default=DEFAULT_APP_URL)
    p.add_argument("--edge", default=DEFAULT_EDGE if Path(DEFAULT_EDGE).exists() else "")
    p.add_argument("--launch-timeout", type=int, default=60)
    p.add_argument("--audit-timeout", type=int, default=30)
    p.add_argument(
        "--audit-process-timeout",
        type=int,
        default=240,
        help="Overall deadline for the full installed page/API audit.",
    )
    p.add_argument("--skip-audit", action="store_true")
    p.add_argument("--allow-existing", action="store_true", help="Run against an already responding app without proving launch.")
    p.add_argument("--keep-running", action="store_true", help="Leave the spawned app running after the smoke test.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    report = build_report(args)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
