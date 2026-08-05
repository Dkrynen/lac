from __future__ import annotations

from pathlib import Path

import pytest

from backend.agent_launch.customize import (
    open_agent_profile,
    resolve_editor,
)


def test_open_agent_profile_writes_profiles_then_opens_primary(tmp_path):
    events = {}

    def fake_write_profiles(pd, model):
        events["profiles"] = (Path(pd), model)
        return {"written": [], "preserved": []}

    def fake_editor(path):
        events["opened"] = Path(path)

    rc = open_agent_profile(
        tmp_path, "qwen3:8b-agent",
        write_profiles_fn=fake_write_profiles, editor_fn=fake_editor,
        out=lambda *a, **k: None,
    )

    assert rc == 0
    assert events["profiles"] == (tmp_path.resolve(), "qwen3:8b-agent")
    assert events["opened"] == tmp_path.resolve() / ".opencode" / "agents" / "lac-local.md"


def test_resolve_editor_prefers_EDITOR_env(monkeypatch):
    monkeypatch.setenv("EDITOR", "code -w")
    monkeypatch.delenv("VISUAL", raising=False)
    assert resolve_editor() == ["code", "-w"]


def test_resolve_editor_falls_back_to_VISUAL(monkeypatch):
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setenv("VISUAL", "nano")
    assert resolve_editor() == ["nano"]


def test_resolve_editor_platform_default_when_no_env(monkeypatch):
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    cmd = resolve_editor()
    assert cmd, "must always produce some editor"
    assert cmd[0]


def test_cli_parser_accepts_customize():
    import cli as cli_mod
    parser = cli_mod.build_parser(include_plugins=False)
    args = parser.parse_args(["agent", ".", "--customize"])
    assert args.customize is True


def test_cmd_agent_customize_requires_a_project_profile(tmp_path, monkeypatch, capsys):
    import cli as cli_mod
    import backend.plugins as plugins_mod

    monkeypatch.setattr(plugins_mod, "discover", lambda: [])
    parser = cli_mod.build_parser(include_plugins=False)
    args = parser.parse_args(["agent", str(tmp_path), "--customize"])
    with pytest.raises(SystemExit) as e:
        cli_mod.cmd_agent(args)
    assert e.value.code == 1
    assert "lac agent" in capsys.readouterr().err


def test_cmd_agent_customize_opens_profile_for_pinned_model(tmp_path, monkeypatch):
    import cli as cli_mod
    import backend.plugins as plugins_mod
    from backend.agent_launch.project_profile import ProjectProfile, save_profile

    save_profile(tmp_path, ProjectProfile(model="qwen3:8b"))
    opened = {}

    def fake_open(project_dir, model, **kw):
        opened["args"] = (Path(project_dir), model)
        return 0

    monkeypatch.setattr(plugins_mod, "discover", lambda: [])
    monkeypatch.setattr("backend.agent_launch.customize.open_agent_profile", fake_open)
    parser = cli_mod.build_parser(include_plugins=False)
    args = parser.parse_args(["agent", str(tmp_path), "--customize"])
    with pytest.raises(SystemExit) as e:
        cli_mod.cmd_agent(args)
    assert e.value.code == 0
    assert opened["args"] == (tmp_path.resolve(), "qwen3:8b-agent")
