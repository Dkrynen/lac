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


class ReadyContainment:
    def __init__(self, result: EvidenceControlResult):
        self.preflight_result = result

    def preflight(self, _plan, _mode, _identity_capture):
        return self.preflight_result, [r"C:\tools\opencode.exe"]


class ReadyDependencies:
    def __init__(self):
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
        self.containment = ReadyContainment(containment)
        self.runner_calls = 0

    def list_models(self):
        return [
            SimpleNamespace(name="gpt-oss:20b"),
            SimpleNamespace(name="gpt-oss:20b-agent"),
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
        "base_model": "gpt-oss:20b",
        "lac_model": "gpt-oss:20b-agent",
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


def test_verified_dry_run_returns_zero_only_when_all_controls_ready(tmp_path):
    request = EvalCommandRequest(
        task="python-empty-mean",
        base_model="gpt-oss:20b",
        lac_model="gpt-oss:20b-agent",
        output_dir=tmp_path,
        run_id="acceptance",
        mode="verified",
        dry_run=True,
    )
    result = execute_eval_command(request, dependencies=ready_dependencies())
    assert result.exit_code == 0
    assert result.report["evidence_ready"] is True
    assert result.report["artifact_valid"] is False


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


def test_completed_verified_command_maps_invalid_evidence_to_exit_one(tmp_path):
    deps = ready_dependencies()

    def invalid_run(_plan, **_kwargs):
        deps.runner_calls += 1
        return {
            "evidence": {"artifact_valid": False},
            "all_arms_executed": True,
            "all_arms_passed": False,
        }

    deps.run = invalid_run
    result = execute_eval_command(verified_request(tmp_path), dependencies=deps)
    assert result.exit_code == 1
    assert deps.runner_calls == 1


def test_diagnostic_command_runs_but_can_never_report_valid_evidence(tmp_path):
    deps = ready_dependencies()
    result = execute_eval_command(
        verified_request(tmp_path, mode="diagnostic"),
        dependencies=deps,
    )
    assert result.exit_code == 1
    assert result.report["mode"] == "diagnostic"
    assert result.report["evidence"]["artifact_valid"] is False
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
