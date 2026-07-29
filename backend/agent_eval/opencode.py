"""Stock and LAC OpenCode evaluation arms for disposable workspaces."""
from __future__ import annotations

import json
import hashlib
import msvcrt
import ctypes
import os
import stat
import subprocess
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from backend.agent_launch.config_writer import (
    write_opencode_config_payload,
)
from backend.agent_launch.opencode_bin import resolve_opencode_binary

from .capture import (
    DEFAULT_CAPTURE_LIMITS,
    CaptureLimits,
    CapturedProcess,
    run_bounded_process,
)
from .raw_ollama import _is_loopback_ollama_host
from .opencode_contract import (
    build_evaluation_argv,
    build_evaluation_config,
    canonical_config_sha256,
    evaluation_environment_flags,
    valid_config_binding,
    valid_evaluation_config,
)
from .result import ArmResult
from .schedule import GenerationSettings, TrialSpec
from .task import EvalTask


_KNOWN_EVENTS = frozenset(
    {"step_start", "tool_use", "text", "step_finish", "reasoning", "error"}
)
_ENV_ALLOWLIST = (
    "COMSPEC",
    "PATH",
    "PATHEXT",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "WINDIR",
)
_GENERIC_READ = 0x80000000
_FILE_SHARE_READ = 0x00000001
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_REPARSE_POINT = 0x0400
_INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value


@dataclass(frozen=True)
class ParsedOpenCode:
    response: str
    completed: bool
    session_id: str | None
    metrics: dict[str, Any]
    errors: tuple[str, ...]
    events: tuple[dict[str, Any], ...]
    unknown_event_types: tuple[str, ...]


@dataclass(frozen=True)
class _ProcessOutcome:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    errors: tuple[str, ...]
    raw_stdout: bytes
    raw_stderr: bytes
    capture: dict[str, Any]


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


def parse_opencode_jsonl(
    stdout: str | bytes,
    *,
    max_line_bytes: int = DEFAULT_CAPTURE_LIMITS.jsonl_line_bytes,
    max_events: int = DEFAULT_CAPTURE_LIMITS.jsonl_events,
) -> ParsedOpenCode:
    if type(max_line_bytes) is not int or max_line_bytes < 0:
        raise ValueError("max_line_bytes must be a non-negative integer")
    if type(max_events) is not int or max_events < 0:
        raise ValueError("max_events must be a non-negative integer")
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

    payload = stdout if isinstance(stdout, bytes) else stdout.encode("utf-8")
    for line_number, raw_line in enumerate(payload.splitlines(), start=1):
        if len(raw_line) > max_line_bytes:
            errors.append(f"jsonl_line_limit_exceeded:{line_number}")
            break
        line_bytes = raw_line.strip()
        if not line_bytes:
            continue
        if len(events) >= max_events:
            errors.append(f"jsonl_event_limit_exceeded:{line_number}")
            break
        line = line_bytes.decode("utf-8", errors="replace")
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
    capture: dict[str, Any] | None = None,
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
        capture=capture or {},
    )


def _config_identity(fd: int, path: Path) -> dict[str, Any]:
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("evaluation config handle is not a regular file")
    digest = hashlib.sha256()
    chunks = []
    measured_size = 0
    os.lseek(fd, 0, os.SEEK_SET)
    while True:
        chunk = os.read(fd, 64 * 1024)
        if not chunk:
            break
        measured_size += len(chunk)
        digest.update(chunk)
        chunks.append(chunk)
    after = os.fstat(fd)
    before_token = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_token = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if before_token != after_token or measured_size != before.st_size:
        raise ValueError("evaluation config changed during identity capture")
    try:
        config = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("evaluation config is not valid UTF-8 JSON") from exc
    if not isinstance(config, dict):
        raise ValueError("evaluation config root is not an object")
    return {
        "path": str(path.resolve(strict=True)),
        "size": measured_size,
        "sha256": digest.hexdigest(),
        "canonical_sha256": canonical_config_sha256(config),
    }


