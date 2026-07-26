from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.agent_eval.evidence import EvidenceMode
from backend.agent_eval.result import ArmResult
from backend.agent_eval.runner import (
    EvalPlanError,
    build_plan,
    run_evaluation,
)
from backend.agent_eval.task import EvalScorer, EvalTask


def _task(tmp_path: Path) -> EvalTask:
    fixture = tmp_path / "suite" / "fixtures" / "python-empty-mean"
    fixture.mkdir(parents=True)
    (fixture / "stats_service.py").write_text(
        "def summarize(values):\n"
        "    return sum(values) / len(values)\n",
        encoding="utf-8",
    )
    return EvalTask(
        schema_version=1,
        id="python-empty-mean",
        prompt="Answer only the exception class.",
        fixture_root=fixture,
        timeout_seconds=180,
        scorer=EvalScorer(type="exact_text", expected="ZeroDivisionError"),
    )


def _result(arm: str, model: str, response: str) -> ArmResult:
    metrics = {"eval_count": 3}
    if arm in {"stock", "lac"}:
        config = {"path": f"C:/test/{arm}/opencode.json", "size": 1, "sha256": "a" * 64}
        metrics["opencode_config_identity"] = {"before": config, "after": dict(config)}
    return ArmResult(
        arm=arm,
        model=model,
        runtime="ollama" if arm == "raw" else "opencode",
        completed=True,
        timed_out=False,
        response=response,
        wall_time_ms=12.5,
        metrics=metrics,
        raw_stdout=f"{arm}-stdout",
        raw_stderr=f"{arm}-stderr",
    )


