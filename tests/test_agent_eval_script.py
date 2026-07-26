from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from backend.agent_eval.containment import ContainmentError
from backend.agent_eval.evidence import (
    EvidenceControlResult,
    EvidenceState,
)
from backend.agent_eval.identity import EvaluationIdentitySnapshot


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "agent_eval.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("agent_eval_script", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _argv(tmp_path, *extra):
    return [
        "--task",
        "python-empty-mean",
        "--base-model",
        "gpt-oss:20b",
        "--lac-model",
        "gpt-oss:20b-agent",
        "--output-dir",
        str(tmp_path / "evidence"),
        *extra,
    ]


def _models():
    return [
        SimpleNamespace(name="gpt-oss:20b"),
        SimpleNamespace(name="gpt-oss:20b-agent"),
    ]


def test_dry_run_validates_real_boundaries_without_creating_output(tmp_path):
    script = _load_script()
    lines = []
    runner_called = False

    def runner(*_args, **_kwargs):
        nonlocal runner_called
        runner_called = True
        raise AssertionError("dry-run must not execute arms")

    rc = script.main(
        _argv(tmp_path, "--dry-run", "--mode", "diagnostic"),
        list_models_fn=_models,
        resolve_bin_fn=lambda: Path(r"C:\tools\opencode.cmd"),
        run_fn=runner,
        out=lines.append,
    )

    report = json.loads("\n".join(lines))
    assert rc == 0
    assert runner_called is False
    assert not (tmp_path / "evidence").exists()
    assert report["ok"] is True
    assert report["dry_run"] is True
    assert report["task"] == "python-empty-mean"
    assert report["models"] == {
        "raw": "gpt-oss:20b",
        "stock": "gpt-oss:20b",
        "lac": "gpt-oss:20b-agent",
    }
    assert report["opencode"]["version"] == "1.18.4"
    assert report["opencode"]["binary"] == r"C:\tools\opencode.cmd"
    assert report["auto_approval"]["scope"] == "disposable_workspace_only"
    assert report["network"] == "loopback_ollama_only"
    assert report["model_downloads"] == "forbidden"
    assert "possible" in report["runtime_dependency_bootstrap"]
    assert report["os_egress_enforced"] is False
    assert report["evidence_ready"] is False
    assert report["mode"] == "diagnostic"
    assert report["artifact_valid"] is False
    assert report["controls"]["results"][0]["state"] == "unsupported"


class _DryRunContainmentProvider:
    launcher = None

    def __init__(self, *, close_error=None):
        self.verify_calls = 0
        self.close_calls = 0
        self.close_error = close_error

    def verify_active(self):
        self.verify_calls += 1
        return EvidenceControlResult(
            "os_loopback_only_egress",
            EvidenceState.PASS,
            "verified fake dynamic WFP policy",
            {"provider": "fake_wfp", "active": True},
        )

    def close(self):
        self.close_calls += 1
        if self.close_error:
            raise self.close_error


def test_verified_dry_run_opens_verifies_and_closes_containment_without_arms(
    tmp_path,
):
    script = _load_script()
    provider = _DryRunContainmentProvider()
    snapshot = EvaluationIdentitySnapshot.for_test()
    received = {}
    lines = []

    def select(mode, platform, endpoint, applications):
        received.update(
            mode=mode,
            platform=platform,
            endpoint=endpoint,
            applications=applications,
        )
        return provider

    rc = script.main(
        _argv(tmp_path, "--dry-run"),
        list_models_fn=_models,
        resolve_bin_fn=lambda: snapshot.opencode.path,
        run_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("dry-run must not execute arms")
        ),
        identity_capture_fn=lambda _plan: snapshot,
        containment_provider_fn=select,
        out=lines.append,
    )

    report = json.loads("\n".join(lines))
    assert rc == 0
    assert report["os_egress_enforced"] is True
    assert report["containment"]["state"] == "pass"
    assert received["applications"] == [snapshot.opencode]
    assert received["endpoint"] == "http://localhost:11434"
    assert provider.verify_calls == 1
    assert provider.close_calls == 1
    assert not (tmp_path / "evidence").exists()


