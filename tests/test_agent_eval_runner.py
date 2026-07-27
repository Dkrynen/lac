from __future__ import annotations

import json
import hashlib
import inspect
import os
import threading
import urllib.request
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.agent_eval.identity as identity_module
import backend.agent_eval.fixture as fixture_module
import backend.agent_eval.opencode as opencode_module
import backend.agent_eval.runtime_provenance as runtime_provenance_module
import backend.agent_eval.runner as runner_module
from backend.agent_eval.evidence import (
    EvidenceControlResult,
    EvidenceMode,
    EvidenceState,
)
from backend.agent_eval.containment import ContainmentError
from backend.agent_eval.identity import (
    EvaluationIdentitySnapshot,
    IdentityError,
    file_identity,
)
from backend.agent_eval.result import ArmResult
from backend.agent_eval.schedule import (
    GenerationSettings,
    TrialSpec,
    build_schedule,
)
from backend.agent_eval.fixture import FixtureSealResult
from backend.agent_eval.runner import (
    EvalPlanError,
    _windows_containment_result,
    build_plan,
    run_evaluation,
)
from backend.agent_eval.task import EvalScorer, EvalTask, task_contract_sha256
from backend.agent_eval.windows_job import WindowsJobProcess

OPENCODE_SHA256 = (
    "b7b469b83cc3561e5129a1803b746f7e2c1974297909f5b346398dc9c56a477e"
)


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


