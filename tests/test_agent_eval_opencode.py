from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.agent_eval.opencode as opencode_module
from backend.agent_eval.opencode import (
    _lock_config,
    parse_opencode_jsonl,
    run_lac,
    run_stock,
)
from backend.agent_eval.capture import (
    DEFAULT_CAPTURE_LIMITS,
    CapturedProcess,
)
from backend.agent_eval.task import EvalScorer, EvalTask
from backend.agent_eval.schedule import GenerationSettings, TrialSpec


def _task(workspace: Path) -> EvalTask:
    (workspace / "stats_service.py").write_text(
        "def summarize(values):\n"
        "    return sum(values) / len(values)\n",
        encoding="utf-8",
    )
    return EvalTask(
        schema_version=1,
        id="python-empty-mean",
        prompt="Inspect the fixture and answer only the exception class.",
        fixture_root=workspace,
        timeout_seconds=180,
        scorer=EvalScorer(type="exact_text", expected="ZeroDivisionError"),
    )


def _jsonl(*events) -> str:
    return "\n".join(json.dumps(event) for event in events)


def _successful_stdout() -> str:
    return _jsonl(
        {
            "type": "step_start",
            "sessionID": "ses_1",
            "part": {"type": "step-start"},
        },
        {
            "type": "tool_use",
            "sessionID": "ses_1",
            "part": {
                "type": "tool",
                "tool": "read",
                "state": {"status": "completed"},
            },
        },
        {
            "type": "text",
            "sessionID": "ses_1",
            "part": {"type": "text", "text": "ZeroDivisionError"},
        },
        {
            "type": "step_finish",
            "sessionID": "ses_1",
            "part": {
                "type": "step-finish",
                "reason": "stop",
                "cost": 0,
                "tokens": {
                    "input": 100,
                    "output": 4,
                    "reasoning": 2,
                    "cache": {"read": 10, "write": 1},
                },
            },
        },
    )


def _assert_config_identity_complete_and_released(result):
    measured = result.metrics["opencode_config_identity"]
    assert isinstance(measured["before"], dict)
    assert isinstance(measured["after"], dict)
    assert measured["before"] == measured["after"]
    config_path = Path(measured["after"]["path"])
    config_path.write_text(
        config_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )


def test_parse_opencode_jsonl_extracts_text_usage_tools_and_unknown_events():
    stdout = _successful_stdout() + "\n" + json.dumps(
        {"type": "future_event", "part": {}}
    )

    parsed = parse_opencode_jsonl(stdout)

    assert parsed.response == "ZeroDivisionError"
    assert parsed.completed is True
    assert parsed.session_id == "ses_1"
    assert parsed.metrics == {
        "input_tokens": 100,
        "output_tokens": 4,
        "reasoning_tokens": 2,
        "cache_read_tokens": 10,
        "cache_write_tokens": 1,
        "cost_usd": 0.0,
        "tool_calls": 1,
        "tool_errors": 0,
    }
    assert parsed.unknown_event_types == ("future_event",)
    assert parsed.errors == ()
    assert len(parsed.events) == 5


def test_parse_opencode_jsonl_aggregates_multiple_steps():
    first = json.loads(_successful_stdout().splitlines()[-1])
    first["part"]["reason"] = "tool-calls"
    second = {
        "type": "step_finish",
        "sessionID": "ses_1",
        "part": {
            "type": "step-finish",
            "reason": "stop",
            "cost": 0.25,
            "tokens": {
                "input": 7,
                "output": 3,
                "reasoning": 0,
                "cache": {"read": 2, "write": 0},
            },
        },
    }

    parsed = parse_opencode_jsonl(
        _jsonl(
            {"type": "text", "sessionID": "ses_1", "part": {"text": "final"}},
            first,
            second,
        )
    )

    assert parsed.completed is True
    assert parsed.metrics["input_tokens"] == 107
    assert parsed.metrics["output_tokens"] == 7
    assert parsed.metrics["cost_usd"] == 0.25


