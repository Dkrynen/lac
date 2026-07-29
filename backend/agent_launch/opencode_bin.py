"""Locate the stock OpenCode binary that LAC wraps. P1 requires it on PATH;
bundling / auto-fetch is a P3 packaging concern."""
import re
import shutil
import subprocess
from pathlib import Path

from backend.cookbook import proc

SUPPORTED_OPENCODE_VERSION = "1.18.9"


class OpenCodeNotFound(RuntimeError):
    pass


class OpenCodeUnsupportedVersion(RuntimeError):
    pass


_INSTALL_HINT = (
    "OpenCode is not installed or not on PATH. LAC's agent wraps it.\n"
    "Install it (see https://opencode.ai/docs), e.g.:\n"
    f"  npm i -g opencode-ai@{SUPPORTED_OPENCODE_VERSION}\n"
    "then re-run `lac agent`."
)


def _probe_version(binary: Path) -> str:
    try:
        result = proc.run(
            [str(binary), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(str(exc)) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "version probe failed").strip()
        raise RuntimeError(detail)
    match = re.search(r"\b\d+\.\d+\.\d+\b", result.stdout or "")
    if not match:
        raise RuntimeError("OpenCode returned no semantic version")
    return match.group(0)


def resolve_opencode_binary() -> Path:
    found = shutil.which("opencode")
    if not found:
        raise OpenCodeNotFound(_INSTALL_HINT)
    binary = Path(found)
    try:
        version = _probe_version(binary)
    except RuntimeError as exc:
        raise OpenCodeUnsupportedVersion(
            f"Could not verify OpenCode {SUPPORTED_OPENCODE_VERSION}: {exc}"
        ) from exc
    if version != SUPPORTED_OPENCODE_VERSION:
        raise OpenCodeUnsupportedVersion(
            f"OpenCode {version} is installed, but this LAC build supports "
            f"{SUPPORTED_OPENCODE_VERSION}. Install the supported version "
            "before running `lac agent`."
        )
    return binary
