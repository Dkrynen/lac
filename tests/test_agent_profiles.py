from backend.agent_launch.config_writer import (
    write_agent_profiles,
    write_agent_profiles_into,
)


def test_write_agent_profiles_into_writes_local_and_review(tmp_path):
    paths = write_agent_profiles_into(tmp_path, "gpt-oss:20b-agent")
    names = {p.name for p in paths}
    assert names == {"lac-local.md", "lac-review.md"}


def test_lac_local_profile_pins_model_and_agent_caps(tmp_path):
    write_agent_profiles_into(tmp_path, "gpt-oss:20b-agent")
    body = (tmp_path / "lac-local.md").read_text(encoding="utf-8")
    assert "mode: primary" in body
    assert "model: ollama/gpt-oss:20b-agent" in body
    assert "temperature: 0.2" in body
    assert "steps: 20" in body
    assert "edit: ask" in body
    assert "bash: ask" in body
    assert "external_directory: deny" in body
    assert "task: deny" in body


def test_lac_review_profile_is_read_only(tmp_path):
    write_agent_profiles_into(tmp_path, "gpt-oss:20b-agent")
    body = (tmp_path / "lac-review.md").read_text(encoding="utf-8")
    assert "mode: subagent" in body
    assert "edit: deny" in body
    assert "bash: deny" in body
    assert "webfetch: deny" in body
    assert "model: ollama/" not in body


def test_write_agent_profiles_project_wrapper(tmp_path):
    write_agent_profiles(tmp_path, "m-agent")
    assert (tmp_path / ".opencode" / "agents" / "lac-local.md").exists()
    assert (tmp_path / ".opencode" / "agents" / "lac-review.md").exists()
    body = (tmp_path / ".opencode" / "agents" / "lac-local.md").read_text(encoding="utf-8")
    assert "model: ollama/m-agent" in body