def _lock_config(path: Path) -> int:
    """Open config read-only: children may read, nobody may write/delete/replace."""
    if not os.path.lexists(path):
        raise ValueError("evaluation config is missing")
    link_stat = path.lstat()
    if (
        path.is_symlink()
        or getattr(link_stat, "st_file_attributes", 0) & _REPARSE_POINT
        or not stat.S_ISREG(link_stat.st_mode)
    ):
        raise ValueError("evaluation config is linked or not a regular file")
    expected = path.stat()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    raw_handle = create_file(
        str(path),
        _GENERIC_READ,
        _FILE_SHARE_READ,
        None,
        _OPEN_EXISTING,
        _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if raw_handle == _INVALID_HANDLE_VALUE:
        raise OSError(ctypes.get_last_error(), "unable to lock evaluation config")
    try:
        fd = msvcrt.open_osfhandle(raw_handle, os.O_RDONLY | os.O_BINARY)
    except Exception as exc:
        if not close_handle(raw_handle):
            raise OSError(
                ctypes.get_last_error(),
                "unable to close evaluation config handle after fd transfer failure",
            ) from exc
        raise
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
            raise ValueError("evaluation config identity changed before lock")
    except Exception:
        os.close(fd)
        raise
    return fd


def _run_process_outcome(
    run_fn: Callable[..., Any],
    argv: list[str],
    *,
    workspace_path: Path,
    timeout_seconds: int,
    environment: dict[str, str],
    limits: CaptureLimits,
    launcher: Callable[..., object] | None,
) -> _ProcessOutcome:
    try:
        kwargs: dict[str, Any] = {
            "cwd": str(workspace_path),
            "env": environment,
            "timeout": timeout_seconds,
            "limits": limits,
        }
        if launcher is not None:
            kwargs["launcher"] = launcher
        process = run_fn(argv, **kwargs)
        if isinstance(process, CapturedProcess):
            capture = {
                "cleanup_complete": process.cleanup_complete,
                "windows_job_measured": bool(process.containment),
                "windows_job": process.containment,
                "stdout": {
                    "allowed_bytes": limits.stdout_bytes,
                    "observed_bytes": process.observed_stdout_bytes,
                    "overflowed": process.observed_stdout_bytes
                    > limits.stdout_bytes,
                },
                "stderr": {
                    "allowed_bytes": limits.stderr_bytes,
                    "observed_bytes": process.observed_stderr_bytes,
                    "overflowed": process.observed_stderr_bytes
                    > limits.stderr_bytes,
                },
            }
            return _ProcessOutcome(
                process.exit_code,
                process.stdout,
                process.stderr,
                process.timed_out,
                process.errors,
                process.raw_stdout,
                process.raw_stderr,
                capture,
            )
        exit_code = int(getattr(process, "returncode", 1))
        stdout = _as_text(getattr(process, "stdout", ""))
        stderr = _as_text(getattr(process, "stderr", ""))
        raw_stdout = stdout.encode("utf-8")
        raw_stderr = stderr.encode("utf-8")
        errors = () if exit_code == 0 else (f"exit_code:{exit_code}",)
        capture = {
            "stdout": {
                "allowed_bytes": limits.stdout_bytes,
                "observed_bytes": len(raw_stdout),
                "overflowed": False,
            },
            "stderr": {
                "allowed_bytes": limits.stderr_bytes,
                "observed_bytes": len(raw_stderr),
                "overflowed": False,
            },
        }
        return _ProcessOutcome(
            exit_code,
            stdout,
            stderr,
            False,
            errors,
            raw_stdout,
            raw_stderr,
            capture,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _as_text(exc.output)
        stderr = _as_text(exc.stderr)
        return _ProcessOutcome(
            None,
            stdout,
            stderr,
            True,
            (f"timeout:{timeout_seconds}s",),
            stdout.encode("utf-8"),
            stderr.encode("utf-8"),
            {},
        )
    except Exception as exc:
        return _ProcessOutcome(
            None,
            "",
            "",
            False,
            (f"{type(exc).__name__}: {exc}",),
            b"",
            b"",
            {},
        )


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
            **evaluation_environment_flags(),
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
    generation: GenerationSettings | None = None,
    trial: TrialSpec | None = None,
    resolve_bin_fn: Callable[[], Path] = resolve_opencode_binary,
    run_fn: Callable[..., Any] = run_bounded_process,
    capture_limits: CaptureLimits = DEFAULT_CAPTURE_LIMITS,
    launcher: Callable[..., object] | None = None,
    expected_config_binding: dict[str, Any] | None = None,
) -> ArmResult:
    started = time.perf_counter()
    workspace_path = Path(workspace).resolve()
    config_measurement: dict[str, Any] = {
        "expected_canonical_sha256": None,
        "before": None,
        "after": None,
    }
    config_metrics = {"opencode_config_identity": config_measurement}
    if (generation is None) != (trial is None):
        return _failure(
            arm,
            model,
            started,
            ("ValueError: generation and trial must be provided together",),
            metrics=config_metrics,
        )
    if not _is_loopback_ollama_host(ollama_host):
        return _failure(
            arm,
            model,
            started,
            ("non_loopback_ollama_host",),
            metrics=config_metrics,
        )
    try:
        if arm not in {"stock", "lac"}:
            raise ValueError(f"unknown OpenCode evaluation arm: {arm}")
        runtime_root = workspace_path.parent / f".lac-eval-runtime-{arm}"
        config_path = runtime_root / "opencode.json"
        config = build_evaluation_config(
            model,
            ollama_host,
            arm=arm,
            generation=generation,
            seed=None if trial is None else trial.seed,
        )
        if not valid_evaluation_config(
            config,
            arm=arm,
            model=model,
            ollama_host=ollama_host,
            generation=generation,
            seed=None if trial is None else trial.seed,
        ):
            raise ValueError(
                "evaluation config violates the independently validated contract"
            )
        expected_config_sha256 = canonical_config_sha256(config)
        if expected_config_binding is not None:
            if generation is None or trial is None or not valid_config_binding(
                expected_config_binding,
                config,
                trial_index=trial.index,
                arm=arm,
                model=model,
                ollama_host=ollama_host,
                generation=generation,
                seed=trial.seed,
            ):
                raise ValueError(
                    "evaluation config binding does not match runtime contract"
                )
            expected_config_sha256 = expected_config_binding[
                "expected_canonical_sha256"
            ]
        config_measurement["expected_canonical_sha256"] = (
            expected_config_sha256
        )
        write_opencode_config_payload(config_path, config)
        environment = _isolated_environment(
            workspace_path, runtime_root, config_path, ollama_host
        )
        binary = resolve_bin_fn()
        config_lock = _lock_config(config_path)
    except Exception as exc:
        return _failure(
            arm,
            model,
            started,
            (f"{type(exc).__name__}: {exc}",),
            metrics=config_metrics,
        )

    argv = build_evaluation_argv(
        binary,
        task.prompt,
        model,
        workspace_path,
    )
    outcome: _ProcessOutcome | None = None
    lifecycle_errors: list[str] = []
    try:
        try:
            before = _config_identity(
                config_lock,
                config_path,
            )
            config_measurement["before"] = before
            if before["canonical_sha256"] != expected_config_sha256:
                raise ValueError(
                    "locked evaluation config does not match expected contract"
                )
        except Exception as exc:
            outcome = _ProcessOutcome(
                None,
                "",
                "",
                False,
                (f"config_pre_measurement_failed:{type(exc).__name__}: {exc}",),
                b"",
                b"",
                {},
            )
        else:
            outcome = _run_process_outcome(
                run_fn,
                argv,
                workspace_path=workspace_path,
                timeout_seconds=task.timeout_seconds,
                environment=environment,
                limits=capture_limits,
                launcher=launcher,
            )
    finally:
        try:
            after = _config_identity(
                config_lock,
                config_path,
            )
            config_measurement["after"] = after
            if after["canonical_sha256"] != expected_config_sha256:
                raise ValueError(
                    "post-run evaluation config does not match expected contract"
                )
        except Exception as exc:
            lifecycle_errors.append(
                f"config_post_measurement_failed:{type(exc).__name__}: {exc}"
            )
        try:
            os.close(config_lock)
        except Exception as exc:
            lifecycle_errors.append(
                f"config_lock_close_failed:{type(exc).__name__}: {exc}"
            )

    if outcome is None:
        outcome = _ProcessOutcome(
            None,
            "",
            "",
            False,
            ("config_process_outcome_missing",),
            b"",
            b"",
            {},
        )
    outcome_errors = (*outcome.errors, *lifecycle_errors)
    if outcome_errors:
        return _failure(
            arm,
            model,
            started,
            outcome_errors,
            timed_out=outcome.timed_out,
            exit_code=outcome.exit_code,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            metrics=config_metrics,
            capture=outcome.capture,
        )

    parsed = parse_opencode_jsonl(
        outcome.raw_stdout,
        max_line_bytes=capture_limits.jsonl_line_bytes,
        max_events=capture_limits.jsonl_events,
    )
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
        exit_code=outcome.exit_code,
        raw_stdout=outcome.stdout,
        raw_stderr=outcome.stderr,
        events=parsed.events,
        unknown_event_types=parsed.unknown_event_types,
        capture=outcome.capture,
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
