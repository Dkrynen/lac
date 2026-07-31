"""Locate the stock OpenCode binary that LAC wraps. P1 requires it on PATH;
bundling / auto-fetch is a P3 packaging concern.

Version policy (user-facing launcher only):
- ``SUPPORTED_OPENCODE_VERSION`` is the exact build the evidence pipeline
  (backend/agent_eval) verifies and stays pinned there -- evidence runs are
  only comparable on the reviewed build.
- The launcher is looser: the verified version passes silently, a NEWER patch
  in the same minor passes with a warning (OpenCode ships weekly; a hard pin
  bricks `lac agent` for anyone on the latest release), anything else fails
  with an explicit override (``LAC_OPENCODE_ALLOW_ANY=1``) for power users.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

from backend.cookbook import proc

SUPPORTED_OPENCODE_VERSION = "1.18.9"
ALLOW_ANY_ENV = "LAC_OPENCODE_ALLOW_ANY"


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

_SEMVER = re.compile(r"\b(\d+)\.(\d+)\.(\d+)\b")


def _parse_semver(version: str):
    match = _SEMVER.search(version or "")
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def version_verdict(version: str) -> tuple[str, str]:
    """Classify an installed OpenCode version against the verified pin.

    Returns ``(status, detail)`` where status is one of ``verified``
    (exact verified build), ``compatible`` (newer patch, same minor),
    ``incompatible`` (older patch, different minor/major) or
    ``unparseable`` (no semantic version found).
    """
    got = _parse_semver(version)
    if got is None:
        return ("unparseable", f"could not read a semantic version from {version!r}")
    want = _parse_semver(SUPPORTED_OPENCODE_VERSION)
    if got == want:
        return ("verified", "")
    if got[0] == want[0] and got[1] == want[1] and got > want:
        return (
            "compatible",
            f"OpenCode {version} is newer than the verified "
            f"{SUPPORTED_OPENCODE_VERSION}; proceeding.",
        )
    return (
        "incompatible",
        f"OpenCode {version} is outside the supported range "
        f"(verified {SUPPORTED_OPENCODE_VERSION}, accepts newer "
        f"{want[0]}.{want[1]}.x).",
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


def resolve_opencode_binary(warn=print) -> Path:
    """Resolve the OpenCode binary to launch, applying the version policy.

    Verified passes silently; compatible-newer passes with a warning;
    incompatible fails unless ``LAC_OPENCODE_ALLOW_ANY=1``, which passes
    with a loud UNVERIFIED warning (unparseable versions always fail).
    """
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

    status, detail = version_verdict(version)
    if status == "verified":
        return binary
    if status == "compatible":
        warn(detail)
        return binary
    if status == "unparseable":
        raise OpenCodeUnsupportedVersion(
            f"Could not verify OpenCode {SUPPORTED_OPENCODE_VERSION}: {detail}"
        )
    # incompatible
    if os.environ.get(ALLOW_ANY_ENV, "").strip() not in ("", "0"):
        warn(
            f"{ALLOW_ANY_ENV}=1: running against UNVERIFIED OpenCode {version} "
            f"(LAC verified {SUPPORTED_OPENCODE_VERSION}); evidence runs remain "
            f"pinned to the verified build."
        )
        return binary
    want = _parse_semver(SUPPORTED_OPENCODE_VERSION)
    raise OpenCodeUnsupportedVersion(
        f"OpenCode {version} is installed. LAC verified "
        f"{SUPPORTED_OPENCODE_VERSION} and accepts newer {want[0]}.{want[1]}.x "
        f"releases. Upgrade OpenCode, or set {ALLOW_ANY_ENV}=1 to use this "
        f"version anyway (unverified)."
    )
