"""Bounded, local-only repository mapping with durable evidence receipts."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from backend.cookbook.config import CONFIG_DIR
from backend.project_paths import inspect_project_root


SCHEMA_VERSION = 1
DEFAULT_RECEIPT_ROOT = (
    CONFIG_DIR / "receipts" / "repository-inspections"
)
_EXCLUDED_DIRECTORIES = {
    ".git",
    ".hg",
    ".aws",
    ".cache",
    ".gnupg",
    ".model-hub",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".ssh",
    ".tox",
    ".tmp",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "venv",
}
_SECRET_NAMES = {
    ".netrc",
    ".npmrc",
    ".pypirc",
    "auth.json",
    "credentials",
    "credentials.json",
    "secrets.json",
    "token.json",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
}
_SECRET_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}
_INSTRUCTION_NAMES = {"agents.md", "claude.md", "readme.md", "contributing.md"}
_MANIFEST_NAMES = {
    "pyproject.toml",
    "pytest.ini",
    "setup.cfg",
    "requirements.txt",
    "package.json",
    "cargo.toml",
    "go.mod",
}
_ENTRYPOINT_NAMES = {
    "app.py",
    "cli.py",
    "main.py",
    "server.py",
    "manage.py",
    "main.go",
}


@dataclass(frozen=True)
class RepositoryFinding:
    kind: str
    severity: str
    summary: str
    evidence: dict[str, Any]


@dataclass(frozen=True)
class RepositoryReceipt:
    schema_version: int
    receipt_id: str
    created_at: str
    repository: str
    repository_fingerprint: str
    privacy: dict[str, Any]
    stack: tuple[str, ...]
    entry_points: tuple[str, ...]
    instruction_files: tuple[str, ...]
    check_candidates: tuple[str, ...]
    findings: tuple[RepositoryFinding, ...]
    limits: dict[str, Any]


def _is_secret(path: Path) -> bool:
    name = path.name.casefold()
    return (
        name == ".env"
        or name.startswith(".env.")
        or name in _SECRET_NAMES
        or path.suffix.casefold() in _SECRET_SUFFIXES
    )


def _is_indirection(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(
        attributes
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _safe_files(root: Path, *, discovery_limit: int) -> list[Path]:
    safe: list[Path] = []
    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        kept_directories = []
        for directory in sorted(directories, key=str.casefold):
            candidate = current_path / directory
            if directory.casefold() in _EXCLUDED_DIRECTORIES:
                continue
            if _is_indirection(candidate):
                continue
            kept_directories.append(directory)
        directories[:] = kept_directories
        for filename in sorted(
            filenames,
            key=lambda value: (
                0
                if value.casefold() in _MANIFEST_NAMES | _INSTRUCTION_NAMES
                else 1,
                value.casefold(),
            ),
        ):
            candidate = current_path / filename
            if _is_indirection(candidate) or _is_secret(candidate):
                continue
            try:
                if candidate.is_file():
                    safe.append(candidate)
            except OSError:
                continue
            if len(safe) >= discovery_limit:
                return safe
    return safe


def _priority(root: Path, path: Path) -> tuple[int, str]:
    relative = path.relative_to(root).as_posix()
    name = path.name.casefold()
    important = name in _MANIFEST_NAMES or name in _INSTRUCTION_NAMES
    return (0 if important else 1, relative.casefold())


def _fingerprint(
    root: Path,
    files: list[Path],
    *,
    max_files: int,
    max_bytes: int,
) -> tuple[str, dict[str, bytes], dict[str, Any]]:
    digest = hashlib.sha256(b"lac-repository-fingerprint-v1\0")
    content: dict[str, bytes] = {}
    bytes_read = 0
    files_fingerprinted = 0
    truncated = len(files) > max_files

    for path in sorted(files, key=lambda item: _priority(root, item)):
        if files_fingerprinted >= max_files:
            truncated = True
            break
        relative = path.relative_to(root).as_posix()
        try:
            size = path.stat().st_size
        except OSError:
            truncated = True
            continue
        if size < 0 or size > max_bytes - bytes_read:
            truncated = True
            continue
        try:
            payload = path.read_bytes()
        except OSError:
            truncated = True
            continue
        if len(payload) > max_bytes - bytes_read:
            truncated = True
            continue
        digest.update(relative.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update(str(len(payload)).encode("ascii"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(payload).digest())
        bytes_read += len(payload)
        files_fingerprinted += 1
        if path.name.casefold() in _MANIFEST_NAMES | _INSTRUCTION_NAMES:
            content[relative] = payload

    limits = {
        "max_files": max_files,
        "max_bytes": max_bytes,
        "safe_files_discovered": len(files),
        "files_fingerprinted": files_fingerprinted,
        "bytes_read": bytes_read,
        "truncated": truncated,
    }
    return digest.hexdigest(), content, limits


def _decode_json(content: dict[str, bytes], relative: str) -> dict[str, Any]:
    payload = content.get(relative)
    if payload is None:
        return {}
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _classify(
    root: Path,
    files: list[Path],
    content: dict[str, bytes],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    relative_paths = tuple(
        sorted(path.relative_to(root).as_posix() for path in files)
    )
    lowered = {relative.casefold(): relative for relative in relative_paths}
    basenames = {Path(relative).name.casefold() for relative in relative_paths}
    suffixes = {Path(relative).suffix.casefold() for relative in relative_paths}
    stack: list[str] = []
    checks: list[str] = []

    if (
        basenames & {"pyproject.toml", "requirements.txt", "setup.cfg"}
        or ".py" in suffixes
    ):
        stack.append("python")
        if (
            "pytest.ini" in basenames
            or any(
                relative.casefold().startswith("tests/")
                for relative in relative_paths
            )
            or any(
                Path(relative).name.casefold().startswith("test_")
                for relative in relative_paths
            )
        ):
            checks.append("python -m pytest -q")
    if "package.json" in basenames or suffixes & {".js", ".jsx", ".ts", ".tsx"}:
        stack.append("node")
        package_relative = lowered.get("package.json")
        if package_relative:
            scripts = _decode_json(content, package_relative).get("scripts", {})
            if isinstance(scripts, dict):
                if isinstance(scripts.get("test"), str):
                    checks.append("npm test")
                if isinstance(scripts.get("typecheck"), str):
                    checks.append("npm run typecheck")
    if "cargo.toml" in basenames or ".rs" in suffixes:
        stack.append("rust")
        checks.extend(["cargo test", "cargo check"])
    if "go.mod" in basenames or ".go" in suffixes:
        stack.append("go")
        checks.append("go test ./...")

    instructions = tuple(
        relative
        for relative in relative_paths
        if Path(relative).name.casefold() in _INSTRUCTION_NAMES
    )
    entry_points = tuple(
        relative
        for relative in relative_paths
        if (
            Path(relative).name.casefold() in _ENTRYPOINT_NAMES
            or relative.casefold()
            in {"src/main.rs", "src/main.ts", "src/main.tsx", "src/index.ts"}
        )
    )
    return (
        tuple(sorted(set(stack))),
        tuple(sorted(set(entry_points))),
        tuple(sorted(set(instructions))),
        tuple(dict.fromkeys(checks)),
    )


def _atomic_receipt(receipt: RepositoryReceipt, receipt_root: Path) -> Path:
    receipt_root.mkdir(parents=True, exist_ok=True)
    timestamp = receipt.created_at.replace("-", "").replace(":", "")
    timestamp = timestamp.replace("+00:00", "Z")
    target = receipt_root / f"{timestamp}-{receipt.receipt_id}.json"
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{receipt.receipt_id}-",
        suffix=".tmp",
        dir=receipt_root,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(asdict(receipt), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            Path(temporary).unlink(missing_ok=True)
        finally:
            raise
    return target


def inspect_repository(
    root: str | Path,
    *,
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    receipt_root: str | Path = DEFAULT_RECEIPT_ROOT,
    max_files: int = 5000,
    max_bytes: int = 2_000_000,
    home: Path | None = None,
    data_root: Path | None = None,
) -> tuple[RepositoryReceipt, Path]:
    """Map a repository locally without executing it or following indirections."""
    if max_files <= 0 or max_bytes <= 0:
        raise ValueError("inspection limits must be positive")
    supplied = Path(root)
    absolute = supplied if supplied.is_absolute() else supplied.absolute()
    identity = inspect_project_root(
        str(absolute),
        home=home,
        data_root=data_root,
    )
    repository = identity.path
    resolved_receipt_root = Path(receipt_root).resolve(strict=False)
    if (
        resolved_receipt_root == repository
        or repository in resolved_receipt_root.parents
    ):
        raise ValueError(
            "receipt store must remain outside the inspected repository"
        )
    files = _safe_files(repository, discovery_limit=max_files + 1)
    fingerprint, content, limits = _fingerprint(
        repository,
        files,
        max_files=max_files,
        max_bytes=max_bytes,
    )
    stack, entry_points, instructions, checks = _classify(
        repository, files, content
    )

    findings: list[RepositoryFinding] = []
    if not stack:
        findings.append(
            RepositoryFinding(
                "unknown_stack",
                "warn",
                "No supported project manifest was detected.",
                {"supported": ["python", "node", "rust", "go"]},
            )
        )
    if not checks:
        findings.append(
            RepositoryFinding(
                "no_recognized_checks",
                "warn",
                "No recognized existing check command was discovered.",
                {"commands_executed": []},
            )
        )
    if limits["truncated"]:
        findings.append(
            RepositoryFinding(
                "inspection_limit",
                "warn",
                "Repository inspection reached a configured safety limit.",
                dict(limits),
            )
        )
    if instructions:
        findings.append(
            RepositoryFinding(
                "repository_instructions",
                "info",
                "Repository instruction files were found.",
                {"paths": list(instructions)},
            )
        )

    created = now_fn()
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    created_at = created.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    receipt_id = hashlib.sha256(
        f"{fingerprint}\0{created_at}".encode("utf-8")
    ).hexdigest()[:16]
    receipt = RepositoryReceipt(
        schema_version=SCHEMA_VERSION,
        receipt_id=receipt_id,
        created_at=created_at,
        repository=str(repository),
        repository_fingerprint=fingerprint,
        privacy={
            "local_only": True,
            "network_access": False,
            "commands_executed": [],
            "repository_modified": False,
            "secret_shaped_files_excluded": True,
            "symlinks_followed": False,
        },
        stack=stack,
        entry_points=entry_points,
        instruction_files=instructions,
        check_candidates=checks,
        findings=tuple(findings),
        limits=limits,
    )
    path = _atomic_receipt(receipt, resolved_receipt_root)
    return receipt, path
