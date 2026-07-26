"""Stock and LAC OpenCode evaluation arms for disposable workspaces."""
from __future__ import annotations

import json
import hashlib
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from backend.agent_launch.config_writer import (
    _FAIL_CLOSED_PERMISSIONS,
    write_opencode_config_file,
)
from backend.agent_launch.opencode_bin import resolve_opencode_binary
from backend.cookbook.proc import run as run_process

from .raw_ollama import _is_loopback_ollama_host
from .result import ArmResult
from .task import EvalTask


_KNOWN_EVENTS = frozenset(
    {"step_start", "tool_use", "text", "step_finish", "reasoning", "error"}
)
_READ_ONLY_EVAL_TOOLS = {
    "*": False,
    "read": True,
    "glob": True,
    "grep": True,
}
_STOCK_EVAL_PERMISSIONS = {"external_directory": "deny"}
_ENV_ALLOWLIST = (
    "COMSPEC",
    "PATH",
    "PATHEXT",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "WINDIR",
)


@dataclass(frozen=True)
class ParsedOpenCode:
    response: str
    completed: bool
    session_id: str | None
    metrics: dict[str, Any]
    errors: tuple[str, ...]
    events: tuple[dict[str, Any], ...]
    unknown_event_types: tuple[str, ...]


def _nonnegative_number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return value


def _error_text(event: dict[str, Any]) -> str:
    error = event.get("error")
    if not isinstance(error, dict):
        return str(error or "unknown error")
    name = str(error.get("name") or "Error")
    message = error.get("message")
    data = error.get("data")
    if not message and isinstance(data, dict):
        message = data.get("message")
    return f"{name}: {message or 'unknown error'}"


def parse_opencode_jsonl(stdout: str) -> ParsedOpenCode:
    events: list[dict[str, Any]] = []
    current_text_parts: list[str] = []
    terminal_text_parts: list[str] = []
    errors: list[str] = []
    unknown: list[str] = []
    session_id: str | None = None
    terminal_stop = False
    totals: dict[str, float | int] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "cost_usd": 0.0,
        "tool_calls": 0,
        "tool_errors": 0,
    }

    for line_number, raw_line in enumerate(str(stdout).splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"malformed_json_line:{line_number}")
            continue
        if not isinstance(event, dict):
            errors.append(f"non_object_json_line:{line_number}")
            continue
        events.append(event)
        event_type = event.get("type")
        if not isinstance(event_type, str):
            errors.append(f"missing_event_type:{line_number}")
            continue
        if event_type not in _KNOWN_EVENTS and event_type not in unknown:
            unknown.append(event_type)
        if session_id is None and isinstance(event.get("sessionID"), str):
            session_id = event["sessionID"]

        part = event.get("part")
        if not isinstance(part, dict):
            part = {}
        if event_type == "step_start":
            current_text_parts = []
        elif event_type == "text":
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                current_text_parts.append(text)
        elif event_type == "tool_use":
            totals["tool_calls"] += 1
            state = part.get("state")
            if (
                part.get("tool") == "invalid"
                or isinstance(state, dict)
                and state.get("status") == "error"
            ):
                totals["tool_errors"] += 1
        elif event_type == "step_finish":
            if part.get("reason") == "stop":
                terminal_stop = True
                terminal_text_parts = list(current_text_parts)
            cost = _nonnegative_number(part.get("cost"))
            if cost is not None:
                totals["cost_usd"] += float(cost)
            tokens = part.get("tokens")
            if isinstance(tokens, dict):
                for source, target in (
                    ("input", "input_tokens"),
                    ("output", "output_tokens"),
                    ("reasoning", "reasoning_tokens"),
                ):
                    value = _nonnegative_number(tokens.get(source))
                    if value is not None:
                        totals[target] += int(value)
                cache = tokens.get("cache")
                if isinstance(cache, dict):
                    for source, target in (
                        ("read", "cache_read_tokens"),
                        ("write", "cache_write_tokens"),
                    ):
                        value = _nonnegative_number(cache.get(source))
                        if value is not None:
                            totals[target] += int(value)
        elif event_type == "error":
            errors.append(f"opencode_error: {_error_text(event)}")

    response = "\n".join(terminal_text_parts)
    if not response.strip() and not errors:
        errors.append("empty_response")
    if not terminal_stop and not errors:
        errors.append("missing_terminal_step")
    totals["cost_usd"] = round(float(totals["cost_usd"]), 8)
    return ParsedOpenCode(
        response=response,
        completed=bool(response.strip()) and terminal_stop and not errors,
        session_id=session_id,
        metrics=totals,
        errors=tuple(errors),
        events=tuple(events),
        unknown_event_types=tuple(unknown),
    )


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _failure(
    arm: str,
    model: str,
    started: float,
    errors: tuple[str, ...],
    *,
    timed_out: bool = False,
    exit_code: int | None = None,
    stdout: str = "",
    stderr: str = "",
    metrics: dict[str, Any] | None = None,
) -> ArmResult:
    return ArmResult(
        arm=arm,
        model=model,
        runtime="opencode",
        completed=False,
        timed_out=timed_out,
        response="",
        wall_time_ms=round((time.perf_counter() - started) * 1000, 3),
        errors=errors, metrics=metrics or {},
        exit_code=exit_code,
        raw_stdout=stdout,
        raw_stderr=stderr,
    )


