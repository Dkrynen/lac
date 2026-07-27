"""Three-arm evaluation orchestration and durable evidence artifacts."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
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
from .http_observer import (
    HttpObservationError,
    LoopbackRecordingProxy,
    attach_observed_request,
)
from .ledger import atomic_write_json, seal_evidence
from .raw_ollama import _is_loopback_ollama_host, build_raw_prompt, run_raw
from .result import ArmResult
from .schedule import EvaluationSchedule, build_schedule
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


def _artifact_token(path: Path) -> tuple[int, int, int, int, int, str]:
    before = path.lstat()
    if (
        path.is_symlink()
        or getattr(before, "st_file_attributes", 0) & 0x0400
        or not stat.S_ISREG(before.st_mode)
    ):
        raise ValueError(f"artifact is linked or not regular: {path}")
    payload = path.read_bytes()
    after = path.stat()
    before_token = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_token = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if before_token != after_token or len(payload) != before.st_size:
        raise ValueError(f"artifact changed during measurement: {path}")
    return (*before_token, hashlib.sha256(payload).hexdigest())


def _sampling_metadata_matches(
    arm: str,
    metadata: object,
    *,
    model: str,
    trial_index: int,
    seed: int,
    temperature: float,
    max_output_tokens: int,
) -> bool:
    if not isinstance(metadata, dict):
        return False
    common_keys = {
        "source",
        "observed",
        "capture_token",
        "method",
        "path",
        "raw_body_sha256",
        "raw_body",
        "trial_index",
    }
    if (
        metadata.get("source") != "loopback_recording_proxy"
        or metadata.get("observed") is not True
        or metadata.get("capture_token")
        != f"{trial_index:03d}-{arm}"
        or metadata.get("method") != "POST"
        or type(metadata.get("trial_index")) is not int
        or metadata["trial_index"] != trial_index
        or not isinstance(metadata.get("raw_body"), str)
        or not isinstance(metadata.get("raw_body_sha256"), str)
        or hashlib.sha256(
            metadata["raw_body"].encode("utf-8")
        ).hexdigest()
        != metadata["raw_body_sha256"]
    ):
        return False
    try:
        body = json.loads(metadata["raw_body"])
    except json.JSONDecodeError:
        return False
    if not isinstance(body, dict) or body.get("model") != model:
        return False
    if arm == "raw":
        options = metadata.get("options")
        return (
            set(metadata) == common_keys | {"stream", "options"}
            and metadata.get("path") == "/api/chat"
            and metadata.get("stream") is False
            and body.get("stream") is False
            and isinstance(options, dict)
            and set(options) == {"temperature", "seed", "num_predict"}
            and options == body.get("options")
            and type(options.get("seed")) is int
            and options["seed"] == seed
            and not isinstance(options.get("temperature"), bool)
            and options["temperature"] == temperature
            and type(options.get("num_predict")) is int
            and options["num_predict"] == max_output_tokens
        )
    return (
        set(metadata)
        == common_keys
        | {"temperature", "seed", "max_output_tokens"}
        and metadata.get("path") == "/v1/chat/completions"
        and type(metadata.get("seed")) is int
        and metadata["seed"] == seed
        and metadata["seed"] == body.get("seed")
        and not isinstance(metadata.get("temperature"), bool)
        and metadata["temperature"] == temperature
        and metadata["temperature"] == body.get("temperature")
        and type(metadata.get("max_output_tokens")) is int
        and metadata["max_output_tokens"] == max_output_tokens
        and metadata["max_output_tokens"] == body.get("max_tokens")
    )


def _counterbalanced_sampling_result(
    run_root: Path,
    schedule_payload: dict[str, Any] | None,
    schedule_token: tuple[int, int, int, int, int, str] | None,
    result_tokens: dict[Path, tuple[int, int, int, int, int, str]],
) -> EvidenceControlResult:
    errors: list[str] = []
    if schedule_payload is None or schedule_token is None:
        return EvidenceControlResult(
            "counterbalanced_deterministic_sampling",
            EvidenceState.FAIL,
            "persisted deterministic schedule was not available",
            {"record_count": 0, "errors": ["schedule missing"]},
        )
    schedule_path = run_root / "schedule.json"
    try:
        if _artifact_token(schedule_path) != schedule_token:
            errors.append("schedule was replaced or changed after persistence")
        observed_schedule = json.loads(
            schedule_path.read_text(encoding="utf-8")
        )
        if _artifact_token(schedule_path) != schedule_token:
            errors.append("schedule changed while it was being validated")
        if observed_schedule != schedule_payload:
            errors.append("schedule payload does not match derived schedule")
    except Exception as exc:
        observed_schedule = None
        errors.append(f"schedule read failed: {type(exc).__name__}: {exc}")

    expected_paths: dict[Path, tuple[dict[str, Any], str, int]] = {}
    for trial in schedule_payload["trials"]:
        for position, arm in enumerate(trial["arm_order"]):
            expected_paths[
                (
                    run_root
                    / "trials"
                    / f"{trial['index']:03d}"
                    / arm
                    / "result.json"
                ).resolve()
            ] = (trial, arm, position)
    actual_paths = {
        path.resolve()
        for path in (run_root / "trials").glob("*/*/result.json")
    }
    if actual_paths != set(expected_paths):
        missing = sorted(str(path) for path in set(expected_paths) - actual_paths)
        extra = sorted(str(path) for path in actual_paths - set(expected_paths))
        errors.append(f"result path set mismatch; missing={missing}; extra={extra}")

    identities: set[tuple[int, str]] = set()
    valid_records = 0
    generation = schedule_payload["generation"]
    for path, (trial, arm, position) in expected_paths.items():
        try:
            token = result_tokens.get(path)
            if token is None or _artifact_token(path) != token:
                errors.append(f"{path}: result was missing, replaced, or changed")
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            if _artifact_token(path) != token:
                errors.append(f"{path}: result changed while being validated")
                continue
            if not isinstance(payload, dict):
                raise ValueError("record is not an object")
            observed_trial = payload.get("trial")
            expected_trial = {
                "index": trial["index"],
                "seed": trial["seed"],
                "order_position": position,
                "arm_order": trial["arm_order"],
            }
            if observed_trial != expected_trial:
                raise ValueError("trial identity/order/seed mismatch")
            if (
                type(observed_trial.get("index")) is not int
                or type(observed_trial.get("seed")) is not int
                or type(observed_trial.get("order_position")) is not int
            ):
                raise ValueError("trial integer field has invalid type")
            identity = (observed_trial["index"], arm)
            if identity in identities:
                raise ValueError("duplicate trial/arm identity")
            identities.add(identity)
            observed_generation = payload.get("generation")
            if (
                not isinstance(observed_generation, dict)
                or set(observed_generation)
                != {"temperature", "seed_base", "max_output_tokens"}
                or type(observed_generation.get("temperature")) is not float
                or type(observed_generation.get("seed_base")) is not int
                or type(observed_generation.get("max_output_tokens"))
                is not int
                or observed_generation != generation
            ):
                raise ValueError("generation settings mismatch")
            if payload.get("model_digest") != schedule_payload[
                "model_digests"
            ][arm]:
                raise ValueError("model digest mismatch")
            result = payload.get("result")
            if (
                not isinstance(result, dict)
                or result.get("arm") != arm
                or not _sampling_metadata_matches(
                    arm,
                    result.get("request_metadata"),
                    model=result.get("model"),
                    trial_index=trial["index"],
                    seed=trial["seed"],
                    temperature=generation["temperature"],
                    max_output_tokens=generation["max_output_tokens"],
                )
            ):
                raise ValueError("captured request metadata mismatch")
            valid_records += 1
        except Exception as exc:
            errors.append(f"{path}: {type(exc).__name__}: {exc}")

    return EvidenceControlResult(
        "counterbalanced_deterministic_sampling",
        EvidenceState.FAIL if errors else EvidenceState.PASS,
        (
            "; ".join(errors)
            if errors
            else (
                "three canonical cyclic trials and nine observed requests "
                "matched the immutable deterministic schedule"
            )
        ),
        {
            "record_count": valid_records,
            "expected_record_count": 9,
            "errors": errors,
        },
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
    for record_key, result in results.items():
        arm = result.arm
        capture = result.capture
        details[record_key] = capture
        if not isinstance(capture, dict):
            invalid.append(
                f"{record_key}: capture evidence is not an object"
            )
            continue
        if arm == "raw":
            if not _valid_capture_record(capture.get("response")):
                invalid.append(
                    f"{record_key}: response capture is missing or invalid"
                )
            continue
        if capture.get("cleanup_complete") is not True:
            invalid.append(f"{record_key}: capture cleanup was not complete")
        for stream in ("stdout", "stderr"):
            if not _valid_capture_record(capture.get(stream)):
                invalid.append(
                    f"{record_key}: {stream} capture is missing or invalid"
                )
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
    for record_key, result in results.items():
        arm = result.arm
        if arm == "raw":
            continue
        capture = result.capture
        measured = (
            capture.get("windows_job")
            if isinstance(capture, dict)
            else None
        )
        details[record_key] = measured
        if record_key not in measured_arms:
            invalid.append(
                f"{record_key}: containment was not produced by the measured "
                "default adapter"
            )
            continue
        if not isinstance(measured, dict):
            invalid.append(f"{record_key}: Windows Job evidence is missing")
            continue
        if capture.get("windows_job_measured") is not True:
            invalid.append(
                f"{record_key}: typed Job measurement marker is missing"
            )
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
                invalid.append(
                    f"{record_key}: {name} is not an exact boolean"
                )
            elif name in {
                "active_process_limit",
                "final_active_processes",
            } and type(observed) is not int:
                invalid.append(
                    f"{record_key}: {name} is not an exact integer"
                )
            elif observed != value:
                invalid.append(
                    f"{record_key}: {name} was not proven as {value!r}"
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

    v2_sampling = (
        plan.task.schema_version == 2
        and plan.task.trials == 3
        and plan.task.generation is not None
    )
    workspaces_root = run_root / "workspaces"
    arms_root = run_root / "arms"
    trials_root = run_root / "trials"
    if v2_sampling:
        trials_root.mkdir(parents=True)
    else:
        workspaces_root.mkdir(parents=True)
        arms_root.mkdir()
    manifest = _manifest(plan, environment or {}, mode)
    atomic_write_json(run_root / "manifest.json", manifest)

    identity_results: tuple[EvidenceControlResult, ...]
    preflight: EvaluationIdentitySnapshot | None = None
    runtime_leases: Any | None = None
    schedule: EvaluationSchedule | None = None
    schedule_payload: dict[str, Any] | None = None
    schedule_token: tuple[int, int, int, int, int, str] | None = None
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
        if v2_sampling:
            model_digests = {
                "raw": preflight.models.base.digest,
                "stock": preflight.models.base.digest,
                "lac": preflight.models.lac.digest,
            }
            schedule = build_schedule(
                task_contract_sha256(plan.task),
                model_digests,
                plan.task.generation,
                plan.task.trials,
            )
            schedule_payload = {
                "schema_version": 1,
                "task_contract_sha256": task_contract_sha256(plan.task),
                "model_digests": dict(sorted(model_digests.items())),
                "generation": plan.task.generation.to_dict(),
                **schedule.to_dict(),
            }
            schedule_path = run_root / "schedule.json"
            atomic_write_json(schedule_path, schedule_payload)
            schedule_token = _artifact_token(schedule_path)
    except Exception as exc:
        reason = f"identity preflight failed: {type(exc).__name__}: {exc}"
        identity_results = _identity_failures(reason)

    http_observer: LoopbackRecordingProxy | None = None
    observer_start_error: str | None = None
    observer_cleanup_complete = not v2_sampling
    containment_endpoint = plan.ollama_host
    if v2_sampling:
        try:
            http_observer = LoopbackRecordingProxy.open(plan.ollama_host)
            containment_endpoint = http_observer.endpoint
            atomic_write_json(
                run_root / "http-observer.before.json",
                {
                    "schema_version": 1,
                    "upstream": plan.ollama_host,
                    "endpoint": containment_endpoint,
                    "active": True,
                },
            )
        except Exception as exc:
            observer_start_error = (
                f"{type(exc).__name__}: {exc}"
            )

    containment_provider: Any | None = None
    containment_result = EvidenceControlResult(
        "os_loopback_only_egress",
        EvidenceState.FAIL,
        "containment provider was not initialized",
        {},
    )
    applications = [preflight.opencode] if preflight is not None else []
    try:
        if v2_sampling and http_observer is None:
            raise EvalPlanError(
                "loopback HTTP observer was not initialized: "
                f"{observer_start_error}"
            )
        containment_provider = select_containment_provider(
            mode,
            os.name,
            containment_endpoint,
            applications,
            wfp_api=containment_wfp_api,
        )
        containment_result = derive_containment_result(
            mode,
            containment_provider,
            containment_endpoint,
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
    all_results: dict[str, ArmResult] = {}
    measured_windows_arms: set[str] = set()
    scores: dict[str, ScoreResult] = {}
    all_scores: dict[str, ScoreResult] = {}
    result_tokens: dict[
        Path, tuple[int, int, int, int, int, str]
    ] = {}
    fixture_controls: list[EvidenceControlResult] = []
    try:
        adapters = {
            "raw": (plan.base_model, raw_fn),
            "stock": (plan.base_model, stock_fn),
            "lac": (plan.lac_model, lac_fn),
        }
        if v2_sampling and schedule is not None:
            execution_specs = [
                (trial, arm, *adapters[arm])
                for trial in schedule.trials
                for arm in trial.arm_order
            ]
        elif v2_sampling:
            execution_specs = []
        else:
            execution_specs = [
                (None, arm, model, adapter)
                for arm, (model, adapter) in adapters.items()
            ]
        for trial, arm, model, adapter in execution_specs:
            if trial is None:
                workspace = workspaces_root / arm
                arm_dir = arms_root / arm
                result_key = arm
            else:
                arm_dir = trials_root / f"{trial.index:03d}" / arm
                workspace = arm_dir / "workspace"
                result_key = f"{trial.index:03d}/{arm}"
            arm_dir.mkdir(parents=True)
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
            capture_started = False
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
                if trial is not None:
                    if http_observer is None:
                        raise EvalPlanError(
                            "loopback HTTP observer was not available"
                        )
                    http_observer.begin_capture(
                        f"{trial.index:03d}-{arm}",
                        (
                            "/api/chat"
                            if arm == "raw"
                            else "/v1/chat/completions"
                        ),
                    )
                    capture_started = True
                sampling_kwargs = (
                    {}
                    if trial is None
                    else {
                        "generation": plan.task.generation,
                        "trial": trial,
                    }
                )
                if arm == "raw":
                    candidate = adapter(
                        arm_task,
                        model,
                        containment_endpoint,
                        **sampling_kwargs,
                    )
                else:
                    adapter_kwargs = dict(sampling_kwargs)
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
                        containment_endpoint,
                        workspace,
                        **adapter_kwargs,
                    )
                result = _validated_result(arm, model, candidate)
                if (
                    os.name == "nt"
                    and adapter in (run_stock, run_lac)
                    and result.capture.get("windows_job_measured") is True
                ):
                    measured_windows_arms.add(result_key)
            except Exception as exc:
                result = _adapter_failure(arm, model, exc)
            if trial is not None:
                observation_error: str | None = None
                observation = None
                if capture_started and http_observer is not None:
                    try:
                        observation = http_observer.finish_capture(
                            f"{trial.index:03d}-{arm}"
                        )
                    except Exception as exc:
                        observation_error = (
                            f"{type(exc).__name__}: {exc}"
                        )
                else:
                    observation_error = (
                        "capture window did not start"
                    )
                if observation is not None:
                    try:
                        result = attach_observed_request(
                            result,
                            observation,
                            trial,
                        )
                    except Exception as exc:
                        observation_error = (
                            f"{type(exc).__name__}: {exc}"
                        )
                if observation_error is not None:
                    result = replace(
                        result,
                        request_metadata={},
                        errors=(
                            *result.errors,
                            "http_observation_failed:"
                            f"{observation_error}",
                        ),
                    )
            score = _score_result(result, plan.task.scorer.expected)
            results[arm] = result
            scores[arm] = score
            all_results[result_key] = result
            all_scores[result_key] = score

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
            result_payload: dict[str, Any] = {
                "result": asdict(result),
                "score": asdict(score),
            }
            if trial is not None and schedule_payload is not None:
                result_payload.update(
                    {
                        "trial": {
                            "index": trial.index,
                            "seed": trial.seed,
                            "order_position": trial.arm_order.index(arm),
                            "arm_order": list(trial.arm_order),
                        },
                        "generation": plan.task.generation.to_dict(),
                        "model_digest": schedule_payload[
                            "model_digests"
                        ][arm],
                    }
                )
            result_path = arm_dir / "result.json"
            atomic_write_json(result_path, result_payload)
            result_tokens[result_path.resolve()] = _artifact_token(result_path)
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

        for arm, (model, _adapter) in adapters.items():
            if arm not in results:
                result = _adapter_failure(
                    arm,
                    model,
                    EvalPlanError(
                        "deterministic schedule was not available"
                    ),
                )
                results[arm] = result
                scores[arm] = _score_result(
                    result,
                    plan.task.scorer.expected,
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
                    containment_endpoint,
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
        if http_observer is not None:
            try:
                http_observer.close()
                observer_cleanup_complete = True
                atomic_write_json(
                    run_root / "http-observer.after.json",
                    {
                        "schema_version": 1,
                        "upstream": plan.ollama_host,
                        "endpoint": containment_endpoint,
                        "active": False,
                        "cleanup_complete": True,
                    },
                )
            except Exception as exc:
                observer_cleanup_complete = False
                observer_start_error = (
                    "observer cleanup failed: "
                    f"{type(exc).__name__}: {exc}"
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
    config_candidates = all_results if v2_sampling else results
    for record_key, result in config_candidates.items():
        if result.arm == "raw":
            continue
        measured = result.metrics.get("opencode_config_identity")
        config_evidence[record_key] = measured
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
        "all_arms_executed": (
            len(all_results) == 9
            if v2_sampling
            else len(results) == 3
        ) and all(
            result.completed
            for result in (
                all_results.values() if v2_sampling else results.values()
            )
        ),
        "all_arms_passed": (
            len(all_scores) == 9
            if v2_sampling
            else len(scores) == 3
        ) and all(
            score.passed
            for score in (
                all_scores.values() if v2_sampling else scores.values()
            )
        ),
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
    if v2_sampling and schedule is not None:
        per_trial = []
        pass_counts = {"raw": 0, "stock": 0, "lac": 0}
        for trial in schedule.trials:
            trial_scores = {
                arm: all_scores[f"{trial.index:03d}/{arm}"].score
                for arm in trial.arm_order
            }
            trial_passes = {
                arm: all_scores[f"{trial.index:03d}/{arm}"].passed
                for arm in trial.arm_order
            }
            for arm, passed in trial_passes.items():
                pass_counts[arm] += int(passed)
            per_trial.append(
                {
                    "index": trial.index,
                    "seed": trial.seed,
                    "arm_order": list(trial.arm_order),
                    "scores": trial_scores,
                    "passes": trial_passes,
                }
            )
        comparison["trials"] = per_trial
        comparison["aggregate"] = {
            "trial_count": 3,
            "record_count": len(all_results),
            "pass_counts": pass_counts,
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
            "counterbalanced_deterministic_sampling",
        }
    ]
    expected_fixture_controls = 9 if v2_sampling else 3
    fixture_ok = len(fixture_controls) == expected_fixture_controls and all(
        item.state is EvidenceState.PASS for item in fixture_controls
    )
    fixture_result = EvidenceControlResult(
        "sealed_fixture_materialization",
        EvidenceState.PASS if fixture_ok else EvidenceState.FAIL,
        (
            "all scheduled arms received distinct post-verified sealed fixtures"
            if fixture_ok
            else "one or more sealed fixture checks failed"
        ),
        {"arms": [item.details for item in fixture_controls]},
    )
    control_results = all_results if v2_sampling else results
    capture_result = _bounded_capture_result(control_results)
    windows_containment_result = _windows_containment_result(
        control_results,
        measured_windows_arms,
    )
    sampling_result = _counterbalanced_sampling_result(
        run_root,
        schedule_payload,
        schedule_token,
        result_tokens,
    )
    if v2_sampling and not observer_cleanup_complete:
        sampling_result = EvidenceControlResult(
            "counterbalanced_deterministic_sampling",
            EvidenceState.FAIL,
            observer_start_error
            or "loopback HTTP observer cleanup was incomplete",
            {
                **sampling_result.details,
                "observer_cleanup_complete": False,
            },
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
            sampling_result,
        ],
    )
    return {**comparison, "evidence": evidence}
