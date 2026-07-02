from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import resolve_config
from .base import Agent
from .permissions import FULL_PERMISSIONS, READONLY_PERMISSIONS, Permissions

AGENT_DIR = Path(".apt") / "agent"

_BUILTIN_AGENTS: list[dict[str, Any]] = [
    {
        "name": "build",
        "type": "build",
        "description": "Full-access implementation agent. Can edit files, run commands, install packages.",
        "system_prompt": "You are the Apt build agent. You have full filesystem and shell access. Make changes, run commands, and verify your work. Be direct and minimal.",
        "permissions": FULL_PERMISSIONS.to_dict(),
        "tools": ["read_file", "write_file", "list_files", "run_bash"],
    },
    {
        "name": "plan",
        "type": "plan",
        "description": "Read-only analysis agent. Can read files and search code, but makes NO edits.",
        "system_prompt": "You are the Apt plan agent. You have read-only access. Analyze, propose plans, and answer questions. Never edit or delete files.",
        "permissions": READONLY_PERMISSIONS.to_dict(),
        "tools": ["read_file", "list_files"],
    },
    {
        "name": "explore",
        "type": "explore",
        "description": "Research agent. Gathers information from the web, docs, and the codebase.",
        "system_prompt": "You are the Apt explore agent. You research and gather context using web search and codebase exploration. Summarize findings with sources.",
        "permissions": READONLY_PERMISSIONS.to_dict(),
        "tools": ["read_file", "list_files", "web_search"],
    },
]


def _agent_from_dict(data: dict[str, Any]) -> Agent:
    perms = Permissions.from_dict(data.get("permissions", {}) or {})
    return Agent(
        name=data.get("name", "agent"),
        type=data.get("type", "build"),
        description=data.get("description", ""),
        model=data.get("model"),
        system_prompt=data.get("system_prompt", ""),
        permissions=perms,
        tools=list(data.get("tools", []) or []),
        raw=data,
    )


def _builtin_agents() -> list[Agent]:
    return [_agent_from_dict(d) for d in _BUILTIN_AGENTS]


def _project_agents(start: Path | None = None) -> list[Agent]:
    root = resolve_config(start).project_root
    out: list[Agent] = []
    if not root:
        return out
    agent_dir = root / AGENT_DIR
    if not agent_dir.exists():
        return out
    for f in sorted(agent_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        data.pop("$schema", None)
        out.append(_agent_from_dict(data))
    return out


def list_agents(start: Path | None = None) -> list[Agent]:
    by_name: dict[str, Agent] = {}
    for a in _builtin_agents():
        by_name[a.name] = a
    for a in _project_agents(start):
        by_name[a.name] = a
    enabled = resolve_config(start).project.agents
    if enabled:
        return [by_name[n] for n in enabled if n in by_name]
    return list(by_name.values())


def get_agent(name: str, start: Path | None = None) -> Agent | None:
    for a in list_agents(start):
        if a.name == name:
            return a
    return None


def default_agent(start: Path | None = None) -> Agent:
    agents = list_agents(start)
    return agents[0] if agents else _agent_from_dict(_BUILTIN_AGENTS[0])
