from __future__ import annotations

import json
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

import backend.agent_eval.identity as identity_module
import backend.agent_eval.fixture as fixture_module
import backend.agent_eval.opencode as opencode_module
from backend.agent_eval.evidence import (
    EvidenceControlResult,
    EvidenceMode,
    EvidenceState,
)
from backend.agent_eval.identity import (
    EvaluationIdentitySnapshot,
    IdentityError,
    file_identity,
)
from backend.agent_eval.result import ArmResult
from backend.agent_eval.fixture import FixtureSealResult
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


def _result(
    arm: str,
    model: str,
    response: str,
    *,
    capture_valid: bool = True,
) -> ArmResult:
    metrics = {"eval_count": 3}
    if arm in {"stock", "lac"}:
        config = {"path": f"C:/test/{arm}/opencode.json", "size": 1, "sha256": "a" * 64}
        metrics["opencode_config_identity"] = {"before": config, "after": dict(config)}
    capture = {}
    if capture_valid and arm == "raw":
        capture = {
            "response": {
                "allowed_bytes": 8 * 1024 * 1024,
                "observed_bytes": 512,
                "overflowed": False,
            }
        }
    elif capture_valid:
        capture = {
            "cleanup_complete": True,
            "stdout": {
                "allowed_bytes": 4 * 1024 * 1024,
                "observed_bytes": 512,
                "overflowed": False,
            },
            "stderr": {
                "allowed_bytes": 1024 * 1024,
                "observed_bytes": 0,
                "overflowed": False,
            },
        }
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
        capture=capture,
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


def _runtime_snapshot(tmp_path):
    runtime = tmp_path / "runtime.exe"
    runtime.write_bytes(b"runtime")
    measured = file_identity(
        runtime,
        version="1.18.4",
        authenticode_fn=lambda _path: "unsigned",
    )
    snapshot = EvaluationIdentitySnapshot.for_test()
    return runtime, replace(
        snapshot,
        lac=replace(measured, version=None),
        ollama=replace(measured, version="0.1"),
        opencode=measured,
    )


def _passing_identity_compare(*_args):
    return (
        EvidenceControlResult(
            "runtime_dependency_provenance",
            EvidenceState.PASS,
            "unchanged",
        ),
        EvidenceControlResult(
            "immutable_ollama_model_lineage",
            EvidenceState.PASS,
            "unchanged",
        ),
    )


class _NoopIdentityLease:
    def close(self):
        return None


def _noop_identity_lease(_snapshot):
    return _NoopIdentityLease()


@pytest.fixture(autouse=True)
def _restore_workspace_acls_after_test(tmp_path):
    yield
    evidence_root = tmp_path / "evidence"
    if not evidence_root.exists():
        return
    for workspace in evidence_root.glob("*/workspaces/*"):
        if workspace.is_dir():
            fixture_module._restore_fixture_access(workspace)


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
        return _result("stock", model, "wrong")

    def lac(task, model, host, workspace):
        seen["lac"] = (task.fixture_root, model, host, Path(workspace))
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
    fixture_control = next(
        item for item in evidence["controls"]["results"]
        if item["name"] == "sealed_fixture_materialization"
    )
    assert fixture_control["state"] == "pass"
    for name in ("raw", "stock", "lac"):
        arm_dir = run_root / "arms" / name
        before = json.loads((arm_dir / "fixture-manifest.before.json").read_text(encoding="utf-8"))
        after = json.loads((arm_dir / "fixture-manifest.after.json").read_text(encoding="utf-8"))
        assert after["expected"] == before
        assert after["verification"] == {"ok": True, "reason": None}
        assert after["observed"]["entries"] == before["entries"]
        assert after["observed"]["aggregate_sha256"] == before["aggregate_sha256"]
        assert after["observed"]["directories"] == []


