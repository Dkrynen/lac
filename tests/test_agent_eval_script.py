from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


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
        _argv(tmp_path, "--dry-run"),
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
    assert report["mode"] == "verified"
    assert report["artifact_valid"] is False
    assert report["controls"]["results"][0]["state"] == "unsupported"


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