def test_parse_opencode_jsonl_scores_only_terminal_step_text():
    parsed = parse_opencode_jsonl(
        _jsonl(
            {"type": "step_start", "sessionID": "ses_1", "part": {}},
            {
                "type": "text",
                "sessionID": "ses_1",
                "part": {"text": "I will inspect the file."},
            },
            {
                "type": "step_finish",
                "sessionID": "ses_1",
                "part": {"reason": "tool-calls", "tokens": {}},
            },
            {"type": "step_start", "sessionID": "ses_1", "part": {}},
            {
                "type": "text",
                "sessionID": "ses_1",
                "part": {"text": "ZeroDivisionError"},
            },
            {
                "type": "step_finish",
                "sessionID": "ses_1",
                "part": {"reason": "stop", "tokens": {}},
            },
        )
    )

    assert parsed.completed is True
    assert parsed.response == "ZeroDivisionError"


def test_parse_opencode_jsonl_counts_invalid_tool_as_tool_error():
    parsed = parse_opencode_jsonl(
        _jsonl(
            {
                "type": "tool_use",
                "part": {
                    "tool": "invalid",
                    "state": {"status": "completed"},
                },
            },
            {"type": "text", "part": {"text": "answer"}},
            {
                "type": "step_finish",
                "part": {"reason": "stop", "tokens": {}},
            },
        )
    )

    assert parsed.metrics["tool_calls"] == 1
    assert parsed.metrics["tool_errors"] == 1


def test_parse_opencode_jsonl_marks_malformed_stream_failed():
    parsed = parse_opencode_jsonl(
        '{"type":"text","part":{"text":"answer"}}\nnot-json\n'
        '{"type":"step_finish","part":{"reason":"stop","tokens":{}}}'
    )

    assert parsed.response == "answer"
    assert parsed.completed is False
    assert parsed.errors == ("malformed_json_line:2",)


def test_parse_opencode_jsonl_records_error_event():
    parsed = parse_opencode_jsonl(
        _jsonl(
            {"type": "error", "error": {"name": "ProviderError", "message": "boom"}}
        )
    )

    assert parsed.completed is False
    assert parsed.errors == ("opencode_error: ProviderError: boom",)


def test_parse_opencode_jsonl_fails_on_line_byte_ceiling():
    parsed = parse_opencode_jsonl(
        b'{"type":"text","part":{"text":"' + b"x" * 64 + b'"}}\n',
        max_line_bytes=32,
        max_events=10,
    )

    assert parsed.completed is False
    assert parsed.errors == ("jsonl_line_limit_exceeded:1",)
    assert parsed.events == ()


def test_parse_opencode_jsonl_counts_whitespace_in_physical_line_ceiling():
    parsed = parse_opencode_jsonl(
        b" " * 64 + b'{"type":"reasoning","part":{}}' + b" " * 64 + b"\n",
        max_line_bytes=64,
        max_events=10,
    )

    assert parsed.completed is False
    assert parsed.errors == ("jsonl_line_limit_exceeded:1",)
    assert parsed.events == ()


def test_parse_opencode_jsonl_fails_on_event_ceiling():
    parsed = parse_opencode_jsonl(
        b'{"type":"reasoning","part":{}}\n' * 4,
        max_line_bytes=256,
        max_events=3,
    )

    assert parsed.completed is False
    assert parsed.errors == ("jsonl_event_limit_exceeded:4",)
    assert len(parsed.events) == 3


