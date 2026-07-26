"""Three-arm evaluation orchestration and durable evidence artifacts."""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from backend.agent_launch.opencode_bin import SUPPORTED_OPENCODE_VERSION
from backend.agent_launch.variant import (
    agent_variant_name,
    is_installed,
)

from .evidence import (
    REQUIRED_CONTROLS,
    EvidenceControlResult,
    EvidenceMode,
    EvidenceState,
)
from .containment import (
    derive_containment_result,
    select_containment_provider,
)
from .opencode import run_lac, run_stock
from .ledger import atomic_write_json, seal_evidence
from .raw_ollama import _is_loopback_ollama_host, build_raw_prompt, run_raw
from .result import ArmResult
from .scoring import ScoreResult, score_exact_text
from .fixture import (
    FixtureManifest,
    FixtureSealError,
    build_fixture_manifest,
    materialize_fixture,
    verify_materialized_fixture,
)
from .task import EvalTask, snapshot_fixture, task_contract_sha256
from .identity import (
    EvaluationIdentitySnapshot,
    acquire_runtime_identity_leases,
    capture_preflight_identities,
    compare_postflight_identities,
)
from .windows_job import WindowsJobProcess


_RUN_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")
_EVIDENCE_BLOCKERS = REQUIRED_CONTROLS[:-1]


class EvalPlanError(ValueError):
    """The requested evaluation cannot be made isolated and reproducible."""


@dataclass(frozen=True)
class EvaluationPlan:
    task: EvalTask
    base_model: str
    lac_model: str
    ollama_host: str
    output_root: Path
    opencode_binary: Path
    opencode_version: str
    fixture_sha256: str
    fixture_manifest: FixtureManifest
    auto_approval_scope: str = "disposable_workspace_only"


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def build_plan(
    task: EvalTask,
    *,
    base_model: str,
    lac_model: str,
    ollama_host: str,
    output_root: str | Path,
    installed_models: Iterable[str],
    opencode_binary: str | Path,
    opencode_version: str,
    source_root: str | Path,
) -> EvaluationPlan:
    expected_variant = agent_variant_name(base_model)
    if lac_model != expected_variant:
        raise EvalPlanError(
            f"LAC model must be the expected agent variant {expected_variant}"
        )
    installed = tuple(installed_models)
    if not is_installed(base_model, installed):
        raise EvalPlanError(f"base model is not installed: {base_model}")
    if not is_installed(lac_model, installed):
        raise EvalPlanError(f"LAC model is not installed: {lac_model}")
    if not _is_loopback_ollama_host(ollama_host):
        raise EvalPlanError("Ollama host must be an unauthenticated loopback HTTP URL")
    if opencode_version != SUPPORTED_OPENCODE_VERSION:
        raise EvalPlanError(
            f"OpenCode {opencode_version} is not the supported "
            f"{SUPPORTED_OPENCODE_VERSION}"
        )

    source = Path(source_root).resolve()
    output = Path(output_root).resolve()
    if output == Path(output.anchor):
        raise EvalPlanError("output root cannot be a filesystem root")
    if _inside(output, source):
        raise EvalPlanError("evaluation output must be outside the source repo")

    fixture_manifest = build_fixture_manifest(task)
    return EvaluationPlan(
        task=task,
        base_model=base_model,
        lac_model=lac_model,
        ollama_host=ollama_host.rstrip("/"),
        output_root=output,
        opencode_binary=Path(opencode_binary),
        opencode_version=opencode_version,
        fixture_sha256=fixture_manifest.aggregate_sha256,
        fixture_manifest=fixture_manifest,
    )


def preliminary_evidence_results() -> tuple[EvidenceControlResult, ...]:
    """Report Task 1's controls as explicitly unsupported until later tasks add them."""
    return tuple(
        EvidenceControlResult(
            name,
            EvidenceState.UNSUPPORTED,
            "not implemented in the current evidence pipeline",
            {},
        )
        for name in _EVIDENCE_BLOCKERS
    )


