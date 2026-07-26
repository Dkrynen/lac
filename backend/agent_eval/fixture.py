"""Create and verify sealed, task-bound evaluation fixture copies."""
from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

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
    if before.st_nlink != 1:
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


def _collect_entries(root: str | Path) -> tuple[FixtureEntry, ...]:
    root_path = Path(root)
    _require_safe_directory(root_path, "fixture root")
    entries: list[FixtureEntry] = []
    casefolded: set[str] = set()
    total = 0
    for current, directory_names, file_names in os.walk(root_path, followlinks=False):
        current_path = Path(current)
        for name in sorted(directory_names):
            directory = current_path / name
            relative = directory.relative_to(root_path).as_posix()
            _validate_relative(relative)
            if _is_link_or_reparse(directory):
                raise FixtureSealError(f"fixture contains a link or reparse point: {relative}")
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
            if details.st_nlink != 1:
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
    if not entries:
        raise FixtureSealError("fixture contains no regular files")
    return tuple(sorted(entries, key=lambda entry: entry.path))


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
    if _is_link_or_reparse(destination, created) or not stat.S_ISREG(created.st_mode) or created.st_nlink != 1:
        raise FixtureSealError(f"fixture destination is unsafe: {entry.path}")


def mark_fixture_read_only(destination: str | Path) -> FixtureSealResult:
    """Apply filesystem read-only attributes; this is not an execution sandbox."""

    root = Path(destination)
    try:
        entries = _collect_entries(root)
        for entry in entries:
            path = root / Path(entry.path)
            os.chmod(path, 0o444)
            if os.name == "nt":
                import ctypes

                if not ctypes.WinDLL("kernel32", use_last_error=True).SetFileAttributesW(
                    str(path), 0x1
                ):
                    raise ctypes.WinError(ctypes.get_last_error())
        for current, directory_names, _file_names in os.walk(root, topdown=False, followlinks=False):
            for name in directory_names:
                os.chmod(Path(current) / name, 0o555)
        os.chmod(root, 0o555)
        return FixtureSealResult(True, False, "ACL hardening is not an execution sandbox")
    except (FixtureSealError, OSError) as exc:
        return FixtureSealResult(False, False, f"read-only marking failed: {exc}")


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
    for entry in manifest.entries:
        _copy_entry(source / Path(entry.path), target / Path(entry.path), entry)
    post_copy = _collect_entries(source)
    if post_copy != manifest.entries:
        raise FixtureSealError("source fixture drift during materialization")
    verification = verify_materialized_fixture(manifest, target)
    if not verification.ok:
        raise FixtureSealError(f"materialized fixture verification failed: {verification.reason}")
    return mark_fixture_read_only(target)


def verify_materialized_fixture(
    manifest: FixtureManifest,
    destination: str | Path,
) -> FixtureVerification:
    try:
        _validate_manifest(manifest)
        actual = _collect_entries(destination)
        aggregate = _aggregate(actual)
    except (FixtureSealError, OSError) as exc:
        return FixtureVerification(False, None, str(exc))
    if actual != manifest.entries:
        return FixtureVerification(False, aggregate, "fixture entries do not match manifest")
    if aggregate != manifest.aggregate_sha256:
        return FixtureVerification(False, aggregate, "fixture aggregate does not match manifest")
    return FixtureVerification(True, aggregate)
