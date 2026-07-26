from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from backend.agent_eval.opencode import (
    parse_opencode_jsonl,
    run_lac,
    run_stock,
)
from backend.agent_eval.task import EvalScorer, EvalTask


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
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 180,
        "env": captured["kwargs"]["env"],
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
