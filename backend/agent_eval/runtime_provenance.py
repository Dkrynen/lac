"""Deterministic attestation of the verified OpenCode bootstrap contract."""
from __future__ import annotations

import platform
import re
from pathlib import Path
from typing import Any

from backend.agent_launch import config_writer

from . import opencode_contract as opencode
from .schedule import build_schedule
from .task import task_contract_sha256


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVIEWED_BUILD_ID = "opencode-1.18.4-windows-amd64-native"
_ALLOWED_RUNTIME_PROVIDERS = {
    (
        "windows",
        "amd64",
        "opencode-1.18.4-windows-amd64-native",
        "1.18.4",
        "59b66e1983b2665b498f234a17bf92e78e0e9e3f8c77406edf8dcf3e6239ee5c",
    ): opencode.EVALUATION_PROVIDER_NPM,
}
_REQUIRED_ENVIRONMENT = {
    "OPENCODE_DISABLE_AUTOUPDATE": "1",
    "OPENCODE_DISABLE_PRUNE": "1",
    "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
    "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
    "OPENCODE_DISABLE_MODELS_FETCH": "1",
    "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
    "OPENCODE_DISABLE_CLAUDE_CODE": "1",
    "OPENCODE_DISABLE_CLAUDE_CODE_PROMPT": "1",
    "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS": "1",
    "OPENCODE_AUTO_SHARE": "false",
    "OPENCODE_ENABLE_EXA": "0",
    "OPENCODE_PURE": "1",
}


def _platform_key() -> tuple[str, str]:
    system = platform.system().casefold()
    machine = platform.machine().casefold()
    architecture = (
        "amd64"
        if machine in {"amd64", "x86_64"}
        else machine
    )
    return system, architecture


def attest_runtime_bootstrap(plan, identity_snapshot) -> dict[str, Any]:
    blockers: list[str] = []
    system, architecture = _platform_key()
    try:
        target = identity_snapshot.opencode
        target_path = Path(target.path)
        version = target.version
        executable_sha256 = target.sha256
    except (AttributeError, TypeError):
        target_path = Path()
        version = None
        executable_sha256 = None
    allowlist_key = (
        system,
        architecture,
        _REVIEWED_BUILD_ID,
        version,
        executable_sha256,
    )
    provider_npm = _ALLOWED_RUNTIME_PROVIDERS.get(allowlist_key)
    if (
        not isinstance(version, str)
        or not isinstance(executable_sha256, str)
        or _SHA256.fullmatch(executable_sha256) is None
        or provider_npm is None
    ):
        blockers.append("executable_allowlist")

    config = None
    config_error = None
    config_manifest: list[dict[str, Any]] = []
    try:
        model_digests = {
            "raw": identity_snapshot.models.base.digest,
            "stock": identity_snapshot.models.base.digest,
            "lac": identity_snapshot.models.lac.digest,
        }
        schedule = build_schedule(
            task_contract_sha256(plan.task),
            model_digests,
            plan.task.generation,
            plan.task.trials,
        )
        for trial in schedule.trials:
            for arm, model in (
                ("stock", plan.base_model),
                ("lac", plan.lac_model),
            ):
                candidate = opencode.build_evaluation_config(
                    model,
                    plan.ollama_host,
                    arm=arm,
                    generation=plan.task.generation,
                    seed=trial.seed,
                )
                if not opencode.valid_evaluation_config(
                    candidate,
                    arm=arm,
                    model=model,
                    ollama_host=plan.ollama_host,
                    generation=plan.task.generation,
                    seed=trial.seed,
                    provider_npm=provider_npm,
                ):
                    raise ValueError(
                        "evaluation config violates the reviewed semantics"
                    )
                if config is None:
                    config = candidate
                config_manifest.append(
                    opencode.build_config_manifest_entry(
                        trial_index=trial.index,
                        arm=arm,
                        model=model,
                        generation=plan.task.generation,
                        seed=trial.seed,
                    )
                )
    except Exception as exc:
        config_error = f"{type(exc).__name__}: {exc}"
    provider = config.get("provider") if isinstance(config, dict) else None
    ollama_provider = (
        provider.get("ollama")
        if isinstance(provider, dict)
        else None
    )
    observed_provider_npm = (
        ollama_provider.get("npm")
        if isinstance(ollama_provider, dict)
        else None
    )
    config_valid = (
        len(config_manifest) == 6
        and config is not None
        and provider_npm is not None
        and opencode.valid_evaluation_config(
            config,
            arm="stock",
            model=plan.base_model,
            ollama_host=plan.ollama_host,
            generation=plan.task.generation,
            seed=config_manifest[0]["seed"],
            provider_npm=provider_npm,
        )
    )
    if not config_valid:
        blockers.append("evaluation_config")

    workspace = Path(plan.output_root) / ".runtime-attestation-workspace"
    argv = opencode.build_evaluation_argv(
        target_path,
        plan.task.prompt,
        plan.base_model,
        workspace,
    )
    expected_argv = [
        str(target_path),
        "run",
        plan.task.prompt,
        "--format",
        "json",
        "--pure",
        "--auto",
        "--model",
        f"ollama/{plan.base_model}",
        "--dir",
        str(workspace),
    ]
    environment = opencode.evaluation_environment_flags()
    execution_valid = (
        argv == expected_argv
        and argv.count("--pure") == 1
        and environment == _REQUIRED_ENVIRONMENT
    )
    if not execution_valid:
        blockers.append("execution_contract")

    wrapper = getattr(identity_snapshot, "opencode_wrapper", None)
    return {
        "schema_version": 1,
        "ok": not blockers,
        "blockers": blockers,
        "executable": {
            "platform": system,
            "architecture": architecture,
            "reviewed_build_id": _REVIEWED_BUILD_ID,
            "version": version,
            "sha256": executable_sha256,
        },
        "wrapper": (
            {
                "path": str(wrapper.path),
                "sha256": wrapper.sha256,
                "leased_separately": True,
            }
            if wrapper is not None
            else None
        ),
        "provider_npm": observed_provider_npm,
        "upstream_audit_rationale": (
            "OpenCode 1.18.4 source lists "
            "@ai-sdk/openai-compatible in BUNDLED_PROVIDERS; "
            "Npm.add is fallback only. This text is not runtime proof."
        ),
        "config": {
            **(
                opencode.config_invariants(config)
                if isinstance(config, dict)
                else {}
            ),
            "provider_keys": (
                sorted(provider)
                if isinstance(provider, dict)
                else None
            ),
            "canonical_sha256": (
                opencode.canonical_config_sha256(config)
                if isinstance(config, dict)
                else None
            ),
            "error": config_error,
        },
        "config_manifest": config_manifest,
        "execution": {
            "argv": argv,
            "pure_flag": argv.count("--pure") == 1,
            "environment": environment,
        },
    }
