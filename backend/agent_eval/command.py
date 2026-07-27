"""Application service for bounded local-agent evidence evaluation.

The packaged CLI and the source script both enter through this module. The
service keeps parsing separate from orchestration and exposes dependencies so
unit tests need no model, process, WFP, or network call.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from backend.agent_eval.containment import (
    ContainmentElevationRequired,
    derive_containment_result,
    select_containment_provider,
)
from backend.agent_eval.evidence import (
    REQUIRED_CONTROLS,
    EvidenceControlResult,
    EvidenceMode,
    EvidenceState,
    EvidenceVerdict,
)
from backend.agent_eval.identity import capture_preflight_identities
from backend.agent_eval.runner import build_plan, run_evaluation
from backend.agent_eval.task import load_task
from backend.agent_launch.opencode_bin import (
    SUPPORTED_OPENCODE_VERSION,
    resolve_opencode_binary,
)
from backend.cookbook.proc import run as run_process


ROOT = Path(__file__).resolve().parents[2]
SUITE_ROOT = ROOT / "evals" / "agent"
OUTPUT_PROTECTED_ROOT = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else ROOT
)


@dataclass(frozen=True)
class EvalCommandRequest:
    task: str
    base_model: str
    lac_model: str
    output_dir: str | Path
    run_id: str | None = None
    mode: str | EvidenceMode = EvidenceMode.VERIFIED
    dry_run: bool = False
    ollama_host: str = "http://127.0.0.1:11434"
    original_command: str | None = None


@dataclass(frozen=True)
class EvalCommandResult:
    exit_code: int
    report: dict[str, Any]


def _default_list_models():
    from backend.provider.registry import create_provider

    return create_provider("ollama").list_models()


def _model_names(models) -> list[str]:
    names = []
    for model in models:
        if isinstance(model, dict):
            name = model.get("name") or model.get("model")
        else:
            name = getattr(model, "name", getattr(model, "model", None))
        if isinstance(name, str) and name:
            names.append(name)
    return names


def _git_environment() -> dict[str, Any]:
    commit = run_process(
        ["git", "rev-parse", "HEAD"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    status = run_process(
        ["git", "status", "--porcelain"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    return {
        "commit": commit.stdout.strip() if commit.returncode == 0 else None,
        "dirty": bool(status.stdout.strip()) if status.returncode == 0 else None,
    }


def _hardware_environment() -> dict[str, Any]:
    from backend.cookbook.hardware import detect

    info = detect()
    return {
        "os": info.os,
        "cpu": info.cpu,
        "ram_gb": info.ram_gb,
        "verified_total_vram_gb": info.total_vram_gb,
        "gpus": [
            {
                "name": gpu.name,
                "vram_gb": gpu.vram_gb,
                "tier": gpu.tier,
                "backend": gpu.backend,
                "split_verified": gpu.split_verified,
            }
            for gpu in info.gpus
        ],
    }


def _ollama_version(ollama_host: str) -> str | None:
    request = urllib.request.Request(
        ollama_host.rstrip("/") + "/api/version",
        headers={"User-Agent": "LAC-agent-eval/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
        version = data.get("version")
        return version if isinstance(version, str) else None
    except Exception:
        return None


def _default_environment(ollama_host: str) -> dict[str, Any]:
    environment: dict[str, Any] = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "git": {},
        "hardware": {},
        "ollama": {"version": _ollama_version(ollama_host)},
    }
    try:
        environment["git"] = _git_environment()
    except Exception as exc:
        environment["git"] = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        environment["hardware"] = _hardware_environment()
    except Exception as exc:
        environment["hardware"] = {"error": f"{type(exc).__name__}: {exc}"}
    return environment


def _containment_preflight(
    plan,
    mode: EvidenceMode,
    identity_capture: Callable[..., Any],
    *,
    wfp_api: object | None = None,
) -> tuple[EvidenceControlResult, list[str]]:
    provider = None
    application_paths: list[str] = []
    result = EvidenceControlResult(
        "os_loopback_only_egress",
        EvidenceState.FAIL,
        "containment capability preflight was not completed",
        {},
    )
    try:
        applications = []
        if mode is EvidenceMode.VERIFIED:
            snapshot = identity_capture(plan)
            applications = [snapshot.opencode]
            application_paths = [str(item.path) for item in applications]
        provider = select_containment_provider(
            mode,
            os.name,
            plan.ollama_host,
            applications,
            wfp_api=wfp_api,
        )
        result = derive_containment_result(
            mode,
            provider,
            plan.ollama_host,
            applications,
        )
    except Exception as exc:
        result = EvidenceControlResult(
            "os_loopback_only_egress",
            EvidenceState.FAIL,
            "containment capability preflight failed: "
            f"{type(exc).__name__}: {exc}",
            {
                "active": False,
                "elevation_required": isinstance(
                    exc,
                    ContainmentElevationRequired,
                ),
            },
        )
    finally:
        if provider is not None:
            try:
                provider.close()
            except Exception as exc:
                result = EvidenceControlResult(
                    "os_loopback_only_egress",
                    EvidenceState.FAIL,
                    "containment cleanup uncertain: "
                    f"{type(exc).__name__}: {exc}",
                    {
                        **result.details,
                        "active": False,
                        "cleanup_certain": False,
                        "elevation_required": isinstance(
                            exc,
                            ContainmentElevationRequired,
                        ),
                    },
                )
    return result, application_paths


class _DefaultContainment:
    def preflight(self, plan, mode, identity_capture):
        return _containment_preflight(plan, mode, identity_capture)


def _default_readiness_results(
    _plan,
    containment: EvidenceControlResult,
) -> tuple[EvidenceControlResult, ...]:
    reasons = {
        "runtime_dependency_provenance": (
            "exact runtime identity capture and postflight comparison are available"
        ),
        "immutable_ollama_model_lineage": (
            "full model digest capture and postflight comparison are available"
        ),
        "sealed_fixture_materialization": (
            "sealed per-arm fixture materialization is available"
        ),
        "windows_process_tree_containment": (
            "Windows Job Object descendant containment is available"
        ),
        "bounded_process_and_http_capture": (
            "bounded process and loopback HTTP capture are available"
        ),
        "counterbalanced_deterministic_sampling": (
            "the packaged task declares the canonical nine-arm schedule"
        ),
        "artifact_ledger_integrity": (
            "artifact ledger sealing and independent verification are available"
        ),
    }
    results = []
    for name in REQUIRED_CONTROLS:
        if name == "os_loopback_only_egress":
            results.append(containment)
        else:
            results.append(
                EvidenceControlResult(
                    name,
                    EvidenceState.PASS,
                    reasons[name],
                    {"capability_preflight": True},
                )
            )
    return tuple(results)


class EvalCommandDependencies:
    def __init__(self):
        self.containment = _DefaultContainment()

    def list_models(self):
        return _default_list_models()

    def preflight_elevation(self) -> bool | None:
        if os.name != "nt":
            return None
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return None

    def resolve_binary(self):
        return resolve_opencode_binary()

    def capture_identity(self, plan):
        return capture_preflight_identities(plan)

    def readiness_results(self, plan, containment):
        return _default_readiness_results(plan, containment)

    def capture_environment(self, ollama_host):
        return _default_environment(ollama_host)

    def run(self, plan, **kwargs):
        return run_evaluation(plan, **kwargs)


def _merge_containment(
    results,
    containment: EvidenceControlResult,
) -> tuple[EvidenceControlResult, ...]:
    return tuple(
        containment if item.name == "os_loopback_only_egress" else item
        for item in results
    )


def _unready_controls(verdict: EvidenceVerdict) -> list[str]:
    names = [
        item.name
        for item in verdict.results
        if item.state is not EvidenceState.PASS
    ]
    return names + [name for name in verdict.missing if name not in names]


def _fallback_command(request: EvalCommandRequest) -> str:
    argv = [
        "lac",
        "eval",
        "--task",
        request.task,
        "--base-model",
        request.base_model,
        "--lac-model",
        request.lac_model,
        "--output-dir",
        str(request.output_dir),
    ]
    if request.run_id:
        argv.extend(["--run-id", request.run_id])
    if EvidenceMode(request.mode) is EvidenceMode.DIAGNOSTIC:
        argv.extend(["--mode", "diagnostic"])
    if request.dry_run:
        argv.append("--dry-run")
    argv.append("--json")
    return subprocess.list2cmdline(argv)


def _dry_report(
    request: EvalCommandRequest,
    plan,
    mode: EvidenceMode,
    verdict: EvidenceVerdict,
    containment: EvidenceControlResult,
    application_paths: list[str],
) -> dict[str, Any]:
    missing = _unready_controls(verdict)
    evidence_ready = (
        mode is EvidenceMode.VERIFIED
        and not missing
        and all(item.state is EvidenceState.PASS for item in verdict.results)
    )
    report = {
        "ok": mode is EvidenceMode.DIAGNOSTIC or evidence_ready,
        "dry_run": True,
        "mode": mode.value,
        "task": plan.task.id,
        "models": {
            "raw": plan.base_model,
            "stock": plan.base_model,
            "lac": plan.lac_model,
        },
        "ollama_host": plan.ollama_host,
        "opencode": {
            "binary": str(plan.opencode_binary),
            "version": plan.opencode_version,
        },
        "output_root": str(plan.output_root),
        "fixture_sha256": plan.fixture_sha256,
        "auto_approval": {
            "enabled": True,
            "scope": plan.auto_approval_scope,
        },
        "network": "loopback_ollama_only",
        "os_egress_enforced": containment.state is EvidenceState.PASS,
        "containment": {
            "name": containment.name,
            "state": containment.state.value,
            "reason": containment.reason,
            "details": containment.details,
            "application_paths": application_paths,
        },
        "evidence_ready": evidence_ready,
        "controls": verdict.to_dict(),
        "artifact_valid": False,
        "missing_controls": missing,
        "evidence_blockers": missing,
        "model_downloads": "forbidden",
        "planned_arm_runs": 9,
        "operator_runtime_approval_required": True,
        "runtime_dependency_bootstrap": (
            "possible_on_cold_opencode_config; source is not yet traced"
        ),
    }
    if (
        mode is EvidenceMode.VERIFIED
        and containment.state is not EvidenceState.PASS
        and containment.details.get("elevation_required") is True
    ):
        command = request.original_command or _fallback_command(request)
        report["containment"]["elevation_present"] = False
        report["rerun_command"] = command
        report["error"] = (
            "Verified Windows network containment requires an elevated "
            "terminal.\n"
            "Reopen PowerShell as Administrator and rerun:\n"
            f"{command}"
        )
    elif mode is EvidenceMode.VERIFIED and missing:
        report["error"] = containment.reason
    return report


def _elevation_failure_report(
    request: EvalCommandRequest,
) -> dict[str, Any]:
    containment = EvidenceControlResult(
        "os_loopback_only_egress",
        EvidenceState.FAIL,
        "verified Windows containment requires elevation",
        {"active": False, "elevation_required": True},
    )
    verdict = EvidenceVerdict.from_results(
        EvidenceMode.VERIFIED,
        _default_readiness_results(None, containment),
    )
    command = request.original_command or _fallback_command(request)
    missing = _unready_controls(verdict)
    return {
        "ok": False,
        "dry_run": request.dry_run,
        "mode": EvidenceMode.VERIFIED.value,
        "task": request.task,
        "models": {
            "raw": request.base_model,
            "stock": request.base_model,
            "lac": request.lac_model,
        },
        "ollama_host": request.ollama_host,
        "output_root": str(Path(request.output_dir).resolve()),
        "network": "loopback_ollama_only",
        "os_egress_enforced": False,
        "containment": {
            "name": containment.name,
            "state": containment.state.value,
            "reason": containment.reason,
            "details": containment.details,
            "elevation_present": False,
            "application_paths": [],
        },
        "evidence_ready": False,
        "controls": verdict.to_dict(),
        "artifact_valid": False,
        "missing_controls": missing,
        "evidence_blockers": missing,
        "model_downloads": "forbidden",
        "planned_arm_runs": 9,
        "operator_runtime_approval_required": True,
        "rerun_command": command,
        "error": (
            "Verified Windows network containment requires an elevated "
            "terminal.\n"
            "Reopen PowerShell as Administrator and rerun:\n"
            f"{command}"
        ),
    }


def execute_eval_command(
    request: EvalCommandRequest,
    dependencies=None,
) -> EvalCommandResult:
    deps = dependencies or EvalCommandDependencies()
    try:
        mode = EvidenceMode(request.mode)
        elevation_check = getattr(deps, "preflight_elevation", None)
        if (
            mode is EvidenceMode.VERIFIED
            and elevation_check is not None
            and elevation_check() is False
        ):
            return EvalCommandResult(2, _elevation_failure_report(request))
        task = load_task(request.task, SUITE_ROOT, mode=mode)
        plan = build_plan(
            task,
            base_model=request.base_model,
            lac_model=request.lac_model,
            ollama_host=request.ollama_host,
            output_root=request.output_dir,
            installed_models=_model_names(deps.list_models()),
            opencode_binary=deps.resolve_binary(),
            opencode_version=SUPPORTED_OPENCODE_VERSION,
            source_root=OUTPUT_PROTECTED_ROOT,
        )
        containment, application_paths = deps.containment.preflight(
            plan,
            mode,
            deps.capture_identity,
        )
        readiness_results = _merge_containment(
            deps.readiness_results(plan, containment),
            containment,
        )
        if (
            not request.dry_run
            and hasattr(deps, "legacy_non_dry_readiness_results")
        ):
            readiness_results = deps.legacy_non_dry_readiness_results()
        verdict = EvidenceVerdict.from_results(mode, readiness_results)
        missing = _unready_controls(verdict)

        if request.dry_run:
            report = _dry_report(
                request,
                plan,
                mode,
                verdict,
                containment,
                application_paths,
            )
            if getattr(deps, "legacy_dry_run_semantics", False):
                report["ok"] = (
                    mode is EvidenceMode.DIAGNOSTIC
                    or containment.state is EvidenceState.PASS
                )
            return EvalCommandResult(0 if report["ok"] else 2, report)

        if mode is EvidenceMode.VERIFIED and missing:
            report = _dry_report(
                request,
                plan,
                mode,
                verdict,
                containment,
                application_paths,
            )
            report["dry_run"] = False
            report["error"] = report.get(
                "error",
                "verified evidence controls are not ready",
            )
            return EvalCommandResult(2, report)

        comparison = deps.run(
            plan,
            run_id=request.run_id,
            environment=deps.capture_environment(plan.ollama_host),
            mode=mode,
        )
        report = dict(comparison)
        evidence = dict(report.get("evidence", {}))
        if mode is EvidenceMode.DIAGNOSTIC:
            evidence["artifact_valid"] = False
        report["evidence"] = evidence
        report["mode"] = mode.value
        complete = (
            mode is EvidenceMode.VERIFIED
            and evidence.get("artifact_valid") is True
            and report.get("all_arms_executed") is True
        )
        report["ok"] = complete
        return EvalCommandResult(0 if complete else 1, report)
    except Exception as exc:
        return EvalCommandResult(
            2,
            {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
        )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare raw Ollama, stock OpenCode, and the LAC harness."
    )
    parser.add_argument("--task", required=True, help="Packaged trusted task id.")
    parser.add_argument("--base-model", required=True, help="Installed base model.")
    parser.add_argument(
        "--lac-model",
        required=True,
        help="Installed <base>-agent model variant.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Evidence root outside the LAC installation and source tree.",
    )
    parser.add_argument(
        "--ollama-host",
        default="http://127.0.0.1:11434",
        help="Literal loopback Ollama URL.",
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--mode",
        choices=("verified", "diagnostic"),
        default="verified",
        help="verified fails closed; diagnostic artifacts are always invalid",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate controls without files or model tokens.",
    )
    return parser.parse_args(argv)


class _LegacyContainment:
    def __init__(self, identity_capture_fn, containment_wfp_api):
        self._identity_capture_fn = identity_capture_fn
        self._containment_wfp_api = containment_wfp_api

    def preflight(self, plan, mode, _identity_capture):
        return _containment_preflight(
            plan,
            mode,
            self._identity_capture_fn,
            wfp_api=self._containment_wfp_api,
        )


class _LegacyDependencies(EvalCommandDependencies):
    legacy_dry_run_semantics = True

    def __init__(
        self,
        list_models_fn=None,
        resolve_bin_fn=resolve_opencode_binary,
        run_fn=run_evaluation,
        environment_fn=None,
        identity_capture_fn=capture_preflight_identities,
        containment_wfp_api=None,
    ):
        self._list_models_fn = list_models_fn or _default_list_models
        self._resolve_bin_fn = resolve_bin_fn
        self._run_fn = run_fn
        self._environment_fn = environment_fn
        self._identity_capture_fn = identity_capture_fn
        self.containment = _LegacyContainment(
            identity_capture_fn,
            containment_wfp_api,
        )

    def list_models(self):
        return self._list_models_fn()

    def preflight_elevation(self):
        return None

    def resolve_binary(self):
        return self._resolve_bin_fn()

    def capture_identity(self, plan):
        return self._identity_capture_fn(plan)

    def readiness_results(self, _plan, _containment):
        return tuple(
            EvidenceControlResult(
                name,
                EvidenceState.UNSUPPORTED,
                "not implemented in the legacy script preflight",
                {},
            )
            for name in REQUIRED_CONTROLS
        )

    def capture_environment(self, ollama_host):
        if self._environment_fn is not None:
            return self._environment_fn()
        return _default_environment(ollama_host)

    def legacy_non_dry_readiness_results(self):
        return tuple(
            EvidenceControlResult(
                name,
                EvidenceState.UNSUPPORTED,
                "not implemented in the legacy script preflight",
                {},
            )
            for name in REQUIRED_CONTROLS
        )

    def run(self, plan, **kwargs):
        return self._run_fn(plan, **kwargs)


def main(
    argv: list[str] | None = None,
    *,
    dependencies=None,
    out: Callable[[str], None] = print,
    _entrypoint_path: str | Path | None = None,
    **legacy_dependencies,
) -> int:
    raw_argv = argv if argv is not None else sys.argv[1:]
    args = parse_args(raw_argv)
    if dependencies is None and legacy_dependencies:
        dependencies = _LegacyDependencies(**legacy_dependencies)
    original_command = subprocess.list2cmdline(
        [
            sys.executable,
            str(Path(_entrypoint_path or __file__).resolve()),
            *raw_argv,
        ]
    )
    result = execute_eval_command(
        EvalCommandRequest(
            task=args.task,
            base_model=args.base_model,
            lac_model=args.lac_model,
            output_dir=args.output_dir,
            run_id=args.run_id,
            mode=args.mode,
            dry_run=args.dry_run,
            ollama_host=args.ollama_host,
            original_command=original_command,
        ),
        dependencies=dependencies,
    )
    out(json.dumps(result.report, indent=2, sort_keys=True))
    return result.exit_code