def test_run_stock_writes_minimal_config_and_exact_bounded_argv(tmp_path):
    workspace = tmp_path / "stock"
    workspace.mkdir()
    task = _task(workspace)
    captured = {}

    def run(argv, **kwargs):
        captured.update(argv=argv, kwargs=kwargs)
        return SimpleNamespace(returncode=0, stdout=_successful_stdout(), stderr="")

    result = run_stock(
        task,
        "gpt-oss:20b",
        "http://localhost:11434",
        workspace,
        resolve_bin_fn=lambda: Path(r"C:\tools\opencode.cmd"),
        run_fn=run,
    )

    assert captured["argv"] == [
        r"C:\tools\opencode.cmd",
        "run",
        task.prompt,
        "--format",
        "json",
        "--pure",
        "--auto",
        "--model",
        "ollama/gpt-oss:20b",
        "--dir",
        str(workspace.resolve()),
    ]
    assert captured["kwargs"] == {
        "cwd": str(workspace.resolve()),
        "timeout": 180,
        "env": captured["kwargs"]["env"],
        "limits": DEFAULT_CAPTURE_LIMITS,
    }
    env = captured["kwargs"]["env"]
    config_path = Path(env["OPENCODE_CONFIG"])
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert workspace not in config_path.parents
    assert config["permission"] == {"external_directory": "deny"}
    assert config["tools"] == {
        "*": False,
        "read": True,
        "glob": True,
        "grep": True,
    }
    assert env["OPENCODE_DISABLE_AUTOUPDATE"] == "1"
    assert env["OPENCODE_DISABLE_CLAUDE_CODE"] == "1"
    assert env["OPENCODE_AUTO_SHARE"] == "false"
    assert "ANTHROPIC_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert result.arm == "stock"
    assert result.completed is True
    assert result.response == "ZeroDivisionError"
    assert result.metrics["approval_mode"] == "auto_disposable_workspace"
    assert result.exit_code == 0
    assert result.raw_stdout == _successful_stdout()
    assert result.raw_stderr == ""
    assert result.request_metadata == {}


def test_opencode_1184_http_capture_proves_build_sampling_reaches_ollama(
    tmp_path,
):
    workspace = tmp_path / "stock"
    workspace.mkdir()
    generation = GenerationSettings(1.0, 20260726, 128)
    trial = TrialSpec(1, 1209934845, ("raw", "stock", "lac"))
    captured = {}

    def compatible_opencode_1184(argv, **kwargs):
        config = json.loads(
            Path(kwargs["env"]["OPENCODE_CONFIG"]).read_text(
                encoding="utf-8"
            )
        )
        build = config["agent"]["build"]
        outgoing = {
            "model": "gpt-oss:20b",
            "temperature": build["temperature"],
            "seed": build["options"]["seed"],
            "max_tokens": build["options"]["max_tokens"],
        }
        captured.update(
            path="/v1/chat/completions",
            body=outgoing,
        )
        return SimpleNamespace(
            returncode=0,
            stdout=_successful_stdout(),
            stderr="",
            ollama_http_capture={
                "path": "/v1/chat/completions",
                "body": outgoing,
            },
        )

    result = run_stock(
        _task(workspace),
        "gpt-oss:20b",
        "http://localhost:11434",
        workspace,
        generation=generation,
        trial=trial,
        resolve_bin_fn=lambda: Path(r"C:\tools\opencode.cmd"),
        run_fn=compatible_opencode_1184,
    )

    assert captured == {
        "path": "/v1/chat/completions",
        "body": {
            "model": "gpt-oss:20b",
            "temperature": 1.0,
            "seed": 1209934845,
            "max_tokens": 128,
        },
    }
    assert result.request_metadata == {
        "source": "opencode_1.18.4_ollama_http_capture",
        "observed": True,
        "path": "/v1/chat/completions",
        "temperature": 1.0,
        "seed": 1209934845,
        "max_output_tokens": 128,
        "trial_index": 1,
    }


def test_run_lac_uses_fail_closed_config_and_agent_variant(tmp_path):
    workspace = tmp_path / "lac"
    workspace.mkdir()
    task = _task(workspace)

    result = run_lac(
        task,
        "gpt-oss:20b-agent",
        "http://localhost:11434",
        workspace,
        resolve_bin_fn=lambda: Path("opencode"),
        run_fn=lambda *_a, **_kw: SimpleNamespace(
            returncode=0, stdout=_successful_stdout(), stderr=""
        ),
    )

    config_dirs = list((workspace.parent / ".lac-eval-runtime-lac").glob("**/opencode.json"))
    assert len(config_dirs) == 1
    config = json.loads(config_dirs[0].read_text(encoding="utf-8"))
    assert config["model"] == "ollama/gpt-oss:20b-agent"
    assert config["permission"]["*"] == "ask"
    assert config["permission"]["external_directory"] == "deny"
    assert config["permission"]["task"] == "deny"
    assert config["tools"] == {
        "*": False,
        "read": True,
        "glob": True,
        "grep": True,
    }
    assert not (workspace / ".opencode").exists()
    assert result.arm == "lac"
    assert result.model == "gpt-oss:20b-agent"


