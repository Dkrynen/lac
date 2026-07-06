#!/usr/bin/env python3
import sys
import os
import webbrowser
import threading
import time
import json
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.cookbook import proc

HOST = "127.0.0.1"
PORT = 5050

_here = Path(__file__).parent


def get_version():
    try:
        from backend.version import __version__
        return __version__
    except Exception:
        return "0.0.0"


def check_for_update(current_version: str) -> dict | None:
    try:
        url = "https://api.github.com/repos/Dkrynen/lac/releases/latest"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read().decode())
        latest = data.get("tag_name", "").lstrip("v")
        if latest and latest != current_version:
            return {
                "latest_version": latest,
                "download_url": data.get("html_url", ""),
                "release_notes": data.get("body", "")[:500],
            }
    except Exception:
        pass
    return None


def ollama_is_installed() -> bool:
    import shutil
    return shutil.which("ollama") is not None


def find_port_pids(port: int) -> list[str]:
    pids = set()
    try:
        out = proc.run(["netstat", "-ano"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return []
    for line in out.splitlines():
        s = line.strip()
        if "LISTENING" not in s.upper():
            continue
        parts = s.split()
        if len(parts) < 5:
            continue
        local = parts[1]
        if local.rsplit(":", 1)[-1] != str(port):
            continue
        pid = parts[-1]
        if pid and pid != "0":
            pids.add(pid)
    return sorted(pids)


def kill_pids(pids: list[str]) -> list[str]:
    import subprocess
    killed = []
    for pid in pids:
        try:
            if os.name == "nt":
                proc.run(["taskkill", "/F", "/T", "/PID", pid], capture_output=True, timeout=10)
            else:
                os.kill(int(pid), 9)
            killed.append(pid)
        except Exception:
            pass
    return killed


def clear_port(port: int, force: bool) -> bool:
    pids = find_port_pids(port)
    if not pids:
        return True
    print(f"  ! Port {port} is already in use by PID(s): {', '.join(pids)}")
    if not force:
        print(f"  ! This is likely a stale LAC server. Re-run with --force to kill it,")
        print(f"  ! or stop it manually:  taskkill /F /PID {pids[0]}")
        print(f"  ! (Refusing to start to avoid serving stale code.)")
        return False
    killed = kill_pids(pids)
    print(f"  ! Killed stale process(es): {', '.join(killed)}")
    time.sleep(0.5)
    return not find_port_pids(port)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="LAC web UI server")
    parser.add_argument("--host", default=HOST, help="Bind host")
    parser.add_argument("--port", type=int, default=PORT, help="Bind port")
    parser.add_argument("--force", action="store_true", help="Kill any process already using the port, then start")
    parser.add_argument("--kill-port", action="store_true", help="Kill whatever holds the port and exit")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser")
    args = parser.parse_args()

    host = args.host
    port = args.port

    if args.kill_port:
        pids = find_port_pids(port)
        if not pids:
            print(f"  Port {port} is free.")
        else:
            killed = kill_pids(pids)
            print(f"  Killed: {', '.join(killed)}")
        return

    version = get_version()
    from backend.api import run_server

    print()
    print("  +------------------------------------------+")
    print(f"  |              LAC v{version:<22} |")
    print("  |  Find your perfect local LLM              |")
    print("  +------------------------------------------+")
    print()

    if not ollama_is_installed():
        print("  ! Ollama is not installed.")
        print("  ! Download it from: https://ollama.com/download")
        print("  ! The app will still run but cannot install models.")
        print()

    update = check_for_update(version)
    if update:
        print(f"  ! Update available: v{update['latest_version']}")
        print(f"  ! Download: {update['download_url']}")
        print()

    if not clear_port(port, args.force):
        sys.exit(1)

    if not args.no_browser:
        def open_browser():
            time.sleep(1.5)
            webbrowser.open(f"http://{host}:{port}")

        threading.Thread(target=open_browser, daemon=True).start()
    run_server(host=host, port=port)


if __name__ == "__main__":
    main()
