"""Canonical cross-platform validation for registered project roots and paths."""
from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path


MAX_PROJECT_RELATIVE_PATH_CHARS = 512
MAX_PROJECT_ROOT_CHARS = 4096
_INVALID_WINDOWS_CHARS = re.compile(r'[<>:"\\|?*\x00-\x1f\x7f]')
_RESERVED_WINDOWS_STEMS = frozenset(
    {"con", "prn", "aux", "nul", "clock$", "conin$", "conout$"}
)

_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
_WINDOWS_DRIVE_REMOTE = 4


@dataclass(frozen=True)
class ProjectRootIdentity:
    """Canonical path plus the filesystem identity captured at registration."""

    path: Path
    root_key: str
    device: str
    inode: str


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path))).casefold()


def _is_same_or_ancestor(candidate: Path, target: Path) -> bool:
    return candidate == target or candidate in target.parents


def _has_reparse_or_symlink_component(path: Path) -> bool:
    """Inspect the supplied path before ``resolve`` can hide indirection."""

    absolute = path.absolute()
    for component in (absolute, *absolute.parents):
        if component == Path(component.anchor):
            continue
        try:
            if os.path.ismount(component):
                return True
            stat_result = component.lstat()
        except OSError as exc:
            raise ValueError(
                "project root is missing, invalid, or could not be inspected for indirection"
            ) from exc
        attributes = int(getattr(stat_result, "st_file_attributes", 0) or 0)
        if stat.S_ISLNK(stat_result.st_mode) or attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            return True
    return False


def _windows_drive_type(path: Path) -> int | None:
    if os.name != "nt":
        return None
    import ctypes

    anchor = str(path.anchor or "")
    if not anchor:
        return None
    return int(ctypes.windll.kernel32.GetDriveTypeW(anchor))


def _is_windows_network_or_device_path(value: str, path: Path) -> bool:
    if os.name != "nt":
        return False
    normalized = value.replace("/", "\\")
    if normalized.startswith("\\\\"):
        return True
    return _windows_drive_type(path) == _WINDOWS_DRIVE_REMOTE


def inspect_project_root(
    value: str,
    *,
    home: Path | None = None,
    data_root: Path | None = None,
) -> ProjectRootIdentity:
    """Validate an existing local project directory without mutating it."""

    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > MAX_PROJECT_ROOT_CHARS
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("project root must be a bounded absolute path")

    supplied = Path(value)
    if not supplied.is_absolute():
        raise ValueError("project root must be absolute")
    if _is_windows_network_or_device_path(value, supplied):
        raise ValueError("project root must be on a local filesystem")
    if _has_reparse_or_symlink_component(supplied):
        raise ValueError(
            "project root contains a symlink, reparse-point, or mount indirection"
        )

    try:
        canonical = supplied.resolve(strict=True)
    except OSError as exc:
        raise ValueError("project root is missing or invalid") from exc
    if not canonical.is_dir():
        raise ValueError("project root must be an existing directory")
    if canonical == Path(canonical.anchor) or os.path.ismount(canonical):
        raise ValueError("project root cannot be a filesystem or volume root")

    resolved_home = (home or Path.home()).resolve(strict=False)
    resolved_data_root = (data_root or (resolved_home / ".model-hub")).resolve(
        strict=False
    )
    if _is_same_or_ancestor(canonical, resolved_home):
        raise ValueError("project root cannot be the user home or its ancestor")
    if (
        _is_same_or_ancestor(canonical, resolved_data_root)
        or canonical == resolved_data_root
        or resolved_data_root in canonical.parents
    ):
        raise ValueError("project root cannot expose the LAC private data root")

    try:
        stat_result = canonical.stat()
    except OSError as exc:
        raise ValueError("project root is missing or invalid") from exc
    return ProjectRootIdentity(
        path=canonical,
        root_key=_path_key(canonical),
        device=str(stat_result.st_dev),
        inode=str(stat_result.st_ino),
    )
_RESERVED_WINDOWS_PORT = re.compile(
    r"^(?:com|lpt)(?:[1-9]|\u00b9|\u00b2|\u00b3)$", re.IGNORECASE
)


def validate_relative_project_path(value: str) -> str:
    """Return one canonical POSIX relative path or raise ``ValueError``."""

    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_PROJECT_RELATIVE_PATH_CHARS
        or value.startswith("/")
        or _INVALID_WINDOWS_CHARS.search(value)
    ):
        raise ValueError("path must be a bounded portable relative path")
    parts = value.split("/")
    for part in parts:
        if not part or part in (".", "..") or part.endswith((".", " ")):
            raise ValueError("path contains a non-canonical component")
        stem = part.split(".", 1)[0].casefold()
        if stem in _RESERVED_WINDOWS_STEMS or _RESERVED_WINDOWS_PORT.fullmatch(stem):
            raise ValueError("path contains a reserved Windows component")
    return "/".join(parts)


__all__ = [
    "MAX_PROJECT_RELATIVE_PATH_CHARS",
    "MAX_PROJECT_ROOT_CHARS",
    "ProjectRootIdentity",
    "inspect_project_root",
    "validate_relative_project_path",
]