def _config_identity(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("evaluation config is missing or linked")
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("evaluation config changed during identity capture")
    return {"path": str(path.resolve()), "size": before.st_size, "sha256": digest.hexdigest()}


def _isolated_environment(
    workspace: Path,
    runtime_root: Path,
    config_path: Path,
    ollama_host: str,
) -> dict[str, str]:
    isolated_home = runtime_root / "home"
    temp_dir = runtime_root / "temp"
    appdata = isolated_home / "AppData" / "Roaming"
    local_appdata = isolated_home / "AppData" / "Local"
    xdg_config = isolated_home / ".config"
    xdg_data = isolated_home / ".local" / "share"
    xdg_cache = isolated_home / ".cache"
    for directory in (
        isolated_home,
        temp_dir,
        appdata,
        local_appdata,
        xdg_config,
        xdg_data,
        xdg_cache,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    environment = {
        key: os.environ[key]
        for key in _ENV_ALLOWLIST
        if key in os.environ
    }
    environment.update(
        {
            "HOME": str(isolated_home),
            "USERPROFILE": str(isolated_home),
            "APPDATA": str(appdata),
            "LOCALAPPDATA": str(local_appdata),
            "XDG_CONFIG_HOME": str(xdg_config),
            "XDG_DATA_HOME": str(xdg_data),
            "XDG_CACHE_HOME": str(xdg_cache),
            "TEMP": str(temp_dir),
            "TMP": str(temp_dir),
            "OLLAMA_HOST": ollama_host,
            "NO_PROXY": "localhost,127.0.0.1,::1",
            "OPENCODE_CONFIG": str(config_path),
            "OPENCODE_CONFIG_DIR": str(runtime_root),
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
            "OPENCODE_DISABLE_PRUNE": "1",
            "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
            "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
            "OPENCODE_DISABLE_MODELS_FETCH": "1",
            "OPENCODE_DISABLE_CLAUDE_CODE": "1",
            "OPENCODE_DISABLE_CLAUDE_CODE_PROMPT": "1",
            "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS": "1",
            "OPENCODE_AUTO_SHARE": "false",
            "OPENCODE_ENABLE_EXA": "0",
        }
    )
    return environment


def _run_opencode(
    task: EvalTask,
    model: str,
    ollama_host: str,
    workspace: str | Path,
    arm: str,
    *,
    resolve_bin_fn: Callable[[], Path] = resolve_opencode_binary,
    run_fn: Callable[..., Any] = run_process,
) -> ArmResult:
    started = time.perf_counter()
    workspace_path = Path(workspace).resolve()
    if not _is_loopback_ollama_host(ollama_host):
        return _failure(
            arm, model, started, ("non_loopback_ollama_host",)
        )
    try:
        if arm not in {"stock", "lac"}:
            raise ValueError(f"unknown OpenCode evaluation arm: {arm}")
        runtime_root = workspace_path.parent / f".lac-eval-runtime-{arm}"
        config_path = runtime_root / "opencode.json"
        permission = (
            _STOCK_EVAL_PERMISSIONS
            if arm == "stock"
            else _FAIL_CLOSED_PERMISSIONS
        )
        write_opencode_config_file(
            config_path,
            model,
            ollama_host,
            permission=permission,
            tools=_READ_ONLY_EVAL_TOOLS,
        )
        config_before = _config_identity(config_path)
        environment = _isolated_environment(
            workspace_path, runtime_root, config_path, ollama_host
        )
        binary = resolve_bin_fn()
    except Exception as exc:
        return _failure(
            arm, model, started, (f"{type(exc).__name__}: {exc}",)
        )

    argv = [
        str(binary),
        "run",
        task.prompt,
        "--format",
        "json",
        "--pure",
        "--auto",
        "--model",
        f"ollama/{model}",
        "--dir",
        str(workspace_path),
    ]
    try:
        process = run_fn(
            argv,
            cwd=str(workspace_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=task.timeout_seconds,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        config_after = _config_identity(config_path)
        return _failure(
            arm,
            model,
            started,
            (f"timeout:{task.timeout_seconds}s",),
            timed_out=True,
            stdout=_as_text(exc.output),
            stderr=_as_text(exc.stderr),
            metrics={"opencode_config_identity": {"before": config_before, "after": config_after}},
        )
    except Exception as exc:
        config_after = _config_identity(config_path)
        return _failure(
            arm, model, started, (f"{type(exc).__name__}: {exc}",), metrics={"opencode_config_identity": {"before": config_before, "after": config_after}},
        )

    config_after = _config_identity(config_path)
    config_metrics = {"opencode_config_identity": {"before": config_before, "after": config_after}}

    stdout = _as_text(getattr(process, "stdout", ""))
    stderr = _as_text(getattr(process, "stderr", ""))
    exit_code = int(getattr(process, "returncode", 1))
    if exit_code != 0:
        return _failure(
            arm,
            model,
            started,
            (f"exit_code:{exit_code}",),
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            metrics=config_metrics,
        )

    parsed = parse_opencode_jsonl(stdout)
    metrics = dict(parsed.metrics)
    metrics["approval_mode"] = "auto_disposable_workspace"
    metrics.update(config_metrics)
    return ArmResult(
        arm=arm,
        model=model,
        runtime="opencode",
        completed=parsed.completed,
        timed_out=False,
        response=parsed.response,
        wall_time_ms=round((time.perf_counter() - started) * 1000, 3),
        metrics=metrics,
        errors=parsed.errors,
        exit_code=exit_code,
        raw_stdout=stdout,
        raw_stderr=stderr,
        events=parsed.events,
        unknown_event_types=parsed.unknown_event_types,
    )


def run_stock(
    task: EvalTask,
    model: str,
    ollama_host: str,
    workspace: str | Path,
    **kwargs: Any,
) -> ArmResult:
    return _run_opencode(
        task, model, ollama_host, workspace, "stock", **kwargs
    )


def run_lac(
    task: EvalTask,
    model: str,
    ollama_host: str,
    workspace: str | Path,
    **kwargs: Any,
) -> ArmResult:
    return _run_opencode(
        task, model, ollama_host, workspace, "lac", **kwargs
    )
