"""Drift tripwire for the OpenCode surface LAC emits.

Validates that the config, agent profiles, slash commands, and plugin source
LAC generates stay inside the documented OpenCode schema LAC depends on.
When OpenCode's surface moves, these checks fail in CI before users do.
"""
import re
from typing import Any

OPENCODE_PERMISSION_KEYS = {
    "*",
    "read",
    "edit",
    "glob",
    "grep",
    "list",
    "bash",
    "task",
    "external_directory",
    "todowrite",
    "webfetch",
    "websearch",
    "lsp",
    "skill",
    "question",
    "doom_loop",
}

AGENT_FRONTMATTER_KEYS = {
    "description",
    "mode",
    "model",
    "prompt",
    "temperature",
    "top_p",
    "steps",
    "disable",
    "permission",
    "hidden",
    "color",
    "tools",
}

AGENT_MODES = {"primary", "subagent", "all"}

COMMAND_FRONTMATTER_KEYS = {"description", "agent", "model"}

_MODEL_REF = re.compile(r"^[A-Za-z0-9_.-]+/[^\s/].*$")


class SurfaceViolation(ValueError):
    pass


def _frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SurfaceViolation("missing frontmatter block")
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        raise SurfaceViolation("unterminated frontmatter block")
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip() or line[0] in (" ", "\t"):
            # Indented lines belong to a nested block (e.g. permission entries).
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise SurfaceViolation(f"malformed frontmatter line: {line!r}")
        fields[key.strip()] = value.strip()
    return fields


def validate_opencode_config(config: Any) -> None:
    if not isinstance(config, dict):
        raise SurfaceViolation("config must be an object")
    provider = config.get("provider")
    if not isinstance(provider, dict) or not provider:
        raise SurfaceViolation("config must declare at least one provider")
    for name, spec in provider.items():
        if not isinstance(spec, dict):
            raise SurfaceViolation(f"provider {name!r} must be an object")
        models = spec.get("models")
        if not isinstance(models, dict) or not models:
            raise SurfaceViolation(f"provider {name!r} must declare models")
    model = config.get("model")
    if not isinstance(model, str) or not _MODEL_REF.match(model):
        raise SurfaceViolation(f"model {model!r} is not a provider/model reference")
    permission = config.get("permission")
    if permission is not None:
        if not isinstance(permission, dict):
            raise SurfaceViolation("permission must be an object")
        unknown = set(permission) - OPENCODE_PERMISSION_KEYS
        if unknown:
            raise SurfaceViolation(f"unknown permission keys: {sorted(unknown)}")
    agent = config.get("agent")
    if agent is not None and not isinstance(agent, dict):
        raise SurfaceViolation("agent must be an object")


def validate_agent_profile(text: str) -> None:
    fields = _frontmatter(text)
    unknown = set(fields) - AGENT_FRONTMATTER_KEYS
    if unknown:
        raise SurfaceViolation(f"unknown agent frontmatter keys: {sorted(unknown)}")
    if not fields.get("description"):
        raise SurfaceViolation("agent profile requires a description")
    mode = fields.get("mode", "all")
    if mode not in AGENT_MODES:
        raise SurfaceViolation(f"unknown agent mode: {mode!r}")
    model = fields.get("model")
    if model is not None and not _MODEL_REF.match(model):
        raise SurfaceViolation(
            f"agent model {model!r} is not a provider/model reference"
        )
    steps = fields.get("steps")
    if steps is not None and not steps.isdigit():
        raise SurfaceViolation(f"agent steps must be an integer: {steps!r}")
    temperature = fields.get("temperature")
    if temperature is not None:
        try:
            float(temperature)
        except ValueError as exc:
            raise SurfaceViolation(
                f"agent temperature must be numeric: {temperature!r}"
            ) from exc


def validate_command_file(text: str) -> None:
    fields = _frontmatter(text)
    unknown = set(fields) - COMMAND_FRONTMATTER_KEYS
    if unknown:
        raise SurfaceViolation(f"unknown command frontmatter keys: {sorted(unknown)}")
    if not fields.get("description"):
        raise SurfaceViolation("command requires a description")


def validate_plugin_source(text: str) -> None:
    if "@opencode-ai/plugin" not in text:
        raise SurfaceViolation("plugin must import @opencode-ai/plugin")
    if "export const" not in text:
        raise SurfaceViolation("plugin must export a plugin function")
