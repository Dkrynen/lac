"""Atomic evidence artifacts and hash-sealed ledger verification."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .evidence import (
    EvidenceControlResult,
    EvidenceMode,
    EvidenceState,
    EvidenceVerdict,
)


_REPARSE_POINT = 0x0400


class ArtifactLedgerError(ValueError):
    """An evidence artifact is incomplete, unsafe, or cannot be sealed."""


@dataclass(frozen=True)
class LedgerVerification:
    ok: bool
    reason: str | None = None


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Create an immutable artifact after flushing it to disk."""
    destination = Path(path)
    if os.path.lexists(destination):
        raise FileExistsError(destination)

    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    descriptor: int | None = None
    temporary_owned = False
    try:
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        temporary_owned = True
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, destination)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_owned:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def atomic_write_json(path: Path, value: Any) -> None:
    payload = json.dumps(
        value,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8") + b"\n"
    atomic_write_bytes(Path(path), payload)


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return bool(attributes & _REPARSE_POINT)


def _reject_unsafe_path(path: Path) -> None:
    if path.is_symlink():
        raise ArtifactLedgerError(f"link artifact is not allowed: {path}")
    if _is_reparse_point(path):
        raise ArtifactLedgerError(f"reparse-point artifact is not allowed: {path}")


def _validate_excluded_roots(run_root: Path, excluded_roots: tuple[str, ...]) -> set[str]:
    names: set[str] = set()
    for root_name in excluded_roots:
        if (
            not isinstance(root_name, str)
            or not root_name
            or Path(root_name).name != root_name
            or root_name in {".", ".."}
        ):
            raise ArtifactLedgerError(f"invalid excluded root: {root_name!r}")
        if root_name == "evidence.json":
            raise ArtifactLedgerError("reserved evidence output cannot be excluded")
        if root_name.endswith(".tmp"):
            raise ArtifactLedgerError(
                f"temporary artifact is not allowed: {root_name}"
            )
        if root_name in names:
            raise ArtifactLedgerError(f"duplicate excluded root: {root_name}")
        names.add(root_name)
        path = run_root / root_name
        if not os.path.lexists(path):
            continue
        _reject_unsafe_path(path)
        if not stat.S_ISDIR(path.lstat().st_mode):
            raise ArtifactLedgerError(
                f"excluded root must be a directory: {root_name}"
            )
    return names


def _artifact_hashes(run_root: Path, excluded_roots: tuple[str, ...]) -> dict[str, str]:
    if not run_root.is_dir():
        raise ArtifactLedgerError(f"run root is not a directory: {run_root}")
    _reject_unsafe_path(run_root)

    excluded = _validate_excluded_roots(run_root, excluded_roots)
    hashes: dict[str, str] = {}

    def visit(directory: Path, relative: Path) -> None:
        for entry in sorted(directory.iterdir(), key=lambda child: child.name):
            child_relative = relative / entry.name
            normalized = child_relative.as_posix()
            if relative == Path(".") and entry.name in excluded:
                continue
            if entry.name.endswith(".tmp"):
                raise ArtifactLedgerError(f"temporary artifact is not allowed: {normalized}")
            _reject_unsafe_path(entry)
            mode = entry.lstat().st_mode
            if stat.S_ISDIR(mode):
                visit(entry, child_relative)
                continue
            if not stat.S_ISREG(mode):
                raise ArtifactLedgerError(f"non-regular artifact is not allowed: {normalized}")
            if normalized == "evidence.json":
                continue
            digest = hashlib.sha256()
            with entry.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            hashes[normalized] = digest.hexdigest()

    visit(run_root, Path("."))
    return dict(sorted(hashes.items()))


def _root_hash(artifact_hashes: dict[str, str]) -> str:
    pairs = sorted(artifact_hashes.items())
    payload = json.dumps(pairs, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return hashlib.sha256(payload).hexdigest()


def seal_evidence(
    run_root: Path,
    mode: EvidenceMode,
    preliminary_results: Iterable[EvidenceControlResult],
    *,
    excluded_roots: tuple[str, ...] = ("workspaces",),
) -> dict[str, Any]:
    """Hash final artifacts, append the ledger control, and write evidence last."""
    items = tuple(preliminary_results)
    if any(item.name == "artifact_ledger_integrity" for item in items):
        raise ArtifactLedgerError(
            "artifact_ledger_integrity is computed only while sealing"
        )

    artifact_hashes = _artifact_hashes(Path(run_root), excluded_roots)
    ledger_result = EvidenceControlResult(
        "artifact_ledger_integrity",
        EvidenceState.PASS,
        "all declared artifacts hashed",
        {"artifact_count": len(artifact_hashes)},
    )
    verdict = EvidenceVerdict.from_results(mode, [*items, ledger_result])
    evidence = {
        "schema_version": 2,
        "mode": verdict.mode.value,
        "controls": verdict.to_dict(),
        "artifacts": artifact_hashes,
        "evidence_root_sha256": _root_hash(artifact_hashes),
        "excluded_roots": list(excluded_roots),
        "artifact_valid": verdict.artifact_valid,
    }
    atomic_write_json(Path(run_root) / "evidence.json", evidence)
    return evidence


def _verified_verdict(
    evidence: dict[str, Any],
) -> tuple[EvidenceVerdict, EvidenceControlResult]:
    if evidence.get("schema_version") != 2:
        raise ArtifactLedgerError("unsupported evidence schema version")
    raw_mode = evidence.get("mode")
    if not isinstance(raw_mode, str):
        raise ArtifactLedgerError("evidence mode must be a string")
    try:
        mode = EvidenceMode(raw_mode)
    except ValueError as exc:
        raise ArtifactLedgerError(f"unknown evidence mode: {raw_mode}") from exc

    controls = evidence.get("controls")
    if not isinstance(controls, dict):
        raise ArtifactLedgerError("evidence controls must be an object")
    if controls.get("mode") != mode.value:
        raise ArtifactLedgerError("nested evidence mode does not match top-level mode")
    raw_results = controls.get("results")
    if not isinstance(raw_results, list):
        raise ArtifactLedgerError("evidence control results must be a list")

    results: list[EvidenceControlResult] = []
    for raw_result in raw_results:
        if not isinstance(raw_result, dict):
            raise ArtifactLedgerError("evidence control result must be an object")
        name = raw_result.get("name")
        state = raw_result.get("state")
        reason = raw_result.get("reason")
        details = raw_result.get("details")
        if not isinstance(name, str) or not isinstance(reason, str):
            raise ArtifactLedgerError("evidence control name and reason must be strings")
        if not isinstance(details, dict):
            raise ArtifactLedgerError("evidence control details must be an object")
        try:
            typed_state = EvidenceState(state)
        except (TypeError, ValueError) as exc:
            raise ArtifactLedgerError(f"unknown evidence control state: {state!r}") from exc
        results.append(EvidenceControlResult(name, typed_state, reason, details))

    ledger_results = [
        result
        for result in results
        if result.name == "artifact_ledger_integrity"
    ]
    if len(ledger_results) != 1:
        raise ArtifactLedgerError(
            "evidence must contain exactly one artifact_ledger_integrity control"
        )
    artifact_count = ledger_results[0].details.get("artifact_count")
    if type(artifact_count) is not int:
        raise ArtifactLedgerError("artifact_count must be an integer")

    try:
        verdict = EvidenceVerdict.from_results(mode, results)
    except ValueError as exc:
        raise ArtifactLedgerError(str(exc)) from exc
    if controls.get("missing") != list(verdict.missing):
        raise ArtifactLedgerError("nested missing controls do not match derived verdict")
    nested_valid = controls.get("artifact_valid")
    if not isinstance(nested_valid, bool) or nested_valid is not verdict.artifact_valid:
        raise ArtifactLedgerError("nested artifact_valid does not match derived verdict")
    top_level_valid = evidence.get("artifact_valid")
    if (
        not isinstance(top_level_valid, bool)
        or top_level_valid is not verdict.artifact_valid
    ):
        raise ArtifactLedgerError("top-level artifact_valid does not match derived verdict")
    return verdict, ledger_results[0]


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ArtifactLedgerError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def verify_evidence(run_root: Path) -> LedgerVerification:
    """Recompute the declared ledger and report whether its artifacts match."""
    root = Path(run_root)
    evidence_path = root / "evidence.json"
    try:
        _reject_unsafe_path(evidence_path)
        with evidence_path.open(encoding="utf-8") as handle:
            evidence = json.load(handle, object_pairs_hook=_reject_duplicate_json_keys)
        if not isinstance(evidence, dict):
            return LedgerVerification(False, "evidence JSON must be an object")
        _verdict, ledger_result = _verified_verdict(evidence)
        expected_hashes = evidence.get("artifacts")
        if not isinstance(expected_hashes, dict) or not all(
            isinstance(path, str) and isinstance(digest, str)
            for path, digest in expected_hashes.items()
        ):
            return LedgerVerification(False, "artifact ledger has an invalid shape")
        excluded_roots = evidence.get("excluded_roots", ["workspaces"])
        if not isinstance(excluded_roots, list) or not all(
            isinstance(root_name, str) for root_name in excluded_roots
        ):
            return LedgerVerification(False, "excluded roots have an invalid shape")
        actual_hashes = _artifact_hashes(root, tuple(excluded_roots))
    except (ArtifactLedgerError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return LedgerVerification(False, str(exc))

    if actual_hashes != expected_hashes:
        return LedgerVerification(False, "artifact hashes do not match the ledger")
    if ledger_result.details["artifact_count"] != len(actual_hashes):
        return LedgerVerification(False, "artifact_count does not match artifact ledger")
    if evidence.get("evidence_root_sha256") != _root_hash(actual_hashes):
        return LedgerVerification(False, "evidence root hash does not match the ledger")
    return LedgerVerification(True)
