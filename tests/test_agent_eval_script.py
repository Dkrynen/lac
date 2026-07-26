from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from backend.agent_eval.identity import (
    EvaluationIdentitySnapshot,
    file_identity,
)


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


class _ScriptWfpApi:
    def __init__(self):
        self.calls = []
        self.filters = {}
        self.next_filter_id = 100
        self.fail = {}

    def _raise(self, name):
        error = self.fail.get(name)
        if error is not None:
            raise error

    def engine_open_dynamic(self, session_key):
        self.calls.append(("engine_open", session_key))
        self._raise("engine_open")
        return 10

    def engine_close(self, engine):
        self.calls.append(("engine_close", engine))
        self._raise("engine_close")

    def sublayer_add(self, engine, key):
        self.calls.append(("sublayer_add", key))
        self._raise("sublayer_add")

    def sublayer_delete(self, engine, key):
        self.calls.append(("sublayer_delete", key))
        self._raise("sublayer_delete")

    def get_app_id(self, path):
        self.calls.append(("get_app_id", path))
        self._raise("get_app_id")
        return SimpleNamespace(value=b"app-id", token=object())

    def free_memory(self, token):
        self.calls.append(("free_memory", token))
        self._raise("free_memory")

    def filter_add(self, engine, spec):
        self.calls.append(("filter_add", spec))
        self._raise("filter_add")
        filter_id = self.next_filter_id
        self.next_filter_id += 1
        self.filters[filter_id] = spec
        return filter_id

    def filter_get(self, engine, filter_id):
        self.calls.append(("filter_get", filter_id))
        self._raise("filter_get")
        return self.filters[filter_id]

    def filter_delete(self, engine, filter_id):
        self.calls.append(("filter_delete", filter_id))
        self._raise("filter_delete")
        self.filters.pop(filter_id)


def _snapshot(tmp_path):
    executable = tmp_path / "opencode.exe"
    executable.write_bytes(b"native-opencode")
    measured = file_identity(
        executable,
        version="1.18.4",
        authenticode_fn=lambda _path: "unsigned",
    )
    return replace(
        EvaluationIdentitySnapshot.for_test(),
        opencode=measured,
    )


def test_cli_default_is_literal_ipv4_loopback():
    script = _load_script()

    args = script.parse_args(_argv(Path("C:/safe")))

    assert args.ollama_host == "http://127.0.0.1:11434"


def test_verified_dry_run_opens_verifies_and_closes_containment_without_arms(
    tmp_path,
):
    script = _load_script()
    api = _ScriptWfpApi()
    snapshot = _snapshot(tmp_path)
    lines = []

    rc = script.main(
        _argv(tmp_path, "--dry-run"),
        list_models_fn=_models,
        resolve_bin_fn=lambda: snapshot.opencode.path,
        run_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("dry-run must not execute arms")
        ),
        identity_capture_fn=lambda _plan: snapshot,
        containment_wfp_api=api,
        out=lines.append,
    )

    report = json.loads("\n".join(lines))
    assert rc == 0
    assert report["os_egress_enforced"] is True
    assert report["containment"]["state"] == "pass"
    assert report["containment"]["application_paths"] == [
        str(snapshot.opencode.path)
    ]
    assert report["ollama_host"] == "http://127.0.0.1:11434"
    assert len([call for call in api.calls if call[0] == "filter_add"]) == 4
    assert api.calls[-1][0] == "engine_close"
    assert not (tmp_path / "evidence").exists()


def test_verified_dry_run_fails_when_containment_cleanup_is_uncertain(tmp_path):
    script = _load_script()
    api = _ScriptWfpApi()
    api.fail["filter_delete"] = RuntimeError("filter cleanup uncertain")
    snapshot = _snapshot(tmp_path)
    lines = []

    rc = script.main(
        _argv(tmp_path, "--dry-run"),
        list_models_fn=_models,
        resolve_bin_fn=lambda: snapshot.opencode.path,
        identity_capture_fn=lambda _plan: snapshot,
        containment_wfp_api=api,
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
    snapshot = _snapshot(tmp_path)
    api = _ScriptWfpApi()
    denied = OSError("access denied")
    denied.winerror = 5
    api.fail["engine_open"] = denied
    lines = []
    argv = _argv(tmp_path, "--dry-run", "--run-id", "quoted run")

    rc = script.main(
        argv,
        list_models_fn=_models,
        resolve_bin_fn=lambda: snapshot.opencode.path,
        identity_capture_fn=lambda _plan: snapshot,
        containment_wfp_api=api,
        out=lines.append,
    )

    report = json.loads("\n".join(lines))
    exact_command = subprocess.list2cmdline(
        [sys.executable, str(SCRIPT), *argv]
    )
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


def test_verified_dry_run_preserves_non_elevation_containment_failure(
    tmp_path,
):
    script = _load_script()
    snapshot = _snapshot(tmp_path)
    api = _ScriptWfpApi()
    api.fail["engine_open"] = OSError("policy database corrupted")
    lines = []

    rc = script.main(
        _argv(tmp_path, "--dry-run"),
        list_models_fn=_models,
        resolve_bin_fn=lambda: snapshot.opencode.path,
        identity_capture_fn=lambda _plan: snapshot,
        containment_wfp_api=api,
        out=lines.append,
    )

    report = json.loads("\n".join(lines))
    assert rc == 2
    assert report["containment"]["state"] == "fail"
    assert "policy database corrupted" in report["error"]
    assert "elevated terminal" not in report["error"]
    assert "rerun_command" not in report


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
