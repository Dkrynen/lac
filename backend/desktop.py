"""Native desktop window for LAC.

Boots the existing Flask app on a fixed loopback port in a daemon thread,
waits until it is serving, then opens a pywebview (WebView2) window over it.
Closing the window exits the process and the daemon server dies with it, so
orphan servers cannot accumulate. Windows-only window; no-ops elsewhere.

Non-goals (locked): no tray, no autostart, no in-window auto-update, no
multi-window.
"""
import sys
import threading
import time
import urllib.request

HOST = "127.0.0.1"
PORT = 5050
WINDOW_TITLE = "LAC"
APP_USER_MODEL_ID = "Acend.LAC"


def _serving(host: str, port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://{host}:{port}/", timeout=1)
        return True
    except Exception:
        return False


def _wait_until_serving(host: str, port: int, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _serving(host, port):
            return True
        time.sleep(0.15)
    return False


def _start_server_thread(host: str, port: int) -> None:
    from backend.api import run_server
    t = threading.Thread(target=lambda: run_server(host=host, port=port), daemon=True)
    t.start()


def _set_taskbar_identity() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def _show_startup_error(host: str, port: int) -> None:
    msg = f"LAC could not start its local server on {host}:{port}."
    print(f"  ! {msg}")
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg, "LAC", 0x10)
        except Exception:
            pass


def acquire_single_instance() -> bool:  # replaced/expanded in Task B2
    return True


def focus_existing_window() -> None:  # replaced/expanded in Task B2
    return None


def launch_desktop(host: str = HOST, port: int = PORT) -> int:
    # Single-instance FIRST: never even start a server if one is running.
    if not acquire_single_instance():
        focus_existing_window()
        return 0

    _set_taskbar_identity()
    _start_server_thread(host, port)

    if not _wait_until_serving(host, port):
        _show_startup_error(host, port)
        return 1

    return _open_window(host, port)


def _open_window(host: str, port: int) -> int:
    # Real implementation of window open + WebView2 fallback lands in Task B3.
    import webview
    webview.create_window(WINDOW_TITLE, f"http://{host}:{port}", min_size=(1024, 700))
    webview.start()
    return 0
