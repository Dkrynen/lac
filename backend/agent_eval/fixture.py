"""Create and verify sealed, task-bound evaluation fixture copies."""
from __future__ import annotations

import hashlib
import getpass
import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from backend.cookbook import proc
from backend.project_security import is_sensitive_project_path

from .identity import canonical_sha256
from .task import (
    MAX_FIXTURE_FILE_BYTES,
    MAX_FIXTURE_TOTAL_BYTES,
    EvalTask,
    task_contract_sha256,
)


_REPARSE_POINT = 0x0400
_CHUNK_SIZE = 64 * 1024
_ACL_TIMEOUT_SECONDS = 10


class FixtureSealError(ValueError):
    """A fixture cannot be deterministically materialized or verified."""


@dataclass(frozen=True)
class FixtureEntry:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class FixtureManifest:
    entries: tuple[FixtureEntry, ...]
    aggregate_sha256: str
    task_contract_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "entries": [
                {"path": entry.path, "size": entry.size, "sha256": entry.sha256}
                for entry in self.entries
            ],
            "aggregate_sha256": self.aggregate_sha256,
            "task_contract_sha256": self.task_contract_sha256,
        }


@dataclass(frozen=True)
class FixtureVerification:
    ok: bool
    aggregate_sha256: str | None
    reason: str | None = None
    entries: tuple[FixtureEntry, ...] = ()
    directories: tuple[str, ...] = ()

    def observed_dict(self) -> dict[str, object]:
        return {
            "entries": [
                {"path": entry.path, "size": entry.size, "sha256": entry.sha256}
                for entry in self.entries
            ],
            "directories": list(self.directories),
            "aggregate_sha256": self.aggregate_sha256,
        }


@dataclass(frozen=True)
class FixtureSealResult:
    ok: bool
    acl_hardened: bool
    reason: str | None = None


def _is_link_or_reparse(path: Path, details: os.stat_result | None = None) -> bool:
    details = details or path.lstat()
    attributes = int(getattr(details, "st_file_attributes", 0))
    return stat.S_ISLNK(details.st_mode) or bool(attributes & _REPARSE_POINT)


def _path_key(relative: str) -> str:
    return "/".join(part.casefold().rstrip(". ") for part in relative.split("/"))


def _validate_relative(relative: str) -> None:
    parts = relative.split("/")
    if (
        not relative
        or relative.startswith("/")
        or any(part in {"", ".", ".."} or ":" in part for part in parts)
    ):
        raise FixtureSealError(f"fixture path is unsafe: {relative!r}")
    if is_sensitive_project_path(relative):
        raise FixtureSealError(f"fixture contains a sensitive path: {relative}")


def _object_token(details: os.stat_result) -> tuple[int, int, int, int]:
    return (int(details.st_dev), int(details.st_ino), int(details.st_size), int(details.st_mtime_ns))


