from __future__ import annotations

import json
from pathlib import Path

from backend.config import (
    AptProjectConfig,
    find_project_root,
    parse_jsonc,
    resolve_config,
    strip_jsonc,
)


def test_strip_jsonc_line_comments():
    src = '{\n  "a": 1, // inline\n  "b": "http://x" // url\n}\n'
    cleaned = strip_jsonc(src)
    assert "// " not in cleaned
    data = json.loads(cleaned)
    assert data == {"a": 1, "b": "http://x"}


def test_strip_jsonc_block_comments():
    src = '{"a": 1, /* block \n multiline */ "b": 2}'
    data = json.loads(strip_jsonc(src))
    assert data == {"a": 1, "b": 2}


def test_strip_jsonc_presives_slashes_in_strings():
    src = '{"url": "http://localhost:11434/api"}  // trailing'
    data = json.loads(strip_jsonc(src))
    assert data["url"] == "http://localhost:11434/api"


def test_parse_jsonc_real_file():
    data = parse_jsonc(Path(".apt/apt.jsonc"))
    assert data["default_model"] == "llama3.2:3b"
    assert "filesystem" in data["mcp"]["servers"]


def test_find_project_root_finds_repo():
    root = find_project_root()
    assert root is not None
    assert (root / ".apt" / "apt.jsonc").exists()


def test_resolve_config_merge(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    cfg = resolve_config()
    assert cfg.ollama_host.startswith("http://")
    assert cfg.theme in ("apt-dark", "dark")
    assert isinstance(cfg.project, AptProjectConfig)


def test_resolve_config_env_overrides_host(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://example:9999")
    cfg = resolve_config()
    assert cfg.ollama_host == "http://example:9999"


def test_resolve_config_mcp_servers_filtered_by_enabled():
    cfg = resolve_config()
    assert "filesystem" in cfg.mcp_servers
