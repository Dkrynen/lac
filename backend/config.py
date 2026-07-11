from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from backend.cookbook.config import (
    CONFIG_DIR as USER_CONFIG_DIR,
    DEFAULT_WORKSPACE,
    load_config as load_user_config,
)

PROJECT_DIR = Path(".apt")
PROJECT_CONFIG = PROJECT_DIR / "apt.jsonc"
MAX_PROJECT_CONFIG_BYTES = 1024 * 1024
_FILE_ATTRIBUTE_REPARSE_POINT = int(
    getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
)

SCHEMA_PATH = Path(__file__).resolve().parent / "schema" / "apt.schema.json"


class UnsafeProjectConfigError(ValueError):
    """A project config path crossed the local no-link trust boundary."""


@dataclass(frozen=True)
class _ProjectConfigIdentity:
    root: Path
    path: Path
    root_file_id: tuple[int, int]
    apt_file_id: tuple[int, int]
    config_file_id: tuple[int, int]
    size: int
    mtime_ns: int


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


_SANDBOX_TASK_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_SANDBOX_CONTEXT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SANDBOX_LOCAL_IMAGE_ID = re.compile(r"^sha256:[0-9a-fA-F]{64}$")
_SANDBOX_PINNED_IMAGE = re.compile(
    r"^[A-Za-z0-9][^\s@]*@sha256:[0-9a-fA-F]{64}$"
)


class SandboxTaskConfig(BaseModel):
    """One operator-authored command exposed to the model by name only."""

    model_config = {"extra": "forbid"}

    argv: list[str] = Field(min_length=1, max_length=64)
    timeout_seconds: int = Field(default=120, ge=1, le=300)

    @field_validator("argv")
    @classmethod
    def _validate_argv(cls, argv: list[str]) -> list[str]:
        total = 0
        for index, value in enumerate(argv):
            if not isinstance(value, str):
                raise ValueError("sandbox task argv entries must be strings")
            if not value or len(value) > 4096:
                raise ValueError("sandbox task argv entries must contain 1-4096 characters")
            if "\x00" in value or any(ord(ch) < 32 for ch in value):
                raise ValueError("sandbox task argv entries cannot contain control characters")
            if index == 0 and value.startswith("-"):
                raise ValueError("sandbox task executable cannot begin with '-'")
            total += len(value)
        if total > 32768:
            raise ValueError("sandbox task argv exceeds 32768 characters")
        return argv


class SandboxConfig(BaseModel):
    """Docker-only, local-first task sandbox configuration."""

    model_config = {"extra": "forbid"}

    engine: Literal["docker"] = "docker"
    context: str
    image: str
    snapshot_include: list[str] = Field(min_length=1, max_length=128)
    tasks: dict[str, SandboxTaskConfig] = Field(min_length=1, max_length=64)

    @field_validator("context")
    @classmethod
    def _validate_context(cls, context: str) -> str:
        if not _SANDBOX_CONTEXT_NAME.fullmatch(context):
            raise ValueError("sandbox context must be a bounded Docker context name")
        return context

    @field_validator("image")
    @classmethod
    def _validate_image(cls, image: str) -> str:
        if len(image) > 512 or not (
            _SANDBOX_LOCAL_IMAGE_ID.fullmatch(image)
            or _SANDBOX_PINNED_IMAGE.fullmatch(image)
        ):
            raise ValueError(
                "sandbox image must be digest-pinned or an exact sha256 image id"
            )
        return image

    @field_validator("snapshot_include")
    @classmethod
    def _validate_snapshot_include(cls, patterns: list[str]) -> list[str]:
        total = 0
        forbidden_catchalls = {"*", "**", "**/*"}
        for pattern in patterns:
            if (
                not isinstance(pattern, str)
                or not pattern
                or len(pattern) > 256
                or pattern in forbidden_catchalls
                or pattern.startswith("/")
                or "\\" in pattern
                or "\x00" in pattern
                or any(ord(character) < 32 for character in pattern)
            ):
                raise ValueError(
                    "sandbox snapshot patterns must be bounded relative POSIX globs"
                )
            parts = pattern.split("/")
            if any(part in ("", ".", "..") for part in parts):
                raise ValueError(
                    "sandbox snapshot patterns cannot contain empty or parent segments"
                )
            total += len(pattern)
        if total > 16384:
            raise ValueError("sandbox snapshot include policy is too large")
        if len(set(patterns)) != len(patterns):
            raise ValueError("sandbox snapshot patterns must be unique")
        return patterns

    @field_validator("tasks")
    @classmethod
    def _validate_task_names(
        cls, tasks: dict[str, SandboxTaskConfig]
    ) -> dict[str, SandboxTaskConfig]:
        invalid = [name for name in tasks if not _SANDBOX_TASK_NAME.fullmatch(name)]
        if invalid:
            raise ValueError(f"invalid sandbox task name: {invalid[0]!r}")
        return tasks


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
    sandbox: SandboxConfig | None = None


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