def _task_v2(tmp_path: Path) -> EvalTask:
    return replace(
        _task(tmp_path),
        schema_version=2,
        trials=3,
        generation=GenerationSettings(1.0, 20260726, 128),
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
        config = {
            "path": f"C:/test/{arm}/opencode.json",
            "size": 1,
            "sha256": "a" * 64,
            "canonical_sha256": "b" * 64,
        }
        metrics["opencode_config_identity"] = {
            "expected_canonical_sha256": "b" * 64,
            "before": config,
            "after": dict(config),
        }
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
            "windows_job_measured": True,
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
            "windows_job": {
                "real_windows_job": True,
                "assignment_proven": True,
                "active_process_limit": 1,
                "memory_limit_bytes": None,
                "kill_on_close": True,
                "resume_after_assignment": True,
                "final_active_processes": 0,
                "handles_closed": True,
                "cleanup_certain": True,
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
        ollama_host="http://127.0.0.1:11434",
        output_root=tmp_path / "evidence",
        installed_models=["gpt-oss:20b", "gpt-oss:20b-agent"],
        opencode_binary=Path(r"C:\tools\opencode.cmd"),
        opencode_version="1.18.7",
        source_root=source,
    )


def _plan_v2(
    tmp_path: Path,
    ollama_host: str = "http://127.0.0.1:11434",
):
    source = tmp_path / "source"
    source.mkdir()
    return build_plan(
        _task_v2(tmp_path),
        base_model="gpt-oss:20b",
        lac_model="gpt-oss:20b-agent",
        ollama_host=ollama_host,
        output_root=tmp_path / "evidence",
        installed_models=["gpt-oss:20b", "gpt-oss:20b-agent"],
        opencode_binary=Path(r"C:\tools\opencode.cmd"),
        opencode_version="1.18.7",
        source_root=source,
    )


def _sampling_metadata(arm: str, trial: TrialSpec) -> dict:
    if arm == "raw":
        return {
            "stream": False,
            "options": {
                "temperature": 1.0,
                "seed": trial.seed,
                "num_predict": 128,
            },
            "response_max_bytes": 8 * 1024 * 1024,
            "trial_index": trial.index,
        }
    return {
        "source": "opencode_1.18.7_ollama_http_capture",
        "observed": True,
        "path": "/v1/chat/completions",
        "temperature": 1.0,
        "seed": trial.seed,
        "max_output_tokens": 128,
        "trial_index": trial.index,
    }


def _send_sampling_request(
    endpoint: str,
    arm: str,
    trial: TrialSpec,
) -> None:
    if arm == "raw":
        path = "/api/chat"
        body = {
            "model": "gpt-oss:20b",
            "stream": False,
            "options": {
                "temperature": 1.0,
                "seed": trial.seed,
                "num_predict": 128,
            },
        }
    else:
        path = "/v1/chat/completions"
        body = {
            "model": (
                "gpt-oss:20b-agent" if arm == "lac" else "gpt-oss:20b"
            ),
            "temperature": 1.0,
            "seed": trial.seed,
            "max_tokens": 128,
        }
    request = urllib.request.Request(
        endpoint + path,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        assert response.status == 200


@pytest.fixture
def recording_upstream():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            self.rfile.read(length)
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _runtime_snapshot(tmp_path, *, attested=True):
    runtime = tmp_path / "runtime.exe"
    runtime.write_bytes(b"runtime")
    measured = file_identity(
        runtime,
        version="1.18.7",
        authenticode_fn=lambda _path: "unsigned",
    )
    snapshot = EvaluationIdentitySnapshot.for_test()
    return runtime, replace(
        snapshot,
        lac=replace(measured, version=None),
        ollama=replace(measured, version="0.1"),
        opencode=replace(
            measured,
            sha256=(
                OPENCODE_SHA256
                if attested
                else measured.sha256
            ),
        ),
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


def _allow_fixture_runtime(monkeypatch, snapshot):
    system, architecture = runtime_provenance_module._platform_key()
    monkeypatch.setitem(
        runtime_provenance_module._ALLOWED_RUNTIME_PROVIDERS,
        (
            system,
            architecture,
            runtime_provenance_module._REVIEWED_BUILD_ID,
            snapshot.opencode.version,
            snapshot.opencode.sha256,
        ),
        runtime_provenance_module.opencode.EVALUATION_PROVIDER_NPM,
    )


@pytest.fixture(autouse=True)
def _restore_workspace_acls_after_test(tmp_path):
    yield
    evidence_root = tmp_path / "evidence"
    if not evidence_root.exists():
        return
    for workspace in evidence_root.glob("*/workspaces/*"):
        if workspace.is_dir():
            fixture_module._restore_fixture_access(workspace)
    for workspace in evidence_root.glob("*/trials/*/*/workspace"):
        if workspace.is_dir():
            fixture_module._restore_fixture_access(workspace)


def test_build_plan_is_dry_and_records_exact_identities(tmp_path):
    plan = _plan(tmp_path)

    assert not plan.output_root.exists()
    assert plan.task.id == "python-empty-mean"
    assert plan.base_model == "gpt-oss:20b"
    assert plan.lac_model == "gpt-oss:20b-agent"
    assert plan.ollama_host == "http://127.0.0.1:11434"
    assert plan.opencode_binary == Path(r"C:\tools\opencode.cmd")
    assert plan.opencode_version == "1.18.7"
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
            opencode_version="1.18.7",
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
            opencode_version="1.18.7",
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
    assert manifest["containment"]["runtime_dependency_bootstrap"] == (
        "verified_by_runtime_attestation_in_verified_mode"
    )
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


def test_v2_runner_persists_schedule_before_first_arm_and_nine_records(
    tmp_path,
    recording_upstream,
):
    plan = _plan_v2(tmp_path, recording_upstream)
    _runtime, snapshot = _runtime_snapshot(tmp_path)
    calls = []

    def adapter_for(arm):
        def run(task, model, host, workspace=None, *, generation, trial):
            run_root = plan.output_root / "v2-nine"
            schedule_path = run_root / "schedule.json"
            assert schedule_path.is_file()
            assert generation == plan.task.generation
            assert host != recording_upstream
            assert task.fixture_root == (
                run_root
                / "trials"
                / f"{trial.index:03d}"
                / arm
                / "workspace"
            )
            if workspace is not None:
                assert Path(workspace) == task.fixture_root
            calls.append((trial.index, arm, trial.seed))
            _send_sampling_request(host, arm, trial)
            return _result(arm, model, "ZeroDivisionError")

        return run

    comparison = run_evaluation(
        plan,
        run_id="v2-nine",
        raw_fn=adapter_for("raw"),
        stock_fn=adapter_for("stock"),
        lac_fn=adapter_for("lac"),
        identity_capture_fn=lambda _plan: snapshot,
        identity_compare_fn=_passing_identity_compare,
        identity_lease_fn=_noop_identity_lease,
    )

    expected_schedule = build_schedule(
        task_contract_sha256(plan.task),
        {
            "raw": snapshot.models.base.digest,
            "stock": snapshot.models.base.digest,
            "lac": snapshot.models.lac.digest,
        },
        plan.task.generation,
        3,
    )
    expected_seeds = [trial.seed for trial in expected_schedule.trials]
    expected_calls = [
        (1, "raw", expected_seeds[0]),
        (1, "stock", expected_seeds[0]),
        (1, "lac", expected_seeds[0]),
        (2, "stock", expected_seeds[1]),
        (2, "lac", expected_seeds[1]),
        (2, "raw", expected_seeds[1]),
        (3, "lac", expected_seeds[2]),
        (3, "raw", expected_seeds[2]),
        (3, "stock", expected_seeds[2]),
    ]
    assert calls == expected_calls

    run_root = plan.output_root / "v2-nine"
    schedule = json.loads(
        (run_root / "schedule.json").read_text(encoding="utf-8")
    )
    assert [item["seed"] for item in schedule["trials"]] == expected_seeds
    assert [item["arm_order"] for item in schedule["trials"]] == [
        ["raw", "stock", "lac"],
        ["stock", "lac", "raw"],
        ["lac", "raw", "stock"],
    ]
    assert schedule["generation"] == {
        "temperature": 1.0,
        "seed_base": 20260726,
        "max_output_tokens": 128,
    }
    result_paths = sorted(run_root.glob("trials/*/*/result.json"))
    assert len(result_paths) == 9
    for trial_index, arm, seed in expected_calls:
        payload = json.loads(
            (
                run_root
                / "trials"
                / f"{trial_index:03d}"
                / arm
                / "result.json"
            ).read_text(encoding="utf-8")
        )
        assert payload["trial"] == {
            "index": trial_index,
            "seed": seed,
            "order_position": schedule["trials"][trial_index - 1][
                "arm_order"
            ].index(arm),
            "arm_order": schedule["trials"][trial_index - 1]["arm_order"],
        }
        assert payload["generation"] == schedule["generation"]
        assert payload["model_digest"] == schedule["model_digests"][arm]
    assert comparison["aggregate"] == {
        "trial_count": 3,
        "record_count": 9,
        "pass_counts": {"raw": 3, "stock": 3, "lac": 3},
    }
    assert len(comparison["trials"]) == 3
    sampling = next(
        item
        for item in comparison["evidence"]["controls"]["results"]
        if item["name"] == "counterbalanced_deterministic_sampling"
    )
    assert sampling["state"] == "pass"
    assert sampling["details"]["record_count"] == 9


def test_v2_runner_continues_in_schedule_order_after_arm_failure(tmp_path):
    plan = _plan_v2(tmp_path)
    _runtime, snapshot = _runtime_snapshot(tmp_path)
    calls = []

    def adapter_for(arm):
        def run(task, model, host, workspace=None, *, generation, trial):
            calls.append((trial.index, arm))
            if trial.index == 1 and arm == "stock":
                raise RuntimeError("planned adapter failure")
            return replace(
                _result(arm, model, "ZeroDivisionError"),
                request_metadata=_sampling_metadata(arm, trial),
            )

        return run

    comparison = run_evaluation(
        plan,
        run_id="v2-continue",
        raw_fn=adapter_for("raw"),
        stock_fn=adapter_for("stock"),
        lac_fn=adapter_for("lac"),
        identity_capture_fn=lambda _plan: snapshot,
        identity_compare_fn=_passing_identity_compare,
        identity_lease_fn=_noop_identity_lease,
    )

    assert calls == [
        (1, "raw"), (1, "stock"), (1, "lac"),
        (2, "stock"), (2, "lac"), (2, "raw"),
        (3, "lac"), (3, "raw"), (3, "stock"),
    ]
    assert comparison["aggregate"]["record_count"] == 9
    assert comparison["aggregate"]["pass_counts"]["stock"] == 2


@pytest.mark.parametrize(
    "tamper",
    ["missing", "duplicate", "mismatch", "bool", "extra", "schedule_replace"],
)
def test_v2_runner_derives_sampling_and_rejects_disk_tampering(
    tmp_path,
    tamper,
):
    plan = _plan_v2(tmp_path)
    _runtime, snapshot = _runtime_snapshot(tmp_path)

    def alter_json(path, mutate):
        payload = json.loads(path.read_text(encoding="utf-8"))
        mutate(payload)
        replacement = path.with_suffix(".replacement")
        replacement.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(replacement, path)

    def adapter_for(arm):
        def run(task, model, host, workspace=None, *, generation, trial):
            if trial.index == 3 and arm == "stock":
                run_root = plan.output_root / f"v2-{tamper}"
                first = run_root / "trials" / "001" / "raw" / "result.json"
                if tamper == "missing":
                    first.unlink()
                elif tamper == "duplicate":
                    alter_json(
                        first,
                        lambda payload: payload["trial"].update(index=2),
                    )
                elif tamper == "mismatch":
                    alter_json(
                        first,
                        lambda payload: payload["trial"].update(seed=7),
                    )
                elif tamper == "bool":
                    alter_json(
                        first,
                        lambda payload: payload["trial"].update(seed=True),
                    )
                elif tamper == "extra":
                    extra = run_root / "trials" / "004" / "raw"
                    extra.mkdir(parents=True)
                    (extra / "result.json").write_text(
                        json.dumps({"unexpected": True}),
                        encoding="utf-8",
                    )
                else:
                    alter_json(
                        run_root / "schedule.json",
                        lambda payload: payload["trials"][0].update(seed=7),
                    )
            return replace(
                _result(arm, model, "ZeroDivisionError"),
                request_metadata=_sampling_metadata(arm, trial),
            )

        return run

    injected = EvidenceControlResult(
        "counterbalanced_deterministic_sampling",
        EvidenceState.PASS,
        "caller says pass",
        {"injected": True},
    )
    comparison = run_evaluation(
        plan,
        run_id=f"v2-{tamper}",
        raw_fn=adapter_for("raw"),
        stock_fn=adapter_for("stock"),
        lac_fn=adapter_for("lac"),
        preliminary_results=(injected,),
        identity_capture_fn=lambda _plan: snapshot,
        identity_compare_fn=_passing_identity_compare,
        identity_lease_fn=_noop_identity_lease,
    )
    sampling = next(
        item
        for item in comparison["evidence"]["controls"]["results"]
        if item["name"] == "counterbalanced_deterministic_sampling"
    )
    assert sampling["state"] == "fail"
    assert sampling["details"].get("injected") is not True


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
        executed[arm] = (
            kwargs["resolve_bin_fn"](),
            kwargs["launcher"],
        )
        return _result(arm, model, "ZeroDivisionError")

    monkeypatch.setattr(opencode_module, "_run_opencode", fake_run)

    comparison = run_evaluation(
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

    assert executed == {
        "stock": (target, WindowsJobProcess.start),
        "lac": (target, WindowsJobProcess.start),
    }
    containment = next(
        item
        for item in comparison["evidence"]["controls"]["results"]
        if item["name"] == "windows_process_tree_containment"
    )
    assert containment["state"] == "pass"


def test_runtime_files_cannot_be_mutated_during_runner_execution(tmp_path):
    runtime, snapshot = _runtime_snapshot(tmp_path, attested=False)
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


def test_runner_rejects_fabricated_containment_from_injected_adapters(tmp_path):
    injected = EvidenceControlResult(
        "windows_process_tree_containment",
        EvidenceState.FAIL,
        "caller verdict must be replaced",
    )
    comparison = run_evaluation(
        _plan(tmp_path),
        run_id="derived-windows-containment",
        raw_fn=lambda task, model, host: _result(
            "raw", model, "ZeroDivisionError"
        ),
        stock_fn=lambda task, model, host, workspace: _result(
            "stock", model, "ZeroDivisionError"
        ),
        lac_fn=lambda task, model, host, workspace: _result(
            "lac", model, "ZeroDivisionError"
        ),
        preliminary_results=(injected,),
    )

    control = next(
        item
        for item in comparison["evidence"]["controls"]["results"]
        if item["name"] == "windows_process_tree_containment"
    )
    assert control["state"] == "fail"
    assert "not produced by the measured default adapter" in control["reason"]


@pytest.mark.parametrize(
    "field",
    [
        "real_windows_job",
        "assignment_proven",
        "kill_on_close",
        "resume_after_assignment",
        "handles_closed",
        "cleanup_certain",
    ],
)
@pytest.mark.parametrize("invalid", [1, 0, "truthy", ""])
def test_measured_containment_requires_exact_boolean_fields(field, invalid):
    results = {
        "raw": _result("raw", "base", "ok"),
        "stock": _result("stock", "base", "ok"),
        "lac": _result("lac", "lac", "ok"),
    }
    stock = results["stock"]
    capture = dict(stock.capture)
    measurement = dict(capture["windows_job"])
    measurement[field] = invalid
    capture["windows_job"] = measurement
    results["stock"] = replace(stock, capture=capture)

    control = _windows_containment_result(results, {"stock", "lac"})

    assert control.state is EvidenceState.FAIL
    assert f"stock: {field} is not an exact boolean" in control.reason


@pytest.mark.parametrize(
    ("arm", "change"),
    [
        ("stock", {"real_windows_job": False}),
        ("stock", {"assignment_proven": False}),
        ("lac", {"active_process_limit": 2}),
        ("lac", {"final_active_processes": 1}),
        ("lac", {"handles_closed": False}),
        ("stock", {"cleanup_certain": False}),
        ("stock", {"active_process_limit": True}),
        ("lac", {"final_active_processes": False}),
    ],
)
def test_runner_replaces_injected_windows_pass_with_measured_failure(
    tmp_path, arm, change
):
    injected = EvidenceControlResult(
        "windows_process_tree_containment",
        EvidenceState.PASS,
        "caller assertion must be ignored",
    )

    def result_for(candidate, model):
        result = _result(candidate, model, "ZeroDivisionError")
        if candidate == arm:
            capture = dict(result.capture)
            windows_job = dict(capture["windows_job"])
            windows_job.update(change)
            capture["windows_job"] = windows_job
            return replace(result, capture=capture)
        return result

    comparison = run_evaluation(
        _plan(tmp_path),
        run_id=f"invalid-windows-{arm}-{next(iter(change))}",
        raw_fn=lambda task, model, host: result_for("raw", model),
        stock_fn=lambda task, model, host, workspace: result_for(
            "stock", model
        ),
        lac_fn=lambda task, model, host, workspace: result_for("lac", model),
        preliminary_results=(injected,),
    )

    control = next(
        item
        for item in comparison["evidence"]["controls"]["results"]
        if item["name"] == "windows_process_tree_containment"
    )
    assert control["state"] == "fail"
    assert arm in control["reason"]


class _RunnerWfpApi:
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
        return SimpleNamespace(
            value=("app:" + str(path).lower()).encode(),
            token=object(),
        )

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


def test_runner_replaces_injected_egress_pass_with_diagnostic_unsupported(
    tmp_path,
):
    injected = EvidenceControlResult(
        "os_loopback_only_egress",
        EvidenceState.PASS,
        "caller assertion must be ignored",
    )
    comparison = run_evaluation(
        _plan(tmp_path),
        run_id="diagnostic-containment",
        raw_fn=lambda task, model, host: _result(
            "raw", model, "ZeroDivisionError"
        ),
        stock_fn=lambda task, model, host, workspace: _result(
            "stock", model, "ZeroDivisionError"
        ),
        lac_fn=lambda task, model, host, workspace: _result(
            "lac", model, "ZeroDivisionError"
        ),
        preliminary_results=(injected,),
    )
    control = next(
        item
        for item in comparison["evidence"]["controls"]["results"]
        if item["name"] == "os_loopback_only_egress"
    )
    assert control["state"] == "unsupported"
    assert control["details"]["provider"] == "diagnostic"


def test_verified_runner_uses_measured_provider_and_task5_launcher(
    tmp_path,
    monkeypatch,
):
    _runtime, snapshot = _runtime_snapshot(tmp_path, attested=False)
    _allow_fixture_runtime(monkeypatch, snapshot)
    api = _RunnerWfpApi()

    plan = _plan_v2(tmp_path)
    comparison = run_evaluation(
        plan,
        run_id="verified-containment",
        raw_fn=lambda task, model, host, **_kwargs: _result(
            "raw", model, "ZeroDivisionError"
        ),
        stock_fn=lambda task, model, host, workspace, **_kwargs: _result(
            "stock", model, "ZeroDivisionError"
        ),
        lac_fn=lambda task, model, host, workspace, **_kwargs: _result(
            "lac", model, "ZeroDivisionError"
        ),
        mode=EvidenceMode.VERIFIED,
        containment_wfp_api=api,
        identity_capture_fn=lambda _: snapshot,
        identity_compare_fn=_passing_identity_compare,
        identity_lease_fn=_noop_identity_lease,
    )

    control = next(
        item
        for item in comparison["evidence"]["controls"]["results"]
        if item["name"] == "os_loopback_only_egress"
    )
    assert control["state"] == "pass"
    assert control["details"]["provider"] == "windows_wfp"
    assert control["details"]["applications"] == [str(snapshot.opencode.path)]
    assert control["details"]["endpoint"].startswith("http://127.0.0.1:")
    assert control["details"]["endpoint"] != plan.ollama_host
    assert len([call for call in api.calls if call[0] == "filter_add"]) == 4
    assert [call[0] for call in api.calls[-6:]] == [
        "filter_delete",
        "filter_delete",
        "filter_delete",
        "filter_delete",
        "sublayer_delete",
        "engine_close",
    ]


def test_verified_runner_has_no_free_form_provider_evidence_seam():
    assert "containment_provider_fn" not in inspect.signature(
        run_evaluation
    ).parameters


def test_verified_runner_stops_adapters_when_provider_open_fails(
    tmp_path,
    monkeypatch,
):
    called = []
    _runtime, snapshot = _runtime_snapshot(tmp_path, attested=False)
    _allow_fixture_runtime(monkeypatch, snapshot)
    api = _RunnerWfpApi()
    api.fail["engine_open"] = ContainmentError("elevation required")

    comparison = run_evaluation(
        _plan_v2(tmp_path),
        run_id="containment-open-failure",
        raw_fn=lambda *_args: called.append("raw"),
        stock_fn=lambda *_args: called.append("stock"),
        lac_fn=lambda *_args: called.append("lac"),
        mode=EvidenceMode.VERIFIED,
        containment_wfp_api=api,
        identity_capture_fn=lambda _: snapshot,
        identity_compare_fn=_passing_identity_compare,
        identity_lease_fn=_noop_identity_lease,
    )

    assert called == []
    control = next(
        item
        for item in comparison["evidence"]["controls"]["results"]
        if item["name"] == "os_loopback_only_egress"
    )
    assert control["state"] == "fail"
    assert "elevation required" in control["reason"]


def test_provider_close_uncertainty_forces_egress_failure(
    tmp_path,
    monkeypatch,
):
    _runtime, snapshot = _runtime_snapshot(tmp_path, attested=False)
    _allow_fixture_runtime(monkeypatch, snapshot)
    api = _RunnerWfpApi()
    api.fail["filter_delete"] = ContainmentError(
        "dynamic cleanup uncertain"
    )
    comparison = run_evaluation(
        _plan_v2(tmp_path),
        run_id="containment-close-failure",
        raw_fn=lambda task, model, host, **_kwargs: _result(
            "raw", model, "ZeroDivisionError"
        ),
        stock_fn=lambda task, model, host, workspace, **_kwargs: _result(
            "stock", model, "ZeroDivisionError"
        ),
        lac_fn=lambda task, model, host, workspace, **_kwargs: _result(
            "lac", model, "ZeroDivisionError"
        ),
        mode=EvidenceMode.VERIFIED,
        containment_wfp_api=api,
        identity_capture_fn=lambda _: snapshot,
        identity_compare_fn=_passing_identity_compare,
        identity_lease_fn=_noop_identity_lease,
    )
    control = next(
        item
        for item in comparison["evidence"]["controls"]["results"]
        if item["name"] == "os_loopback_only_egress"
    )
    assert control["state"] == "fail"
    assert "cleanup uncertain" in control["reason"]


def test_verified_runner_attests_fresh_snapshot_before_lease_schedule_or_adapters(
    tmp_path,
    monkeypatch,
):
    plan = _plan_v2(tmp_path)
    _runtime, snapshot = _runtime_snapshot(tmp_path)
    snapshot = replace(
        snapshot,
        opencode=replace(snapshot.opencode, sha256="0" * 64),
    )
    called = []
    api = _RunnerWfpApi()
    monkeypatch.setattr(
        runner_module.LoopbackRecordingProxy,
        "open",
        lambda *_args, **_kwargs: (
            called.append("proxy"),
            (_ for _ in ()).throw(RuntimeError("proxy must not open")),
        )[1],
    )

    comparison = run_evaluation(
        plan,
        run_id="bootstrap-attestation-failure",
        raw_fn=lambda *_args, **_kwargs: called.append("raw"),
        stock_fn=lambda *_args, **_kwargs: called.append("stock"),
        lac_fn=lambda *_args, **_kwargs: called.append("lac"),
        mode=EvidenceMode.VERIFIED,
        containment_wfp_api=api,
        identity_capture_fn=lambda _: snapshot,
        identity_compare_fn=_passing_identity_compare,
        identity_lease_fn=lambda _snapshot: called.append("lease"),
    )

    assert called == []
    assert not (
        plan.output_root / "bootstrap-attestation-failure"
    ).exists()
    assert not (
        plan.output_root
        / "bootstrap-attestation-failure"
        / "schedule.json"
    ).exists()
    assert not (
        plan.output_root
        / "bootstrap-attestation-failure"
        / "http-observer.before.json"
    ).exists()
    assert comparison["runtime_bootstrap_attestation"]["ok"] is False


def test_verified_runner_attestation_exception_has_zero_side_effects(
    tmp_path,
    monkeypatch,
):
    plan = _plan_v2(tmp_path)
    _runtime, snapshot = _runtime_snapshot(tmp_path)
    called = []
    monkeypatch.setattr(
        runner_module,
        "attest_runtime_bootstrap",
        lambda *_args, **_kwargs: (
            called.append("attestation"),
            (_ for _ in ()).throw(RuntimeError("attestation crashed")),
        )[1],
    )
    monkeypatch.setattr(
        runner_module.LoopbackRecordingProxy,
        "open",
        lambda *_args, **_kwargs: called.append("proxy"),
    )

    comparison = run_evaluation(
        plan,
        run_id="attestation-exception",
        raw_fn=lambda *_args, **_kwargs: called.append("raw"),
        stock_fn=lambda *_args, **_kwargs: called.append("stock"),
        lac_fn=lambda *_args, **_kwargs: called.append("lac"),
        mode=EvidenceMode.VERIFIED,
        containment_wfp_api=_RunnerWfpApi(),
        identity_capture_fn=lambda _: snapshot,
        identity_compare_fn=_passing_identity_compare,
        identity_lease_fn=lambda _snapshot: called.append("lease"),
    )

    assert called == ["attestation"]
    assert not (plan.output_root / "attestation-exception").exists()
    assert comparison["evidence"]["artifact_valid"] is False


def test_verified_runner_attests_before_run_root_lease_schedule_and_proxy(
    tmp_path,
    monkeypatch,
):
    plan = _plan_v2(tmp_path)
    runtime, snapshot = _runtime_snapshot(tmp_path, attested=False)
    _allow_fixture_runtime(monkeypatch, snapshot)
    run_root = plan.output_root / "preflight-order"
    events = []
    real_attest = runtime_provenance_module.attest_runtime_bootstrap
    real_proxy_open = runner_module.LoopbackRecordingProxy.open
    real_build_schedule = runner_module.build_schedule

    def capture(_plan):
        assert not run_root.exists()
        events.append("capture")
        return snapshot

    def attest(*args, **kwargs):
        assert not run_root.exists()
        events.append("attest")
        return real_attest(*args, **kwargs)

    def lease(_snapshot):
        assert not run_root.exists()
        events.append("lease")
        return _NoopIdentityLease()

    def schedule(*args, **kwargs):
        assert not run_root.exists()
        events.append("schedule")
        return real_build_schedule(*args, **kwargs)

    def proxy_open(*args, **kwargs):
        events.append("proxy")
        return real_proxy_open(*args, **kwargs)

    monkeypatch.setattr(
        runner_module,
        "attest_runtime_bootstrap",
        attest,
    )
    monkeypatch.setattr(
        runner_module.LoopbackRecordingProxy,
        "open",
        proxy_open,
    )
    monkeypatch.setattr(
        runner_module,
        "build_schedule",
        schedule,
    )

    run_evaluation(
        plan,
        run_id="preflight-order",
        raw_fn=lambda *_args, **_kwargs: _result(
            "raw",
            "gpt-oss:20b",
            "ZeroDivisionError",
        ),
        stock_fn=lambda *_args, **_kwargs: _result(
            "stock",
            "gpt-oss:20b",
            "ZeroDivisionError",
        ),
        lac_fn=lambda *_args, **_kwargs: _result(
            "lac",
            "gpt-oss:20b-agent",
            "ZeroDivisionError",
        ),
        mode=EvidenceMode.VERIFIED,
        containment_wfp_api=_RunnerWfpApi(),
        identity_capture_fn=capture,
        identity_compare_fn=_passing_identity_compare,
        identity_lease_fn=lease,
    )

    assert events[:5] == [
        "capture",
        "attest",
        "lease",
        "schedule",
        "proxy",
    ]
    assert run_root.exists()
    runtime.write_bytes(b"cleanup")


def test_verified_runner_schedule_failure_closes_lease_before_side_effects(
    tmp_path,
    monkeypatch,
):
    plan = _plan_v2(tmp_path)
    _runtime, snapshot = _runtime_snapshot(tmp_path, attested=False)
    _allow_fixture_runtime(monkeypatch, snapshot)
    called = []

    class Lease:
        close_calls = 0

        def close(self):
            self.close_calls += 1

    lease = Lease()
    monkeypatch.setattr(
        runner_module,
        "build_schedule",
        lambda *_args, **_kwargs: (
            called.append("schedule"),
            (_ for _ in ()).throw(RuntimeError("schedule failed")),
        )[1],
    )
    monkeypatch.setattr(
        runner_module.LoopbackRecordingProxy,
        "open",
        lambda *_args, **_kwargs: called.append("proxy"),
    )

    comparison = run_evaluation(
        plan,
        run_id="schedule-failure",
        raw_fn=lambda *_args, **_kwargs: called.append("raw"),
        stock_fn=lambda *_args, **_kwargs: called.append("stock"),
        lac_fn=lambda *_args, **_kwargs: called.append("lac"),
        mode=EvidenceMode.VERIFIED,
        containment_wfp_api=_RunnerWfpApi(),
        identity_capture_fn=lambda _: snapshot,
        identity_compare_fn=_passing_identity_compare,
        identity_lease_fn=lambda _snapshot: lease,
    )

    assert comparison["evidence"]["artifact_valid"] is False
    assert called == ["schedule"]
    assert lease.close_calls == 1
    assert not (plan.output_root / "schedule-failure").exists()


def test_verified_runner_initial_write_failure_closes_observer_and_lease_once(
    tmp_path,
    monkeypatch,
):
    plan = _plan_v2(tmp_path)
    _runtime, snapshot = _runtime_snapshot(tmp_path, attested=False)
    _allow_fixture_runtime(monkeypatch, snapshot)
    called = []

    class Lease:
        close_calls = 0

        def close(self):
            self.close_calls += 1

    class Observer:
        endpoint = "http://127.0.0.1:54321"
        close_calls = 0

        def close(self):
            self.close_calls += 1

    lease = Lease()
    observer = Observer()
    monkeypatch.setattr(
        runner_module.LoopbackRecordingProxy,
        "open",
        lambda *_args, **_kwargs: observer,
    )
    monkeypatch.setattr(
        runner_module,
        "atomic_write_json",
        lambda *_args, **_kwargs: (
            called.append("write"),
            (_ for _ in ()).throw(OSError("initial write failed")),
        )[1],
    )

    with pytest.raises(OSError, match="initial write failed"):
        run_evaluation(
            plan,
            run_id="write-failure",
            raw_fn=lambda *_args, **_kwargs: called.append("raw"),
            stock_fn=lambda *_args, **_kwargs: called.append("stock"),
            lac_fn=lambda *_args, **_kwargs: called.append("lac"),
            mode=EvidenceMode.VERIFIED,
            containment_wfp_api=_RunnerWfpApi(),
            identity_capture_fn=lambda _: snapshot,
            identity_compare_fn=_passing_identity_compare,
            identity_lease_fn=lambda _snapshot: lease,
        )

    assert called == ["write"]
    assert observer.close_calls == 1
    assert lease.close_calls == 1


def test_verified_runner_rebind_and_observer_close_failure_still_closes_lease(
    tmp_path,
    monkeypatch,
):
    plan = _plan_v2(tmp_path)
    _runtime, snapshot = _runtime_snapshot(tmp_path, attested=False)
    _allow_fixture_runtime(monkeypatch, snapshot)
    called = []

    class Lease:
        close_calls = 0

        def close(self):
            self.close_calls += 1

    class Observer:
        endpoint = "http://127.0.0.1:54321"
        close_calls = 0

        def close(self):
            self.close_calls += 1
            raise RuntimeError("observer close failed")

    lease = Lease()
    observer = Observer()
    monkeypatch.setattr(
        runner_module.LoopbackRecordingProxy,
        "open",
        lambda *_args, **_kwargs: observer,
    )
    monkeypatch.setattr(
        runner_module,
        "rebind_config_manifest",
        lambda *_args, **_kwargs: (
            called.append("rebind"),
            (_ for _ in ()).throw(ValueError("rebind failed")),
        )[1],
    )

    with pytest.raises(RuntimeError, match="observer close failed"):
        run_evaluation(
            plan,
            run_id="rebind-close-failure",
            raw_fn=lambda *_args, **_kwargs: called.append("raw"),
            stock_fn=lambda *_args, **_kwargs: called.append("stock"),
            lac_fn=lambda *_args, **_kwargs: called.append("lac"),
            mode=EvidenceMode.VERIFIED,
            containment_wfp_api=_RunnerWfpApi(),
            identity_capture_fn=lambda _: snapshot,
            identity_compare_fn=_passing_identity_compare,
            identity_lease_fn=lambda _snapshot: lease,
        )

    assert called == ["rebind"]
    assert observer.close_calls == 1
    assert lease.close_calls == 1
    assert not (plan.output_root / "rebind-close-failure").exists()


def test_verified_runner_real_attestation_then_rejects_target_replacement(
    tmp_path,
    monkeypatch,
):
    plan = _plan_v2(tmp_path)
    runtime, snapshot = _runtime_snapshot(tmp_path, attested=False)
    _allow_fixture_runtime(monkeypatch, snapshot)
    runtime.write_bytes(b"replaced-after-attestation-snapshot")
    called = []
    monkeypatch.setattr(
        runner_module.LoopbackRecordingProxy,
        "open",
        lambda *_args, **_kwargs: called.append("proxy"),
    )

    comparison = run_evaluation(
        plan,
        run_id="target-replacement",
        raw_fn=lambda *_args, **_kwargs: called.append("raw"),
        stock_fn=lambda *_args, **_kwargs: called.append("stock"),
        lac_fn=lambda *_args, **_kwargs: called.append("lac"),
        mode=EvidenceMode.VERIFIED,
        containment_wfp_api=_RunnerWfpApi(),
        identity_capture_fn=lambda _: snapshot,
        identity_compare_fn=_passing_identity_compare,
        identity_lease_fn=identity_module.acquire_runtime_identity_leases,
    )

    assert comparison["runtime_bootstrap_attestation"]["ok"] is True
    assert called == []
    assert not (
        plan.output_root
        / "target-replacement"
        / "http-observer.before.json"
    ).exists()
    runtime_control = next(
        item
        for item in comparison["identity_controls"]
        if item["name"] == "runtime_dependency_provenance"
    )
    assert runtime_control["state"] == "fail"
    assert "lease does not match captured identity" in runtime_control["reason"]


def test_verified_runner_binds_all_six_default_opencode_configs_to_proxy_endpoint(
    tmp_path,
    monkeypatch,
):
    plan = _plan_v2(tmp_path)
    _runtime, snapshot = _runtime_snapshot(tmp_path, attested=False)
    _allow_fixture_runtime(monkeypatch, snapshot)
    process_calls = []

    def successful_process(*_args, **_kwargs):
        process_calls.append(True)
        return opencode_module._ProcessOutcome(
            0,
            (
                '{"type":"text","part":{"text":"ZeroDivisionError"}}\n'
                '{"type":"step_finish","part":{"reason":"stop",'
                '"sessionID":"session-1"}}\n'
            ),
            "",
            False,
            (),
            b"",
            b"",
            {},
        )

    monkeypatch.setattr(
        opencode_module,
        "_run_process_outcome",
        successful_process,
    )

    comparison = run_evaluation(
        plan,
        run_id="six-config-bindings",
        raw_fn=lambda task, model, host, **_kwargs: _result(
            "raw",
            model,
            "ZeroDivisionError",
        ),
        stock_fn=opencode_module.run_stock,
        lac_fn=opencode_module.run_lac,
        mode=EvidenceMode.VERIFIED,
        containment_wfp_api=_RunnerWfpApi(),
        identity_capture_fn=lambda _: snapshot,
        identity_compare_fn=_passing_identity_compare,
        identity_lease_fn=_noop_identity_lease,
    )

    assert process_calls == [True] * 6
    bindings = {
        f"{item['trial_index']:03d}/{item['arm']}": item
        for item in comparison[
            "runtime_bootstrap_attestation"
        ]["runtime_config_bindings"]
    }
    configs = comparison["opencode_config_identities"]
    assert set(bindings) == {
        f"{trial_index:03d}/{arm}"
        for trial_index in (1, 2, 3)
        for arm in ("stock", "lac")
    }
    assert set(configs) == set(bindings)
    for key, measured in configs.items():
        assert measured["before"] == measured["after"]
        assert measured["before"]["canonical_sha256"] == (
            bindings[key]["expected_canonical_sha256"]
        )