def _manifest(
    plan: EvaluationPlan,
    environment: dict[str, Any],
    mode: EvidenceMode,
) -> dict[str, Any]:
    task_contract = {
        "schema_version": plan.task.schema_version,
        "id": plan.task.id,
        "prompt": plan.task.prompt,
        "timeout_seconds": plan.task.timeout_seconds,
        "scorer": {
            "type": plan.task.scorer.type,
            "expected_sha256": hashlib.sha256(
                plan.task.scorer.expected.encode("utf-8")
            ).hexdigest(),
        },
        "fixture_sha256": plan.fixture_sha256,
    }
    return {
        "schema_version": 1,
        "task": task_contract,
        "task_contract_sha256": task_contract_sha256(plan.task),
        "fixture_manifest": plan.fixture_manifest.to_dict(),
        "models": {
            "raw": plan.base_model,
            "stock": plan.base_model,
            "lac": plan.lac_model,
        },
        "runtimes": {
            "raw": "ollama",
            "stock": {
                "name": "opencode",
                "version": plan.opencode_version,
                "binary": str(plan.opencode_binary),
            },
            "lac": {
                "name": "opencode",
                "version": plan.opencode_version,
                "binary": str(plan.opencode_binary),
            },
        },
        "ollama_host": plan.ollama_host,
        "prompt_delivery": {
            "raw": "task prompt plus bounded source snapshot",
            "stock": "task prompt plus read-only project tools",
            "lac": "task prompt plus read-only project tools",
        },
        "auto_approval": {
            "enabled": True,
            "scope": plan.auto_approval_scope,
            "reason": "reproducible non-interactive run in copied fixture only",
        },
        "containment": {
            "workspace": "per-arm copied fixture",
            "environment": "allowlist with isolated home/config/data/cache",
            "allowed_tools": ["read", "glob", "grep"],
            "external_directory": "deny",
            "model_downloads": "forbidden",
            "runtime_dependency_bootstrap": (
                "possible_on_cold_opencode_config; source is not yet traced"
            ),
            "os_egress_enforced": False,
        },
        "evidence_mode": mode.value,
        "environment": environment,
    }


def _adapter_failure(arm: str, model: str, exc: Exception) -> ArmResult:
    return ArmResult(
        arm=arm,
        model=model,
        runtime="ollama" if arm == "raw" else "opencode",
        completed=False,
        timed_out=False,
        response="",
        wall_time_ms=0.0,
        errors=(f"{type(exc).__name__}: {exc}",),
    )


def _validated_result(arm: str, model: str, result: Any) -> ArmResult:
    if not isinstance(result, ArmResult):
        raise TypeError(f"{arm} adapter returned {type(result).__name__}, not ArmResult")
    if result.arm != arm:
        raise ValueError(f"{arm} adapter mislabeled result as {result.arm}")
    if result.model != model:
        raise ValueError(f"{arm} adapter reported unexpected model {result.model}")
    return result


def _score_result(result: ArmResult, expected: str) -> ScoreResult:
    scored = score_exact_text(result.response, expected)
    if result.completed:
        return scored
    return ScoreResult(
        passed=False,
        score=0.0,
        actual=scored.actual,
        expected=scored.expected,
    )


