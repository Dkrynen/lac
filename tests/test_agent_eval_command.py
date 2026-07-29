from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import backend.agent_eval.command as command
from backend.agent_eval.command import EvalCommandRequest, execute_eval_command
from backend.agent_eval.evidence import (
    REQUIRED_CONTROLS,
    EvidenceControlResult,
    EvidenceState,
)

BASE_MODEL = "gpt-oss:20b"
LAC_MODEL = "gpt-oss:20b-agent"
BASE_DIGEST = "a" * 64
LAC_DIGEST = "b" * 64
FROM_BLOB_SHA256 = "c" * 64
OPENCODE_SHA256 = (
    "b7b469b83cc3561e5129a1803b746f7e2c1974297909f5b346398dc9c56a477e"
)


def realistic_identity_snapshot(**changes):
    values = {
        "base_name": BASE_MODEL,
        "lac_name": LAC_MODEL,
        "base_digest": BASE_DIGEST,
        "lac_digest": LAC_DIGEST,
        "base_from": FROM_BLOB_SHA256,
        "lac_from": FROM_BLOB_SHA256,
        "lac_parent": BASE_MODEL,
    }
    values.update(changes)
    return SimpleNamespace(
        opencode=SimpleNamespace(
            path=Path(r"C:\tools\opencode.exe"),
            version="1.18.9",
            sha256=OPENCODE_SHA256,
        ),
        models=SimpleNamespace(
            base=SimpleNamespace(
                name=values["base_name"],
                digest=values["base_digest"],
                from_blob_sha256=values["base_from"],
            ),
            lac=SimpleNamespace(
                name=values["lac_name"],
                digest=values["lac_digest"],
                parent_model=values["lac_parent"],
                from_blob_sha256=values["lac_from"],
            ),
        )
    )


class ReadyContainment:
    def __init__(self, result: EvidenceControlResult, identity_snapshot):
        self.preflight_result = result
        self.identity_snapshot = identity_snapshot

    def preflight(self, _plan, _mode, _identity_capture):
        return (
            self.preflight_result,
            [r"C:\tools\opencode.exe"],
            self.identity_snapshot,
        )


class ReadyDependencies:
    def __init__(self):
        self.identity_snapshot = realistic_identity_snapshot()
        self.controls = tuple(
            EvidenceControlResult(
                name,
                EvidenceState.PASS,
                f"{name} ready",
                {},
            )
            for name in REQUIRED_CONTROLS
        )
        containment = next(
            item
            for item in self.controls
            if item.name == "os_loopback_only_egress"
        )
        self.containment = ReadyContainment(
            containment,
            self.identity_snapshot,
        )
        self.runner_calls = 0

    def list_models(self):
        return [
            SimpleNamespace(name=BASE_MODEL),
            SimpleNamespace(name=LAC_MODEL),
        ]

    def resolve_binary(self):
        return Path(r"C:\tools\opencode.exe")

    def capture_identity(self, _plan):
        return SimpleNamespace(opencode=SimpleNamespace(path=self.resolve_binary()))

    def readiness_results(self, _plan, _containment):
        return self.controls

    def capture_environment(self, _ollama_host):
        return {"test": "bounded"}

    def run(self, _plan, **_kwargs):
        self.runner_calls += 1
        return {
            "evidence": {"artifact_valid": True},
            "all_arms_executed": True,
            "all_arms_passed": True,
        }


def ready_dependencies() -> ReadyDependencies:
    return ReadyDependencies()


def verified_request(tmp_path, **changes) -> EvalCommandRequest:
    values = {
        "task": "python-empty-mean",
        "base_model": BASE_MODEL,
        "lac_model": LAC_MODEL,
        "output_dir": tmp_path,
        "run_id": "acceptance",
        "mode": "verified",
        "dry_run": False,
    }
    values.update(changes)
    return EvalCommandRequest(**values)


def failed_result(name: str) -> EvidenceControlResult:
    return EvidenceControlResult(
        name,
        EvidenceState.FAIL,
        f"{name} unavailable",
        {},
    )


