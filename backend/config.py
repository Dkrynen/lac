from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.cookbook.config import (
    CONFIG_DIR as USER_CONFIG_DIR,
    DEFAULT_WORKSPACE,
    load_config as load_user_config,
)

PROJECT_DIR = Path(".apt")
PROJECT_CONFIG = PROJECT_DIR / "apt.jsonc"

SCHEMA_PATH = Path(__file__).resolve().parent / "schema" / "apt.schema.json"


class ProviderConfig(BaseModel):
    type: str = "ollama"
    base_url: str | None = None
    api_key: str | None = None
    api_key_env: str | None = None
    default_model: str | None = None


class MCPServerConfig(BaseModel):
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    transport: str = "stdio"
    enabled: bool = True


class MCPConfig(BaseModel):
    servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


class AgentRef(BaseModel):
    name: str
    type: str = "build"
    description: str = ""
    model: str | None = None
    system_prompt: str = ""
    permissions: dict[str, Any] = Field(default_factory=dict)
    tools: list[str] = Field(default_factory=list)


class AptProjectConfig(BaseModel):
    model_config = {"extra": "allow"}

    schema_ref: str | None = Field(default=None, alias="$schema")
    workspace: str | None = None
    ollama_host: str | None = None
    theme: str | None = None
    default_model: str | None = None
    default_context: int | None = None
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    agents: list[str] | None = None
    plugins: dict[str, dict] = Field(default_factory=dict)
    permission: dict[str, Any] = Field(default_factory=dict)
    update: dict[str, Any] = Field(default_factory=dict)


def strip_jsonc(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    lines = []
    for line in text.splitlines():
        in_str = False
        esc = False
        out = []
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "\\" and in_str and not esc:
                esc = True
                out.append(ch)
                i += 1
                continue
            if ch == '"' and not esc:
                in_str = not in_str
                out.append(ch)
                i += 1
                continue
            if not in_str and ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
                break
            esc = False
            out.append(ch)
            i += 1
        lines.append("".join(out))
    return "\n".join(lines)


def parse_jsonc(path: Path) -> dict:
    raw = Path(path).read_text(encoding="utf-8")
    cleaned = strip_jsonc(raw)
    return json.loads(cleaned)


def project_config_path() -> Path:
    cwd = Path.cwd()
    return cwd / PROJECT_CONFIG


def find_project_root(start: Path | None = None) -> Path | None:
    here = Path(start) if start else Path.cwd()
    for cand in [here, *here.parents]:
        if (cand / ".apt" / "apt.jsonc").exists():
            return cand
    return None


def load_project_config(start: Path | None = None) -> AptProjectConfig:
    root = find_project_root(start)
    if not root:
        return AptProjectConfig()
    path = root / ".apt" / "apt.jsonc"
    if not path.exists():
        return AptProjectConfig()
    try:
        data = parse_jsonc(path)
        return AptProjectConfig.model_validate(data)
    except Exception:
        return AptProjectConfig()


@dataclass
class ResolvedConfig:
    workspace: str
    ollama_host: str
    theme: str
    default_model: str
    default_context: int
    project: AptProjectConfig
    project_root: Path | None

    @property
    def providers(self) -> dict[str, ProviderConfig]:
        return self.project.providers

    @property
    def mcp_servers(self) -> dict[str, MCPServerConfig]:
        return {k: v for k, v in self.project.mcp.servers.items() if v.enabled}


_DEFAULTS = {
    "workspace": DEFAULT_WORKSPACE,
    "ollama_host": "http://localhost:11434",
    "theme": "apt-dark",
    "default_model": "",
    "default_context": 4096,
}


def resolve_config(start: Path | None = None) -> ResolvedConfig:
    user = load_user_config()
    project = load_project_config(start)
    root = find_project_root(start)

    def pick(field: str) -> Any:
        pv = getattr(project, field, None)
        return pv if pv is not None else getattr(user, field, None) or _DEFAULTS[field]

    host = pick("ollama_host")
    if os.environ.get("OLLAMA_HOST"):
        host = os.environ["OLLAMA_HOST"]

    return ResolvedConfig(
        workspace=pick("workspace"),
        ollama_host=host,
        theme=pick("theme"),
        default_model=pick("default_model"),
        default_context=pick("default_context"),
        project=project,
        project_root=root,
    )


def schema_path() -> Path:
    return SCHEMA_PATH


def schema_uri() -> str:
    return SCHEMA_PATH.as_uri()
