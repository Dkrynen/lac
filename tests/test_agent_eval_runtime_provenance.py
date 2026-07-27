from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import importlib
import copy

import pytest

from backend.agent_eval import runtime_provenance
from backend.agent_eval import opencode_contract
from backend.agent_eval.schedule import GenerationSettings
from backend.agent_eval.task import EvalScorer, EvalTask


OPENCODE_SHA256 = (
    "b7b469b83cc3561e5129a1803b746f7e2c1974297909f5b346398dc9c56a477e"
)


def plan():
    return SimpleNamespace(
        task=EvalTask(
            schema_version=2,
            id="runtime-contract",
            prompt="answer exactly",
            fixture_root=Path(r"C:\fixtures\runtime-contract"),
            timeout_seconds=180,
            scorer=EvalScorer(type="exact_text", expected="answer"),
            trials=3,
            generation=GenerationSettings(1.0, 20260726, 128),
        ),
        base_model="gpt-oss:20b",
        lac_model="gpt-oss:20b-agent",
        ollama_host="http://127.0.0.1:11434",
        opencode_binary=Path(r"C:\tools\opencode.cmd"),
        output_root=Path(r"C:\evidence"),
    )


def snapshot(*, version="1.18.7", sha256=OPENCODE_SHA256):
    return SimpleNamespace(
        opencode=SimpleNamespace(
            path=Path(r"C:\tools\opencode.exe"),
            version=version,
            sha256=sha256,
        ),
        opencode_wrapper=SimpleNamespace(
            path=Path(r"C:\tools\opencode.cmd"),
            sha256="f" * 64,
        ),
        models=SimpleNamespace(
            base=SimpleNamespace(digest="a" * 64),
            lac=SimpleNamespace(digest="b" * 64),
        ),
    )


def test_attestation_accepts_exact_allowlisted_runtime_contract():
    result = runtime_provenance.attest_runtime_bootstrap(plan(), snapshot())

    assert result["ok"] is True
    assert result["executable"] == {
        "platform": "windows",
        "architecture": "amd64",
        "reviewed_build_id": "opencode-1.18.7-windows-amd64-native",
        "version": "1.18.7",
        "sha256": OPENCODE_SHA256,
    }
    assert result["provider_npm"] == "@ai-sdk/openai-compatible"
    assert result["config"]["enabled_providers"] == ["ollama"]
    assert result["execution"]["pure_flag"] is True
    assert result["execution"]["environment"]["OPENCODE_PURE"] == "1"
    assert result["execution"]["argv"][0] == str(
        snapshot().opencode.path
    )
    assert len(result["config_manifest"]) == 6
    assert {
        (entry["trial_index"], entry["arm"])
        for entry in result["config_manifest"]
    } == {
        (trial_index, arm)
        for trial_index in (1, 2, 3)
        for arm in ("stock", "lac")
    }


@pytest.mark.parametrize(
    "tamper",
    ("duplicate", "missing", "extra", "seed", "model", "contract"),
)
def test_config_manifest_rebind_rejects_non_exact_six_key_set(tamper):
    attestation = runtime_provenance.attest_runtime_bootstrap(
        plan(),
        snapshot(),
    )
    manifest = list(attestation["config_manifest"])
    if tamper == "duplicate":
        manifest[-1] = dict(manifest[0])
    elif tamper == "missing":
        manifest.pop()
    else:
        if tamper == "extra":
            manifest.append(
                {
                    **manifest[0],
                    "trial_index": 4,
                }
            )
        elif tamper == "seed":
            manifest[0] = {
                **manifest[0],
                "seed": manifest[0]["seed"] + 1,
            }
        elif tamper == "model":
            manifest[0] = {
                **manifest[0],
                "model": "wrong",
            }
        else:
            manifest[0] = {
                **manifest[0],
                "contract_sha256": "0" * 64,
            }

    with pytest.raises(ValueError, match="manifest"):
        opencode_contract.rebind_config_manifest(
            manifest,
            "http://127.0.0.1:54321",
        )


def test_attestation_allowlist_uses_target_not_wrapper_identity():
    identity = snapshot()
    identity.opencode_wrapper.sha256 = "0" * 64

    result = runtime_provenance.attest_runtime_bootstrap(plan(), identity)

    assert result["ok"] is True
    assert result["executable"]["sha256"] == OPENCODE_SHA256
    assert result["wrapper"]["sha256"] == "0" * 64


