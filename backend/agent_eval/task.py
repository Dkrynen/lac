"""Trusted, bounded task loading for local-agent evaluation."""
from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.project_security import is_sensitive_project_path


TASK_SCHEMA_VERSION = 1
MAX_FIXTURE_FILE_BYTES = 128 * 1024
MAX_FIXTURE_TOTAL_BYTES = 192 * 1024
MAX_TIMEOUT_SECONDS = 900

_TASK_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_MANIFEST_KEYS = frozenset(
    {"schema_version", "id", "prompt", "fixture", "timeout_seconds", "scorer"}
)
_SCORER_KEYS = frozenset({"type", "expected"})


class EvalTaskError(ValueError):
    """The packaged evaluation task is invalid or unsafe to load."""


@dataclass(frozen=True)
class EvalScorer:
    type: str
    expected: str


@dataclass(frozen=True)
class EvalTask:
    schema_version: int
    id: str
    prompt: str
    fixture_root: Path
    timeout_seconds: int
    scorer: EvalScorer


def _require_exact_keys(data: dict[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = sorted(set(data) - allowed)
    missing = sorted(allowed - set(data))
    if unknown:
        raise EvalTaskError(f"{label} has unknown keys: {', '.join(unknown)}")
    if missing:
        raise EvalTaskError(f"{label} is missing keys: {', '.join(missing)}")


def _safe_child(root: Path, child: Path, label: str) -> Path:
    root = root.resolve()
    resolved = child.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise EvalTaskError(f"{label} resolves outside its trusted root") from exc
    return resolved


def load_task(task_id: str, suite_root: str | Path) -> EvalTask:
    if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
        raise EvalTaskError("task id must be lowercase letters, digits, and hyphens")

    suite = Path(suite_root).resolve()
    manifest = _safe_child(
        suite / "tasks", suite / "tasks" / f"{task_id}.json", "task manifest"
    )
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvalTaskError(f"cannot read task manifest {task_id}: {exc}") from exc
    if not isinstance(raw, dict):
        raise EvalTaskError("task manifest must be a JSON object")
    _require_exact_keys(raw, _MANIFEST_KEYS, "task manifest")

    if raw["schema_version"] != TASK_SCHEMA_VERSION:
        raise EvalTaskError(
            f"schema_version must be exactly {TASK_SCHEMA_VERSION}"
        )
    if raw["id"] != task_id:
        raise EvalTaskError("manifest id must match the requested task id")
    prompt = raw["prompt"]
    if not isinstance(prompt, str) or not prompt.strip():
        raise EvalTaskError("prompt must be a non-empty string")

    fixture_name = raw["fixture"]
    if not isinstance(fixture_name, str) or not _TASK_ID.fullmatch(fixture_name):
        raise EvalTaskError("fixture must be a safe lowercase fixture id")
    fixture_root = _safe_child(
        suite / "fixtures",
        suite / "fixtures" / fixture_name,
        "fixture",
    )
    if not fixture_root.is_dir():
        raise EvalTaskError(f"fixture does not exist: {fixture_name}")

    timeout = raw["timeout_seconds"]
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int)
        or timeout < 1
        or timeout > MAX_TIMEOUT_SECONDS
    ):
        raise EvalTaskError(
            f"timeout_seconds must be between 1 and {MAX_TIMEOUT_SECONDS}"
        )

    scorer = raw["scorer"]
    if not isinstance(scorer, dict):
        raise EvalTaskError("scorer must be an object")
    _require_exact_keys(scorer, _SCORER_KEYS, "scorer")
    if scorer["type"] != "exact_text":
        raise EvalTaskError("scorer type must be exact_text")
    expected = scorer["expected"]
    if not isinstance(expected, str) or not expected.strip():
        raise EvalTaskError("scorer expected must be a non-empty string")

    return EvalTask(
        schema_version=TASK_SCHEMA_VERSION,
        id=task_id,
        prompt=prompt.strip(),
        fixture_root=fixture_root,
        timeout_seconds=timeout,
        scorer=EvalScorer(type="exact_text", expected=expected.strip()),
    )


def _is_link_or_reparse(path: Path) -> bool:
    details = path.lstat()
    attributes = int(getattr(details, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(details.st_mode) or bool(attributes & reparse_flag)


def snapshot_fixture(fixture_root: str | Path) -> str:
    """Return a stable UTF-8 snapshot without following links or secrets."""

    root = Path(fixture_root).resolve()
    if not root.is_dir():
        raise EvalTaskError("fixture root must be an existing directory")

    files: list[tuple[str, Path, int]] = []
    total_bytes = 0
    for current, directory_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in sorted(directory_names):
            directory = current_path / name
            if _is_link_or_reparse(directory):
                raise EvalTaskError(
                    f"fixture contains a link or reparse point: "
                    f"{directory.relative_to(root).as_posix()}"
                )
        for name in sorted(file_names):
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if _is_link_or_reparse(path):
                raise EvalTaskError(
                    f"fixture contains a link or reparse point: {relative}"
                )
            details = path.stat()
            if not stat.S_ISREG(details.st_mode):
                raise EvalTaskError(f"fixture contains a non-regular file: {relative}")
            if is_sensitive_project_path(relative):
                raise EvalTaskError(f"fixture contains a sensitive path: {relative}")
            size = int(details.st_size)
            if size > MAX_FIXTURE_FILE_BYTES:
                raise EvalTaskError(
                    f"fixture file exceeds the per-file byte limit: {relative}"
                )
            total_bytes += size
            if total_bytes > MAX_FIXTURE_TOTAL_BYTES:
                raise EvalTaskError("fixture exceeds the total byte limit")
            files.append((relative, path, size))

    if not files:
        raise EvalTaskError("fixture contains no regular files")

    sections: list[str] = []
    for relative, path, _ in sorted(files):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise EvalTaskError(f"fixture file is not UTF-8: {relative}") from exc
        body = text if text.endswith("\n") else text + "\n"
        sections.append(
            f"--- FILE: {relative} ---\n{body}--- END FILE ---"
        )
    return "\n".join(sections)
