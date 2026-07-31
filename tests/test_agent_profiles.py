import json

from backend.agent_launch.config_writer import (
    write_agent_profiles,
    write_agent_profiles_into,
)


def test_write_agent_profiles_into_writes_local_and_review(tmp_path):
    report = write_agent_profiles_into(tmp_path, "gpt-oss:20b-agent")
    assert {p.name for p in report["written"]} == {"lac-local.md", "lac-review.md"}
    assert report["preserved"] == []


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


def test_rerun_is_idempotent(tmp_path):
    write_agent_profiles_into(tmp_path, "m-agent")
    first = (tmp_path / "lac-local.md").read_text(encoding="utf-8")
    second = write_agent_profiles_into(tmp_path, "m-agent")
    assert {p.name for p in second["written"]} == {"lac-local.md", "lac-review.md"}
    assert second["preserved"] == []
    assert (tmp_path / "lac-local.md").read_text(encoding="utf-8") == first


def test_model_update_propagates_to_lac_managed_profile(tmp_path):
    write_agent_profiles_into(tmp_path, "m1-agent")
    report = write_agent_profiles_into(tmp_path, "m2-agent")
    local = (tmp_path / "lac-local.md").read_text(encoding="utf-8")
    assert "model: ollama/m2-agent" in local
    assert any(p.name == "lac-local.md" for p in report["written"])
    assert report["preserved"] == []


def test_user_edited_profile_is_never_clobbered(tmp_path):
    write_agent_profiles_into(tmp_path, "m-agent")
    edited = (tmp_path / "lac-local.md").read_text(encoding="utf-8")
    edited += "\nMy custom instructions.\n"
    (tmp_path / "lac-local.md").write_text(edited, encoding="utf-8")

    report = write_agent_profiles_into(tmp_path, "m2-agent")

    assert (tmp_path / "lac-local.md").read_text(encoding="utf-8") == edited
    assert any(p.name == "lac-local.md" for p in report["preserved"])
    assert {p.name for p in report["written"]} == {"lac-review.md"}


def test_unmanaged_existing_profile_is_preserved(tmp_path):
    (tmp_path / "lac-local.md").write_text("foreign content", encoding="utf-8")
    report = write_agent_profiles_into(tmp_path, "m-agent")
    assert (tmp_path / "lac-local.md").read_text(encoding="utf-8") == "foreign content"
    assert any(p.name == "lac-local.md" for p in report["preserved"])


def test_manifest_records_lac_written_hashes(tmp_path):
    write_agent_profiles_into(tmp_path, "m-agent")
    manifest = json.loads((tmp_path / ".lac-profiles.json").read_text(encoding="utf-8"))
    assert set(manifest) == {"lac-local.md", "lac-review.md"}