def test_verified_dry_run_fails_when_containment_cleanup_is_uncertain(tmp_path):
    script = _load_script()
    provider = _DryRunContainmentProvider(
        close_error=RuntimeError("filter cleanup uncertain")
    )
    snapshot = EvaluationIdentitySnapshot.for_test()
    lines = []

    rc = script.main(
        _argv(tmp_path, "--dry-run"),
        list_models_fn=_models,
        resolve_bin_fn=lambda: snapshot.opencode.path,
        identity_capture_fn=lambda _plan: snapshot,
        containment_provider_fn=lambda *_args: provider,
        out=lines.append,
    )

    report = json.loads("\n".join(lines))
    assert rc == 2
    assert report["ok"] is False
    assert report["os_egress_enforced"] is False
    assert report["containment"]["state"] == "fail"
    assert "cleanup uncertain" in report["containment"]["reason"]


def test_verified_dry_run_reports_elevation_and_exact_rerun_command(tmp_path):
    script = _load_script()
    snapshot = EvaluationIdentitySnapshot.for_test()
    lines = []
    argv = _argv(tmp_path, "--dry-run", "--run-id", "quoted run")

    def deny(*_args):
        raise ContainmentError(
            "engine_open denied: run from an Administrator elevated PowerShell"
        )

    rc = script.main(
        argv,
        list_models_fn=_models,
        resolve_bin_fn=lambda: snapshot.opencode.path,
        identity_capture_fn=lambda _plan: snapshot,
        containment_provider_fn=deny,
        out=lines.append,
    )

    report = json.loads("\n".join(lines))
    exact_command = subprocess.list2cmdline([str(SCRIPT), *argv])
    assert rc == 2
    assert report["containment"]["elevation_present"] is False
    assert report["containment"]["application_paths"] == [
        str(snapshot.opencode.path)
    ]
    assert report["rerun_command"] == exact_command
    assert report["error"] == (
        "Verified Windows network containment requires an elevated terminal.\n"
        "Reopen PowerShell as Administrator and rerun:\n"
        + exact_command
    )


def test_script_refuses_missing_models_before_runner(tmp_path):
    script = _load_script()
    lines = []

    rc = script.main(
        _argv(tmp_path, "--dry-run"),
        list_models_fn=lambda: [SimpleNamespace(name="gpt-oss:20b")],
        resolve_bin_fn=lambda: Path("opencode"),
        run_fn=lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("must not run")
        ),
        out=lines.append,
    )

    report = json.loads("\n".join(lines))
    assert rc == 2
    assert report["ok"] is False
    assert "LAC model is not installed" in report["error"]


def test_verified_script_stops_before_generation_when_controls_are_missing(tmp_path):
    script = _load_script()
    lines = []

    rc = script.main(
        _argv(tmp_path, "--run-id", "live-001"),
        list_models_fn=_models,
        resolve_bin_fn=lambda: Path("opencode"),
        run_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("verified mode must not execute arms")
        ),
        out=lines.append,
    )

    report = json.loads("\n".join(lines))
    assert rc == 2
    assert report["mode"] == "verified"
    assert "runtime_dependency_provenance" in report["missing_controls"]


def test_diagnostic_script_runs_and_returns_invalid_artifact_status(tmp_path):
    script = _load_script()
    lines = []

    rc = script.main(
        _argv(tmp_path, "--mode", "diagnostic"),
        list_models_fn=_models,
        resolve_bin_fn=lambda: Path("opencode"),
        run_fn=lambda *_a, **_kw: {
            "evidence": {"artifact_valid": False},
            "all_arms_executed": False,
            "all_arms_passed": False,
        },
        environment_fn=dict,
        out=lines.append,
    )

    assert rc == 1