@pytest.mark.parametrize("arm,model", [("stock", "gpt-oss:20b"), ("lac", "gpt-oss:20b-agent")])
def test_real_generated_config_is_measured_before_and_after_process(tmp_path, arm, model):
    workspace = tmp_path / arm
    workspace.mkdir()
    runner = run_stock if arm == "stock" else run_lac
    result = runner(
        _task(workspace), model, "http://localhost:11434", workspace,
        resolve_bin_fn=lambda: Path("opencode"),
        run_fn=lambda *_a, **_kw: SimpleNamespace(returncode=0, stdout=_successful_stdout(), stderr=""),
    )
    measured = result.metrics["opencode_config_identity"]
    assert measured["before"] == measured["after"]
    assert Path(measured["before"]["path"]).is_file()
    assert measured["before"]["size"] > 0
    assert len(measured["before"]["sha256"]) == 64


def test_real_generated_config_cannot_be_mutated_deleted_or_replaced_during_process(
    tmp_path,
):
    workspace = tmp_path / "stock"
    workspace.mkdir()

    denied = []

    def attempt_mutation(_argv, **kwargs):
        config_path = Path(kwargs["env"]["OPENCODE_CONFIG"])
        original = config_path.read_text(encoding="utf-8")
        replacement = config_path.with_suffix(".replacement")
        replacement.write_text('{"model":"ollama/attacker"}', encoding="utf-8")
        for operation in (
            lambda: config_path.write_text(
                '{"model":"ollama/attacker"}',
                encoding="utf-8",
            ),
            config_path.unlink,
            lambda: replacement.replace(config_path),
        ):
            with pytest.raises(OSError):
                operation()
            denied.append(True)
        assert config_path.read_text(encoding="utf-8") == original
        return SimpleNamespace(returncode=0, stdout=_successful_stdout(), stderr="")

    result = run_stock(
        _task(workspace),
        "gpt-oss:20b",
        "http://localhost:11434",
        workspace,
        resolve_bin_fn=lambda: Path("opencode"),
        run_fn=attempt_mutation,
    )
    measured = result.metrics["opencode_config_identity"]
    assert denied == [True, True, True]
    assert measured["before"] == measured["after"]
    assert result.completed is True


def test_binary_resolution_finishes_before_config_lock_is_acquired(tmp_path):
    workspace = tmp_path / "stock"
    workspace.mkdir()
    config_path = workspace.parent / ".lac-eval-runtime-stock" / "opencode.json"

    def resolve():
        original = config_path.read_text(encoding="utf-8")
        config_path.write_text(original, encoding="utf-8")
        return Path("opencode")

    result = run_stock(
        _task(workspace),
        "gpt-oss:20b",
        "http://localhost:11434",
        workspace,
        resolve_bin_fn=resolve,
        run_fn=lambda *_a, **_kw: SimpleNamespace(
            returncode=0,
            stdout=_successful_stdout(),
            stderr="",
        ),
    )

    assert result.completed is True
    _assert_config_identity_complete_and_released(result)


def test_lock_config_reports_closehandle_failure_during_fd_transfer(
    tmp_path, monkeypatch
):
    config_path = tmp_path / "opencode.json"
    config_path.write_text("{}", encoding="utf-8")

    class FakeFunction:
        def __init__(self, result):
            self.result = result
            self.calls = []
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            self.calls.append(args)
            return self.result

    class FakeKernel32:
        CreateFileW = FakeFunction(123)
        CloseHandle = FakeFunction(0)

    kernel32 = FakeKernel32()
    monkeypatch.setattr(
        opencode_module.ctypes,
        "WinDLL",
        lambda *_args, **_kwargs: kernel32,
    )
    monkeypatch.setattr(
        opencode_module.ctypes,
        "get_last_error",
        lambda: 6,
    )

    def fail_transfer(*_args, **_kwargs):
        raise RuntimeError("fd transfer failed")

    monkeypatch.setattr(opencode_module.msvcrt, "open_osfhandle", fail_transfer)

    with pytest.raises(OSError, match="close"):
        _lock_config(config_path)

    create_args = kernel32.CreateFileW.calls[0]
    assert create_args[1] == 0x80000000
    assert create_args[2] == 0x00000001
    assert create_args[4] == 3
    assert create_args[5] == 0x00200080
    assert len(kernel32.CloseHandle.calls) == 1


