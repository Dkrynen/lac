import json
from backend.agent_launch.config_writer import write_opencode_config, write_agent_commands


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


def test_write_agent_commands_emit_lac_shellouts(tmp_path):
    paths = write_agent_commands(tmp_path)
    names = {p.name for p in paths}
    assert names == {"scan.md", "recommend.md"}
    scan = (tmp_path / ".opencode" / "commands" / "scan.md").read_text(encoding="utf-8")
    rec = (tmp_path / ".opencode" / "commands" / "recommend.md").read_text(encoding="utf-8")
    assert "!`lac scan`" in scan
    assert "!`lac recommend --use-case agent`" in rec