def test_verified_dry_run_exposes_digests_and_truthful_runtime_bounds(
    tmp_path,
):
    request = EvalCommandRequest(
        task="python-empty-mean",
        base_model=BASE_MODEL,
        lac_model=LAC_MODEL,
        output_dir=tmp_path,
        run_id="acceptance",
        mode="verified",
        dry_run=True,
    )
    result = execute_eval_command(request, dependencies=ready_dependencies())
    assert result.exit_code == 0
    assert result.report["evidence_ready"] is True
    assert result.report["artifact_valid"] is False
    assert result.report["model_identities"] == {
        "base": {
            "name": BASE_MODEL,
            "digest": BASE_DIGEST,
            "from_blob_sha256": FROM_BLOB_SHA256,
        },
        "lac": {
            "name": LAC_MODEL,
            "digest": LAC_DIGEST,
            "parent_model": BASE_MODEL,
            "from_blob_sha256": FROM_BLOB_SHA256,
        },
        "arms": {
            "raw": {"name": BASE_MODEL, "digest": BASE_DIGEST},
            "stock": {"name": BASE_MODEL, "digest": BASE_DIGEST},
            "lac": {"name": LAC_MODEL, "digest": LAC_DIGEST},
        },
    }
    runtime = result.report["runtime_bounds"]
    assert runtime["planned_arm_runs"] == 9
    assert runtime["task_timeout_budget"] == {
        "value": 1620,
        "unit": "seconds",
        "derivation": "planned_arm_runs * per_arm_timeout_seconds",
        "per_arm_timeout_seconds": 180,
        "scope": "model_and_process_task_deadlines_only",
    }
    assert runtime["bounded_arm_path_subtotal"]["value"] == 1938
    assert runtime["bounded_arm_path_subtotal"]["unit"] == "seconds"
    assert runtime["bounded_arm_path_subtotal"]["scope"] == (
        "bounded_per_arm_stages_only_not_whole_run_wall_clock"
    )
    assert runtime["whole_run_maximum"]["value"] is None
    assert runtime["whole_run_maximum"]["unit"] == "seconds"
    assert runtime["whole_run_maximum"]["status"] == (
        "unavailable_no_enforced_global_deadline"
    )
    assert "whole_run_maximum_runtime" not in result.report["evidence_blockers"]
    assert "runtime_dependency_bootstrap_unbounded" not in result.report[
        "evidence_blockers"
    ]
    assert result.report["runtime_bootstrap_attestation"]["ok"] is True
    assert len(
        result.report["runtime_bootstrap_attestation"]["config_manifest"]
    ) == 6


def test_verified_dry_run_fails_closed_without_exact_model_identity(tmp_path):
    for snapshot in (
        None,
        realistic_identity_snapshot(base_digest="short"),
        realistic_identity_snapshot(base_from="short"),
        realistic_identity_snapshot(lac_from="C" * 64),
    ):
        deps = ready_dependencies()
        deps.containment.identity_snapshot = snapshot

        result = execute_eval_command(
            verified_request(tmp_path, dry_run=True),
            dependencies=deps,
        )

        assert result.exit_code == 2
        assert result.report["evidence_ready"] is False
        assert "exact_model_identity" in result.report["evidence_blockers"]


def test_verified_dry_run_rejects_plan_identity_binding_mismatches(tmp_path):
    mismatches = (
        {"base_name": "other:20b"},
        {"lac_name": "other:20b-agent"},
        {"lac_parent": "other:20b"},
        {"lac_from": "d" * 64},
    )
    for changes in mismatches:
        deps = ready_dependencies()
        deps.containment.identity_snapshot = realistic_identity_snapshot(
            **changes
        )

        result = execute_eval_command(
            verified_request(tmp_path, dry_run=True),
            dependencies=deps,
        )

        assert result.exit_code == 2
        assert result.report["evidence_ready"] is False
        assert result.report["model_identities"] is None
        assert "exact_model_identity" in result.report["evidence_blockers"]


def test_dry_report_reuses_containment_preflight_identity_snapshot(tmp_path):
    deps = ready_dependencies()
    deps.capture_identity = lambda _plan: (_ for _ in ()).throw(
        AssertionError("identity must not be recaptured after preflight")
    )

    result = execute_eval_command(
        verified_request(tmp_path, dry_run=True),
        dependencies=deps,
    )

    assert result.report["model_identities"]["base"]["digest"] == BASE_DIGEST
    assert result.report["model_identities"]["lac"]["digest"] == LAC_DIGEST