def test_config_lock_failure_returns_fail_closed_identity_metrics(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "stock"
    workspace.mkdir()
    monkeypatch.setattr(
        opencode_module,
        "_lock_config",
        lambda _path: (_ for _ in ()).throw(OSError("lock denied")),
    )

    result = run_stock(
        _task(workspace),
        "gpt-oss:20b",
        "http://localhost:11434",
        workspace,
        resolve_bin_fn=lambda: Path("opencode"),
        run_fn=lambda *_a, **_kw: pytest.fail("process must not run"),
    )

    assert result.completed is False
    assert "lock denied" in result.errors[0]
    assert result.metrics["opencode_config_identity"] == {
        "before": None,
        "after": None,
    }


def test_pre_measurement_failure_prevents_process_and_releases_lock(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "stock"
    workspace.mkdir()
    calls = []

    def fail_measurement(_fd, _path):
        calls.append("measure")
        raise OSError("measurement denied")

    monkeypatch.setattr(opencode_module, "_config_identity", fail_measurement)

    result = run_stock(
        _task(workspace),
        "gpt-oss:20b",
        "http://localhost:11434",
        workspace,
        resolve_bin_fn=lambda: Path("opencode"),
        run_fn=lambda *_a, **_kw: pytest.fail("process must not run"),
    )

    assert result.completed is False
    assert calls == ["measure", "measure"]
    assert result.metrics["opencode_config_identity"] == {
        "before": None,
        "after": None,
    }
    assert result.errors[0].startswith("config_pre_measurement_failed:")
    assert result.errors[1].startswith("config_post_measurement_failed:")
    config_path = workspace.parent / ".lac-eval-runtime-stock" / "opencode.json"
    config_path.write_text(
        config_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )


def test_post_measurement_failure_invalidates_success_and_releases_lock(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "stock"
    workspace.mkdir()
    real_identity = opencode_module._config_identity
    calls = 0

    def fail_post(fd, path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("post measurement denied")
        return real_identity(fd, path)

    monkeypatch.setattr(opencode_module, "_config_identity", fail_post)

    result = run_stock(
        _task(workspace),
        "gpt-oss:20b",
        "http://localhost:11434",
        workspace,
        resolve_bin_fn=lambda: Path("opencode"),
        run_fn=lambda *_a, **_kw: SimpleNamespace(
            returncode=0,
            stdout=_successful_stdout(),
            stderr="",
        ),
    )

    assert result.completed is False
    assert result.errors == (
        "config_post_measurement_failed:OSError: post measurement denied",
    )
    assert isinstance(
        result.metrics["opencode_config_identity"]["before"],
        dict,
    )
    assert result.metrics["opencode_config_identity"]["after"] is None
    config_path = workspace.parent / ".lac-eval-runtime-stock" / "opencode.json"
    config_path.write_text(
        config_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )


def test_run_opencode_records_nonzero_exit_and_stderr(tmp_path):
    workspace = tmp_path / "stock"
    workspace.mkdir()

    result = run_stock(
        _task(workspace),
        "gpt-oss:20b",
        "http://localhost:11434",
        workspace,
        resolve_bin_fn=lambda: Path("opencode"),
        run_fn=lambda *_a, **_kw: SimpleNamespace(
            returncode=7, stdout="", stderr="provider failed"
        ),
    )

    assert result.completed is False
    assert result.exit_code == 7
    assert result.raw_stderr == "provider failed"
    assert result.errors == ("exit_code:7",)
    _assert_config_identity_complete_and_released(result)


def test_run_opencode_persists_exact_capture_overflow_reason(tmp_path):
    workspace = tmp_path / "stock"
    workspace.mkdir()

    result = run_stock(
        _task(workspace),
        "gpt-oss:20b",
        "http://localhost:11434",
        workspace,
        resolve_bin_fn=lambda: Path("opencode"),
        run_fn=lambda *_a, **_kw: CapturedProcess(
            exit_code=1,
            stdout="x" * 128,
            stderr="",
            completed=False,
            timed_out=False,
            overflowed=True,
            observed_stdout_bytes=129,
            observed_stderr_bytes=0,
            limits=DEFAULT_CAPTURE_LIMITS,
            errors=("stdout_capture_limit_exceeded",),
            temporary_paths=(),
            raw_stdout=b"x" * 128,
            raw_stderr=b"",
        ),
        capture_limits=opencode_module.CaptureLimits(
            stdout_bytes=128,
            stderr_bytes=128,
        ),
    )

    assert result.completed is False
    assert result.errors == ("stdout_capture_limit_exceeded",)
    assert result.capture["stdout"] == {
        "allowed_bytes": 128,
        "observed_bytes": 129,
        "overflowed": True,
    }
    _assert_config_identity_complete_and_released(result)


def test_run_opencode_preserves_measured_windows_job_evidence(tmp_path):
    workspace = tmp_path / "stock"
    workspace.mkdir()
    containment = {
        "real_windows_job": True,
        "assignment_proven": True,
        "active_process_limit": 1,
        "memory_limit_bytes": None,
        "kill_on_close": True,
        "resume_after_assignment": True,
        "final_active_processes": 0,
        "handles_closed": True,
        "cleanup_certain": True,
    }
    raw = _successful_stdout().encode("utf-8")

    result = run_stock(
        _task(workspace),
        "gpt-oss:20b",
        "http://localhost:11434",
        workspace,
        resolve_bin_fn=lambda: Path("opencode"),
        run_fn=lambda *_a, **_kw: CapturedProcess(
            exit_code=0,
            stdout=raw.decode("utf-8"),
            stderr="",
            completed=True,
            timed_out=False,
            overflowed=False,
            observed_stdout_bytes=len(raw),
            observed_stderr_bytes=0,
            limits=DEFAULT_CAPTURE_LIMITS,
            errors=(),
            temporary_paths=(),
            raw_stdout=raw,
            raw_stderr=b"",
            containment=containment,
        ),
    )

    assert result.completed is True
    assert result.capture["windows_job_measured"] is True
    assert result.capture["windows_job"] == containment
    _assert_config_identity_complete_and_released(result)


def test_run_opencode_records_timeout(tmp_path):
    workspace = tmp_path / "lac"
    workspace.mkdir()

    def run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("opencode", 180, output="partial", stderr="slow")

    result = run_lac(
        _task(workspace),
        "gpt-oss:20b-agent",
        "http://localhost:11434",
        workspace,
        resolve_bin_fn=lambda: Path("opencode"),
        run_fn=run,
    )

    assert result.completed is False
    assert result.timed_out is True
    assert result.exit_code is None
    assert result.raw_stdout == "partial"
    assert result.raw_stderr == "slow"
    assert result.errors == ("timeout:180s",)
    _assert_config_identity_complete_and_released(result)


def test_run_opencode_records_process_exception_with_post_identity_and_releases_lock(
    tmp_path,
):
    workspace = tmp_path / "stock"
    workspace.mkdir()

    def run(*_args, **_kwargs):
        raise RuntimeError("process launch failed")

    result = run_stock(
        _task(workspace),
        "gpt-oss:20b",
        "http://localhost:11434",
        workspace,
        resolve_bin_fn=lambda: Path("opencode"),
        run_fn=run,
    )

    assert result.completed is False
    assert result.timed_out is False
    assert result.exit_code is None
    assert result.errors == ("RuntimeError: process launch failed",)
    _assert_config_identity_complete_and_released(result)