def _is_link_or_reparse(st: os.stat_result) -> bool:
    attributes = int(getattr(st, "st_file_attributes", 0) or 0)
    return stat.S_ISLNK(st.st_mode) or bool(
        attributes & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _inspect_project_config_at_root(
    root: Path,
) -> _ProjectConfigIdentity | None:
    """Inspect one exact config path without following child indirection."""

    try:
        canonical_root = root.resolve(strict=True)
        root_st = canonical_root.stat()
    except (OSError, RuntimeError):
        return None
    if not stat.S_ISDIR(root_st.st_mode):
        return None

    apt_dir = root / ".apt"
    try:
        apt_st = apt_dir.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise UnsafeProjectConfigError(
            "project config directory is unavailable or unsafe"
        ) from exc
    if (
        _is_link_or_reparse(apt_st)
        or not stat.S_ISDIR(apt_st.st_mode)
        or apt_st.st_dev != root_st.st_dev
        or os.path.ismount(apt_dir)
    ):
        raise UnsafeProjectConfigError(
            "project config directory is linked or unsafe"
        )

    path = apt_dir / "apt.jsonc"
    try:
        config_st = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise UnsafeProjectConfigError(
            "project config file is unavailable or unsafe"
        ) from exc
    if (
        _is_link_or_reparse(config_st)
        or not stat.S_ISREG(config_st.st_mode)
        or config_st.st_dev != root_st.st_dev
        or int(getattr(config_st, "st_nlink", 1) or 1) != 1
        or config_st.st_size > MAX_PROJECT_CONFIG_BYTES
    ):
        raise UnsafeProjectConfigError(
            "project config file is linked, oversized, or unsafe"
        )
    try:
        path.resolve(strict=True).relative_to(canonical_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise UnsafeProjectConfigError(
            "project config file escapes its project root"
        ) from exc
    return _ProjectConfigIdentity(
        root=canonical_root,
        path=path,
        root_file_id=(int(root_st.st_dev), int(root_st.st_ino)),
        apt_file_id=(int(apt_st.st_dev), int(apt_st.st_ino)),
        config_file_id=(int(config_st.st_dev), int(config_st.st_ino)),
        size=int(config_st.st_size),
        mtime_ns=int(config_st.st_mtime_ns),
    )


def _read_project_config(identity: _ProjectConfigIdentity) -> str:
    """Read one bounded config and prove its path identity stayed stable."""

    flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0))
    flags |= int(getattr(os, "O_NOFOLLOW", 0))
    fd: int | None = None
    try:
        fd = os.open(identity.path, flags)
        with os.fdopen(fd, "rb") as handle:
            fd = None
            opened = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or int(getattr(opened, "st_nlink", 1) or 1) != 1
                or (int(opened.st_dev), int(opened.st_ino))
                != identity.config_file_id
            ):
                raise UnsafeProjectConfigError(
                    "project config file changed during read"
                )
            payload = handle.read(MAX_PROJECT_CONFIG_BYTES + 1)
            completed = os.fstat(handle.fileno())
    except UnsafeProjectConfigError:
        raise
    except OSError as exc:
        raise UnsafeProjectConfigError(
            "project config file could not be read safely"
        ) from exc
    finally:
        if fd is not None:
            os.close(fd)
    if len(payload) > MAX_PROJECT_CONFIG_BYTES:
        raise UnsafeProjectConfigError("project config file exceeds the size limit")
    final_identity = _inspect_project_config_at_root(identity.root)
    if (
        final_identity != identity
        or (int(completed.st_dev), int(completed.st_ino))
        != identity.config_file_id
        or int(completed.st_size) != identity.size
        or int(completed.st_mtime_ns) != identity.mtime_ns
    ):
        raise UnsafeProjectConfigError("project config file changed during read")
    try:
        return payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise UnsafeProjectConfigError(
            "project config file is not valid UTF-8"
        ) from exc


def find_project_root(start: Path | None = None) -> Path | None:
    here = Path(start) if start else Path.cwd()
    for candidate in [here, *here.parents]:
        identity = _inspect_project_config_at_root(candidate)
        if identity is not None:
            return identity.root
    return None


def _load_project_config_at_root(root: Path) -> AptProjectConfig:
    identity = _inspect_project_config_at_root(root)
    if identity is None:
        return AptProjectConfig()
    raw = _read_project_config(identity)
    try:
        data = json.loads(strip_jsonc(raw))
        return AptProjectConfig.model_validate(data)
    except Exception:
        return AptProjectConfig()


def load_project_config(start: Path | None = None) -> AptProjectConfig:
    root = find_project_root(start)
    return _load_project_config_at_root(root) if root else AptProjectConfig()


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
    root = find_project_root(start)
    project = _load_project_config_at_root(root) if root else AptProjectConfig()

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
