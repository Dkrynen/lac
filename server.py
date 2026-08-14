#!/usr/bin/env python3
import sys
import os
import signal
import webbrowser
import threading
import time
import json
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HOST = "127.0.0.1"
PORT = 5050

_here = Path(__file__).parent
PID_FILE = _here / "model-hub.pid"


def write_pid():
    try:
        PID_FILE.write_text(str(os.getpid()))
    except Exception:
        pass


def cleanup_pid():
    try:
        if PID_FILE.exists():
            PID_FILE.unlink()
    except Exception:
        pass


def get_version():
    try:
        from backend.version import __version__
        return __version__
    except Exception:
        return "0.0.0"


def _parse_version(v: str) -> tuple:
    parts = v.strip().lstrip("v").split(".")
    return tuple(int(p) if p.isdigit() else p for p in parts)

def check_for_update(current_version: str) -> dict | None:
    try:
        url = "https://api.github.com/repos/Dkrynen/model-hub/releases/latest"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read().decode())
        latest = data.get("tag_name", "").lstrip("v")
        if latest and _parse_version(latest) > _parse_version(current_version):
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


def stop_existing():
    if not PID_FILE.exists():
        return
    try:
        old_pid = int(PID_FILE.read_text().strip())
        if old_pid == os.getpid():
            return
        if sys.platform == "win32":
            os.system(f"taskkill /F /PID {old_pid} 2>nul")
        else:
            os.kill(old_pid, signal.SIGTERM)
        time.sleep(1)
    except Exception:
        pass


def main():
    stop_existing()
    write_pid()
    version = get_version()
    from backend.api import run_server

    print()
    print("  +------------------------------------------+")
    print(f"  |       Leaf v{version:<23} |")
    print("  |  Local LLM companion for Ollama          |")
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

    def open_browser():
        time.sleep(1.5)
        webbrowser.open(f"http://{HOST}:{PORT}")

    threading.Thread(target=open_browser, daemon=True).start()
    try:
        run_server(host=HOST, port=PORT)
    finally:
        cleanup_pid()


if __name__ == "__main__":
    main()