@pytest.mark.parametrize("change", ["mutation", "addition", "deletion"])
def test_run_evaluation_after_artifact_records_observed_drift(
    tmp_path, monkeypatch, change
):
    monkeypatch.setattr(
        fixture_module,
        "mark_fixture_read_only",
        lambda _destination: FixtureSealResult(True, True),
    )

    def mutate(task, model, host, workspace):
        target = task.fixture_root / "stats_service.py"
        if change == "mutation":
            target.write_bytes(b"mutated\n")
        elif change == "addition":
            (task.fixture_root / "unexpected.txt").write_text(
                "added\n", encoding="utf-8"
            )
        else:
            target.unlink()
        return _result("stock", model, "ZeroDivisionError")

    comparison = run_evaluation(
        _plan(tmp_path),
        run_id=f"observed-after-{change}",
        raw_fn=lambda task, model, host: _result(
            "raw", model, "ZeroDivisionError"
        ),
        stock_fn=mutate,
        lac_fn=lambda task, model, host, workspace: _result(
            "lac", model, "ZeroDivisionError"
        ),
    )

    arm_dir = (
        tmp_path
        / "evidence"
        / f"observed-after-{change}"
        / "arms"
        / "stock"
    )
    before = json.loads(
        (arm_dir / "fixture-manifest.before.json").read_text(encoding="utf-8")
    )
    after = json.loads(
        (arm_dir / "fixture-manifest.after.json").read_text(encoding="utf-8")
    )
    observed = after["observed"]["entries"]
    expected = after["expected"]["entries"]
    assert after["verification"]["ok"] is False
    assert after["expected"] == before
    observed_paths = {entry["path"] for entry in observed}
    expected_paths = {entry["path"] for entry in expected}
    if change == "mutation":
        assert observed[0]["sha256"] == hashlib.sha256(b"mutated\n").hexdigest()
        assert observed[0]["sha256"] != expected[0]["sha256"]
    elif change == "addition":
        assert observed_paths == {"stats_service.py", "unexpected.txt"}
        assert expected_paths == {"stats_service.py"}
    else:
        assert observed_paths == set()
        assert expected_paths == {"stats_service.py"}
    control = next(
        item
        for item in comparison["evidence"]["controls"]["results"]
        if item["name"] == "sealed_fixture_materialization"
    )
    assert control["state"] == "fail"


def test_runner_rejects_unverified_acl_seal_before_adapter(
    tmp_path, monkeypatch
):
    called = []

    def unverified_materialize(manifest, source, destination):
        destination.mkdir()
        for entry in manifest.entries:
            target = destination / entry.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((Path(source) / entry.path).read_bytes())
        return FixtureSealResult(True, False, "ACL verification unavailable")

    monkeypatch.setattr(
        "backend.agent_eval.runner.materialize_fixture",
        unverified_materialize,
    )

    comparison = run_evaluation(
        _plan(tmp_path),
        run_id="unverified-acl",
        raw_fn=lambda *_args: called.append("raw"),
        stock_fn=lambda *_args: called.append("stock"),
        lac_fn=lambda *_args: called.append("lac"),
    )

    assert called == []
    control = next(
        item
        for item in comparison["evidence"]["controls"]["results"]
        if item["name"] == "sealed_fixture_materialization"
    )
    assert control["state"] == "fail"
    assert all(
        arm["acl_hardened"] is False
        for arm in control["details"]["arms"]
        if "acl_hardened" in arm
    )


def test_run_evaluation_persists_pre_and_post_identity_controls(tmp_path):
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
        identity_capture_fn=capture,
        identity_compare_fn=compare,
        identity_lease_fn=_noop_identity_lease,
    )
    identity_root = tmp_path / "evidence" / "identity-run" / "identities"
    assert {path.name for path in identity_root.iterdir()} == {"lac.json", "ollama.json", "opencode.json", "opencode-configs.json", "models.json"}
    states = {item["name"]: item["state"] for item in comparison["evidence"]["controls"]["results"]}
    assert states["runtime_dependency_provenance"] == "pass"
    assert states["immutable_ollama_model_lineage"] == "pass"
    configs = json.loads((identity_root / "opencode-configs.json").read_text(encoding="utf-8"))
    assert configs["stock"]["before"] == configs["stock"]["after"]
    assert configs["lac"]["before"] == configs["lac"]["after"]


def test_missing_or_drifted_arm_config_fails_runtime_provenance(tmp_path):
    snapshot = EvaluationIdentitySnapshot.for_test()
    def bad(arm, model):
        result = _result(arm, model, "ZeroDivisionError")
        if arm == "stock":
            result.metrics["opencode_config_identity"] = {"before": {"sha256": "a" * 64}, "after": {"sha256": "b" * 64}}
        if arm == "lac":
            result.metrics.pop("opencode_config_identity")
        return result
    comparison = run_evaluation(
        _plan(tmp_path), run_id="config-drift",
        raw_fn=lambda task, model, host: _result("raw", model, "ZeroDivisionError"),
        stock_fn=lambda task, model, host, workspace: bad("stock", model),
        lac_fn=lambda task, model, host, workspace: bad("lac", model),
        identity_capture_fn=lambda _: snapshot,
        identity_compare_fn=lambda *_: (
            EvidenceControlResult("runtime_dependency_provenance", EvidenceState.PASS, "unchanged"),
            EvidenceControlResult("immutable_ollama_model_lineage", EvidenceState.PASS, "unchanged"),
        ),
        identity_lease_fn=_noop_identity_lease,
    )
    control = next(item for item in comparison["evidence"]["controls"]["results"] if item["name"] == "runtime_dependency_provenance")
    assert control["state"] == "fail"


