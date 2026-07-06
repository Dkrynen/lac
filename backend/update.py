from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from enum import Enum
from pathlib import Path
from typing import Any

from backend.cookbook import proc

from .version import __version__ as CURRENT_VERSION, __github_url__, __download_url__

GITHUB_API = "https://api.github.com/repos/Dkrynen/lac/releases/latest"


class UpdateMode(Enum):
    ENABLE = "enable"
    DISABLE = "disable"
    CHECK_ONLY = "check-only"

    @classmethod
    def parse(cls, value: Any) -> "UpdateMode":
        if isinstance(value, cls):
            return value
        s = str(value).strip().lower()
        if s in ("enable", "yes", "true", "1"):
            return cls.ENABLE
        if s in ("disable", "no", "false", "0"):
            return cls.DISABLE
        return cls.CHECK_ONLY


def detect_install_method() -> str:
    if getattr(sys, "frozen", False):
        return "pyinstaller"
    exe_dir = Path(sys.executable).resolve()
    try:
        repo_root = Path(__file__).resolve().parent.parent
        if (repo_root / ".git").exists() and str(repo_root) in str(Path.cwd().resolve()):
            return "source"
    except Exception:
        pass
    if "site-packages" in str(exe_dir).lower():
        return "uv" if any(p in str(exe_dir).lower() for p in ("uv", "tools")) else "pip"
    return "source"


def _parse_version(v: str) -> tuple:
    v = v.strip().lstrip("vV")
    parts = []
    for chunk in v.split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(latest: str, current: str = CURRENT_VERSION) -> bool:
    return _parse_version(latest) > _parse_version(current)


def check_update(timeout: int = 10) -> dict | None:
    req = urllib.request.Request(GITHUB_API)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", f"LAC/{CURRENT_VERSION}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return None
    tag = data.get("tag_name", "").strip()
    if not tag:
        return None
    latest = tag.lstrip("vV")
    if not is_newer(latest):
        return None
    assets = data.get("assets", []) or []
    download_url = next((a.get("browser_download_url") for a in assets if a.get("browser_download_url")), None)
    body = (data.get("body") or "").strip()
    return {
        "latest_version": latest,
        "current_version": CURRENT_VERSION,
        "tag": tag,
        "html_url": data.get("html_url", __github_url__),
        "download_url": download_url or __download_url__,
        "changelog": body[:1500],
        "install_method": detect_install_method(),
    }


def upgrade_command(method: str | None = None) -> str:
    method = method or detect_install_method()
    if method == "pip":
        return "pip install --upgrade lac-ai"
    if method == "uv":
        return "uv pip install --upgrade lac-ai"
    if method == "pyinstaller":
        return f"download latest exe from {__download_url__}"
    return "git pull && uv pip install -r requirements.txt"


def do_update(mode: UpdateMode = UpdateMode.CHECK_ONLY) -> dict:
    info = check_update()
    method = detect_install_method()
    result = {"checked_at": True, "method": method, "mode": mode.value, "update_available": False}
    if info is None:
        return result
    result["update_available"] = True
    result["latest_version"] = info["latest_version"]
    result["changelog"] = info["changelog"]
    if mode == UpdateMode.DISABLE:
        return result
    if mode == UpdateMode.CHECK_ONLY:
        return result
    if method == "source":
        try:
            proc.run(["git", "pull"], check=True, capture_output=True, text=True, timeout=60)
            result["applied"] = True
        except Exception as e:
            result["applied"] = False
            result["error"] = f"git pull failed: {e}"
        return result
    if method in ("pip", "uv"):
        cmd = upgrade_command(method).split()
        try:
            proc_result = proc.run(cmd, check=False, capture_output=True, text=True, timeout=120)
            result["applied"] = proc_result.returncode == 0
            if proc_result.returncode != 0:
                result["error"] = (proc_result.stderr or proc_result.stdout or "")[:300]
        except Exception as e:
            result["applied"] = False
            result["error"] = str(e)
        return result
    result["applied"] = False
    result["error"] = f"automatic update not supported for install method '{method}'; download from {__download_url__}"
    return result


def configured_mode() -> UpdateMode:
    try:
        from .config import resolve_config

        raw = resolve_config().project.update.get("auto_update", "check-only")
        return UpdateMode.parse(raw)
    except Exception:
        return UpdateMode.CHECK_ONLY
