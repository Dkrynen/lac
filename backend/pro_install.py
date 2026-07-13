"""Generic licensed-plugin bootstrap: fetch an artifact from a license gate
and install it into the LAC plugin directory.

``lac unlock <key>`` POSTs ``{"license_key": <key>}`` to the delivery gate
(a Cloudflare Worker, see ``worker/``); a valid key streams back a plugin
artifact — a ZIP whose ROOT holds a compiled module + its ``*.dist-info/``.
Install = extract that ZIP into ``PLUGIN_DIR``, which ``backend.plugins``
prepends to ``sys.path`` before entry-point discovery, so the plugin mounts
on the next start.

This module is deliberately plugin-agnostic: it delivers ANY licensed plugin
artifact and knows nothing about what the plugin does (no tuning, benchmark,
or license logic lives here).

``install_pro_plugin`` NEVER raises — every failure returns an honest
``{"state": "failed", "error_type": ..., "message": ...}`` with one of four
error types (the CLI decides exit codes):

- ``invalid_key``  the gate rejected the key (HTTP 403 — invalid or expired)
- ``network``      the gate could not be reached at all
- ``download``     the gate answered but the artifact did not arrive intact
                   (non-200 status, truncated read, corrupt/unsafe archive)
- ``install``      the artifact was fine but writing it to disk failed
"""
from __future__ import annotations

import io
import hashlib
import hmac
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
import zlib
from pathlib import Path

#: Where licensed plugins are installed. ``backend.plugins`` puts this dir on
#: ``sys.path`` before discovery. Tests patch this module attribute.
PLUGIN_DIR = Path.home() / ".model-hub" / "plugins"

#: Public-source placeholder for the LAC Pro delivery gate.
#: Duan-gated release builds must replace this with the approved production
#: Worker URL. Source/development runs may use ``LAC_PRO_GATE_URL``; frozen
#: releases ignore overrides and trust only this baked endpoint.
PRO_GATE_URL = "https://replace-with-approved-pro-gate.example.invalid/pro/download"

GATE_TIMEOUT_S = 60


class _GateReadError(Exception):
    """The gate responded, but reading the body failed / was truncated."""


class _UnsafeArchiveError(Exception):
    """The archive is empty, corrupt, or tries to escape the install dir."""


class _ArtifactIntegrityError(Exception):
    """The gate supplied integrity metadata that is malformed or mismatched."""


def _gate_url(explicit: str | None) -> str:
    """Resolve source overrides, but pin frozen releases to the baked gate."""
    if bool(getattr(sys, "frozen", False)):
        return PRO_GATE_URL
    return explicit or os.environ.get("LAC_PRO_GATE_URL") or PRO_GATE_URL


# The delivery gate sits behind Cloudflare bot protection, which 403s the
# default "Python-urllib/x.y" User-Agent BEFORE the request ever reaches the
# Worker (the same WAF gotcha ls.py documents for the Polar client). Without a
# real UA, EVERY unlock — web and CLI — fails as "invalid_key". Send a real one.
_USER_AGENT = "LAC-Pro-Client/1.0"


def _http_post(url: str, payload: dict) -> tuple[int, bytes, list[tuple[str, str]]]:
    """POST JSON to the gate, return ``(status, body, headers)``.

    Failures to *reach* the gate (DNS, refused, timeout) propagate raw — the
    caller maps them to ``network``. A body read that fails AFTER the gate
    responded raises ``_GateReadError`` — the caller maps it to ``download``.
    Non-2xx HTTP statuses are returned, not raised.
    """
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=GATE_TIMEOUT_S)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except Exception:  # noqa: BLE001 — the status code is what matters here
            body = b""
        return exc.code, body, list(exc.headers.items()) if exc.headers else []
    try:
        with resp:
            headers = getattr(resp, "headers", None)
            return (
                resp.getcode(),
                resp.read(),
                list(headers.items()) if headers is not None else [],
            )
    except Exception as exc:  # noqa: BLE001 — connection dropped mid-body
        raise _GateReadError(str(exc)) from exc


def _normalize_gate_response(response) -> tuple[int, bytes, object]:
    """Accept legacy injected ``(status, body)`` and header-aware triples."""
    if not isinstance(response, (tuple, list)) or len(response) not in {2, 3}:
        raise _GateReadError("the gate returned an invalid response shape")
    status, body = response[0], response[1]
    headers = response[2] if len(response) == 3 else {}
    if not isinstance(status, int) or not isinstance(body, (bytes, bytearray)):
        raise _GateReadError("the gate returned an invalid status or body")
    return status, bytes(body), headers


def _integrity_header_values(headers: object) -> list[str]:
    if headers is None:
        return []
    try:
        items = headers.items() if hasattr(headers, "items") else headers
        pairs = list(items)
    except Exception as exc:  # noqa: BLE001 - untrusted injected/transport metadata
        raise _ArtifactIntegrityError("artifact integrity metadata is unreadable") from exc
    values: list[str] = []
    for pair in pairs:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            raise _ArtifactIntegrityError("artifact integrity metadata is malformed")
        name, value = pair
        if str(name).lower() == "x-lac-artifact-sha256":
            if not isinstance(value, str):
                raise _ArtifactIntegrityError("artifact integrity metadata is malformed")
            values.append(value)
    return values


