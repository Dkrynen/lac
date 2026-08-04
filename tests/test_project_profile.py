from __future__ import annotations

import json

import pytest

from backend.agent_launch.project_profile import (
    PROFILE_FILENAME,
    ProfileError,
    ProjectProfile,
    load_profile,
    profile_path,
    save_profile,
)


def test_profile_path_lives_in_project_opencode_dir(tmp_path):
    assert profile_path(tmp_path) == tmp_path / ".opencode" / PROFILE_FILENAME


def test_load_profile_returns_none_when_absent(tmp_path):
    assert load_profile(tmp_path) is None


def test_save_load_round_trip(tmp_path):
    saved = save_profile(tmp_path, ProjectProfile(model="qwen3:8b", context=65536, preset="strict"))
    assert saved == profile_path(tmp_path)
    loaded = load_profile(tmp_path)
    assert loaded is not None
    assert loaded.model == "qwen3:8b"
    assert loaded.context == 65536
    assert loaded.preset == "strict"
    assert loaded.updated_at != ""


def test_save_stamps_updated_at(tmp_path):
    save_profile(tmp_path, ProjectProfile(model="qwen3:8b"))
    raw = json.loads(profile_path(tmp_path).read_text(encoding="utf-8"))
    assert raw["updated_at"]


def test_load_profile_rejects_invalid_json(tmp_path):
    p = profile_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ProfileError):
        load_profile(tmp_path)


def test_load_profile_rejects_wrong_schema_version(tmp_path):
    p = profile_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"schema_version": 99, "model": "qwen3:8b"}), encoding="utf-8")
    with pytest.raises(ProfileError, match="schema"):
        load_profile(tmp_path)


def test_load_profile_rejects_missing_model(tmp_path):
    p = profile_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"schema_version": 1, "preset": "strict"}), encoding="utf-8")
    with pytest.raises(ProfileError, match="model"):
        load_profile(tmp_path)


def test_load_profile_rejects_empty_model(tmp_path):
    p = profile_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"schema_version": 1, "model": "  "}), encoding="utf-8")
    with pytest.raises(ProfileError, match="model"):
        load_profile(tmp_path)


def test_load_profile_rejects_unknown_preset(tmp_path):
    p = profile_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"schema_version": 1, "model": "qwen3:8b", "preset": "yolo"}), encoding="utf-8")
    with pytest.raises(ProfileError, match="preset"):
        load_profile(tmp_path)


def test_load_profile_defaults_context_and_preset(tmp_path):
    p = profile_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"schema_version": 1, "model": "qwen3:8b"}), encoding="utf-8")
    loaded = load_profile(tmp_path)
    assert loaded is not None
    assert loaded.context is None
    assert loaded.preset == "strict"
