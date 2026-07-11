"""Non-configurable file boundary for registered project operations.

Project registration grants access to source files, not to every credential or
piece of tool metadata below the selected directory.  These helpers provide a
single lexical sensitive-path policy plus bounded, no-link read/list primitives
that can be shared by agent tools and HTTP handlers.
"""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from backend.project_paths import validate_relative_project_path


MAX_PROJECT_FILE_BYTES = 2 * 1024 * 1024
MAX_PROJECT_LIST_ENTRIES = 1_000
MAX_PROJECT_LIST_SCAN_ENTRIES = 4_096

_SENSITIVE_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".ssh",
        ".aws",
        ".azure",
        ".gcloud",
        "gcloud",
        ".kube",
        ".docker",
        ".terraform",
        ".terraform.d",
        ".pulumi",
        ".secrets",
        "secrets",
        ".credentials",
        "credentials",
        ".credential",
        "credential",
        ".tokens",
        "tokens",
        ".token",
        "token",
        ".apt",
        ".model-hub",
        ".direnv",
        ".gnupg",
        ".password-store",
        ".vault",
        ".cloudflared",
        ".oci",
    }
)

_SENSITIVE_FILE_NAMES = frozenset(
    {
        ".git",
        ".npmrc",
        ".pypirc",
        ".netrc",
        ".git-credentials",
        ".vault-token",
        "credentials.json",
        "token.json",
        "docker-config.json",
        "client_secret.json",
        "client-secrets.json",
        "service-account.json",
        "service_account.json",
        "application_default_credentials.json",
        "accesstokens.json",
        "id_rsa",
        "id_rsa.pub",
        "id_ed25519",
        "id_ed25519.pub",
        "id_ecdsa",
        "id_ecdsa.pub",
        "id_dsa",
        "id_dsa.pub",
    }
)

_PRIVATE_FILE_SUFFIXES = (
    ".tfstate.backup",
    ".tfstate",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".ppk",
    ".keystore",
    ".jks",
    ".kdbx",
    ".der",
    ".crt",
    ".cer",
    ".cert",
)
_BACKUP_FILE_SUFFIXES = (".backup", ".bak", ".old")

_SECRET_DATA_SUFFIXES = frozenset(
    {
        "",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".txt",
        ".xml",
        ".env",
        ".csv",
        ".tsv",
        ".properties",
        ".tfvars",
        ".asc",
    }
)
_CLEAR_SECRET_STEMS = frozenset(
    {
        "secret",
        "secrets",
        "credential",
        "credentials",
        "token",
        "tokens",
        "api_key",
        "api-key",
        "api_token",
        "api-token",
        "access_token",
        "access-token",
        "refresh_token",
        "refresh-token",
        "client_secret",
        "client-secret",
        "service_account",
        "service-account",
        "private_key",
        "private-key",
        "signing_key",
        "signing-key",
        "ssh_key",
        "ssh-key",
        "password",
        "passwords",
    }
)


