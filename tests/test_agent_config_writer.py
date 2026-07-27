import json
from fnmatch import fnmatchcase

import pytest

from backend.agent_launch.config_writer import (
    build_opencode_config,
    write_agent_commands,
    write_opencode_config,
    write_opencode_config_file,
    write_stock_opencode_config,
)
from backend.agent_eval.schedule import GenerationSettings


def test_evaluation_config_is_local_only_and_disables_ambient_features(tmp_path):
    out = write_opencode_config_file(
        tmp_path / "runtime" / "opencode.json", "gpt-oss:20b", "http://127.0.0.1:11434"
    )
    cfg = json.loads(out.read_text(encoding="utf-8"))
    assert "$schema" not in cfg
    assert cfg["autoupdate"] is False
    assert cfg["share"] == "disabled"
    assert cfg["enabled_providers"] == ["ollama"]
    assert cfg["plugin"] == []
    assert cfg["mcp"] == {}
    assert cfg["instructions"] == []
    assert cfg["formatter"] is False
    assert cfg["snapshot"] is False


def test_evaluation_config_pins_build_agent_generation_settings(tmp_path):
    generation = GenerationSettings(1.0, 20260726, 128)

    out = write_opencode_config_file(
        tmp_path / "runtime" / "opencode.json",
        "gpt-oss:20b",
        "http://127.0.0.1:11434",
        generation=generation,
        seed=1209934845,
    )

    cfg = json.loads(out.read_text(encoding="utf-8"))
    assert cfg["agent"]["build"] == {
        "temperature": 1.0,
        "options": {
            "seed": 1209934845,
            "max_tokens": 128,
        },
        "steps": 1,
    }


@pytest.mark.parametrize(
    "host",
    [
        "https://127.0.0.1:11434",
        "http://user:pass@127.0.0.1:11434",
        "http://127.0.0.1:11434/base",
        "http://127.0.0.1:11434?query=1",
        "http://127.0.0.1:11434#fragment",
        "http://127.0.0.1.evil:11434",
    ],
)
def test_evaluation_config_rejects_noncanonical_or_nonloopback_host(tmp_path, host):
    with pytest.raises(ValueError, match="loopback"):
        write_opencode_config_file(
            tmp_path / "runtime" / "opencode.json",
            "gpt-oss:20b",
            host,
        )


def test_write_opencode_config_points_at_ollama_and_model(tmp_path):
    out = write_opencode_config(tmp_path, "qwen3:8b-agent", "http://localhost:11434")
    assert out == tmp_path / ".opencode" / "opencode.json"
    cfg = json.loads(out.read_text(encoding="utf-8"))
    prov = cfg["provider"]["ollama"]
    assert prov["npm"] == "@ai-sdk/openai-compatible"
    assert prov["options"]["baseURL"] == "http://localhost:11434/v1"
    assert "qwen3:8b-agent" in prov["models"]
    assert cfg["model"] == "ollama/qwen3:8b-agent"


def test_write_opencode_config_normalizes_trailing_slash(tmp_path):
    out = write_opencode_config(tmp_path, "m", "http://localhost:11434/")
    cfg = json.loads(out.read_text(encoding="utf-8"))
    assert cfg["provider"]["ollama"]["options"]["baseURL"] == "http://localhost:11434/v1"


def test_write_stock_opencode_config_has_provider_but_no_lac_policy(tmp_path):
    out = write_stock_opencode_config(
        tmp_path, "gpt-oss:20b", "http://localhost:11434/"
    )
    cfg = json.loads(out.read_text(encoding="utf-8"))

    assert cfg["model"] == "ollama/gpt-oss:20b"
    assert cfg["provider"]["ollama"]["options"]["baseURL"] == (
        "http://localhost:11434/v1"
    )
    assert cfg["provider"]["ollama"]["models"] == {
        "gpt-oss:20b": {"name": "gpt-oss:20b"}
    }
    assert "permission" not in cfg
    assert not (tmp_path / ".opencode" / "commands").exists()


def test_write_opencode_config_is_fail_closed(tmp_path):
    out = write_opencode_config(
        tmp_path, "qwen3:8b-agent", "http://localhost:11434"
    )
    permission = json.loads(out.read_text(encoding="utf-8"))["permission"]

    assert permission["*"] == "ask"
    assert permission["edit"] == "ask"
    assert permission["bash"] == "ask"
    assert permission["grep"] == "ask"
    assert permission["webfetch"] == "ask"
    assert permission["websearch"] == "ask"
    assert permission["external_directory"] == "deny"
    assert permission["task"] == "deny"
    assert permission["read"]["*"] == "allow"
    assert permission["read"]["*.env"] == "deny"
    assert permission["read"]["*.env.*"] == "deny"
    assert permission["read"]["*credentials.json"] == "deny"
    assert permission["read"]["*token.json"] == "deny"
    assert permission["read"]["*.pem"] == "deny"
    assert permission["read"]["*.key"] == "deny"

    def read_action(path):
        action = None
        for pattern, candidate in permission["read"].items():
            if fnmatchcase(path, pattern):
                action = candidate
        return action

    for secret_path in (
        ".env",
        ".env.local",
        "nested/.env",
        "nested/.env.production",
        "credentials.json",
        "nested/credentials.json",
        "token.json",
        "nested/token.json",
        "private.pem",
        "nested/private.pem",
        "private.key",
        "nested/private.key",
    ):
        assert read_action(secret_path) == "deny", secret_path


def test_fail_closed_policy_is_not_shared_between_config_builds():
    first = build_opencode_config(
        "qwen3:8b-agent",
        "http://localhost:11434",
        permission={
            "*": "ask",
            "read": {"*": "allow", "*.env": "deny"},
        },
    )
    second = build_opencode_config(
        "qwen3:8b-agent",
        "http://localhost:11434",
        permission=first["permission"],
    )

    first["permission"]["read"]["*.env"] = "allow"

    assert second["permission"]["read"]["*.env"] == "deny"


def test_write_agent_commands_emit_lac_shellouts(tmp_path):
    paths = write_agent_commands(tmp_path)
    names = {p.name for p in paths}
    assert names == {"scan.md", "recommend.md"}
    scan = (tmp_path / ".opencode" / "commands" / "scan.md").read_text(encoding="utf-8")
    rec = (tmp_path / ".opencode" / "commands" / "recommend.md").read_text(encoding="utf-8")
    assert "!`lac scan`" in scan
    assert "!`lac recommend --use-case agent`" in rec