def test_attestation_invokes_leased_exe_not_cmd_plan_wrapper():
    selected_plan = plan()
    identity = snapshot()

    result = runtime_provenance.attest_runtime_bootstrap(
        selected_plan,
        identity,
    )

    assert selected_plan.opencode_binary.suffix.casefold() == ".cmd"
    assert result["execution"]["argv"][0] == str(identity.opencode.path)
    assert result["execution"]["argv"][0] != str(
        selected_plan.opencode_binary
    )


@pytest.mark.parametrize(
    ("platform_key", "reviewed_build_id"),
    (
        (("linux", "amd64"), "opencode-1.18.7-windows-amd64-native"),
        (("windows", "arm64"), "opencode-1.18.7-windows-amd64-native"),
        (("windows", "amd64"), "unreviewed-build"),
    ),
)
def test_attestation_rejects_wrong_platform_arch_or_reviewed_build(
    monkeypatch,
    platform_key,
    reviewed_build_id,
):
    monkeypatch.setattr(
        runtime_provenance,
        "_platform_key",
        lambda: platform_key,
    )
    monkeypatch.setattr(
        runtime_provenance,
        "_REVIEWED_BUILD_ID",
        reviewed_build_id,
    )

    result = runtime_provenance.attest_runtime_bootstrap(plan(), snapshot())

    assert result["ok"] is False
    assert "executable_allowlist" in result["blockers"]


def test_runtime_modules_import_without_cycle():
    for name in (
        "backend.agent_eval.opencode_contract",
        "backend.agent_eval.runtime_provenance",
        "backend.agent_eval.opencode",
        "backend.agent_eval.runner",
        "backend.agent_eval.command",
    ):
        assert importlib.import_module(name).__name__ == name


@pytest.mark.parametrize(
    ("version", "sha256"),
    (
        ("1.19.0", OPENCODE_SHA256),
        ("1.18.7", "0" * 64),
    ),
)
def test_attestation_rejects_unknown_version_or_executable_hash(
    version,
    sha256,
):
    result = runtime_provenance.attest_runtime_bootstrap(
        plan(),
        snapshot(version=version, sha256=sha256),
    )

    assert result["ok"] is False
    assert "executable_allowlist" in result["blockers"]


def test_attestation_rejects_wrong_provider_npm(monkeypatch):
    original = runtime_provenance.config_writer.build_opencode_config

    def drifted(*args, **kwargs):
        config = original(*args, **kwargs)
        config["provider"]["ollama"]["npm"] = "@ai-sdk/other"
        return config

    monkeypatch.setattr(
        runtime_provenance.config_writer,
        "build_opencode_config",
        drifted,
    )

    result = runtime_provenance.attest_runtime_bootstrap(plan(), snapshot())

    assert result["ok"] is False
    assert "evaluation_config" in result["blockers"]


def test_attestation_rejects_mutated_builder_owned_lac_permissions(
    monkeypatch,
):
    monkeypatch.setattr(
        runtime_provenance.config_writer,
        "_FAIL_CLOSED_PERMISSIONS",
        {"*": "allow"},
    )

    result = runtime_provenance.attest_runtime_bootstrap(plan(), snapshot())

    assert result["ok"] is False
    assert "evaluation_config" in result["blockers"]


@pytest.mark.parametrize(
    ("builder_field", "unsafe_value"),
    (
        ("_STOCK_EVALUATION_PERMISSIONS", {"*": "allow"}),
        ("_READ_ONLY_EVALUATION_TOOLS", {"*": True}),
    ),
)
def test_attestation_rejects_mutated_builder_owned_stock_or_tools(
    monkeypatch,
    builder_field,
    unsafe_value,
):
    monkeypatch.setattr(
        runtime_provenance.opencode,
        builder_field,
        unsafe_value,
    )

    result = runtime_provenance.attest_runtime_bootstrap(plan(), snapshot())

    assert result["ok"] is False
    assert "evaluation_config" in result["blockers"]


