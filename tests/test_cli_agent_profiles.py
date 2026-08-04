from __future__ import annotations

import pytest


def test_parser_accepts_agent_model_and_reselect():
    import cli as cli_mod
    parser = cli_mod.build_parser(include_plugins=False)
    args = parser.parse_args(["agent", ".", "--model", "qwen3:8b", "--reselect"])
    assert args.command == "agent"
    assert args.model == "qwen3:8b"
    assert args.reselect is True


def test_cmd_agent_passes_pin_and_reselect(monkeypatch):
    import cli as cli_mod
    import backend.plugins as plugins_mod
    captured = {}

    def fake_launch(project_dir, **kw):
        captured.update(kw)
        return 0

    monkeypatch.setattr("backend.agent_launch.launcher.launch_agent", fake_launch)
    monkeypatch.setattr(plugins_mod, "discover", lambda: [])
    parser = cli_mod.build_parser(include_plugins=False)
    args = parser.parse_args(["agent", ".", "--model", "qwen3:8b", "--reselect"])
    with pytest.raises(SystemExit) as e:
        cli_mod.cmd_agent(args)
    assert e.value.code == 0
    assert captured["model_pin"] == "qwen3:8b"
    assert captured["reselect"] is True


def test_cmd_agent_profile_error_exits_with_reason(monkeypatch, capsys):
    import cli as cli_mod
    import backend.plugins as plugins_mod
    from backend.agent_launch.project_profile import ProfileError

    def fake_launch(project_dir, **kw):
        raise ProfileError("Project profile at x is unreadable: bad json")

    monkeypatch.setattr("backend.agent_launch.launcher.launch_agent", fake_launch)
    monkeypatch.setattr(plugins_mod, "discover", lambda: [])
    parser = cli_mod.build_parser(include_plugins=False)
    args = parser.parse_args(["agent", "."])
    with pytest.raises(SystemExit) as e:
        cli_mod.cmd_agent(args)
    assert e.value.code == 1
    assert "unreadable" in capsys.readouterr().err