@pytest.mark.parametrize(
    "measurement",
    [
        {"before": None, "after": None},
        {"before": {"sha256": "a" * 64}, "after": None},
        {"before": None, "after": {"sha256": "a" * 64}},
        {"before": {"sha256": "a" * 64}},
    ],
)
def test_missing_before_or_post_config_identity_fails_closed(tmp_path, measurement):
    snapshot = EvaluationIdentitySnapshot.for_test()

    def measured_result(arm, model):
        result = _result(arm, model, "ZeroDivisionError")
        result.metrics["opencode_config_identity"] = measurement
        return result

    comparison = run_evaluation(
        _plan(tmp_path),
        run_id="missing-config-" + str(abs(hash(repr(measurement)))),
        raw_fn=lambda task, model, host: _result(
            "raw", model, "ZeroDivisionError"
        ),
        stock_fn=lambda task, model, host, workspace: measured_result(
            "stock", model
        ),
        lac_fn=lambda task, model, host, workspace: measured_result("lac", model),
        identity_capture_fn=lambda _: snapshot,
        identity_compare_fn=lambda *_: (
            EvidenceControlResult(
                "runtime_dependency_provenance",
                EvidenceState.PASS,
                "unchanged",
            ),
            EvidenceControlResult(
                "immutable_ollama_model_lineage",
                EvidenceState.PASS,
                "unchanged",
            ),
        ),
        identity_lease_fn=_noop_identity_lease,
    )

    control = next(
        item
        for item in comparison["evidence"]["controls"]["results"]
        if item["name"] == "runtime_dependency_provenance"
    )
    assert control["state"] == "fail"


def test_default_opencode_arms_execute_exact_preflight_target(
    tmp_path, monkeypatch
):
    snapshot = EvaluationIdentitySnapshot.for_test()
    target = tmp_path / "trusted" / "opencode.exe"
    snapshot = replace(
        snapshot,
        opencode=replace(snapshot.opencode, path=target),
    )
    executed = {}

    def fake_run(task, model, host, workspace, arm, **kwargs):
        executed[arm] = kwargs["resolve_bin_fn"]()
        return _result(arm, model, "ZeroDivisionError")

    monkeypatch.setattr(opencode_module, "_run_opencode", fake_run)

    run_evaluation(
        _plan(tmp_path),
        run_id="direct-target",
        raw_fn=lambda task, model, host: _result(
            "raw", model, "ZeroDivisionError"
        ),
        identity_capture_fn=lambda _: snapshot,
        identity_compare_fn=lambda *_: (
            EvidenceControlResult(
                "runtime_dependency_provenance",
                EvidenceState.PASS,
                "unchanged",
            ),
            EvidenceControlResult(
                "immutable_ollama_model_lineage",
                EvidenceState.PASS,
                "unchanged",
            ),
        ),
        identity_lease_fn=_noop_identity_lease,
    )

    assert executed == {"stock": target, "lac": target}


def test_runtime_files_cannot_be_mutated_during_runner_execution(tmp_path):
    runtime, snapshot = _runtime_snapshot(tmp_path)
    observed = []

    def stock(task, model, host, workspace):
        try:
            runtime.write_bytes(b"changed")
        except OSError:
            observed.append("denied")
        else:
            observed.append("mutated")
        return _result("stock", model, "ZeroDivisionError")

    comparison = run_evaluation(
        _plan(tmp_path),
        run_id="runtime-lease-window",
        raw_fn=lambda task, model, host: _result(
            "raw", model, "ZeroDivisionError"
        ),
        stock_fn=stock,
        lac_fn=lambda task, model, host, workspace: _result(
            "lac", model, "ZeroDivisionError"
        ),
        identity_capture_fn=lambda _: snapshot,
        identity_compare_fn=_passing_identity_compare,
        identity_lease_fn=identity_module.acquire_runtime_identity_leases,
    )

    assert observed == ["denied"]
    assert comparison["identity_valid"] is True
    runtime.write_bytes(b"changed")