def _valid_capture_record(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    allowed = value.get("allowed_bytes")
    observed = value.get("observed_bytes")
    return (
        type(allowed) is int
        and allowed >= 0
        and type(observed) is int
        and 0 <= observed <= allowed
        and value.get("overflowed") is False
    )


def _bounded_capture_result(
    results: dict[str, ArmResult],
) -> EvidenceControlResult:
    invalid: list[str] = []
    details: dict[str, Any] = {}
    for arm in ("raw", "stock", "lac"):
        capture = results[arm].capture
        details[arm] = capture
        if not isinstance(capture, dict):
            invalid.append(f"{arm}: capture evidence is not an object")
            continue
        if arm == "raw":
            if not _valid_capture_record(capture.get("response")):
                invalid.append(f"{arm}: response capture is missing or invalid")
            continue
        if capture.get("cleanup_complete") is not True:
            invalid.append(f"{arm}: capture cleanup was not complete")
        for stream in ("stdout", "stderr"):
            if not _valid_capture_record(capture.get(stream)):
                invalid.append(f"{arm}: {stream} capture is missing or invalid")
    return EvidenceControlResult(
        "bounded_process_and_http_capture",
        EvidenceState.FAIL if invalid else EvidenceState.PASS,
        (
            "; ".join(invalid)
            if invalid
            else "all arms supplied bounded, non-overflowed capture evidence"
        ),
        {"arms": details},
    )


def _windows_containment_result(
    results: dict[str, ArmResult],
    measured_arms: set[str],
) -> EvidenceControlResult:
    invalid: list[str] = []
    details: dict[str, Any] = {}
    for arm in ("stock", "lac"):
        capture = results[arm].capture
        measured = (
            capture.get("windows_job")
            if isinstance(capture, dict)
            else None
        )
        details[arm] = measured
        if arm not in measured_arms:
            invalid.append(
                f"{arm}: containment was not produced by the measured "
                "default adapter"
            )
            continue
        if not isinstance(measured, dict):
            invalid.append(f"{arm}: Windows Job evidence is missing")
            continue
        if capture.get("windows_job_measured") is not True:
            invalid.append(f"{arm}: typed Job measurement marker is missing")
            continue
        expected = {
            "real_windows_job": True,
            "assignment_proven": True,
            "active_process_limit": 1,
            "kill_on_close": True,
            "resume_after_assignment": True,
            "final_active_processes": 0,
            "handles_closed": True,
            "cleanup_certain": True,
        }
        boolean_fields = {
            "real_windows_job",
            "assignment_proven",
            "kill_on_close",
            "resume_after_assignment",
            "handles_closed",
            "cleanup_certain",
        }
        for name, value in expected.items():
            observed = measured.get(name)
            if name in boolean_fields and type(observed) is not bool:
                invalid.append(f"{arm}: {name} is not an exact boolean")
            elif name in {
                "active_process_limit",
                "final_active_processes",
            } and type(observed) is not int:
                invalid.append(f"{arm}: {name} is not an exact integer")
            elif observed != value:
                invalid.append(
                    f"{arm}: {name} was not proven as {value!r}"
                )
    return EvidenceControlResult(
        "windows_process_tree_containment",
        EvidenceState.FAIL if invalid else EvidenceState.PASS,
        (
            "; ".join(invalid)
            if invalid
            else (
                "stock and LAC arms proved assignment-before-resume, "
                "active-process limiting, and zero-active cleanup"
            )
        ),
        {"arms": details},
    )


def _complete_config_identity(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"path", "size", "sha256"}
        and isinstance(value["path"], str)
        and bool(value["path"])
        and type(value["size"]) is int
        and value["size"] >= 0
        and isinstance(value["sha256"], str)
        and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is not None
    )


def _identity_failures(reason: str) -> tuple[EvidenceControlResult, ...]:
    return (
        EvidenceControlResult(
            "runtime_dependency_provenance",
            EvidenceState.FAIL,
            reason,
            {},
        ),
        EvidenceControlResult(
            "immutable_ollama_model_lineage",
            EvidenceState.FAIL,
            reason,
            {},
        ),
    )


def _fail_runtime_provenance(
    results: tuple[EvidenceControlResult, ...],
    reason: str,
) -> tuple[EvidenceControlResult, ...]:
    updated = []
    found = False
    for item in results:
        if item.name == "runtime_dependency_provenance":
            updated.append(
                EvidenceControlResult(
                    item.name,
                    EvidenceState.FAIL,
                    reason,
                    item.details,
                )
            )
            found = True
        else:
            updated.append(item)
    if not found:
        updated.append(
            EvidenceControlResult(
                "runtime_dependency_provenance",
                EvidenceState.FAIL,
                reason,
                {},
            )
        )
    return tuple(updated)


