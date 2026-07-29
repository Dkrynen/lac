from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("msvcrt", reason="Windows-only eval infrastructure")

import backend.plugins as plugins_module
import cli
import server

from backend.agent_eval.command import EvalCommandResult

from backend.plugins import LoadedPlugin

_LOOPBACK_HOST = "http://127.0.0.1:11434"
_GLOBAL_HOST_FORMS = (
    ("--host", _LOOPBACK_HOST),
    (f"--host={_LOOPBACK_HOST}",),
    ("--hos", _LOOPBACK_HOST),
    (f"--hos={_LOOPBACK_HOST}",),
    ("--ho", _LOOPBACK_HOST),
    (f"--ho={_LOOPBACK_HOST}",),
)

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

@pytest.mark.parametrize("use_explicit_argv", [False, True])
def test_eval_json_bypasses_plugin_discovery_before_elevation_failure(
    monkeypatch,
    capsys,
    use_explicit_argv,
):
    calls = []

    def register_cli(_subparsers):
        calls.append("register_cli")
        print("PLUGIN_NOISE")

    plugin = SimpleNamespace(
        name="noisy",
        version="1.0",
        register_cli=register_cli,
    )

    def discover():
        calls.append("discover")
        return [LoadedPlugin("noisy", "1.0", plugin)]

    remediation = (
        "Verified Windows network containment requires an elevated terminal.\n"
        "Reopen PowerShell as Administrator and rerun:\n"
        r"C:\LAC\lac.exe eval --dry-run --json"
    )
    monkeypatch.setattr(plugins_module, "discover", discover)
    monkeypatch.setattr(
        "backend.agent_eval.command.execute_eval_command",
        lambda _request: EvalCommandResult(
            exit_code=2,
            report={
                "artifact_valid": False,
                "evidence_ready": False,
                "error": remediation,
                "ok": False,
            },
        ),
    )
    arguments = _argv("--dry-run", "--json")
    if not use_explicit_argv:
        monkeypatch.setattr(cli.sys, "argv", ["lac", *arguments])

    with pytest.raises(SystemExit) as raised:
        cli.main(arguments if use_explicit_argv else None)

    assert raised.value.code == 2
    assert calls == []
    assert json.loads(capsys.readouterr().out) == {
        "artifact_valid": False,
        "evidence_ready": False,
        "error": remediation,
        "ok": False,
    }

@pytest.mark.parametrize("use_explicit_argv", [False, True])
def test_eval_help_bypasses_plugin_discovery(
    monkeypatch,
    capsys,
    use_explicit_argv,
):
    calls = []
    monkeypatch.setattr(
        plugins_module,
        "discover",
        lambda: calls.append("discover"),
    )
    arguments = ["eval", "--help"]
    if not use_explicit_argv:
        monkeypatch.setattr(cli.sys, "argv", ["lac", *arguments])

    with pytest.raises(SystemExit) as raised:
        cli.main(arguments if use_explicit_argv else None)

    assert raised.value.code == 0
    assert calls == []
    output = capsys.readouterr().out
    assert output.startswith("usage: lac eval")
    assert "--dry-run" in output

@pytest.mark.parametrize("global_prefix", _GLOBAL_HOST_FORMS)
@pytest.mark.parametrize("use_explicit_argv", [False, True])
def test_eval_json_after_global_host_bypasses_plugins_and_stays_parseable(
    monkeypatch,
    capsys,
    global_prefix,
    use_explicit_argv,
):
    calls = []

    def register_cli(_subparsers):
        calls.append("register_cli")
        print("PLUGIN_NOISE")

    plugin = SimpleNamespace(
        name="noisy",
        version="1.0",
        register_cli=register_cli,
    )
    monkeypatch.setattr(
        plugins_module,
        "discover",
        lambda: calls.append("discover")
        or [LoadedPlugin("noisy", "1.0", plugin)],
    )
    remediation = (
        "Verified Windows network containment requires an elevated terminal.\n"
        "Reopen PowerShell as Administrator and rerun:\n"
        r"C:\LAC\lac.exe eval --dry-run --json"
    )
    monkeypatch.setattr(
        "backend.agent_eval.command.execute_eval_command",
        lambda _request: EvalCommandResult(
            exit_code=2,
            report={
                "artifact_valid": False,
                "evidence_ready": False,
                "error": remediation,
                "ok": False,
            },
        ),
    )
    monkeypatch.setenv("OLLAMA_HOST", "before-eval")
    arguments = [*global_prefix, *_argv("--dry-run", "--json")]
    if not use_explicit_argv:
        monkeypatch.setattr(cli.sys, "argv", ["lac", *arguments])

    with pytest.raises(SystemExit) as raised:
        cli.main(arguments if use_explicit_argv else None)

    assert raised.value.code == 2
    assert calls == []
    assert json.loads(capsys.readouterr().out) == {
        "artifact_valid": False,
        "evidence_ready": False,
        "error": remediation,
        "ok": False,
    }
    assert cli.os.environ["OLLAMA_HOST"] == _LOOPBACK_HOST

@pytest.mark.parametrize("global_prefix", _GLOBAL_HOST_FORMS)
@pytest.mark.parametrize("use_explicit_argv", [False, True])
def test_eval_help_after_global_host_bypasses_plugin_discovery(
    monkeypatch,
    capsys,
    global_prefix,
    use_explicit_argv,
):
    calls = []
    monkeypatch.setattr(
        plugins_module,
        "discover",
        lambda: calls.append("discover"),
    )
    arguments = [*global_prefix, "eval", "--help"]
    if not use_explicit_argv:
        monkeypatch.setattr(cli.sys, "argv", ["lac", *arguments])

    with pytest.raises(SystemExit) as raised:
        cli.main(arguments if use_explicit_argv else None)

    assert raised.value.code == 0
    assert calls == []
    assert capsys.readouterr().out.startswith("usage: lac eval")