@pytest.mark.parametrize(
    "failure_mode",
    ["arm_exception", "arm_timeout", "postflight_failure"],
)
def test_runtime_lease_closes_once_across_failure_paths(
    tmp_path, failure_mode
):
    _, snapshot = _runtime_snapshot(tmp_path)

    class Lease:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    lease = Lease()
    capture_calls = 0

    def capture(_plan):
        nonlocal capture_calls
        capture_calls += 1
        if failure_mode == "postflight_failure" and capture_calls == 2:
            raise IdentityError("postflight failed")
        return snapshot

    def stock(task, model, host, workspace):
        if failure_mode == "arm_exception":
            raise RuntimeError("arm failed")
        result = _result("stock", model, "ZeroDivisionError")
        if failure_mode == "arm_timeout":
            return replace(
                result,
                completed=False,
                timed_out=True,
                errors=("timeout:180s",),
            )
        return result

    comparison = run_evaluation(
        _plan(tmp_path),
        run_id="lease-close-" + failure_mode,
        raw_fn=lambda task, model, host: _result(
            "raw", model, "ZeroDivisionError"
        ),
        stock_fn=stock,
        lac_fn=lambda task, model, host, workspace: _result(
            "lac", model, "ZeroDivisionError"
        ),
        identity_capture_fn=capture,
        identity_compare_fn=_passing_identity_compare,
        identity_lease_fn=lambda _snapshot: lease,
    )

    assert lease.close_calls == 1
    if failure_mode == "postflight_failure":
        assert comparison["identity_valid"] is False


def test_runtime_lease_release_failure_fails_provenance_closed(tmp_path):
    _, snapshot = _runtime_snapshot(tmp_path)

    class Lease:
        def close(self):
            raise OSError("lease release failed")

    comparison = run_evaluation(
        _plan(tmp_path),
        run_id="lease-release-failure",
        raw_fn=lambda task, model, host: _result(
            "raw", model, "ZeroDivisionError"
        ),
        stock_fn=lambda task, model, host, workspace: _result(
            "stock", model, "ZeroDivisionError"
        ),
        lac_fn=lambda task, model, host, workspace: _result(
            "lac", model, "ZeroDivisionError"
        ),
        identity_capture_fn=lambda _: snapshot,
        identity_compare_fn=_passing_identity_compare,
        identity_lease_fn=lambda _snapshot: Lease(),
    )

    runtime_control = next(
        item
        for item in comparison["identity_controls"]
        if item["name"] == "runtime_dependency_provenance"
    )
    assert comparison["identity_valid"] is False
    assert runtime_control["state"] == "fail"
    assert "release" in runtime_control["reason"]


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


def test_runner_replaces_injected_capture_pass_when_arm_evidence_is_missing(tmp_path):
    control = EvidenceControlResult(
        "bounded_process_and_http_capture",
        EvidenceState.PASS,
        "all adapters reported bounded capture",
    )
    comparison = run_evaluation(
        _plan(tmp_path),
        run_id="bounded-capture-control",
        raw_fn=lambda task, model, host: _result(
            "raw", model, "ZeroDivisionError", capture_valid=False
        ),
        stock_fn=lambda task, model, host, workspace: _result(
            "stock", model, "ZeroDivisionError"
        ),
        lac_fn=lambda task, model, host, workspace: _result(
            "lac", model, "ZeroDivisionError"
        ),
        preliminary_results=(control,),
    )

    carried = next(
        item
        for item in comparison["evidence"]["controls"]["results"]
        if item["name"] == "bounded_process_and_http_capture"
    )
    assert carried["state"] == "fail"
    assert "raw" in carried["reason"]


@pytest.mark.parametrize(
    ("arm", "capture"),
    [
        ("raw", {"response": {"allowed_bytes": "8MiB", "observed_bytes": 1, "overflowed": False}}),
        (
            "stock",
            {
                "cleanup_complete": True,
                "stdout": {"allowed_bytes": 128, "observed_bytes": 129, "overflowed": True},
                "stderr": {"allowed_bytes": 128, "observed_bytes": 0, "overflowed": False},
            },
        ),
        (
            "lac",
            {
                "cleanup_complete": False,
                "stdout": {"allowed_bytes": 128, "observed_bytes": 1, "overflowed": False},
                "stderr": {"allowed_bytes": 128, "observed_bytes": 0, "overflowed": False},
            },
        ),
    ],
)
def test_runner_fails_closed_on_malformed_or_overflowed_capture_evidence(
    tmp_path, arm, capture
):
    control = EvidenceControlResult(
        "bounded_process_and_http_capture",
        EvidenceState.PASS,
        "caller assertion must be ignored",
    )

    def result_for(candidate, model):
        result = _result(candidate, model, "ZeroDivisionError")
        return replace(result, capture=capture) if candidate == arm else result

    comparison = run_evaluation(
        _plan(tmp_path),
        run_id=f"invalid-capture-{arm}",
        raw_fn=lambda task, model, host: result_for("raw", model),
        stock_fn=lambda task, model, host, workspace: result_for("stock", model),
        lac_fn=lambda task, model, host, workspace: result_for("lac", model),
        preliminary_results=(control,),
    )

    carried = next(
        item
        for item in comparison["evidence"]["controls"]["results"]
        if item["name"] == "bounded_process_and_http_capture"
    )
    assert carried["state"] == "fail"
    assert arm in carried["reason"]
