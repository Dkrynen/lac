"""Pure, shared builders for the verified OpenCode evaluation contract."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from backend.agent_launch import config_writer
from .schedule import GenerationSettings


EVALUATION_PROVIDER_NPM = "@ai-sdk/openai-compatible"
_STOCK_EVALUATION_PERMISSIONS = {"external_directory": "deny"}
_READ_ONLY_EVALUATION_TOOLS = {
    "*": False,
    "read": True,
    "glob": False,
    "grep": False,
}
_REVIEWED_STOCK_PERMISSION_JSON = (
    '{"external_directory":"deny"}'
)
_REVIEWED_READ_ONLY_TOOLS_JSON = (
    '{"*":false,"read":true,"glob":false,"grep":false}'
)
_REVIEWED_LAC_PERMISSION_JSON = """\
{
  "*": "ask",
  "read": {
    "*": "allow",
    "*.env": "deny",
    "*.env.*": "deny",
    "*credentials.json": "deny",
    "*token.json": "deny",
    "*.pem": "deny",
    "*.key": "deny"
  },
  "glob": "allow",
  "grep": "ask",
  "list": "allow",
  "lsp": "allow",
  "edit": "ask",
  "bash": "ask",
  "webfetch": "ask",
  "websearch": "ask",
  "external_directory": "deny",
  "task": "deny"
}
"""
_CONFIG_INVARIANTS = {
    "enabled_providers": ["ollama"],
    "plugin": [],
    "mcp": {},
    "formatter": False,
    "autoupdate": False,
    "share": "disabled",
    "instructions": [],
    "snapshot": False,
}
_ENVIRONMENT_FLAGS = {
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


def canonical_config_sha256(config: dict[str, Any]) -> str:
    encoded = json.dumps(
        config,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def config_invariants(config: dict[str, Any]) -> dict[str, Any]:
    return {key: config.get(key) for key in _CONFIG_INVARIANTS}


def _expected_permission(arm: str) -> dict[str, Any]:
    if arm == "stock":
        return json.loads(_REVIEWED_STOCK_PERMISSION_JSON)
    if arm == "lac":
        return json.loads(_REVIEWED_LAC_PERMISSION_JSON)
    raise ValueError(f"unknown OpenCode evaluation arm: {arm}")


def expected_evaluation_config(
    *,
    arm: str,
    model: str,
    ollama_host: str,
    generation: GenerationSettings | None,
    seed: int | None,
) -> dict[str, Any]:
    parsed = urlsplit(ollama_host)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username
        or parsed.password
        or parsed.path.rstrip("/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("evaluation endpoint must be unauthenticated loopback")
    if (generation is None) != (seed is None):
        raise ValueError("generation and seed must be provided together")
    if generation is not None and not isinstance(
        generation,
        GenerationSettings,
    ):
        raise TypeError("generation must be GenerationSettings")
    if (
        seed is not None
        and (type(seed) is not int or not 0 <= seed <= 0x7FFFFFFF)
    ):
        raise ValueError("seed must be a 31-bit non-negative integer")
    config = {
        "provider": {
            "ollama": {
                "npm": EVALUATION_PROVIDER_NPM,
                "name": "Ollama (LAC)",
                "options": {
                    "baseURL": ollama_host.rstrip("/") + "/v1",
                },
                "models": {model: {"name": model}},
            }
        },
        "model": f"ollama/{model}",
        "permission": _expected_permission(arm),
        "tools": json.loads(_REVIEWED_READ_ONLY_TOOLS_JSON),
        **copy.deepcopy(_CONFIG_INVARIANTS),
    }
    if generation is not None:
        config["agent"] = {
            "build": {
                "temperature": generation.temperature,
                "options": {
                    "temperature": generation.temperature,
                    "seed": seed,
                    "max_tokens": generation.max_output_tokens,
                },
                "steps": 3,
            },
            "title": {"disable": True},
        }
    return config


def valid_evaluation_config(
    config: object,
    *,
    arm: str,
    model: str,
    ollama_host: str,
    generation=None,
    seed: int | None = None,
    provider_npm: str = EVALUATION_PROVIDER_NPM,
) -> bool:
    if provider_npm != EVALUATION_PROVIDER_NPM:
        return False
    try:
        expected = expected_evaluation_config(
            arm=arm,
            model=model,
            ollama_host=ollama_host,
            generation=generation,
            seed=seed,
        )
    except (TypeError, ValueError):
        return False
    return config == expected


def build_config_manifest_entry(
    *,
    trial_index: int,
    arm: str,
    model: str,
    generation: GenerationSettings,
    seed: int,
) -> dict[str, Any]:
    if trial_index not in {1, 2, 3}:
        raise ValueError("config manifest trial index is invalid")
    permission = _expected_permission(arm)
    if not isinstance(generation, GenerationSettings):
        raise TypeError("generation must be GenerationSettings")
    contract = {
        "schema_version": 1,
        "trial_index": trial_index,
        "arm": arm,
        "model": model,
        "provider_npm": EVALUATION_PROVIDER_NPM,
        "selected_model": f"ollama/{model}",
        "model_map": {model: {"name": model}},
        "permission": permission,
        "tools": json.loads(_REVIEWED_READ_ONLY_TOOLS_JSON),
        "generation": generation.to_dict(),
        "seed": seed,
        "config_invariants": copy.deepcopy(_CONFIG_INVARIANTS),
    }
    return {
        "trial_index": trial_index,
        "arm": arm,
        "model": model,
        "seed": seed,
        "generation": generation.to_dict(),
        "contract_sha256": canonical_config_sha256(contract),
    }


def rebind_config_manifest(
    manifest: list[dict[str, Any]],
    ollama_host: str,
) -> dict[tuple[int, str], dict[str, Any]]:
    if not isinstance(manifest, list) or len(manifest) != 6:
        raise ValueError("config manifest must contain exactly six entries")
    expected_keys = {
        (trial_index, arm)
        for trial_index in (1, 2, 3)
        for arm in ("stock", "lac")
    }
    rebound: dict[tuple[int, str], dict[str, Any]] = {}
    for entry in manifest:
        if not isinstance(entry, dict) or set(entry) != {
            "trial_index",
            "arm",
            "model",
            "seed",
            "generation",
            "contract_sha256",
        }:
            raise ValueError("config manifest entry shape is invalid")
        key = (entry["trial_index"], entry["arm"])
        if key not in expected_keys or key in rebound:
            raise ValueError("config manifest keys are not exact and unique")
        try:
            generation = GenerationSettings(**entry["generation"])
            rebuilt = build_config_manifest_entry(
                trial_index=entry["trial_index"],
                arm=entry["arm"],
                model=entry["model"],
                generation=generation,
                seed=entry["seed"],
            )
            expected_config = expected_evaluation_config(
                arm=entry["arm"],
                model=entry["model"],
                ollama_host=ollama_host,
                generation=generation,
                seed=entry["seed"],
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("config manifest entry is invalid") from exc
        if rebuilt != entry:
            raise ValueError("config manifest contract digest is invalid")
        rebound[key] = {
            **entry,
            "ollama_host": ollama_host.rstrip("/"),
            "expected_canonical_sha256": canonical_config_sha256(
                expected_config
            ),
        }
    if set(rebound) != expected_keys:
        raise ValueError("config manifest key set is incomplete")
    return rebound


def valid_config_binding(
    binding: object,
    config: object,
    *,
    trial_index: int,
    arm: str,
    model: str,
    ollama_host: str,
    generation: GenerationSettings,
    seed: int,
) -> bool:
    if not isinstance(binding, dict) or set(binding) != {
        "trial_index",
        "arm",
        "model",
        "seed",
        "generation",
        "contract_sha256",
        "ollama_host",
        "expected_canonical_sha256",
    }:
        return False
    try:
        entry = build_config_manifest_entry(
            trial_index=trial_index,
            arm=arm,
            model=model,
            generation=generation,
            seed=seed,
        )
        expected = expected_evaluation_config(
            arm=arm,
            model=model,
            ollama_host=ollama_host,
            generation=generation,
            seed=seed,
        )
    except (TypeError, ValueError):
        return False
    return (
        {key: binding[key] for key in entry} == entry
        and binding["ollama_host"] == ollama_host.rstrip("/")
        and binding["expected_canonical_sha256"]
        == canonical_config_sha256(expected)
        and config == expected
    )


def build_evaluation_config(
    model: str,
    ollama_host: str,
    *,
    arm: str,
    generation=None,
    seed: int | None = None,
) -> dict[str, Any]:
    permission = (
        _STOCK_EVALUATION_PERMISSIONS
        if arm == "stock"
        else config_writer._FAIL_CLOSED_PERMISSIONS
    )
    config = config_writer.build_opencode_config(
        model,
        ollama_host,
        permission=permission,
        tools=_READ_ONLY_EVALUATION_TOOLS,
        evaluation=True,
        generation=generation,
        seed=seed,
    )
    if not valid_evaluation_config(
        config,
        arm=arm,
        model=model,
        ollama_host=ollama_host,
        generation=generation,
        seed=seed,
    ):
        raise ValueError(
            "generated evaluation config violates the verified contract"
        )
    return copy.deepcopy(config)


def build_evaluation_argv(
    binary: str | Path,
    prompt: str,
    model: str,
    workspace: str | Path,
) -> list[str]:
    return [
        str(binary),
        "run",
        prompt,
        "--format",
        "json",
        "--pure",
        "--auto",
        "--model",
        f"ollama/{model}",
        "--dir",
        str(workspace),
    ]


def evaluation_environment_flags() -> dict[str, str]:
    return dict(_ENVIRONMENT_FLAGS)
