"""Immutable runtime and Ollama lineage identities for evaluation evidence."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, TYPE_CHECKING

from backend.agent_launch.opencode_bin import SUPPORTED_OPENCODE_VERSION
from backend.agent_launch.opencode_bin import _probe_version

from .capture import IDENTITY_RESPONSE_MAX_BYTES, OLLAMA_RESPONSE_MAX_BYTES, bounded_http_json
from .evidence import EvidenceControlResult, EvidenceState

if TYPE_CHECKING:
    from .runner import EvaluationPlan


_REPARSE_POINT = 0x0400
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_FROM_DIGEST = re.compile(r"(?im)^FROM\s+.*(?:sha256[:-])([0-9a-f]{64})\s*$")
_WRAPPER_COMMAND = re.compile(
    rb'(?im)^\s*(?:"%(?:~dp0|dp0%)\\node_modules\\opencode-ai\\bin\\opencode\.exe"'
    rb'|%(?:~dp0|dp0%)\\node_modules\\opencode-ai\\bin\\opencode\.exe)\s+%\*\s*$'
)
_SHOW_FIELDS = ("modelfile", "parameters", "template", "details", "model_info", "capabilities")


class IdentityError(ValueError):
    """A runtime or model identity cannot be proven exactly."""


@dataclass(frozen=True)
class FileIdentity:
    path: Path
    size: int
    sha256: str
    version: str | None
    authenticode: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["path"] = str(self.path)
        return value


@dataclass(frozen=True)
class ModelIdentity:
    name: str
    digest: str
    size: int
    details: Mapping[str, Any]
    show_sha256: str
    parent_model: str
    from_blob_sha256: str
    parameters: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "digest": self.digest,
            "size": self.size,
            "details": _thaw(self.details),
            "show_sha256": self.show_sha256,
            "parent_model": self.parent_model,
            "from_blob_sha256": self.from_blob_sha256,
            "parameters": self.parameters,
        }


@dataclass(frozen=True)
class ModelIdentities:
    base: ModelIdentity
    lac: ModelIdentity


@dataclass(frozen=True)
class IdentityComparison:
    state: EvidenceState
    reason: str
    details: dict[str, Any]


@dataclass(frozen=True)
class EvaluationIdentitySnapshot:
    lac: FileIdentity
    ollama: FileIdentity
    opencode: FileIdentity
    opencode_wrapper: FileIdentity | None
    package_metadata: FileIdentity | None
    ollama_version: str
    config_sha256: Mapping[str, str]
    models: ModelIdentities

    @classmethod
    def for_test(cls) -> "EvaluationIdentitySnapshot":
        file = FileIdentity(Path("C:/test/runtime.exe"), 1, "a" * 64, "1.18.4", "unsigned")
        base = ModelIdentity("base", "b" * 64, 1, {}, "c" * 64, "", "d" * 64, "")
        lac = ModelIdentity("lac", "e" * 64, 1, {"parent_model": "base"}, "f" * 64, "base", "d" * 64, "")
        return cls(file, file, file, None, None, "0.0", MappingProxyType({"stock": "1" * 64, "lac": "2" * 64}), ModelIdentities(base, lac))

    def runtime_payload(self) -> dict[str, Any]:
        return {
            "lac": self.lac.to_dict(),
            "ollama": self.ollama.to_dict(),
            "opencode": self.opencode.to_dict(),
            "opencode_wrapper": self.opencode_wrapper.to_dict() if self.opencode_wrapper else None,
            "package_metadata": self.package_metadata.to_dict() if self.package_metadata else None,
            "ollama_version": self.ollama_version,
            "config_sha256": dict(sorted(self.config_sha256.items())),
        }

    def models_payload(self) -> dict[str, Any]:
        return {"base": self.models.base.to_dict(), "lac": self.models.lac.to_dict()}

    def to_dict(self) -> dict[str, Any]:
        return {"runtime": self.runtime_payload(), "models": self.models_payload()}

    def artifact_payloads(self) -> dict[str, dict[str, Any]]:
        runtime = self.runtime_payload()
        return {
            "lac": {"lac": runtime["lac"]},
            "ollama": {"ollama": runtime["ollama"], "ollama_version": runtime["ollama_version"]},
            "opencode": {key: runtime[key] for key in ("opencode", "opencode_wrapper", "package_metadata", "config_sha256")},
            "models": self.models_payload(),
        }


def canonical_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _reject_link(path: Path) -> None:
    stat_result = path.lstat()
    if path.is_symlink() or getattr(stat_result, "st_file_attributes", 0) & _REPARSE_POINT:
        raise IdentityError(f"link or reparse point is not an executable identity: {path}")


def _stat_token(path: Path) -> tuple[int, int, int, int, int]:
    result = path.stat()
    return (result.st_dev, result.st_ino, result.st_size, result.st_mtime_ns, result.st_ctime_ns)


def _default_authenticode(path: Path) -> str:
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "(Get-AuthenticodeSignature -LiteralPath $args[0]).Status", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    if completed.returncode != 0:
        return "unavailable"
    status = completed.stdout.strip().lower()
    if status == "valid":
        return "valid"
    if status == "notsigned":
        return "unsigned"
    if status:
        return "invalid"
    return "unavailable"


def file_identity(path: str | Path, *, version: str | None, authenticode_fn: Callable[[Path], str] = _default_authenticode) -> FileIdentity:
    candidate = Path(path)
    if not os.path.lexists(candidate):
        raise IdentityError(f"runtime file is missing: {candidate}")
    _reject_link(candidate)
    resolved = candidate.resolve(strict=True)
    before = _stat_token(resolved)
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    after = _stat_token(resolved)
    if before != after:
        raise IdentityError(f"runtime file changed during identity capture: {resolved}")
    authenticode = authenticode_fn(resolved)
    if authenticode not in {"valid", "invalid", "unsigned", "unavailable"}:
        raise IdentityError("authenticode status is invalid")
    return FileIdentity(resolved, before[2], digest.hexdigest(), version, authenticode)


def _name_key(name: str) -> str:
    return name.strip().casefold()


def _model_from_show(name: str, tag: dict[str, Any], show: dict[str, Any]) -> ModelIdentity:
    digest = tag.get("digest")
    size = tag.get("size")
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise IdentityError(f"model {name} has no full lowercase sha256 digest")
    if type(size) is not int or size < 0:
        raise IdentityError(f"model {name} has an invalid size")
    if set(_SHOW_FIELDS) - set(show):
        raise IdentityError(f"model {name} show response is incomplete")
    selected = {field: show[field] for field in _SHOW_FIELDS}
    if not isinstance(selected["modelfile"], str) or not isinstance(selected["parameters"], str) or not isinstance(selected["template"], str) or not isinstance(selected["model_info"], dict) or not isinstance(selected["capabilities"], list):
        raise IdentityError(f"model {name} show response has invalid field types")
    if not isinstance(selected["details"], dict):
        raise IdentityError(f"model {name} show details are invalid")
    match = _FROM_DIGEST.search(selected["modelfile"])
    if match is None:
        raise IdentityError(f"model {name} Modelfile has no immutable FROM digest")
    parent = selected["details"].get("parent_model", "")
    if not isinstance(parent, str):
        raise IdentityError(f"model {name} parent model is invalid")
    return ModelIdentity(name, digest, size, _freeze(selected["details"]), canonical_sha256(selected), parent, match.group(1), selected["parameters"])


def capture_model_identities(base_name: str, lac_name: str, *, fetch_fn: Callable[[str], dict[str, Any]]) -> ModelIdentities:
    tags = fetch_fn("/api/tags")
    models = tags.get("models") if isinstance(tags, dict) else None
    if not isinstance(models, list):
        raise IdentityError("Ollama tags response has no models list")
    indexed: dict[str, dict[str, Any]] = {}
    for tag in models:
        if not isinstance(tag, dict) or not isinstance(tag.get("name"), str):
            raise IdentityError("Ollama tag is invalid")
        key = _name_key(tag["name"])
        if key in indexed:
            raise IdentityError(f"duplicate normalized model tag: {tag['name']}")
        indexed[key] = tag
    selected = []
    for name in (base_name, lac_name):
        tag = indexed.get(_name_key(name))
        if tag is None:
            raise IdentityError(f"missing model tag: {name}")
        selected.append(_model_from_show(name, tag, fetch_fn(name)))
    base, lac = selected
    if lac.parent_model != base_name:
        raise IdentityError(f"LAC model parent must be exactly {base_name}")
    if lac.from_blob_sha256 != base.from_blob_sha256:
        raise IdentityError("LAC model FROM digest must match base immutable lineage")
    return ModelIdentities(base, lac)


def _ollama_executable() -> Path:
    found = shutil.which("ollama")
    if found is None:
        raise IdentityError("Ollama executable is not discoverable")
    return Path(found)


def _package_metadata(binary: Path) -> Path | None:
    roots = [binary.parent]
    if binary.parent.name.casefold() == "bin":
        roots.insert(0, binary.parent.parent)
    for root in roots:
        for name in ("package.json", "package-lock.json"):
            candidate = root / name
            if candidate.is_file():
                return candidate
    return None


def _reject_unsafe_components(path: Path) -> None:
    for component in (path, *path.parents):
        if component == component.parent:
            break
        if os.path.lexists(component):
            _reject_link(component)


def _wrapper_target(wrapper: Path) -> Path:
    _reject_unsafe_components(wrapper)
    payload = wrapper.read_bytes()
    if len(payload) > 128 * 1024:
        raise IdentityError("OpenCode wrapper is too large")
    if _WRAPPER_COMMAND.search(payload) is None:
        raise IdentityError("OpenCode wrapper has no supported executable target")
    target = wrapper.parent / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
    _reject_unsafe_components(target)
    if not target.is_file():
        raise IdentityError("OpenCode wrapper target is missing")
    return target


def capture_preflight_identities(plan: "EvaluationPlan") -> EvaluationIdentitySnapshot:
    if plan.opencode_version != SUPPORTED_OPENCODE_VERSION:
        raise IdentityError(f"unsupported OpenCode version: {plan.opencode_version}")
    base_url = plan.ollama_host.rstrip("/")
    def fetch(key: str) -> dict[str, Any]:
        if key == "/api/tags":
            return bounded_http_json(base_url + key, method="GET", body=None, timeout=10, max_bytes=OLLAMA_RESPONSE_MAX_BYTES)
        return bounded_http_json(base_url + "/api/show", method="POST", body={"model": key, "verbose": False}, timeout=10, max_bytes=OLLAMA_RESPONSE_MAX_BYTES)
    version = bounded_http_json(base_url + "/api/version", method="GET", body=None, timeout=10, max_bytes=IDENTITY_RESPONSE_MAX_BYTES).get("version")
    if not isinstance(version, str) or not version:
        raise IdentityError("Ollama version response is invalid")
    opencode_path = Path(plan.opencode_binary)
    wrapper_path = opencode_path if opencode_path.suffix.lower() in {".cmd", ".bat"} else None
    executable_path = _wrapper_target(wrapper_path) if wrapper_path else opencode_path
    try:
        actual_opencode_version = _probe_version(executable_path)
    except RuntimeError as exc:
        raise IdentityError(f"unable to prove OpenCode version: {exc}") from exc
    if actual_opencode_version != SUPPORTED_OPENCODE_VERSION:
        raise IdentityError(f"unsupported OpenCode version: {actual_opencode_version}")
    package = _package_metadata(executable_path)
    lac_path = Path(sys.executable)
    return EvaluationIdentitySnapshot(
        lac=file_identity(lac_path, version=None),
        ollama=file_identity(_ollama_executable(), version=version),
        opencode=file_identity(executable_path, version=actual_opencode_version),
        opencode_wrapper=file_identity(wrapper_path, version=None) if wrapper_path else None,
        package_metadata=file_identity(package, version=None) if package else None,
        ollama_version=version,
        config_sha256=MappingProxyType({
            "stock": canonical_sha256({"model": plan.base_model, "host": base_url, "evaluation": True}),
            "lac": canonical_sha256({"model": plan.lac_model, "host": base_url, "evaluation": True}),
        }),
        models=capture_model_identities(plan.base_model, plan.lac_model, fetch_fn=fetch),
    )


def _first_difference(before: Any, after: Any, path: str = "") -> str | None:
    if type(before) is not type(after):
        return path or "payload"
    if isinstance(before, dict):
        for key in sorted(set(before) | set(after)):
            if key not in before or key not in after:
                return f"{path}.{key}" if path else key
            found = _first_difference(before[key], after[key], f"{path}.{key}" if path else key)
            if found:
                return found
        return None
    if before != after:
        return path or "payload"
    return None


def compare_identity_payloads(before: dict[str, Any], after: dict[str, Any]) -> IdentityComparison:
    difference = _first_difference(before, after)
    if difference is None:
        return IdentityComparison(EvidenceState.PASS, "runtime and model identities are unchanged", {})
    return IdentityComparison(EvidenceState.FAIL, f"identity drift detected at {difference}", {"changed": difference})


def compare_postflight_identities(before: EvaluationIdentitySnapshot, after: EvaluationIdentitySnapshot) -> tuple[EvidenceControlResult, EvidenceControlResult]:
    runtime = compare_identity_payloads({"runtime": before.runtime_payload()}, {"runtime": after.runtime_payload()})
    models = compare_identity_payloads({"models": before.models_payload()}, {"models": after.models_payload()})
    return (
        EvidenceControlResult("runtime_dependency_provenance", runtime.state, runtime.reason, runtime.details),
        EvidenceControlResult("immutable_ollama_model_lineage", models.state, models.reason, models.details),
    )
