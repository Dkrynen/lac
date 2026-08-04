"""Per-project agent profiles (tweakability 3b).

A project profile records the agent LAC prepared for THIS project — pinned
model, context request, permission preset — at <project>/.opencode/lac-profile.json.
`lac agent` honors it instead of re-rolling on every launch; `--model` pins
explicitly; `--reselect` re-picks and updates. A malformed profile is a loud
error, never a silent fallback: the user's explicit project state wins.

Safety floor: every permission preset denies secret-shaped reads and keeps
external_directory/task denied. No profile value can relax that floor.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

PROFILE_FILENAME = "lac-profile.json"
PROFILE_SCHEMA_VERSION = 1

_STRICT_PERMISSIONS = {
    "*": "ask",
    "read": {
        "*": "allow",
        "*.env": "deny",
        "*.env.*": "deny",
        "*credentials.json": "deny",
        "*token.json": "deny",
        "*.pem": "deny",
        "*.key": "deny",
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
    "task": "deny",
}

_DEV_PERMISSIONS = {
    **_STRICT_PERMISSIONS,
    "grep": "allow",
    "edit": "allow",
    "bash": "allow",
    "webfetch": "allow",
}

PRESETS: dict[str, dict] = {
    "strict": _STRICT_PERMISSIONS,
    "dev": _DEV_PERMISSIONS,
}


class ProfileError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ProjectProfile:
    model: str
    context: int | None = None
    preset: str = "strict"
    updated_at: str = ""


def profile_path(project_dir) -> Path:
    return Path(project_dir) / ".opencode" / PROFILE_FILENAME


def preset_permissions(preset: str) -> dict:
    if preset not in PRESETS:
        raise ProfileError(
            f"Unknown permission preset '{preset}'. Available: {', '.join(sorted(PRESETS))}."
        )
    return copy.deepcopy(PRESETS[preset])


def load_profile(project_dir) -> ProjectProfile | None:
    path = profile_path(project_dir)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileError(f"Project profile at {path} is unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProfileError(f"Project profile at {path} must be a JSON object.")
    version = raw.get("schema_version")
    if version != PROFILE_SCHEMA_VERSION:
        raise ProfileError(
            f"Project profile at {path} has schema_version {version!r}; "
            f"LAC supports {PROFILE_SCHEMA_VERSION}."
        )
    model = raw.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ProfileError(f"Project profile at {path} needs a non-empty 'model'.")
    preset = raw.get("preset", "strict")
    if not isinstance(preset, str) or preset not in PRESETS:
        raise ProfileError(
            f"Project profile at {path} has unknown preset {preset!r}. "
            f"Available: {', '.join(sorted(PRESETS))}."
        )
    context = raw.get("context")
    if context is not None and (not isinstance(context, int) or isinstance(context, bool) or context <= 0):
        raise ProfileError(f"Project profile at {path} has an invalid 'context'.")
    return ProjectProfile(
        model=model.strip(),
        context=context,
        preset=preset,
        updated_at=raw.get("updated_at", ""),
    )


def save_profile(project_dir, profile: ProjectProfile) -> Path:
    path = profile_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamped = ProjectProfile(
        model=profile.model,
        context=profile.context,
        preset=profile.preset,
        updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    payload = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "model": stamped.model,
        "context": stamped.context,
        "preset": stamped.preset,
        "updated_at": stamped.updated_at,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