def _default_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def run_evaluation(
    plan: EvaluationPlan,
    *,
    run_id: str | None = None,
    raw_fn: Callable[..., ArmResult] = run_raw,
    stock_fn: Callable[..., ArmResult] = run_stock,
    lac_fn: Callable[..., ArmResult] = run_lac,
    environment: dict[str, Any] | None = None,
    mode: EvidenceMode = EvidenceMode.DIAGNOSTIC,
    preliminary_results: Iterable[EvidenceControlResult] = (),
    identity_capture_fn: Callable[[EvaluationPlan], EvaluationIdentitySnapshot] = capture_preflight_identities,
    identity_compare_fn: Callable[[EvaluationIdentitySnapshot, EvaluationIdentitySnapshot], tuple[EvidenceControlResult, EvidenceControlResult]] = compare_postflight_identities,
    identity_lease_fn: Callable[[EvaluationIdentitySnapshot], Any] = acquire_runtime_identity_leases,
    containment_wfp_api: object | None = None,
) -> dict[str, Any]:
    run_id = run_id or _default_run_id()
    if not _RUN_ID.fullmatch(run_id):
        raise EvalPlanError("run id contains unsafe characters")
    run_root = plan.output_root / run_id
    if run_root.exists():
        raise EvalPlanError(f"evaluation run already exists: {run_root}")

    workspaces_root = run_root / "workspaces"
    arms_root = run_root / "arms"
    workspaces_root.mkdir(parents=True)
    arms_root.mkdir()
    manifest = _manifest(plan, environment or {}, mode)
    atomic_write_json(run_root / "manifest.json", manifest)

    identity_results: tuple[EvidenceControlResult, ...]
    preflight: EvaluationIdentitySnapshot | None = None
    runtime_leases: Any | None = None
    try:
        preflight = identity_capture_fn(plan)
        identity_root = run_root / "identities"
        identity_root.mkdir()
        for name, payload in preflight.artifact_payloads().items():
            atomic_write_json(identity_root / f"{name}.json", payload)
        runtime_leases = identity_lease_fn(preflight)
        identity_results = _identity_failures(
            "identity postflight was not completed"
        )
    except Exception as exc:
        reason = f"identity preflight failed: {type(exc).__name__}: {exc}"
        identity_results = _identity_failures(reason)

    containment_provider: Any | None = None
    containment_result = EvidenceControlResult(
        "os_loopback_only_egress",
        EvidenceState.FAIL,
        "containment provider was not initialized",
        {},
    )
    applications = [preflight.opencode] if preflight is not None else []
    try:
        containment_provider = select_containment_provider(
            mode,
            os.name,
            plan.ollama_host,
            applications,
            wfp_api=containment_wfp_api,
        )
        containment_result = derive_containment_result(
            mode,
            containment_provider,
            plan.ollama_host,
            applications,
        )
    except Exception as exc:
        containment_result = EvidenceControlResult(
            "os_loopback_only_egress",
            EvidenceState.FAIL,
            f"containment activation failed: {type(exc).__name__}: {exc}",
            {"active": False},
        )

    results: dict[str, ArmResult] = {}
    measured_windows_arms: set[str] = set()
    scores: dict[str, ScoreResult] = {}
    fixture_controls: list[EvidenceControlResult] = []
    try:
        for arm, model, adapter in (
            ("raw", plan.base_model, raw_fn),
            ("stock", plan.base_model, stock_fn),
            ("lac", plan.lac_model, lac_fn),
        ):
            workspace = workspaces_root / arm
            arm_dir = arms_root / arm
            arm_dir.mkdir()
            try:
                seal = materialize_fixture(plan.fixture_manifest, plan.task.fixture_root, workspace)
                atomic_write_json(arm_dir / "fixture-manifest.before.json", plan.fixture_manifest.to_dict())
            except (FixtureSealError, OSError) as exc:
                seal = None
                fixture_controls.append(
                    EvidenceControlResult(
                        "sealed_fixture_materialization", EvidenceState.FAIL,
                        f"{arm} materialization failed: {type(exc).__name__}: {exc}",
                        {"arm": arm, "acl_hardened": False},
                    )
                )
            arm_task = replace(plan.task, fixture_root=workspace)
            try:
                if (
                    mode is EvidenceMode.VERIFIED
                    and containment_result.state is not EvidenceState.PASS
                ):
                    raise EvalPlanError(
                        "verified OS loopback-only egress containment "
                        "was not available"
                    )
                if seal is None or not seal.ok or not seal.acl_hardened:
                    raise EvalPlanError("sealed fixture materialization was not available")
                if arm == "raw":
                    candidate = adapter(arm_task, model, plan.ollama_host)
                else:
                    adapter_kwargs = {}
                    if adapter in (run_stock, run_lac):
                        if preflight is None or runtime_leases is None:
                            raise EvalPlanError(
                                "OpenCode executable identity lease was not acquired"
                            )
                        # Execute the exact version-probed and retained target.
                        adapter_kwargs["resolve_bin_fn"] = (
                            lambda: preflight.opencode.path
                        )
                        if os.name == "nt":
                            adapter_kwargs["launcher"] = (
                                containment_provider.launcher
                                if containment_provider is not None
                                and containment_provider.launcher is not None
                                else WindowsJobProcess.start
                            )
                    candidate = adapter(
                        arm_task,
                        model,
                        plan.ollama_host,
                        workspace,
                        **adapter_kwargs,
                    )
                result = _validated_result(arm, model, candidate)
                if (
                    os.name == "nt"
                    and adapter in (run_stock, run_lac)
                    and result.capture.get("windows_job_measured") is True
                ):
                    measured_windows_arms.add(arm)
            except Exception as exc:
                result = _adapter_failure(arm, model, exc)
            score = _score_result(result, plan.task.scorer.expected)
            results[arm] = result
            scores[arm] = score

            if arm == "raw":
                try:
                    effective_prompt = build_raw_prompt(arm_task)
                except Exception:
                    effective_prompt = arm_task.prompt
            else:
                effective_prompt = arm_task.prompt
            (arm_dir / "prompt.txt").write_text(
                effective_prompt, encoding="utf-8"
            )
            (arm_dir / "stdout.log").write_text(
                result.raw_stdout, encoding="utf-8"
            )
            (arm_dir / "stderr.log").write_text(
                result.raw_stderr, encoding="utf-8"
            )
            atomic_write_json(
                arm_dir / "result.json",
                {"result": asdict(result), "score": asdict(score)},
            )
            verification = verify_materialized_fixture(plan.fixture_manifest, workspace)
            atomic_write_json(
                arm_dir / "fixture-manifest.after.json",
                {
                    "expected": plan.fixture_manifest.to_dict(),
                    "observed": verification.observed_dict(),
                    "verification": {
                        "ok": verification.ok,
                        "reason": verification.reason,
                    },
                },
            )
            if (
                seal is not None
                and seal.ok
                and seal.acl_hardened
                and verification.ok
            ):
                fixture_controls.append(
                    EvidenceControlResult(
                        "sealed_fixture_materialization", EvidenceState.PASS,
                        f"{arm} fixture matched its sealed manifest",
                        {
                            "arm": arm,
                            "aggregate_sha256": verification.aggregate_sha256,
                            "acl_hardened": seal.acl_hardened,
                        },
                    )
                )
            elif seal is not None:
                fixture_controls.append(
                    EvidenceControlResult(
                        "sealed_fixture_materialization", EvidenceState.FAIL,
                        (
                            f"{arm} fixture seal or verification failed: "
                            f"{seal.reason or verification.reason}"
                        ),
                        {"arm": arm, "acl_hardened": seal.acl_hardened},
                    )
                )

        if preflight is not None and runtime_leases is not None:
            try:
                identity_results = identity_compare_fn(
                    preflight,
                    identity_capture_fn(plan),
                )
            except Exception as exc:
                reason = (
                    f"identity postflight failed: {type(exc).__name__}: {exc}"
                )
                identity_results = _identity_failures(reason)
    finally:
        if (
            containment_provider is not None
            and containment_result.state is EvidenceState.PASS
        ):
            try:
                containment_result = derive_containment_result(
                    mode,
                    containment_provider,
                    plan.ollama_host,
                    applications,
                )
            except Exception as exc:
                containment_result = EvidenceControlResult(
                    "os_loopback_only_egress",
                    EvidenceState.FAIL,
                    f"containment postflight verification failed: "
                    f"{type(exc).__name__}: {exc}",
                    {"active": False},
                )
        if containment_provider is not None:
            try:
                containment_provider.close()
            except Exception as exc:
                containment_result = EvidenceControlResult(
                    "os_loopback_only_egress",
                    EvidenceState.FAIL,
                    f"containment cleanup uncertain: "
                    f"{type(exc).__name__}: {exc}",
                    {
                        **containment_result.details,
                        "cleanup_certain": False,
                    },
                )
        if runtime_leases is not None:
            try:
                runtime_leases.close()
            except Exception as exc:
                identity_results = _fail_runtime_provenance(
                    identity_results,
                    f"runtime identity lease release failed: "
                    f"{type(exc).__name__}: {exc}",
                )

    config_evidence: dict[str, Any] = {}
    config_ok = True
    for arm in ("stock", "lac"):
        measured = results[arm].metrics.get("opencode_config_identity")
        config_evidence[arm] = measured
        if (
            not isinstance(measured, dict)
            or not _complete_config_identity(measured.get("before"))
            or not _complete_config_identity(measured.get("after"))
            or measured["before"] != measured["after"]
        ):
            config_ok = False
    identity_root = run_root / "identities"
    identity_root.mkdir(exist_ok=True)
    atomic_write_json(identity_root / "opencode-configs.json", config_evidence)
    if not config_ok:
        identity_results = tuple(
            EvidenceControlResult(item.name, EvidenceState.FAIL, "actual OpenCode config identity is missing or drifted", {"configs": config_evidence}) if item.name == "runtime_dependency_provenance" else item
            for item in identity_results
        )

    identity_valid = all(
        item.state is EvidenceState.PASS for item in identity_results
    )
    comparison = {
        "schema_version": 1,
        "run_id": run_id,
        "run_root": str(run_root),
        "artifact_written": True,
        "evidence_blockers": list(_EVIDENCE_BLOCKERS),
        "all_arms_executed": all(result.completed for result in results.values()),
        "all_arms_passed": all(score.passed for score in scores.values()),
        "scores": {arm: score.score for arm, score in scores.items()},
        "passes": {arm: score.passed for arm, score in scores.items()},
        "models": {arm: result.model for arm, result in results.items()},
        "fixture_sha256": plan.fixture_sha256,
        "identity_valid": identity_valid,
        "identity_controls": [
            {"name": item.name, "state": item.state.value, "reason": item.reason}
            for item in identity_results
        ],
        "opencode_config_identities": config_evidence,
    }
    atomic_write_json(run_root / "comparison.json", comparison)
    carried_results = [
        item
        for item in preliminary_results
        if item.name not in {
            "runtime_dependency_provenance",
            "immutable_ollama_model_lineage",
            "sealed_fixture_materialization",
            "bounded_process_and_http_capture",
            "windows_process_tree_containment",
            "os_loopback_only_egress",
        }
    ]
    fixture_ok = len(fixture_controls) == 3 and all(
        item.state is EvidenceState.PASS for item in fixture_controls
    )
    fixture_result = EvidenceControlResult(
        "sealed_fixture_materialization",
        EvidenceState.PASS if fixture_ok else EvidenceState.FAIL,
        "all arms received distinct post-verified sealed fixtures" if fixture_ok else "one or more sealed fixture checks failed",
        {"arms": [item.details for item in fixture_controls]},
    )
    capture_result = _bounded_capture_result(results)
    windows_containment_result = _windows_containment_result(
        results,
        measured_windows_arms,
    )
    evidence = seal_evidence(
        run_root,
        mode,
        [
            *carried_results,
            *identity_results,
            containment_result,
            fixture_result,
            windows_containment_result,
            capture_result,
        ],
    )
    return {**comparison, "evidence": evidence}