def _plan(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    return build_plan(
        _task(tmp_path),
        base_model="gpt-oss:20b",
        lac_model="gpt-oss:20b-agent",
        ollama_host="http://localhost:11434",
        output_root=tmp_path / "evidence",
        installed_models=["gpt-oss:20b", "gpt-oss:20b-agent"],
        opencode_binary=Path(r"C:\tools\opencode.cmd"),
        opencode_version="1.18.4",
        source_root=source,
    )


def test_build_plan_is_dry_and_records_exact_identities(tmp_path):
    plan = _plan(tmp_path)

    assert not plan.output_root.exists()
    assert plan.task.id == "python-empty-mean"
    assert plan.base_model == "gpt-oss:20b"
    assert plan.lac_model == "gpt-oss:20b-agent"
    assert plan.ollama_host == "http://localhost:11434"
    assert plan.opencode_binary == Path(r"C:\tools\opencode.cmd")
    assert plan.opencode_version == "1.18.4"
    assert len(plan.fixture_sha256) == 64
    assert plan.auto_approval_scope == "disposable_workspace_only"


@pytest.mark.parametrize(
    ("base", "lac", "installed", "message"),
    [
        (
            "gpt-oss:20b",
            "wrong:agent",
            ["gpt-oss:20b", "wrong:agent"],
            "expected agent variant",
        ),
        (
            "gpt-oss:20b",
            "gpt-oss:20b-agent",
            ["gpt-oss:20b-agent"],
            "base model is not installed",
        ),
        (
            "gpt-oss:20b",
            "gpt-oss:20b-agent",
            ["gpt-oss:20b"],
            "LAC model is not installed",
        ),
    ],
)
def test_build_plan_refuses_wrong_or_missing_model_identities(
    tmp_path, base, lac, installed, message
):
    with pytest.raises(EvalPlanError, match=message):
        build_plan(
            _task(tmp_path),
            base_model=base,
            lac_model=lac,
            ollama_host="http://localhost:11434",
            output_root=tmp_path / "evidence",
            installed_models=installed,
            opencode_binary=Path("opencode"),
            opencode_version="1.18.4",
            source_root=tmp_path / "source",
        )


def test_build_plan_refuses_output_inside_source_repo(tmp_path):
    source = tmp_path / "source"
    source.mkdir()

    with pytest.raises(EvalPlanError, match="outside the source repo"):
        build_plan(
            _task(tmp_path),
            base_model="gpt-oss:20b",
            lac_model="gpt-oss:20b-agent",
            ollama_host="http://localhost:11434",
            output_root=source / "evidence",
            installed_models=["gpt-oss:20b", "gpt-oss:20b-agent"],
            opencode_binary=Path("opencode"),
            opencode_version="1.18.4",
            source_root=source,
        )


def test_run_evaluation_isolates_arms_scores_and_persists_artifacts(tmp_path):
    seen = {}

    def raw(task, model, host):
        seen["raw"] = (task.fixture_root, model, host)
        return _result("raw", model, "ZeroDivisionError")

    def stock(task, model, host, workspace):
        seen["stock"] = (task.fixture_root, model, host, Path(workspace))
        (Path(workspace) / "stock-only.txt").write_text("stock", encoding="utf-8")
        return _result("stock", model, "wrong")

    def lac(task, model, host, workspace):
        seen["lac"] = (task.fixture_root, model, host, Path(workspace))
        assert not (Path(workspace) / "stock-only.txt").exists()
        return _result("lac", model, "ZeroDivisionError")

    comparison = run_evaluation(
        _plan(tmp_path),
        run_id="run-001",
        raw_fn=raw,
        stock_fn=stock,
        lac_fn=lac,
        environment={"git": {"commit": "abc", "dirty": True}},
    )

    run_root = tmp_path / "evidence" / "run-001"
    assert comparison["artifact_written"] is True
    assert "runtime_dependency_provenance" in comparison["evidence_blockers"]
    assert comparison["all_arms_executed"] is True
    assert comparison["all_arms_passed"] is False
    assert comparison["scores"] == {"raw": 1.0, "stock": 0.0, "lac": 1.0}
    assert comparison["passes"] == {"raw": True, "stock": False, "lac": True}
    assert comparison["run_root"] == str(run_root)

    roots = {seen[name][0] for name in ("raw", "stock", "lac")}
    assert len(roots) == 3
    for name in ("raw", "stock", "lac"):
        workspace = run_root / "workspaces" / name
        assert seen[name][0] == workspace
        assert (workspace / "stats_service.py").exists()

        arm_dir = run_root / "arms" / name
        result = json.loads((arm_dir / "result.json").read_text(encoding="utf-8"))
        assert result["score"]["passed"] is comparison["passes"][name]
        assert (arm_dir / "stdout.log").read_text(encoding="utf-8") == (
            f"{name}-stdout"
        )
        assert (arm_dir / "stderr.log").read_text(encoding="utf-8") == (
            f"{name}-stderr"
        )
        prompt = (arm_dir / "prompt.txt").read_text(encoding="utf-8")
        if name == "raw":
            assert "--- FILE: stats_service.py ---" in prompt
            assert "return sum(values) / len(values)" in prompt
        else:
            assert prompt == "Answer only the exception class."

    manifest = json.loads((run_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["task"]["id"] == "python-empty-mean"
    assert manifest["models"] == {
        "raw": "gpt-oss:20b",
        "stock": "gpt-oss:20b",
        "lac": "gpt-oss:20b-agent",
    }
    assert manifest["environment"]["git"]["commit"] == "abc"
    assert manifest["containment"]["allowed_tools"] == ["read", "glob", "grep"]
    assert manifest["containment"]["model_downloads"] == "forbidden"
    assert "possible" in manifest["containment"]["runtime_dependency_bootstrap"]
    assert manifest["prompt_delivery"] == {
        "raw": "task prompt plus bounded source snapshot",
        "stock": "task prompt plus read-only project tools",
        "lac": "task prompt plus read-only project tools",
    }
    assert json.loads(
        (run_root / "comparison.json").read_text(encoding="utf-8")
    ) == {key: value for key, value in comparison.items() if key != "evidence"}
    evidence = json.loads((run_root / "evidence.json").read_text(encoding="utf-8"))
    assert comparison["evidence"] == evidence
    assert evidence["mode"] == EvidenceMode.DIAGNOSTIC.value
    assert evidence["artifact_valid"] is False


def test_run_evaluation_persists_pre_and_post_identity_controls(tmp_path):
    from backend.agent_eval.identity import EvaluationIdentitySnapshot
    from backend.agent_eval.evidence import EvidenceControlResult, EvidenceState

    snapshot = EvaluationIdentitySnapshot.for_test()
    def capture(_plan):
        return snapshot
    def compare(before, after):
        assert before is snapshot and after is snapshot
        return (
            EvidenceControlResult("runtime_dependency_provenance", EvidenceState.PASS, "unchanged"),
            EvidenceControlResult("immutable_ollama_model_lineage", EvidenceState.PASS, "unchanged"),
        )

    comparison = run_evaluation(
        _plan(tmp_path), run_id="identity-run",
        raw_fn=lambda task, model, host: _result("raw", model, "ZeroDivisionError"),
        stock_fn=lambda task, model, host, workspace: _result("stock", model, "ZeroDivisionError"),
        lac_fn=lambda task, model, host, workspace: _result("lac", model, "ZeroDivisionError"),
        identity_capture_fn=capture, identity_compare_fn=compare,
    )
    identity_root = tmp_path / "evidence" / "identity-run" / "identities"
    assert {path.name for path in identity_root.iterdir()} == {"lac.json", "ollama.json", "opencode.json", "opencode-configs.json", "models.json"}
    states = {item["name"]: item["state"] for item in comparison["evidence"]["controls"]["results"]}
    assert states["runtime_dependency_provenance"] == "pass"
    assert states["immutable_ollama_model_lineage"] == "pass"


def test_run_evaluation_persists_partial_arm_failure(tmp_path):
    def crash(*_args):
        raise RuntimeError("adapter bug")

    comparison = run_evaluation(
        _plan(tmp_path),
        run_id="run-partial",
        raw_fn=lambda task, model, host: _result("raw", model, "ZeroDivisionError"),
        stock_fn=crash,
        lac_fn=lambda task, model, host, workspace: _result(
            "lac", model, "ZeroDivisionError"
        ),
    )

    result_path = (
        tmp_path
        / "evidence"
        / "run-partial"
        / "arms"
        / "stock"
        / "result.json"
    )
    stock = json.loads(result_path.read_text(encoding="utf-8"))
    assert comparison["artifact_written"] is True
    assert comparison["all_arms_executed"] is False
    assert comparison["passes"]["stock"] is False
    assert stock["result"]["completed"] is False
    assert stock["result"]["errors"] == ["RuntimeError: adapter bug"]


def test_run_evaluation_refuses_to_overwrite_existing_run(tmp_path):
    plan = _plan(tmp_path)
    existing = plan.output_root / "same-run"
    existing.mkdir(parents=True)

    with pytest.raises(EvalPlanError, match="already exists"):
        run_evaluation(plan, run_id="same-run")
