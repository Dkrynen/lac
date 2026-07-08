from __future__ import annotations

import argparse
import json
import os
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
    proc = subprocess.run(
        cmd,
        cwd=str(args.repo_root),
        capture_output=True,
        text=True,
        timeout=args.audit_timeout + 60,
        check=False,
    )
    parsed: dict[str, Any] | None = None
    try:
        parsed = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        parsed = None
    return {
        "ok": proc.returncode == 0 and bool(parsed and parsed.get("ok")),
        "returncode": proc.returncode,
        "command": cmd,
        "stdout_tail": (proc.stdout or "")[-3000:],
        "stderr_tail": (proc.stderr or "")[-3000:],
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
