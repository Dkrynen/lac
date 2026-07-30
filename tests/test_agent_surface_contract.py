import pytest

from backend.agent_launch import surface_contract
from backend.agent_launch.config_writer import (
    _FAIL_CLOSED_PERMISSIONS,
    _LAC_LOCAL_AGENT_MD,
    _LAC_PLUGIN_TS,
    _LAC_REVIEW_AGENT_MD,
    _RECOMMEND_MD,
    _SCAN_MD,
    _TUNE_MD,
    build_opencode_config,
)
from backend.agent_launch.global_setup import merge_opencode_config


def test_shipped_opencode_config_passes_surface_contract():
    config = build_opencode_config(
        "gpt-oss:20b-agent",
        "http://localhost:11434",
        permission=_FAIL_CLOSED_PERMISSIONS,
    )
    surface_contract.validate_opencode_config(config)


def test_merged_global_config_passes_surface_contract():
    existing = {"model": "anthropic/claude-x", "permission": {"bash": "allow"}}
    lac = build_opencode_config(
        "m-agent", "http://localhost:11434", permission=_FAIL_CLOSED_PERMISSIONS
    )
    merged, _ = merge_opencode_config(existing, lac)
    surface_contract.validate_opencode_config(merged)


def test_config_with_unknown_permission_key_is_rejected():
    config = build_opencode_config(
        "m-agent", "http://localhost:11434", permission={"not_a_real_key": "ask"}
    )
    with pytest.raises(surface_contract.SurfaceViolation):
        surface_contract.validate_opencode_config(config)


def test_config_with_malformed_model_ref_is_rejected():
    config = build_opencode_config("m", "http://localhost:11434")
    config["model"] = "no-provider-slash"
    with pytest.raises(surface_contract.SurfaceViolation):
        surface_contract.validate_opencode_config(config)


def test_config_with_unknown_top_level_shape_is_rejected():
    with pytest.raises(surface_contract.SurfaceViolation):
        surface_contract.validate_opencode_config({"provider": "ollama"})


@pytest.mark.parametrize(
    "profile",
    [_LAC_LOCAL_AGENT_MD.format(model="gpt-oss:20b-agent"), _LAC_REVIEW_AGENT_MD],
)
def test_shipped_agent_profiles_pass_surface_contract(profile):
    surface_contract.validate_agent_profile(profile)


def test_agent_profile_with_unknown_frontmatter_key_is_rejected():
    body = "---\ndescription: x\nmode: primary\nbogus_key: 1\n---\nPrompt.\n"
    with pytest.raises(surface_contract.SurfaceViolation):
        surface_contract.validate_agent_profile(body)


def test_agent_profile_with_bad_mode_is_rejected():
    body = "---\ndescription: x\nmode: commander\n---\nPrompt.\n"
    with pytest.raises(surface_contract.SurfaceViolation):
        surface_contract.validate_agent_profile(body)


def test_agent_profile_without_description_is_rejected():
    body = "---\nmode: primary\n---\nPrompt.\n"
    with pytest.raises(surface_contract.SurfaceViolation):
        surface_contract.validate_agent_profile(body)


def test_agent_profile_with_unpinned_model_is_rejected():
    body = "---\ndescription: x\nmode: primary\nmodel: noslash\n---\nPrompt.\n"
    with pytest.raises(surface_contract.SurfaceViolation):
        surface_contract.validate_agent_profile(body)


@pytest.mark.parametrize(
    "command",
    [
        _SCAN_MD.format(lac="lac"),
        _RECOMMEND_MD.format(lac="lac"),
        _TUNE_MD.format(lac="lac"),
    ],
)
def test_shipped_commands_pass_surface_contract(command):
    surface_contract.validate_command_file(command)


def test_command_without_description_is_rejected():
    with pytest.raises(surface_contract.SurfaceViolation):
        surface_contract.validate_command_file("---\nagent: build\n---\n!`lac scan`\n")


def test_shipped_plugin_passes_surface_contract():
    import json

    body = _LAC_PLUGIN_TS.replace("{lac_cli_json}", json.dumps(["lac"]))
    surface_contract.validate_plugin_source(body)


def test_plugin_missing_sdk_import_is_rejected():
    with pytest.raises(surface_contract.SurfaceViolation):
        surface_contract.validate_plugin_source("export const X = async () => ({})")