class SensitiveProjectPathError(ValueError):
    """A bounded, non-sensitive project-file failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ProjectDirectoryEntry:
    name: str
    is_dir: bool
    size: int


def _is_reparse(st: os.stat_result) -> bool:
    attributes = int(getattr(st, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(st.st_mode) or bool(attributes & reparse_flag)


def _has_multiple_hardlinks(st: os.stat_result) -> bool:
    return int(getattr(st, "st_nlink", 1) or 1) > 1


def _has_clear_secret_name(name: str) -> bool:
    # An exact secret-bearing basename stays sensitive regardless of extension
    # (for example secrets.py or token.sh). This remains narrower than generic
    # substring matching, so tokenizer.py and credential_helper.py stay usable.
    if name.split(".", 1)[0] in _CLEAR_SECRET_STEMS:
        return True
    suffix = PurePosixPath(name).suffix.casefold()
    if suffix not in _SECRET_DATA_SUFFIXES:
        return False
    stem = name[: -len(suffix)] if suffix else name
    if stem in _CLEAR_SECRET_STEMS:
        return True
    for marker in _CLEAR_SECRET_STEMS:
        for separator in ("-", "_", "."):
            if stem.startswith(marker + separator) or stem.endswith(separator + marker):
                return True
    return False


def _filename_is_sensitive(name: str) -> bool:
    if name in _SENSITIVE_FILE_NAMES or _has_clear_secret_name(name):
        return True
    if name.startswith(("id_rsa_", "id_ed25519_", "id_ecdsa_", "id_dsa_")):
        return True
    if name.endswith(_PRIVATE_FILE_SUFFIXES):
        return True
    for suffix in _BACKUP_FILE_SUFFIXES:
        if name.endswith(suffix):
            return _filename_is_sensitive(name[: -len(suffix)])
    return False


def _normalized_path_is_sensitive(relative: str) -> bool:
    parts = tuple(part.casefold() for part in PurePosixPath(relative).parts)
    if not parts:
        return True
    for part in parts:
        if part in _SENSITIVE_DIRECTORY_NAMES:
            return True
        if (
            part == ".env"
            or part.startswith(".env.")
            or part.startswith(".env-")
            or part.startswith(".envrc")
        ):
            return True
        if _filename_is_sensitive(part):
            return True
    return False


def is_sensitive_project_path(value: Any) -> bool:
    """Return True for secrets and for malformed/non-portable relative paths.

    Treating invalid input as sensitive keeps callers fail-closed and makes the
    predicate safe to use while filtering database rows or directory entries.
    The project root marker itself is not a file path and is therefore denied.
    """

    if not isinstance(value, str) or value in ("", "."):
        return True
    try:
        relative = validate_relative_project_path(value)
    except ValueError:
        return True
    return _normalized_path_is_sensitive(relative)


def _normalize_relative_path(value: Any, *, allow_root: bool) -> str:
    if allow_root and value in ("", "."):
        return "."
    if not isinstance(value, str):
        raise SensitiveProjectPathError(
            "invalid_project_path", "path must be a bounded portable relative path"
        )
    try:
        relative = validate_relative_project_path(value)
    except ValueError as exc:
        raise SensitiveProjectPathError(
            "invalid_project_path", "path must be a bounded portable relative path"
        ) from exc
    if _normalized_path_is_sensitive(relative):
        raise SensitiveProjectPathError(
            "sensitive_project_path", "sensitive project path denied"
        )
    return relative


def _canonical_project_root(root: str | os.PathLike[str]) -> tuple[Path, os.stat_result]:
    try:
        raw_root = Path(root)
    except TypeError as exc:
        raise SensitiveProjectPathError(
            "invalid_project_root", "project root is unavailable"
        ) from exc
    if not raw_root.is_absolute():
        raw_root = Path.cwd() / raw_root
    raw_root = Path(os.path.abspath(raw_root))
    try:
        root_st = raw_root.lstat()
        canonical = raw_root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SensitiveProjectPathError(
            "invalid_project_root", "project root is unavailable"
        ) from exc
    if _is_reparse(root_st) or not stat.S_ISDIR(root_st.st_mode):
        raise SensitiveProjectPathError(
            "unsafe_project_root", "project root is unsafe"
        )
    return canonical, root_st


def resolve_project_path(
    root: str | os.PathLike[str],
    value: Any,
    *,
    allow_root: bool = False,
) -> tuple[Path, str]:
    """Resolve a portable project-relative path without following links.

    Missing final paths and missing parents are allowed so the same primitive
    can protect staged writes.  Every component that already exists must remain
    on the root device and must not be a symlink or Windows reparse point.
    """

    canonical_root, root_st = _canonical_project_root(root)
    relative = _normalize_relative_path(value, allow_root=allow_root)
    if relative == ".":
        return canonical_root, relative

    parts = PurePosixPath(relative).parts
    current = canonical_root
    for index, part in enumerate(parts):
        current = current / part
        try:
            current_st = current.lstat()
        except FileNotFoundError:
            break
        except OSError as exc:
            raise SensitiveProjectPathError(
                "unsafe_project_path", "project path could not be inspected"
            ) from exc
        if _is_reparse(current_st) or current_st.st_dev != root_st.st_dev:
            raise SensitiveProjectPathError(
                "unsafe_project_path", "linked project path denied"
            )
        if (
            index == len(parts) - 1
            and stat.S_ISREG(current_st.st_mode)
            and _has_multiple_hardlinks(current_st)
        ):
            raise SensitiveProjectPathError(
                "unsafe_project_path", "project hard link denied"
            )
        if index < len(parts) - 1 and not stat.S_ISDIR(current_st.st_mode):
            raise SensitiveProjectPathError(
                "unsafe_project_path", "project path parent is not a directory"
            )

    target = canonical_root.joinpath(*parts)
    try:
        resolved = target.resolve(strict=False)
        resolved.relative_to(canonical_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise SensitiveProjectPathError(
            "unsafe_project_path", "project path escapes its root"
        ) from exc
    if resolved != target:
        raise SensitiveProjectPathError(
            "unsafe_project_path", "linked project path denied"
        )
    return target, relative


def read_project_text(
    root: str | os.PathLike[str],
    value: Any,
    *,
    max_bytes: int = MAX_PROJECT_FILE_BYTES,
) -> tuple[str, str]:
    """Read one bounded regular UTF-8 file after no-link identity checks."""

    target, relative = resolve_project_path(root, value)
    try:
        before = target.lstat()
    except FileNotFoundError as exc:
        raise SensitiveProjectPathError("project_file_not_found", "project file not found") from exc
    except OSError as exc:
        raise SensitiveProjectPathError(
            "project_file_unavailable", "project file could not be inspected"
        ) from exc
    if _is_reparse(before) or not stat.S_ISREG(before.st_mode):
        raise SensitiveProjectPathError(
            "unsafe_project_file", "project file is not a regular file"
        )
    if _has_multiple_hardlinks(before):
        raise SensitiveProjectPathError(
            "unsafe_project_file", "project file hard links are denied"
        )
    if before.st_size > max_bytes:
        raise SensitiveProjectPathError(
            "project_file_too_large", "project file exceeds the read limit"
        )

    try:
        with target.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if (
                _is_reparse(opened)
                or not stat.S_ISREG(opened.st_mode)
                or _has_multiple_hardlinks(opened)
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            ):
                raise SensitiveProjectPathError(
                    "unsafe_project_file", "project file changed during read"
                )
            payload = handle.read(max_bytes + 1)
            completed = os.fstat(handle.fileno())
        after = target.lstat()
    except SensitiveProjectPathError:
        raise
    except OSError as exc:
        raise SensitiveProjectPathError(
            "project_file_unavailable", "project file could not be read"
        ) from exc
    if len(payload) > max_bytes:
        raise SensitiveProjectPathError(
            "project_file_too_large", "project file exceeds the read limit"
        )
    if (
        _is_reparse(after)
        or _has_multiple_hardlinks(completed)
        or _has_multiple_hardlinks(after)
        or (completed.st_dev, completed.st_ino) != (before.st_dev, before.st_ino)
        or completed.st_size != before.st_size
        or completed.st_mtime_ns != before.st_mtime_ns
        or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
        or after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
    ):
        raise SensitiveProjectPathError(
            "unsafe_project_file", "project file changed during read"
        )
    try:
        return relative, payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SensitiveProjectPathError(
            "project_file_not_utf8", "project file is not valid UTF-8"
        ) from exc


def list_project_directory(
    root: str | os.PathLike[str],
    value: Any = ".",
    *,
    max_entries: int = MAX_PROJECT_LIST_ENTRIES,
) -> tuple[str, tuple[ProjectDirectoryEntry, ...], bool]:
    """List bounded safe children, omitting secrets, links and special files."""

    if not isinstance(max_entries, int) or isinstance(max_entries, bool) or max_entries <= 0:
        raise SensitiveProjectPathError(
            "invalid_list_limit", "project listing limit is invalid"
        )
    max_entries = min(max_entries, MAX_PROJECT_LIST_ENTRIES)
    directory, relative = resolve_project_path(root, value, allow_root=True)
    try:
        directory_st = directory.lstat()
    except FileNotFoundError as exc:
        raise SensitiveProjectPathError(
            "project_directory_not_found", "project directory not found"
        ) from exc
    except OSError as exc:
        raise SensitiveProjectPathError(
            "project_directory_unavailable", "project directory could not be inspected"
        ) from exc
    if _is_reparse(directory_st) or not stat.S_ISDIR(directory_st.st_mode):
        raise SensitiveProjectPathError(
            "unsafe_project_directory", "project path is not a directory"
        )

    entries: list[ProjectDirectoryEntry] = []
    truncated = False
    scanned = 0
    try:
        with os.scandir(directory) as children:
            for child in children:
                scanned += 1
                if scanned > MAX_PROJECT_LIST_SCAN_ENTRIES:
                    truncated = True
                    break
                child_relative = child.name if relative == "." else f"{relative}/{child.name}"
                try:
                    portable = validate_relative_project_path(child_relative)
                except ValueError:
                    continue
                if _normalized_path_is_sensitive(portable):
                    continue
                try:
                    child_st = Path(child.path).lstat()
                except OSError:
                    continue
                if _is_reparse(child_st) or child_st.st_dev != directory_st.st_dev:
                    continue
                if stat.S_ISDIR(child_st.st_mode):
                    entries.append(ProjectDirectoryEntry(child.name, True, 0))
                elif stat.S_ISREG(child_st.st_mode):
                    if _has_multiple_hardlinks(child_st):
                        continue
                    entries.append(
                        ProjectDirectoryEntry(child.name, False, int(child_st.st_size))
                    )
    except OSError as exc:
        raise SensitiveProjectPathError(
            "project_directory_unavailable", "project directory could not be listed"
        ) from exc

    try:
        final_directory_st = directory.lstat()
    except OSError as exc:
        raise SensitiveProjectPathError(
            "project_directory_unavailable", "project directory changed during listing"
        ) from exc
    if (
        _is_reparse(final_directory_st)
        or (final_directory_st.st_dev, final_directory_st.st_ino)
        != (directory_st.st_dev, directory_st.st_ino)
    ):
        raise SensitiveProjectPathError(
            "unsafe_project_directory", "project directory changed during listing"
        )

    entries.sort(key=lambda entry: (entry.name.casefold(), entry.name))
    if len(entries) > max_entries:
        entries = entries[:max_entries]
        truncated = True
    return relative, tuple(entries), truncated


__all__ = [
    "MAX_PROJECT_FILE_BYTES",
    "MAX_PROJECT_LIST_ENTRIES",
    "ProjectDirectoryEntry",
    "SensitiveProjectPathError",
    "is_sensitive_project_path",
    "list_project_directory",
    "read_project_text",
    "resolve_project_path",
]