def test_runtime_bounds_preserve_fractional_authoritative_values(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(command, "_ACL_TIMEOUT_SECONDS", 10.25)
    monkeypatch.setattr(command, "DEFAULT_FINISH_TIMEOUT_SECONDS", 2.5)
    monkeypatch.setattr(
        command,
        "CaptureLimits",
        lambda: SimpleNamespace(cleanup_grace_seconds=5.75),
    )

    result = execute_eval_command(
        verified_request(tmp_path, dry_run=True),
        dependencies=ready_dependencies(),
    )

    bounded = result.report["runtime_bounds"]["bounded_arm_path_subtotal"]
    assert bounded["fixture_acl_timeout_seconds"] == 10.25
    assert bounded["observer_finish_timeout_seconds"] == 2.5
    assert bounded["process_cleanup_grace_seconds"] == 5.75
    assert bounded["value"] == 1953.75


def test_runtime_bounds_reject_invalid_numeric_sources(tmp_path, monkeypatch):
    for invalid in (True, 0, float("inf")):
        monkeypatch.setattr(
            command,
            "DEFAULT_FINISH_TIMEOUT_SECONDS",
            invalid,
        )
        result = execute_eval_command(
            verified_request(tmp_path, dry_run=True),
            dependencies=ready_dependencies(),
        )
        assert result.report["runtime_bounds"] is None
        assert "runtime_bounds_unavailable" in result.report[
            "evidence_blockers"
        ]


def test_verified_dry_run_fails_closed_without_runtime_bounds(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        command,
        "_runtime_bounds_disclosure",
        lambda _plan: None,
    )

    result = execute_eval_command(
        verified_request(tmp_path, dry_run=True),
        dependencies=ready_dependencies(),
    )

    assert result.exit_code == 2
    assert result.report["evidence_ready"] is False
    assert "runtime_bounds_unavailable" in result.report["evidence_blockers"]


def test_verified_command_stops_before_generation_when_control_missing(tmp_path):
    deps = ready_dependencies()
    deps.containment.preflight_result = failed_result("os_loopback_only_egress")
    result = execute_eval_command(verified_request(tmp_path), dependencies=deps)
    assert result.exit_code == 2
    assert deps.runner_calls == 0
    assert result.report["evidence_ready"] is False
    assert result.report["missing_controls"] == ["os_loopback_only_egress"]


def test_non_elevated_verified_request_stops_before_model_or_network_checks(
    tmp_path,
):
    deps = ready_dependencies()
    deps.preflight_elevation = lambda: False
    deps.list_models = lambda: (_ for _ in ()).throw(
        AssertionError("non-elevated verified preflight must stop first")
    )
    exact_command = (
        "lac eval --task python-empty-mean --dry-run --json"
    )

    result = execute_eval_command(
        verified_request(
            tmp_path,
            dry_run=True,
            original_command=exact_command,
        ),
        dependencies=deps,
    )

    assert result.exit_code == 2
    assert result.report["evidence_ready"] is False
    assert result.report["artifact_valid"] is False
    assert result.report["rerun_command"] == exact_command
    assert result.report["error"] == (
        "Verified Windows network containment requires an elevated terminal.\n"
        "Reopen PowerShell as Administrator and rerun:\n"
        + exact_command
    )


def test_verified_command_runs_after_runtime_bootstrap_attestation(tmp_path):
    deps = ready_dependencies()
    result = execute_eval_command(verified_request(tmp_path), dependencies=deps)
    assert result.exit_code == 0
    assert deps.runner_calls == 1


def test_verified_command_stops_before_runner_on_bootstrap_hash_mismatch(
    tmp_path,
):
    deps = ready_dependencies()
    deps.containment.identity_snapshot.opencode.sha256 = "0" * 64

    result = execute_eval_command(
        verified_request(tmp_path),
        dependencies=deps,
    )

    assert result.exit_code == 2
    assert result.report["evidence_ready"] is False
    assert result.report["evidence_blockers"] == [
        "runtime_dependency_bootstrap"
    ]
    assert deps.runner_calls == 0


def test_diagnostic_command_runs_but_can_never_report_valid_evidence(tmp_path):
    deps = ready_dependencies()
    result = execute_eval_command(
        verified_request(tmp_path, mode="diagnostic"),
        dependencies=deps,
    )
    assert result.exit_code == 1
    assert result.report["mode"] == "diagnostic"
    assert result.report["evidence"]["artifact_valid"] is False
    assert result.report["runtime_bootstrap_attestation"]["state"] == (
        "not_required"
    )
    assert result.report["runtime_bootstrap_attestation"]["ok"] is not True
    assert deps.runner_calls == 1


def test_output_directory_must_be_external_to_packaged_installation_tree(
    tmp_path,
    monkeypatch,
):
    install_root = tmp_path / "lac-install"
    monkeypatch.setattr(command, "OUTPUT_PROTECTED_ROOT", install_root)
    deps = ready_dependencies()

    result = execute_eval_command(
        verified_request(
            install_root / "evidence",
            dry_run=True,
        ),
        dependencies=deps,
    )

    assert result.exit_code == 2
    assert "outside" in result.report["error"]
    assert deps.runner_calls == 0
