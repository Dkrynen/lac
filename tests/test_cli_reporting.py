from __future__ import annotations

import argparse

import pytest


def test_cli_inspect_shows_real_size_from_tags(monkeypatch, capsys):
    """/api/show has no top-level 'size' field (only /api/tags does), so
    cmd_inspect's result.get('size', 0) always read 0.0 GB."""
    import cli as cli_mod

    def fake_ollama(method, path, body=None, timeout=30):
        if path == "/api/show":
            return {
                "details": {"parameter_size": "8B", "quantization_level": "Q4_K_M",
                             "family": "qwen3", "format": "gguf"},
                "modified_at": "2026-01-01",
            }
        if path == "/api/tags":
            return {"models": [{"name": "qwen3:8b", "size": 5_000_000_000}]}
        return {"error": "unexpected path"}

    monkeypatch.setattr(cli_mod, "ollama", fake_ollama)
    args = argparse.Namespace(model="qwen3:8b")
    cli_mod.cmd_inspect(args)

    out = capsys.readouterr().out
    assert "4.66 GB" in out


def test_cli_pull_logs_real_size_not_zero(monkeypatch, isolated_home):
    """_log_download read chunk.get('total') from the terminal 'success'
    chunk, which Ollama never populates there -- size_gb was always 0.

    Uses the isolated_home fixture (not plain HOME/USERPROFILE env
    monkeypatching) because backend.cookbook.downloads.CONFIG_DIR is a
    module-level constant computed once from Path.home() at first import
    -- setting the env vars after that has no effect. isolated_home
    patches downloads.CONFIG_DIR directly, which is the only way to keep
    this test from writing a fake "test-model:1b" entry into the real
    ~/.model-hub/downloads/history.jsonl on the machine running the suite."""
    import cli as cli_mod

    def fake_stream(path, body, timeout=300):
        yield {"status": "pulling manifest"}
        yield {"status": "downloading", "completed": 500_000_000, "total": 1_000_000_000}
        yield {"status": "success"}

    monkeypatch.setattr(cli_mod, "ollama_stream", fake_stream)
    # lac-pro is installed as a real plugin in this venv ("pro", confirmed
    # via backend.plugins.discover()) -- on_model_installed would otherwise
    # fire a REAL autopilot benchmark against live Ollama. Not what this
    # test is checking; stub it out.
    monkeypatch.setattr(cli_mod, "_notify_model_installed", lambda model_name: None)
    args = argparse.Namespace(model="test-model:1b")
    cli_mod.cmd_pull(args)

    history = cli_mod._download_history()
    assert any(
        h["model"] == "test-model:1b" and h["status"] == "completed" and h["size_gb"] > 0
        for h in history
    )


def test_cli_session_import_missing_file_exits_clean(capsys):
    import cli as cli_mod

    args = argparse.Namespace(action="import", path="/definitely/does/not/exist.json")
    with pytest.raises(SystemExit) as e:
        cli_mod.cmd_session(args)
    assert e.value.code == 1
    assert "File not found" in capsys.readouterr().err
