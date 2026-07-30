"""Provision the GLOBAL OpenCode config the same way ``lac agent`` provisions
a per-project one: merge LAC's Ollama provider, fail-closed permissions,
slash commands, and plugin into whatever the user already has -- never
clobber. Every run backs the existing config up; ``lac setup --undo``
restores it."""
import copy
import json
from pathlib import Path

from backend.agent_launch.config_writer import (
    _FAIL_CLOSED_PERMISSIONS,
    build_opencode_config,
    write_commands_into,
    write_plugin_into,
)

BACKUP_NAME = "opencode.json.lac-backup"


def global_opencode_dir(opencode_dir=None) -> Path:
    if opencode_dir is not None:
        return Path(opencode_dir)
    return Path.home() / ".config" / "opencode"


def merge_opencode_config(existing, lac_config) -> tuple[dict, dict]:
    """Merge LAC's config over the user's existing one.

    LAC owns ``provider.ollama``; every other provider, an already-chosen
    ``model``, and the user's explicit permission entries win over LAC's
    fail-closed defaults. Returns ``(merged, notes)`` where
    ``notes["model_preserved"]`` says whether the user's model was kept.
    """
    base = copy.deepcopy(existing) if isinstance(existing, dict) else {}
    notes = {"model_preserved": False}
    base.setdefault("$schema", lac_config.get("$schema"))

    existing_providers = base.get("provider")
    providers = (
        copy.deepcopy(existing_providers)
        if isinstance(existing_providers, dict)
        else {}
    )
    providers["ollama"] = copy.deepcopy(lac_config["provider"]["ollama"])
    base["provider"] = providers

    if base.get("model"):
        notes["model_preserved"] = True
    else:
        base["model"] = lac_config["model"]

    lac_permission = lac_config.get("permission")
    if isinstance(lac_permission, dict):
        merged = copy.deepcopy(lac_permission)
        user_permission = base.get("permission")
        if isinstance(user_permission, dict):
            for key, value in user_permission.items():
                if isinstance(value, dict) and isinstance(merged.get(key), dict):
                    merged[key] = {
                        **copy.deepcopy(merged[key]),
                        **copy.deepcopy(value),
                    }
                else:
                    merged[key] = copy.deepcopy(value)
        base["permission"] = merged

    return base, notes


def write_global_opencode_config(
    model,
    ollama_host,
    *,
    opencode_dir=None,
    cli_prefix=None,
    pro_available=False,
) -> dict:
    oc_dir = global_opencode_dir(opencode_dir)
    oc_dir.mkdir(parents=True, exist_ok=True)
    config_path = oc_dir / "opencode.json"

    backup_path = None
    existing = None
    if config_path.exists():
        raw = config_path.read_text(encoding="utf-8")
        backup_path = oc_dir / BACKUP_NAME
        backup_path.write_text(raw, encoding="utf-8")
        try:
            existing = json.loads(raw)
        except json.JSONDecodeError:
            existing = None

    lac_config = build_opencode_config(
        model, ollama_host, permission=_FAIL_CLOSED_PERMISSIONS
    )
    merged, notes = merge_opencode_config(existing, lac_config)
    config_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")

    commands = write_commands_into(oc_dir / "commands", pro_available, cli_prefix)
    plugin = write_plugin_into(oc_dir / "plugins", cli_prefix)

    return {
        "config": config_path,
        "backup": backup_path,
        "commands": commands,
        "plugin": plugin,
        "model": model,
        "model_preserved": notes["model_preserved"],
    }


def undo_global_opencode_setup(opencode_dir=None) -> Path:
    oc_dir = global_opencode_dir(opencode_dir)
    backup = oc_dir / BACKUP_NAME
    if not backup.exists():
        raise FileNotFoundError(f"No LAC backup at {backup} - nothing to undo.")
    target = oc_dir / "opencode.json"
    target.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
    backup.unlink()
    return target
