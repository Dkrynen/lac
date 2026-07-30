import json

import pytest

from backend.agent_launch.config_writer import build_opencode_config
from backend.agent_launch.global_setup import (
    merge_opencode_config,
    undo_global_opencode_setup,
    write_global_opencode_config,
)

PERMISSION = {
    "*": "ask",
    "read": {"*": "allow", "*.env": "deny"},
    "edit": "ask",
    "bash": "ask",
}


def lac_cfg():
    return build_opencode_config(
        "m-agent", "http://localhost:11434", permission=PERMISSION
    )


def test_merge_on_empty_sets_provider_model_and_permission():
    merged, notes = merge_opencode_config(None, lac_cfg())
    assert merged["provider"]["ollama"]["options"]["baseURL"] == (
        "http://localhost:11434/v1"
    )
    assert merged["model"] == "ollama/m-agent"
    assert merged["permission"]["*"] == "ask"
    assert merged["permission"]["read"]["*.env"] == "deny"
    assert notes["model_preserved"] is False


def test_merge_preserves_other_providers():
    existing = {
        "provider": {
            "anthropic": {"options": {"apiKey": "sk-kept"}},
        }
    }
    merged, _ = merge_opencode_config(existing, lac_cfg())
    assert merged["provider"]["anthropic"] == {"options": {"apiKey": "sk-kept"}}
    assert "ollama" in merged["provider"]


def test_merge_preserves_user_model_and_notes_it():
    existing = {"model": "anthropic/claude-x"}
    merged, notes = merge_opencode_config(existing, lac_cfg())
    assert merged["model"] == "anthropic/claude-x"
    assert notes["model_preserved"] is True


def test_merge_user_permission_overrides_win():
    existing = {"permission": {"bash": "allow", "read": {"*.env": "allow"}}}
    merged, _ = merge_opencode_config(existing, lac_cfg())
    assert merged["permission"]["bash"] == "allow"
    assert merged["permission"]["read"]["*.env"] == "allow"
    assert merged["permission"]["read"]["*"] == "allow"
    assert merged["permission"]["edit"] == "ask"


def test_merge_does_not_mutate_inputs():
    existing = {"provider": {"x": {}}, "permission": {"bash": "allow"}}
    cfg = lac_cfg()
    merge_opencode_config(existing, cfg)
    assert "ollama" not in existing["provider"]
    assert "permission" not in cfg or cfg["permission"] == PERMISSION


def test_write_global_setup_backs_up_and_merges(tmp_path):
    original = {"model": "anthropic/claude-x", "theme": "dark"}
    (tmp_path / "opencode.json").write_text(json.dumps(original), encoding="utf-8")

    report = write_global_opencode_config(
        "m-agent",
        "http://localhost:11434",
        opencode_dir=tmp_path,
        cli_prefix=[r"C:\Tools\LAC\lac.exe"],
    )

    backup = tmp_path / "opencode.json.lac-backup"
    assert backup.read_text(encoding="utf-8") == json.dumps(original)

    cfg = json.loads((tmp_path / "opencode.json").read_text(encoding="utf-8"))
    assert cfg["model"] == "anthropic/claude-x"
    assert cfg["theme"] == "dark"
    assert cfg["provider"]["ollama"]["models"] == {"m-agent": {"name": "m-agent"}}

    scan = (tmp_path / "commands" / "scan.md").read_text(encoding="utf-8")
    assert '"C:\\Tools\\LAC\\lac.exe" scan' in scan
    assert (tmp_path / "plugins" / "lac.ts").exists()
    assert report["model_preserved"] is True
    assert report["backup"] == backup


def test_write_global_setup_fresh_dir(tmp_path):
    target = tmp_path / "opencode"
    report = write_global_opencode_config(
        "m-agent", "http://localhost:11434", opencode_dir=target
    )
    assert report["backup"] is None
    cfg = json.loads((target / "opencode.json").read_text(encoding="utf-8"))
    assert cfg["model"] == "ollama/m-agent"


def test_write_global_setup_handles_corrupt_existing(tmp_path):
    (tmp_path / "opencode.json").write_text("not-json{", encoding="utf-8")
    report = write_global_opencode_config(
        "m-agent", "http://localhost:11434", opencode_dir=tmp_path
    )
    assert (tmp_path / "opencode.json.lac-backup").read_text(encoding="utf-8") == (
        "not-json{"
    )
    cfg = json.loads((tmp_path / "opencode.json").read_text(encoding="utf-8"))
    assert cfg["model"] == "ollama/m-agent"
    assert report["backup"] is not None


def test_write_global_setup_second_run_backs_up_lac_state(tmp_path):
    write_global_opencode_config(
        "m-agent", "http://localhost:11434", opencode_dir=tmp_path
    )
    first = json.loads((tmp_path / "opencode.json").read_text(encoding="utf-8"))
    write_global_opencode_config(
        "m-agent", "http://localhost:11434", opencode_dir=tmp_path
    )
    backup = json.loads(
        (tmp_path / "opencode.json.lac-backup").read_text(encoding="utf-8")
    )
    assert backup == first


def test_undo_restores_backup(tmp_path):
    original = {"model": "anthropic/claude-x"}
    (tmp_path / "opencode.json").write_text(json.dumps(original), encoding="utf-8")
    write_global_opencode_config(
        "m-agent", "http://localhost:11434", opencode_dir=tmp_path
    )

    restored = undo_global_opencode_setup(opencode_dir=tmp_path)

    assert json.loads(restored.read_text(encoding="utf-8")) == original
    assert not (tmp_path / "opencode.json.lac-backup").exists()


def test_undo_without_backup_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        undo_global_opencode_setup(opencode_dir=tmp_path)


def test_write_global_setup_writes_agent_profiles(tmp_path):
    write_global_opencode_config(
        "m-agent", "http://localhost:11434", opencode_dir=tmp_path
    )
    local = (tmp_path / "agents" / "lac-local.md").read_text(encoding="utf-8")
    review = (tmp_path / "agents" / "lac-review.md").read_text(encoding="utf-8")
    assert "model: ollama/m-agent" in local
    assert "mode: primary" in local
    assert "mode: subagent" in review