def _windows_file_link_count(path: Path) -> int:
    """Read NumberOfLinks from a no-follow Windows file handle."""

    import ctypes
    from ctypes import wintypes

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ByHandleFileInformation),
    ]
    get_information.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    handle = create_file(
        str(path),
        0x80,
        0x1 | 0x2 | 0x4,
        None,
        3,
        0x00200000 | 0x02000000,
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        information = ByHandleFileInformation()
        if not get_information(handle, ctypes.byref(information)):
            raise ctypes.WinError(ctypes.get_last_error())
        if int(information.dwFileAttributes) & _REPARSE_POINT:
            raise FixtureSealError(
                f"fixture contains a link or reparse point: {path}"
            )
        return int(information.nNumberOfLinks)
    finally:
        close_handle(handle)


def _file_link_count(path: Path, details: os.stat_result) -> int:
    if os.name == "nt":
        return _windows_file_link_count(path)
    return int(details.st_nlink)


def _require_safe_directory(path: Path, label: str) -> None:
    try:
        details = path.lstat()
    except OSError as exc:
        raise FixtureSealError(f"{label} is unavailable") from exc
    if _is_link_or_reparse(path, details) or not stat.S_ISDIR(details.st_mode):
        raise FixtureSealError(f"{label} must be a non-reparse directory")


def _hash_open_file(path: Path, expected: os.stat_result | None = None) -> tuple[int, str]:
    """Hash a regular, unlinked file and reject changes while it is read."""

    before = path.lstat()
    if _is_link_or_reparse(path, before):
        raise FixtureSealError(f"fixture contains a link or reparse point: {path}")
    if not stat.S_ISREG(before.st_mode):
        raise FixtureSealError(f"fixture contains a non-regular file: {path}")
    if _file_link_count(path, before) != 1:
        raise FixtureSealError(f"fixture contains a hardlink: {path}")
    if expected is not None and _object_token(before) != _object_token(expected):
        raise FixtureSealError(f"source fixture drift before copy: {path}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if _object_token(opened) != _object_token(before):
            raise FixtureSealError(f"source fixture drift before read: {path}")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, _CHUNK_SIZE):
            digest.update(chunk)
        after = path.lstat()
        if _object_token(after) != _object_token(before):
            raise FixtureSealError(f"source fixture drift during read: {path}")
        return int(opened.st_size), digest.hexdigest()
    finally:
        os.close(descriptor)


def _manifest_directories(entries: tuple[FixtureEntry, ...]) -> tuple[str, ...]:
    directories: set[str] = set()
    for entry in entries:
        parent = Path(entry.path).parent
        while parent != Path("."):
            directories.add(parent.as_posix())
            parent = parent.parent
    return tuple(sorted(directories))


def _collect_observed(
    root: str | Path,
    *,
    require_files: bool,
) -> tuple[tuple[FixtureEntry, ...], tuple[str, ...]]:
    root_path = Path(root)
    _require_safe_directory(root_path, "fixture root")
    entries: list[FixtureEntry] = []
    directories: set[str] = set()
    casefolded: set[str] = set()
    total = 0
    for current, directory_names, file_names in os.walk(root_path, followlinks=False):
        current_path = Path(current)
        for name in sorted(directory_names):
            directory = current_path / name
            relative = directory.relative_to(root_path).as_posix()
            _validate_relative(relative)
            key = _path_key(relative)
            if key in casefolded:
                raise FixtureSealError(
                    f"fixture contains case-colliding paths: {relative}"
                )
            casefolded.add(key)
            if _is_link_or_reparse(directory):
                raise FixtureSealError(f"fixture contains a link or reparse point: {relative}")
            directories.add(relative)
        for name in sorted(file_names):
            source = current_path / name
            relative = source.relative_to(root_path).as_posix()
            _validate_relative(relative)
            key = _path_key(relative)
            if key in casefolded:
                raise FixtureSealError(f"fixture contains case-colliding paths: {relative}")
            casefolded.add(key)
            details = source.lstat()
            if _is_link_or_reparse(source, details):
                raise FixtureSealError(f"fixture contains a link or reparse point: {relative}")
            if not stat.S_ISREG(details.st_mode):
                raise FixtureSealError(f"fixture contains a non-regular file: {relative}")
            if _file_link_count(source, details) != 1:
                raise FixtureSealError(f"fixture contains a hardlink: {relative}")
            size = int(details.st_size)
            if size > MAX_FIXTURE_FILE_BYTES:
                raise FixtureSealError(f"fixture file exceeds the per-file byte limit: {relative}")
            total += size
            if total > MAX_FIXTURE_TOTAL_BYTES:
                raise FixtureSealError("fixture exceeds the total byte limit")
            actual_size, digest = _hash_open_file(source, details)
            if actual_size != size:
                raise FixtureSealError(f"source fixture drift during manifest: {relative}")
            entries.append(FixtureEntry(relative, size, digest))
    if require_files and not entries:
        raise FixtureSealError("fixture contains no regular files")
    return (
        tuple(sorted(entries, key=lambda entry: entry.path)),
        tuple(sorted(directories)),
    )


def _collect_entries(root: str | Path) -> tuple[FixtureEntry, ...]:
    entries, directories = _collect_observed(root, require_files=True)
    expected_directories = _manifest_directories(entries)
    if directories != expected_directories:
        unexpected = sorted(set(directories) - set(expected_directories))
        name = unexpected[0] if unexpected else directories[0]
        raise FixtureSealError(f"fixture contains an empty directory: {name}")
    return entries


def _aggregate(entries: tuple[FixtureEntry, ...]) -> str:
    return canonical_sha256(
        [
            {"path": entry.path, "sha256": entry.sha256, "size": entry.size}
            for entry in entries
        ]
    )


def build_fixture_manifest(task: EvalTask) -> FixtureManifest:
    entries = _collect_entries(task.fixture_root)
    return FixtureManifest(entries, _aggregate(entries), task_contract_sha256(task))


def _validate_manifest(manifest: FixtureManifest) -> None:
    if not isinstance(manifest, FixtureManifest) or not manifest.entries:
        raise FixtureSealError("fixture manifest is invalid")
    seen: set[str] = set()
    for entry in manifest.entries:
        if (
            not isinstance(entry, FixtureEntry)
            or not isinstance(entry.size, int)
            or entry.size < 0
            or not isinstance(entry.sha256, str)
            or len(entry.sha256) != 64
        ):
            raise FixtureSealError("fixture manifest entry is invalid")
        _validate_relative(entry.path)
        key = _path_key(entry.path)
        if key in seen:
            raise FixtureSealError("fixture manifest contains case-colliding paths")
        seen.add(key)
    if _aggregate(manifest.entries) != manifest.aggregate_sha256:
        raise FixtureSealError("fixture manifest aggregate does not match entries")


def _copy_entry(source: Path, destination: Path, entry: FixtureEntry) -> None:
    expected = source.lstat()
    if int(expected.st_size) != entry.size:
        raise FixtureSealError(f"source fixture drift before copy: {entry.path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    output = os.open(destination, flags, 0o600)
    try:
        source_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        input_descriptor = os.open(source, source_flags)
        try:
            opened = os.fstat(input_descriptor)
            if _object_token(opened) != _object_token(expected):
                raise FixtureSealError(f"source fixture drift before copy: {entry.path}")
            digest = hashlib.sha256()
            copied = 0
            while chunk := os.read(input_descriptor, _CHUNK_SIZE):
                digest.update(chunk)
                copied += len(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(output, view)
                    if written <= 0:
                        raise OSError("failed to write fixture destination")
                    view = view[written:]
            os.fsync(output)
            if copied != entry.size or digest.hexdigest() != entry.sha256:
                raise FixtureSealError(f"source fixture drift during copy: {entry.path}")
            if _object_token(source.lstat()) != _object_token(expected):
                raise FixtureSealError(f"source fixture drift during copy: {entry.path}")
        finally:
            os.close(input_descriptor)
    finally:
        os.close(output)
    created = destination.lstat()
    if (
        _is_link_or_reparse(destination, created)
        or not stat.S_ISREG(created.st_mode)
        or _file_link_count(destination, created) != 1
    ):
        raise FixtureSealError(f"fixture destination is unsafe: {entry.path}")


def _set_windows_read_only(path: Path, enabled: bool) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_attributes = kernel32.GetFileAttributesW
    get_attributes.argtypes = [wintypes.LPCWSTR]
    get_attributes.restype = wintypes.DWORD
    set_attributes = kernel32.SetFileAttributesW
    set_attributes.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    set_attributes.restype = wintypes.BOOL
    attributes = int(get_attributes(str(path)))
    if attributes == 0xFFFFFFFF:
        raise ctypes.WinError(ctypes.get_last_error())
    updated = attributes | 0x1 if enabled else attributes & ~0x1
    if not set_attributes(str(path), updated):
        raise ctypes.WinError(ctypes.get_last_error())


def _mark_read_only_attributes(root: Path) -> None:
    entries = _collect_entries(root)
    for entry in entries:
        path = root / Path(entry.path)
        os.chmod(path, 0o444)
        if os.name == "nt":
            _set_windows_read_only(path, True)
    for current, directory_names, _file_names in os.walk(
        root, topdown=False, followlinks=False
    ):
        for name in directory_names:
            os.chmod(Path(current) / name, 0o555)
    os.chmod(root, 0o555)


def _current_acl_user() -> str:
    if os.name != "nt":
        return getpass.getuser()
    try:
        import ctypes
        from ctypes import wintypes

        size = wintypes.DWORD(256)
        buffer = ctypes.create_unicode_buffer(size.value)
        if ctypes.WinDLL("advapi32", use_last_error=True).GetUserNameW(
            buffer, ctypes.byref(size)
        ):
            return buffer.value
    except (OSError, ValueError):
        pass
    user = getpass.getuser()
    if not user:
        raise FixtureSealError("ACL helper cannot identify the current user")
    return user


def _run_icacls(root: Path, *arguments: str) -> None:
    command = ["icacls.exe", str(root), *arguments, "/Q"]
    try:
        completed = proc.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_ACL_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise FixtureSealError("ACL helper timed out") from exc
    except OSError as exc:
        raise FixtureSealError(f"ACL helper is unavailable: {exc}") from exc
    if completed.returncode != 0:
        raise FixtureSealError(
            f"ACL helper failed with exit code {completed.returncode}"
        )


def _apply_windows_acl(root: Path) -> None:
    principal = _current_acl_user()
    _run_icacls(root, "/grant:r", f"{principal}:RX", "/T", "/C")
    _run_icacls(root, "/inheritance:r", "/T", "/C")
    _run_icacls(root, "/grant", f"{principal}:(OI)(CI)RX")


def _apply_fixture_acl(root: Path) -> None:
    if os.name == "nt":
        _apply_windows_acl(root)


def _verify_fixture_permissions(root: Path) -> None:
    entries = _collect_entries(root)
    sample = root / Path(entries[0].path)
    with sample.open("rb") as handle:
        handle.read(1)

    descriptor: int | None = None
    try:
        descriptor = os.open(
            sample,
            os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0),
        )
    except OSError:
        pass
    else:
        raise FixtureSealError("ACL verification allowed existing-file write access")
    finally:
        if descriptor is not None:
            os.close(descriptor)

    probe = root / ".lac-acl-write-probe"
    if os.path.lexists(probe):
        raise FixtureSealError("ACL verification probe path already exists")
    descriptor = None
    try:
        descriptor = os.open(
            probe,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
    except OSError:
        pass
    else:
        raise FixtureSealError("ACL verification allowed new-file creation")
    finally:
        if descriptor is not None:
            os.close(descriptor)
            try:
                probe.unlink()
            except OSError:
                pass


def mark_fixture_read_only(
    destination: str | Path,
    *,
    acl_apply_fn: Callable[[Path], None] = _apply_fixture_acl,
    acl_verify_fn: Callable[[Path], None] = _verify_fixture_permissions,
) -> FixtureSealResult:
    """Seal a fixture and report success only after empirical permission checks."""

    root = Path(destination)
    try:
        _mark_read_only_attributes(root)
    except (FixtureSealError, OSError) as exc:
        return FixtureSealResult(False, False, f"read-only marking failed: {exc}")
    try:
        acl_apply_fn(root)
    except (FixtureSealError, OSError, subprocess.SubprocessError) as exc:
        return FixtureSealResult(False, False, f"ACL apply failed: {exc}")
    try:
        acl_verify_fn(root)
    except (FixtureSealError, OSError) as exc:
        return FixtureSealResult(False, False, f"ACL verify failed: {exc}")
    return FixtureSealResult(True, True)


def _restore_fixture_access(root: str | Path) -> None:
    path = Path(root)
    if not os.path.lexists(path):
        return
    if os.name == "nt":
        principal = _current_acl_user()
        _run_icacls(path, "/grant:r", f"{principal}:F", "/T", "/C")
        _run_icacls(path, "/inheritance:e", "/T", "/C")
        _run_icacls(path, "/grant", f"{principal}:(OI)(CI)F")
    for current, directory_names, file_names in os.walk(
        path, topdown=False, followlinks=False
    ):
        current_path = Path(current)
        for name in file_names:
            candidate = current_path / name
            if os.name == "nt":
                _set_windows_read_only(candidate, False)
            os.chmod(candidate, 0o666)
        for name in directory_names:
            os.chmod(current_path / name, 0o777)
    os.chmod(path, 0o777)


def _cleanup_partial_fixture(destination: str | Path) -> None:
    target = Path(destination)
    if not os.path.lexists(target):
        return
    details = target.lstat()
    if _is_link_or_reparse(target, details):
        target.unlink()
        return
    _restore_fixture_access(target)
    shutil.rmtree(target)


def materialize_fixture(
    manifest: FixtureManifest,
    source_root: str | Path,
    destination: str | Path,
) -> FixtureSealResult:
    source = Path(source_root)
    target = Path(destination)
    _validate_manifest(manifest)
    if os.path.lexists(target):
        raise FileExistsError(target)
    current = _collect_entries(source)
    if current != manifest.entries or _aggregate(current) != manifest.aggregate_sha256:
        raise FixtureSealError("source fixture drift before materialization")
    target.mkdir()
    try:
        for entry in manifest.entries:
            _copy_entry(source / Path(entry.path), target / Path(entry.path), entry)
        post_copy = _collect_entries(source)
        if post_copy != manifest.entries:
            raise FixtureSealError("source fixture drift during materialization")
        verification = verify_materialized_fixture(manifest, target)
        if not verification.ok:
            raise FixtureSealError(
                f"materialized fixture verification failed: {verification.reason}"
            )
        seal = mark_fixture_read_only(target)
        if not seal.ok or not seal.acl_hardened:
            raise FixtureSealError(seal.reason or "fixture ACL hardening was not verified")
        return seal
    except Exception as exc:
        try:
            _cleanup_partial_fixture(target)
        except Exception as cleanup_exc:
            raise FixtureSealError(
                f"{type(exc).__name__}: {exc}; cleanup failed: "
                f"{type(cleanup_exc).__name__}: {cleanup_exc}"
            ) from exc
        raise


def verify_materialized_fixture(
    manifest: FixtureManifest,
    destination: str | Path,
) -> FixtureVerification:
    try:
        _validate_manifest(manifest)
        actual, directories = _collect_observed(destination, require_files=False)
        aggregate = _aggregate(actual)
    except (FixtureSealError, OSError) as exc:
        return FixtureVerification(False, None, str(exc))
    expected_directories = _manifest_directories(manifest.entries)
    if actual != manifest.entries:
        return FixtureVerification(
            False,
            aggregate,
            "fixture entries do not match manifest",
            actual,
            directories,
        )
    if directories != expected_directories:
        return FixtureVerification(
            False,
            aggregate,
            "fixture directories do not match manifest",
            actual,
            directories,
        )
    if aggregate != manifest.aggregate_sha256:
        return FixtureVerification(
            False,
            aggregate,
            "fixture aggregate does not match manifest",
            actual,
            directories,
        )
    return FixtureVerification(True, aggregate, None, actual, directories)
