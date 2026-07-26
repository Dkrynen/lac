"""Truthful local-agent readiness checks for ``lac doctor``."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from backend.agent_launch.opencode_bin import (
    SUPPORTED_OPENCODE_VERSION,
    resolve_opencode_binary,
)
from backend.agent_launch.variant import normalize_model_name
from backend.cookbook.config import CONFIG_DIR


MIN_FREE_BYTES = 5 * 1024**3


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    summary: str
    evidence: dict[str, Any]
    remediation: str = ""


@dataclass(frozen=True)
class DoctorReport:
    ready: bool
    checks: tuple[DoctorCheck, ...]


def _default_detect():
    from backend.cookbook.hardware import detect

    return detect()


def _default_provider_factory():
    from backend.provider.registry import create_provider

    return create_provider("ollama")


def _default_recommend(info, *, use_case, top_k):
    from backend.cookbook.recommend import recommend

    return recommend(info, use_case=use_case, top_k=top_k)


def _default_receipt_probe(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    descriptor, probe = tempfile.mkstemp(prefix=".lac-doctor-", dir=path)
    os.close(descriptor)
    Path(probe).unlink()


def _model_name(model: Any) -> str:
    if isinstance(model, dict):
        return str(model.get("name") or model.get("model") or "")
    return str(getattr(model, "name", getattr(model, "model", "")) or "")


def _error(exc: BaseException) -> str:
    text = str(exc).strip()
    text = re.sub(r"(https?://)[^/\s@]+@", r"\1", text)
    return text[:500] if text else exc.__class__.__name__


def run_doctor(
    *,
    project_dir: str | Path,
    detect_fn: Callable[[], Any] = _default_detect,
    provider_factory: Callable[[], Any] = _default_provider_factory,
    recommend_fn: Callable[..., list[Any]] = _default_recommend,
    which_fn: Callable[[str], str | None] = shutil.which,
    opencode_probe_fn: Callable[[], Path] = resolve_opencode_binary,
    disk_usage_fn: Callable[[str | Path], Any] = shutil.disk_usage,
    receipt_probe_fn: Callable[[Path], None] = _default_receipt_probe,
    receipt_root: str | Path | None = None,
) -> DoctorReport:
    """Inspect local readiness without downloading, installing, or launching."""
    checks: list[DoctorCheck] = []
    root = Path(project_dir).resolve()
    if root.is_dir():
        checks.append(
            DoctorCheck(
                "project",
                "pass",
                "Project directory is available.",
                {"path": str(root)},
            )
        )
    else:
        checks.append(
            DoctorCheck(
                "project",
                "fail",
                "Project directory is unavailable.",
                {"path": str(root)},
                "Choose an existing repository directory.",
            )
        )

    info = None
    try:
        info = detect_fn()
        excluded_shared = round(
            sum(
                float(gpu.vram_gb)
                for gpu in getattr(info, "gpus", [])
                if getattr(gpu, "tier", "") == "integrated"
                and not getattr(gpu, "split_verified", False)
            ),
            1,
        )
        hardware_evidence = {
            "os": getattr(info, "os", ""),
            "cpu": getattr(info, "cpu", ""),
            "ram_gb": getattr(info, "ram_gb", 0.0),
            "verified_model_fit_vram_gb": getattr(
                info, "combined_vram_gb", 0.0
            ),
            "excluded_shared_memory_gb": excluded_shared,
        }
        usable = (
            float(getattr(info, "ram_gb", 0.0) or 0.0) > 0
            and int(getattr(info, "cpu_cores", 0) or 0) > 0
        )
        status = "fail" if not usable else ("warn" if excluded_shared else "pass")
        checks.append(
            DoctorCheck(
                "hardware",
                status,
                (
                    "Hardware probe returned no usable RAM or CPU evidence."
                    if not usable
                    else
                    f"{excluded_shared} GB of reported shared GPU memory is "
                    "excluded until a runtime split probe verifies it."
                    if excluded_shared
                    else "Hardware capacity was detected."
                ),
                hardware_evidence,
                (
                    "Run `lac scan` in a normal local terminal and resolve "
                    "hardware-probe permissions before trusting recommendations."
                    if not usable
                    else
                    "Run a future LAC split probe before treating shared GPU "
                    "memory as model-fit capacity."
                    if excluded_shared
                    else ""
                ),
            )
        )
    except Exception as exc:  # noqa: BLE001 - every probe must become evidence
        checks.append(
            DoctorCheck(
                "hardware",
                "fail",
                "Hardware detection failed.",
                {"error": _error(exc)},
                "Run `lac scan`; resolve its hardware-detection error, then retry.",
            )
        )

    try:
        usage = disk_usage_fn(root)
        free = int(usage.free)
        checks.append(
            DoctorCheck(
                "disk",
                "pass" if free >= MIN_FREE_BYTES else "fail",
                (
                    "Disk space is sufficient."
                    if free >= MIN_FREE_BYTES
                    else "Disk space is below LAC's 5 GB readiness floor."
                ),
                {"free_bytes": free, "minimum_free_bytes": MIN_FREE_BYTES},
                (
                    ""
                    if free >= MIN_FREE_BYTES
                    else "Free at least 5 GB on the repository volume."
                ),
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(
            DoctorCheck(
                "disk",
                "fail",
                "Disk capacity could not be inspected.",
                {"error": _error(exc)},
                "Check filesystem permissions and retry.",
            )
        )

    installed: list[str] = []
    ollama_ok = False
    try:
        provider = provider_factory()
        installed = sorted(
            name
            for name in (_model_name(model) for model in provider.list_models())
            if name
        )
        ollama_ok = True
        checks.append(
            DoctorCheck(
                "ollama",
                "pass",
                "The configured local Ollama runtime responded.",
                {"installed_model_count": len(installed)},
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(
            DoctorCheck(
                "ollama",
                "fail",
                "The configured local Ollama runtime did not respond.",
                {"error": _error(exc)},
                "Install or start Ollama locally, then retry `lac doctor`.",
            )
        )

    if not ollama_ok:
        checks.append(
            DoctorCheck(
                "ollama_model",
                "fail",
                "Agent-model readiness could not be checked.",
                {"installed": []},
                "Restore the local Ollama runtime first.",
            )
        )
    elif not installed:
        checks.append(
            DoctorCheck(
                "ollama_model",
                "fail",
                "No local model is installed.",
                {"installed": []},
                "Choose a LAC recommendation and explicitly download it; "
                "`lac doctor` never downloads models.",
            )
        )
    elif info is None:
        checks.append(
            DoctorCheck(
                "ollama_model",
                "fail",
                "Installed models cannot be matched without hardware evidence.",
                {"installed": installed},
                "Resolve hardware detection, then retry.",
            )
        )
    else:
        try:
            recommendations = recommend_fn(
                info, use_case="agent", top_k=100
            )
            by_normalized = {
                normalize_model_name(name): name for name in installed
            }
            selected = next(
                (
                    by_normalized[normalize_model_name(rec.model.id)]
                    for rec in recommendations
                    if normalize_model_name(rec.model.id) in by_normalized
                ),
                None,
            )
            checks.append(
                DoctorCheck(
                    "ollama_model",
                    "pass" if selected else "fail",
                    (
                        f"{selected} is installed and fits the agent profile."
                        if selected
                        else "Installed models do not match an agent-capable "
                        "recommendation for this machine."
                    ),
                    {
                        "installed": installed,
                        "selected": selected,
                        "recommended": [
                            rec.model.id for rec in recommendations[:5]
                        ],
                    },
                    (
                        ""
                        if selected
                        else "Run `lac recommend --use-case agent`; explicitly "
                        "download one supported recommendation, then retry."
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(
                DoctorCheck(
                    "ollama_model",
                    "fail",
                    "Agent-model compatibility could not be evaluated.",
                    {"installed": installed, "error": _error(exc)},
                    "Run `lac recommend --use-case agent` and resolve its error.",
                )
            )

    try:
        binary = opencode_probe_fn()
        checks.append(
            DoctorCheck(
                "opencode",
                "pass",
                f"Supported OpenCode {SUPPORTED_OPENCODE_VERSION} is available.",
                {
                    "path": str(binary),
                    "supported_version": SUPPORTED_OPENCODE_VERSION,
                },
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(
            DoctorCheck(
                "opencode",
                "fail",
                "The supported OpenCode runtime is unavailable.",
                {
                    "error": _error(exc),
                    "supported_version": SUPPORTED_OPENCODE_VERSION,
                },
                f"Install OpenCode {SUPPORTED_OPENCODE_VERSION} and ensure "
                "`opencode` is on PATH.",
            )
        )

    lac_path = which_fn("lac")
    checks.append(
        DoctorCheck(
            "cli_path",
            "pass" if lac_path else "warn",
            (
                "The `lac` command is on PATH."
                if lac_path
                else "The `lac` command is not discoverable on PATH."
            ),
            {"path": lac_path},
            (
                ""
                if lac_path
                else "Add the LAC installation directory to PATH or use the "
                "installed launcher."
            ),
        )
    )

    receipts = Path(
        receipt_root
        or CONFIG_DIR / "receipts" / "repository-inspections"
    )
    try:
        receipt_probe_fn(receipts)
        checks.append(
            DoctorCheck(
                "receipts",
                "pass",
                "The local receipt store is writable.",
                {"path": str(receipts)},
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(
            DoctorCheck(
                "receipts",
                "fail",
                "The local receipt store is not writable.",
                {"path": str(receipts), "error": _error(exc)},
                "Fix permissions for the LAC data directory, then retry.",
            )
        )

    result = tuple(checks)
    return DoctorReport(
        ready=all(check.status != "fail" for check in result),
        checks=result,
    )
