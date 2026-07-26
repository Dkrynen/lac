"""Run the trusted raw/stock/LAC local-agent smoke baseline.

This command never downloads or creates a model. Use --dry-run first to prove
the exact local identities and output boundary before generating any tokens.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent_eval.containment import select_containment_provider
from backend.agent_eval.evidence import (
    EvidenceControlResult,
    EvidenceMode,
    EvidenceState,
    EvidenceVerdict,
)
from backend.agent_eval.identity import capture_preflight_identities
from backend.agent_eval.runner import (
    build_plan,
    preliminary_evidence_results,
    run_evaluation,
)
from backend.agent_eval.task import load_task
from backend.agent_launch.opencode_bin import (
    SUPPORTED_OPENCODE_VERSION,
    resolve_opencode_binary,
)
from backend.cookbook.proc import run as run_process


SUITE_ROOT = ROOT / "evals" / "agent"


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


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare raw Ollama, stock OpenCode, and the LAC OpenCode harness."
    )
    parser.add_argument("--task", required=True, help="Packaged trusted task id.")
    parser.add_argument("--base-model", required=True, help="Installed base Ollama model.")
    parser.add_argument(
        "--lac-model", required=True, help="Installed <base>-agent Ollama variant."
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Evidence root outside the model-hub source repository.",
    )
    parser.add_argument(
        "--ollama-host", default="http://localhost:11434", help="Loopback Ollama URL."
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--mode",
        choices=("verified", "diagnostic"),
        default="verified",
        help="verified fails closed; diagnostic artifacts are always invalid",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate identities and boundaries without creating files or model tokens.",
    )
    return parser.parse_args(argv)


def _unready_controls(verdict: EvidenceVerdict) -> list[str]:
    names = [
        item.name
        for item in verdict.results
        if item.state is not EvidenceState.PASS
    ]
    return names + [name for name in verdict.missing if name not in names]


def _dry_containment_preflight(
    plan,
    mode: EvidenceMode,
    *,
    identity_capture_fn: Callable[..., Any],
    containment_provider_fn: Callable[..., Any],
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
            snapshot = identity_capture_fn(plan)
            applications = [snapshot.opencode]
            application_paths = [str(item.path) for item in applications]
        provider = containment_provider_fn(
            mode,
            os.name,
            plan.ollama_host,
            applications,
        )
        result = provider.verify_active()
        if result.name != "os_loopback_only_egress":
            raise ValueError(
                "containment provider returned the wrong evidence control"
            )
    except Exception as exc:
        result = EvidenceControlResult(
            "os_loopback_only_egress",
            EvidenceState.FAIL,
            f"containment capability preflight failed: "
            f"{type(exc).__name__}: {exc}",
            {"active": False},
        )
    finally:
        if provider is not None:
            try:
                provider.close()
            except Exception as exc:
                result = EvidenceControlResult(
                    "os_loopback_only_egress",
                    EvidenceState.FAIL,
                    f"containment cleanup uncertain: "
                    f"{type(exc).__name__}: {exc}",
                    {
                        **result.details,
                        "active": False,
                        "cleanup_certain": False,
                    },
                )
    return result, application_paths


def _dry_report(
    plan,
    mode: EvidenceMode,
    containment: EvidenceControlResult,
    application_paths: list[str],
    original_command: str,
) -> dict[str, Any]:
    preliminary = [
        item
        for item in preliminary_evidence_results()
        if item.name != "os_loopback_only_egress"
    ]
    verdict = EvidenceVerdict.from_results(
        mode,
        [*preliminary, containment],
    )
    containment_ok = containment.state is EvidenceState.PASS
    report = {
        "ok": mode is EvidenceMode.DIAGNOSTIC or containment_ok,
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
        "os_egress_enforced": containment_ok,
        "containment": {
            "name": containment.name,
            "state": containment.state.value,
            "reason": containment.reason,
            "details": containment.details,
            "elevation_present": (
                containment_ok if mode is EvidenceMode.VERIFIED else None
            ),
            "application_paths": application_paths,
        },
        "evidence_ready": False,
        "controls": verdict.to_dict(),
        "artifact_valid": verdict.artifact_valid,
        "missing_controls": _unready_controls(verdict),
        "evidence_blockers": _unready_controls(verdict),
        "model_downloads": "forbidden",
        "runtime_dependency_bootstrap": (
            "possible_on_cold_opencode_config; source is not yet traced"
        ),
    }
    if mode is EvidenceMode.VERIFIED and not containment_ok:
        report["rerun_command"] = original_command
        report["error"] = (
            "Verified Windows network containment requires an elevated "
            "terminal.\n"
            "Reopen PowerShell as Administrator and rerun:\n"
            f"{original_command}"
        )
    return report


def main(
    argv: list[str] | None = None,
    *,
    list_models_fn: Callable[[], Any] | None = None,
    resolve_bin_fn: Callable[[], Path] = resolve_opencode_binary,
    run_fn: Callable[..., dict[str, Any]] = run_evaluation,
    environment_fn: Callable[[], dict[str, Any]] | None = None,
    identity_capture_fn: Callable[..., Any] = capture_preflight_identities,
    containment_provider_fn: Callable[..., Any] = select_containment_provider,
    out: Callable[[str], None] = print,
) -> int:
    raw_argv = argv if argv is not None else sys.argv[1:]
    original_argv = (
        [str(Path(__file__).resolve()), *raw_argv]
        if argv is not None
        else list(sys.argv)
    )
    original_command = subprocess.list2cmdline(original_argv)
    args = parse_args(raw_argv)
    try:
        mode = EvidenceMode(args.mode)
        task = load_task(args.task, SUITE_ROOT)
        installed = _model_names(
            (list_models_fn or _default_list_models)()
        )
        binary = resolve_bin_fn()
        plan = build_plan(
            task,
            base_model=args.base_model,
            lac_model=args.lac_model,
            ollama_host=args.ollama_host,
            output_root=args.output_dir,
            installed_models=installed,
            opencode_binary=binary,
            opencode_version=SUPPORTED_OPENCODE_VERSION,
            source_root=ROOT,
        )
        if args.dry_run:
            containment, application_paths = _dry_containment_preflight(
                plan,
                mode,
                identity_capture_fn=identity_capture_fn,
                containment_provider_fn=containment_provider_fn,
            )
            report = _dry_report(
                plan,
                mode,
                containment,
                application_paths,
                original_command,
            )
            out(json.dumps(report, indent=2, sort_keys=True))
            return 0 if report["ok"] else 2

        preliminary_results = preliminary_evidence_results()
        verdict = EvidenceVerdict.from_results(mode, preliminary_results)
        missing_controls = _unready_controls(verdict)
        if mode is EvidenceMode.VERIFIED and missing_controls:
            out(
                json.dumps(
                    {
                        "ok": False,
                        "mode": mode.value,
                        "controls": verdict.to_dict(),
                        "artifact_valid": verdict.artifact_valid,
                        "missing_controls": missing_controls,
                        "error": "verified evidence controls are not ready",
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 2

        environment = (
            environment_fn()
            if environment_fn is not None
            else _default_environment(plan.ollama_host)
        )
        comparison = run_fn(
            plan,
            run_id=args.run_id,
            environment=environment,
            mode=mode,
            preliminary_results=preliminary_results,
        )
        out(json.dumps(comparison, indent=2, sort_keys=True))
        return (
            0
            if comparison.get("evidence", {}).get("artifact_valid")
            and comparison.get("all_arms_executed")
            else 1
        )
    except Exception as exc:
        out(
            json.dumps(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                indent=2,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