@pytest.mark.parametrize(
    "mutation",
    (
        "base_url",
        "selected_model",
        "model_map",
        "permission",
        "tools",
        "generation",
        "seed",
    ),
)
def test_attestation_rejects_semantically_wrong_shared_builder_output(
    monkeypatch,
    mutation,
):
    original = runtime_provenance.config_writer.build_opencode_config

    def drifted(*args, **kwargs):
        config = copy.deepcopy(original(*args, **kwargs))
        if mutation == "base_url":
            config["provider"]["ollama"]["options"]["baseURL"] = (
                "http://127.0.0.1:11434"
            )
        elif mutation == "selected_model":
            config["model"] = "ollama/wrong"
        elif mutation == "model_map":
            config["provider"]["ollama"]["models"] = {
                "wrong": {"name": "wrong"}
            }
        elif mutation == "permission":
            config["permission"] = {"*": "allow"}
        elif mutation == "tools":
            config["tools"] = {"*": True}
        elif mutation == "generation":
            config["agent"]["build"]["temperature"] = 0.25
        elif mutation == "seed":
            config["agent"]["build"]["options"]["seed"] += 1
        return config

    monkeypatch.setattr(
        runtime_provenance.config_writer,
        "build_opencode_config",
        drifted,
    )

    result = runtime_provenance.attest_runtime_bootstrap(plan(), snapshot())

    assert result["ok"] is False
    assert "evaluation_config" in result["blockers"]


@pytest.mark.parametrize(
    ("field", "drifted_value"),
    (
        ("enabled_providers", ["ollama", "other"]),
        ("plugin", ["unsafe"]),
        ("mcp", {"unsafe": {}}),
        ("formatter", True),
        ("autoupdate", True),
        ("share", "enabled"),
        ("instructions", ["unsafe"]),
        ("snapshot", True),
    ),
)
def test_attestation_rejects_evaluation_config_drift(
    monkeypatch,
    field,
    drifted_value,
):
    original = runtime_provenance.config_writer.build_opencode_config

    def drifted(*args, **kwargs):
        config = original(*args, **kwargs)
        config[field] = drifted_value
        return config

    monkeypatch.setattr(
        runtime_provenance.config_writer,
        "build_opencode_config",
        drifted,
    )

    result = runtime_provenance.attest_runtime_bootstrap(plan(), snapshot())

    assert result["ok"] is False
    assert "evaluation_config" in result["blockers"]


def test_attestation_rejects_missing_pure_flag(monkeypatch):
    original = runtime_provenance.opencode.build_evaluation_argv

    def without_pure(*args, **kwargs):
        return [
            item
            for item in original(*args, **kwargs)
            if item != "--pure"
        ]

    monkeypatch.setattr(
        runtime_provenance.opencode,
        "build_evaluation_argv",
        without_pure,
    )

    result = runtime_provenance.attest_runtime_bootstrap(plan(), snapshot())

    assert result["ok"] is False
    assert "execution_contract" in result["blockers"]


@pytest.mark.parametrize(
    "missing",
    (
        "OPENCODE_PURE",
        "OPENCODE_DISABLE_AUTOUPDATE",
        "OPENCODE_DISABLE_PRUNE",
        "OPENCODE_DISABLE_DEFAULT_PLUGINS",
        "OPENCODE_DISABLE_LSP_DOWNLOAD",
        "OPENCODE_DISABLE_MODELS_FETCH",
        "OPENCODE_DISABLE_PROJECT_CONFIG",
        "OPENCODE_DISABLE_CLAUDE_CODE",
        "OPENCODE_DISABLE_CLAUDE_CODE_PROMPT",
        "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS",
        "OPENCODE_AUTO_SHARE",
        "OPENCODE_ENABLE_EXA",
    ),
)
def test_attestation_rejects_missing_execution_environment_disable(
    monkeypatch,
    missing,
):
    original = runtime_provenance.opencode.evaluation_environment_flags

    def without_required_flag():
        values = original()
        values.pop(missing)
        return values

    monkeypatch.setattr(
        runtime_provenance.opencode,
        "evaluation_environment_flags",
        without_required_flag,
    )

    result = runtime_provenance.attest_runtime_bootstrap(plan(), snapshot())

    assert result["ok"] is False
    assert "execution_contract" in result["blockers"]