def _verify_artifact_integrity(body: bytes, headers: object) -> None:
    """Require and verify the gate's raw SHA-256 before any disk write."""
    values = _integrity_header_values(headers)
    if not values:
        raise _ArtifactIntegrityError("artifact integrity metadata is missing")
    if len(values) != 1 or re.fullmatch(r"[0-9A-Fa-f]{64}", values[0]) is None:
        raise _ArtifactIntegrityError("artifact integrity metadata is malformed")
    actual = hashlib.sha256(body).hexdigest()
    if not hmac.compare_digest(values[0].lower(), actual):
        raise _ArtifactIntegrityError("artifact integrity verification failed")


_WINDOWS_RESERVED_COMPONENT = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$",
    re.IGNORECASE,
)


def _has_windows_reserved_component(name: str) -> bool:
    for component in name.replace("\\", "/").split("/"):
        normalized = component.rstrip(" .")
        if normalized and _WINDOWS_RESERVED_COMPONENT.fullmatch(normalized):
            return True
    return False


def _validate_archive(zf: zipfile.ZipFile, dest_root: Path) -> None:
    """Validate the artifact fully IN MEMORY, before any filesystem write.

    - must contain at least one entry (an empty "install" would be a lie);
    - every entry must resolve to strictly inside ``dest_root`` (zip-slip
      guard — resolve-then-``parents`` containment, the same pattern as the
      workspace path safety in ``backend/cookbook/config.py``);
    - every member's CRC must check out (``testzip`` decompresses in memory).
    """
    names = zf.namelist()
    if not names:
        raise _UnsafeArchiveError("the artifact archive is empty")
    root = dest_root.resolve()
    for name in names:
        if _has_windows_reserved_component(name):
            raise _UnsafeArchiveError(
                f"archive entry {name!r} uses a reserved Windows device name"
            )
        try:
            target = (root / name).resolve()
        except (OSError, ValueError) as exc:
            raise _UnsafeArchiveError(f"unsafe archive entry {name!r}: {exc}") from exc
        if target == root or root not in target.parents:
            raise _UnsafeArchiveError(
                f"archive entry {name!r} escapes the install directory"
            )
    bad = zf.testzip()
    if bad is not None:
        raise _UnsafeArchiveError(f"corrupt archive member: {bad!r}")


def _move_contents(staging: Path, dest: Path) -> None:
    """Move every top-level staged item into ``dest``, replacing what's there
    (re-running unlock = overwrite/upgrade). A locked or undeletable existing
    file surfaces as ``OSError`` → an ``install`` failure."""
    for item in staging.iterdir():
        target = dest / item.name
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
        shutil.move(str(item), str(target))


def _install(zf: zipfile.ZipFile, plugin_dir: Path) -> None:
    """Extract to a staging dir first, then move into place — a failure
    mid-extract leaves the plugin dir's contents exactly as they were."""
    plugin_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".lac-unlock-", dir=str(plugin_dir.parent)))
    try:
        zf.extractall(staging)
        _move_contents(staging, plugin_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _failed(error_type: str, message: str) -> dict:
    return {"state": "failed", "error_type": error_type, "message": message}


def install_pro_plugin(
    license_key: str, *, gate_url: str | None = None, http_post=None
) -> dict:
    """Fetch the licensed plugin artifact from the gate and install it.

    Returns ``{"state": "installed", "path": <plugin dir>}`` on success, or
    ``{"state": "failed", "error_type": ..., "message": ...}``. Never raises.
    """
    url = _gate_url(gate_url)
    post = http_post or _http_post

    # 1) Fetch from the gate.
    try:
        status, body, headers = _normalize_gate_response(
            post(url, {"license_key": license_key})
        )
    except _GateReadError as exc:
        return _failed(
            "download",
            f"The download was interrupted before it completed: {exc}. Try again.",
        )
    except Exception as exc:  # noqa: BLE001 — DNS/timeout/refused/any transport failure
        return _failed(
            "network",
            f"Could not reach the LAC Pro gate at {url}: {exc}. "
            "Check your connection and try again.",
        )

    # 2) Map the gate's answer to the honest states.
    if status == 403:
        return _failed(
            "invalid_key",
            "Your license key was not accepted (invalid or expired). "
            "Check the key and try again.",
        )
    if status != 200:
        return _failed(
            "download",
            f"The gate returned HTTP {status} instead of the artifact. "
            "Try again later.",
        )

    try:
        _verify_artifact_integrity(body, headers)
    except _ArtifactIntegrityError as exc:
        return _failed(
            "download",
            f"The downloaded artifact failed integrity checks: {exc}. Try again.",
        )

    # 3) Validate in memory, then install via staging.
    plugin_dir = Path(PLUGIN_DIR)  # module attribute read at call time (tests patch it)
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as zf:
            _validate_archive(zf, plugin_dir)
            _install(zf, plugin_dir)
    except (zipfile.BadZipFile, zlib.error, _UnsafeArchiveError) as exc:
        return _failed(
            "download", f"The downloaded artifact is not a valid plugin archive: {exc}"
        )
    except Exception as exc:  # noqa: BLE001 — permissions, locked files, disk full
        return _failed(
            "install",
            f"Could not install into {plugin_dir}: {exc}. "
            "If LAC is running, close it and re-run `lac unlock`.",
        )

    return {"state": "installed", "path": str(plugin_dir)}
