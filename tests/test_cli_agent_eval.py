from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import cli
import server
from backend.agent_eval.command import EvalCommandResult


def _argv(*extra: str) -> list[str]:
    return [
        "eval",
        "--task",
        "python-empty-mean",
        "--base-model",
        "gpt-oss:20b",
        "--lac-model",
        "gpt-oss:20b-agent",
        "--output-dir",
        r"C:\evidence",
        *extra,
    ]


def test_parser_exposes_eval_verified_default_and_diagnostic_opt_in():
    parser = cli.build_parser()
    verified = parser.parse_args([*_argv(), "--dry-run"])
    assert verified.command == "eval"
    assert verified.mode == "verified"

    diagnostic = parser.parse_args([*_argv(), "--mode", "diagnostic"])
    assert diagnostic.mode == "diagnostic"


def test_server_routes_eval_to_cli():
    assert server._is_cli_invocation(["eval", "--dry-run"]) is True


def test_json_mode_emits_no_banner(monkeypatch, capsys):
    monkeypatch.setattr(
        "backend.agent_eval.command.execute_eval_command",
        lambda _request: EvalCommandResult(
            exit_code=0,
            report={"ok": True, "evidence_ready": True},
        ),
    )
    monkeypatch.setattr(cli.sys, "argv", ["lac", *_argv("--dry-run", "--json")])

    assert cli.main() == 0

    output = capsys.readouterr().out
    assert json.loads(output)["evidence_ready"] is True
    assert "Local AI, sorted" not in output


def test_nonzero_eval_service_result_becomes_process_exit_code(
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        "backend.agent_eval.command.execute_eval_command",
        lambda _request: EvalCommandResult(
            exit_code=2,
            report={"ok": False, "error": "preflight stopped"},
        ),
    )
    monkeypatch.setattr(cli.sys, "argv", ["lac", *_argv("--dry-run", "--json")])

    with pytest.raises(SystemExit) as raised:
        cli.main()

    assert raised.value.code == 2
    assert json.loads(capsys.readouterr().out)["error"] == "preflight stopped"
