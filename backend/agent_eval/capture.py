"""Bounded process and local HTTP capture primitives for evaluation evidence."""
from __future__ import annotations

import ipaddress
import json
import math
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Callable, Mapping, Sequence
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


IDENTITY_RESPONSE_MAX_BYTES = 2 * 1024 * 1024
OLLAMA_RESPONSE_MAX_BYTES = 8 * 1024 * 1024
_OLLAMA_ENDPOINTS = frozenset(
    {"/api/version", "/api/tags", "/api/show", "/api/chat"}
)
_READ_CHUNK_BYTES = 64 * 1024
_DEFERRED_CLEANUP_POLL_SECONDS = 0.05


@dataclass(frozen=True)
class CaptureLimits:
    stdout_bytes: int = 4 * 1024 * 1024
    stderr_bytes: int = 1 * 1024 * 1024
    jsonl_events: int = 50_000
    jsonl_line_bytes: int = 256 * 1024
    cleanup_grace_seconds: float = 5

    def __post_init__(self) -> None:
        for name in ("stdout_bytes", "stderr_bytes", "jsonl_events", "jsonl_line_bytes"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if (
            isinstance(self.cleanup_grace_seconds, bool)
            or not isinstance(self.cleanup_grace_seconds, (int, float))
            or not math.isfinite(float(self.cleanup_grace_seconds))
            or self.cleanup_grace_seconds < 0
        ):
            raise ValueError("cleanup_grace_seconds must be non-negative")


DEFAULT_CAPTURE_LIMITS = CaptureLimits()


class DeferredCleanupStatus:
    """Thread-safe observable state for capture artifacts owned by a reaper."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = True
        self._state = "waiting_for_readers"
        self._sanitize_attempts = 0
        self._delete_attempts = 0
        self._supervisor_errors = 0
        self._last_error: str | None = None

    def _update(
        self,
        *,
        state: str,
        sanitize_attempt: bool = False,
        delete_attempt: bool = False,
        supervisor_error: bool = False,
        error: BaseException | str | None = None,
        active: bool = True,
    ) -> None:
        with self._lock:
            self._state = state
            self._sanitize_attempts += int(sanitize_attempt)
            self._delete_attempts += int(delete_attempt)
            self._supervisor_errors += int(supervisor_error)
            self._last_error = (
                None
                if error is None
                else (
                    error
                    if isinstance(error, str)
                    else type(error).__name__
                )
            )
            self._active = active

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "active": self._active,
                "state": self._state,
                "sanitize_attempts": self._sanitize_attempts,
                "delete_attempts": self._delete_attempts,
                "supervisor_errors": self._supervisor_errors,
                "last_error": self._last_error,
            }


@dataclass(frozen=True)
class CapturedProcess:
    exit_code: int | None
    stdout: str
    stderr: str
    completed: bool
    timed_out: bool
    overflowed: bool
    observed_stdout_bytes: int
    observed_stderr_bytes: int
    limits: CaptureLimits
    errors: tuple[str, ...]
    temporary_paths: tuple[Path, ...]
    raw_stdout: bytes = b""
    raw_stderr: bytes = b""
    cleanup_complete: bool = True
    cleanup_deferred: bool = False
    deferred_cleanup_status: DeferredCleanupStatus | None = None
    containment: dict[str, object] = field(default_factory=dict)


class CaptureLimitExceeded(ValueError):
    """A captured response exceeded its declared, fail-closed byte limit."""

    def __init__(self, message: str, *, allowed_bytes: int, observed_bytes: int):
        super().__init__(message)
        self.allowed_bytes = allowed_bytes
        self.observed_bytes = observed_bytes


def capture_bounded_response(response: object, max_bytes: int) -> bytes:
    """Read through EOF while observing no more than ``max_bytes + 1`` bytes."""
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError("max_bytes must be a non-negative integer")
    chunks = bytearray()
    while len(chunks) <= max_bytes:
        remaining = max_bytes + 1 - len(chunks)
        chunk = response.read(remaining)
        if not chunk:
            break
        chunks.extend(chunk)
    if len(chunks) > max_bytes:
        raise CaptureLimitExceeded(
            f"response exceeds {max_bytes} byte capture limit",
            allowed_bytes=max_bytes,
            observed_bytes=max_bytes + 1,
        )
    return bytes(chunks)


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _bounded_call(
    function: Callable[[], object],
    deadline: float,
) -> tuple[bool, object | None, BaseException | None]:
    state: dict[str, object] = {}

    def invoke() -> None:
        try:
            state["value"] = function()
        except BaseException as exc:
            state["error"] = exc

    thread = threading.Thread(target=invoke, daemon=True, name="agent-eval-cleanup")
    thread.start()
    thread.join(_remaining(deadline))
    if thread.is_alive():
        return False, None, None
    return True, state.get("value"), state.get("error")  # type: ignore[arg-type]


def _bounded_wait(process: object, deadline: float) -> bool:
    remaining = _remaining(deadline)
    if remaining <= 0:
        return False
    completed, _value, error = _bounded_call(
        lambda: process.wait(timeout=remaining),
        deadline,
    )
    return completed and error is None


def _terminate_process(process: object, deadline: float) -> bool:
    terminate_tree = getattr(process, "terminate_tree", None)
    terminate = terminate_tree if callable(terminate_tree) else process.terminate
    completed, _value, error = _bounded_call(terminate, deadline)
    if not completed or error is not None:
        return False
    if _bounded_wait(process, deadline):
        return True
    kill_tree = getattr(process, "kill_tree", None)
    kill = kill_tree if callable(kill_tree) else process.kill
    completed, _value, error = _bounded_call(kill, deadline)
    if not completed or error is not None:
        return False
    return _bounded_wait(process, deadline)


def _drain_stream(
    stream: BinaryIO,
    destination: BinaryIO,
    limit: int,
    state: dict[str, object],
    overflow_event: threading.Event,
) -> None:
    observed = 0
    try:
        while observed <= limit:
            chunk = stream.read(min(_READ_CHUNK_BYTES, limit + 1 - observed))
            if not chunk:
                break
            retained = chunk[: max(0, limit - observed)]
            if retained:
                destination.write(retained)
            observed += len(chunk)
            if observed > limit:
                overflow_event.set()
                break
    except Exception as exc:
        state["reader_error"] = f"{type(exc).__name__}: {exc}"
        overflow_event.set()
    finally:
        try:
            destination.flush()
        except Exception as exc:
            state.setdefault(
                "reader_error",
                f"{type(exc).__name__}: {exc}",
            )
        try:
            destination.close()
        except Exception as exc:
            state.setdefault(
                "reader_error",
                f"{type(exc).__name__}: {exc}",
            )
        state["observed"] = min(observed, limit + 1)


def _schedule_deferred_capture_cleanup(
    *,
    threads: Sequence[threading.Thread],
    pipes: Sequence[object],
    temporary_root: Path,
    temporary_paths: Sequence[Path],
) -> DeferredCleanupStatus:
    """Transfer incomplete capture cleanup to a non-blocking daemon reaper."""
    status = DeferredCleanupStatus()

    def reap() -> None:
        readers_finished = False
        pipes_closed = False
        while True:
            try:
                if not readers_finished:
                    while any(thread.is_alive() for thread in threads):
                        for thread in threads:
                            thread.join(
                                timeout=_DEFERRED_CLEANUP_POLL_SECONDS
                            )
                    readers_finished = True
                if not pipes_closed:
                    for pipe in pipes:
                        pipe.close()
                    pipes_closed = True
                if not temporary_root.exists():
                    status._update(state="complete", active=False)
                    return

                sanitized = True
                for path in temporary_paths:
                    if not path.exists():
                        continue
                    status._update(
                        state="sanitizing",
                        sanitize_attempt=True,
                    )
                    try:
                        with path.open("r+b") as capture_file:
                            capture_file.seek(0)
                            capture_file.truncate(0)
                            capture_file.flush()
                    except BaseException as exc:
                        sanitized = False
                        status._update(
                            state="sanitize_retry",
                            error=exc,
                        )

                status._update(state="deleting", delete_attempt=True)
                try:
                    shutil.rmtree(temporary_root)
                except BaseException as exc:
                    status._update(
                        state=(
                            "sanitized_waiting_for_delete"
                            if sanitized
                            else "sanitize_retry"
                        ),
                        error=exc,
                    )
                else:
                    status._update(state="complete", active=False)
                    return
                time.sleep(_DEFERRED_CLEANUP_POLL_SECONDS)
            except BaseException as exc:
                status._update(
                    state="supervisor_retry",
                    supervisor_error=True,
                    error=exc,
                )
                time.sleep(_DEFERRED_CLEANUP_POLL_SECONDS)

    threading.Thread(
        target=reap,
        daemon=True,
        name="agent-eval-deferred-cleanup",
    ).start()
    return status


def run_bounded_process(
    argv: Sequence[str],
    *,
    cwd: str | Path,
    env: Mapping[str, str] | None,
    timeout: float,
    limits: CaptureLimits,
    launcher: Callable[..., object] | None = None,
) -> CapturedProcess:
    """Run a process while concurrently bounding both output streams."""
    if not argv:
        raise ValueError("argv must not be empty")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(float(timeout))
        or timeout <= 0
    ):
        raise ValueError("timeout must be positive")
    launch = launcher or subprocess.Popen
    temporary_root = Path(tempfile.mkdtemp(prefix="lac-agent-eval-"))
    stdout_path = temporary_root / "stdout.bin"
    stderr_path = temporary_root / "stderr.bin"
    temporary_paths = (stdout_path, stderr_path)
    process = None
    stdout_state: dict[str, object] = {"observed": 0}
    stderr_state: dict[str, object] = {"observed": 0}
    overflow_event = threading.Event()
    timed_out = False
    threads: list[threading.Thread] = []
    stdout_file: BinaryIO | None = None
    stderr_file: BinaryIO | None = None
    readers_started = False
    cleanup_complete = True
    process_cleanup_complete = True
    job_cleanup_complete = True
    reader_cleanup_complete = True
    pipe_cleanup_complete = True
    temporary_cleanup_complete = True
    cleanup_deadline: float | None = None
    try:
        stdout_file = stdout_path.open("xb")
        stderr_file = stderr_path.open("xb")
        try:
            process = launch(
                list(argv),
                cwd=cwd,
                env=None if env is None else dict(env),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
            )
            windows_job_required = bool(
                getattr(launch, "_windows_job_launcher", False)
            )
            if windows_job_required:
                from .windows_job import WindowsJobProcess

                if not isinstance(process, WindowsJobProcess):
                    deadline = (
                        time.monotonic()
                        + float(limits.cleanup_grace_seconds)
                    )
                    _terminate_process(process, deadline)
                    for pipe_name in ("stdout", "stderr"):
                        pipe = getattr(process, pipe_name, None)
                        if pipe is not None:
                            pipe.close()
                    raise RuntimeError(
                        "verified Windows launcher must return a real "
                        "WindowsJobProcess"
                    )
                started_evidence = process.containment_evidence()
                if not (
                    started_evidence.get("real_windows_job") is True
                    and started_evidence.get("assignment_proven") is True
                    and started_evidence.get("active_process_limit") == 1
                    and started_evidence.get("kill_on_close") is True
                    and started_evidence.get("resume_after_assignment") is True
                ):
                    deadline = (
                        time.monotonic()
                        + float(limits.cleanup_grace_seconds)
                    )
                    _terminate_process(process, deadline)
                    process.close()
                    raise RuntimeError(
                        "Windows Job assignment and limits were not proven"
                    )
            if process.stdout is None or process.stderr is None:
                raise RuntimeError("launcher must provide binary stdout and stderr pipes")
            threads = [
                threading.Thread(
                    target=_drain_stream,
                    args=(
                        process.stdout,
                        stdout_file,
                        limits.stdout_bytes,
                        stdout_state,
                        overflow_event,
                    ),
                    daemon=True,
                    name="agent-eval-stdout",
                ),
                threading.Thread(
                    target=_drain_stream,
                    args=(
                        process.stderr,
                        stderr_file,
                        limits.stderr_bytes,
                        stderr_state,
                        overflow_event,
                    ),
                    daemon=True,
                    name="agent-eval-stderr",
                ),
            ]
            for thread in threads:
                thread.start()
            readers_started = True
            stdout_file = None
            stderr_file = None

            deadline = time.monotonic() + float(timeout)
            while process.poll() is None:
                if overflow_event.wait(timeout=min(0.01, max(0.0, deadline - time.monotonic()))):
                    cleanup_deadline = (
                        time.monotonic() + float(limits.cleanup_grace_seconds)
                    )
                    process_cleanup_complete = _terminate_process(
                        process,
                        cleanup_deadline,
                    )
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    cleanup_deadline = (
                        time.monotonic() + float(limits.cleanup_grace_seconds)
                    )
                    process_cleanup_complete = _terminate_process(
                        process,
                        cleanup_deadline,
                    )
                    break
            if cleanup_deadline is None:
                cleanup_deadline = (
                    time.monotonic() + float(limits.cleanup_grace_seconds)
                )

            probe_seconds = min(
                0.05,
                float(limits.cleanup_grace_seconds) / 4,
            )
            probe_deadline = min(
                cleanup_deadline,
                time.monotonic() + probe_seconds,
            )
            for thread in threads:
                thread.join(_remaining(probe_deadline))
            if any(thread.is_alive() for thread in threads):
                terminate_tree = getattr(process, "terminate_tree", None)
                if process.poll() is not None and callable(terminate_tree):
                    process_cleanup_complete = (
                        _terminate_process(process, cleanup_deadline)
                        and process_cleanup_complete
                    )
                for thread in threads:
                    thread.join(_remaining(cleanup_deadline))
            reader_cleanup_complete = not any(
                thread.is_alive() for thread in threads
            )

            if reader_cleanup_complete:
                for pipe_name in ("stdout", "stderr"):
                    pipe = getattr(process, pipe_name, None)
                    if pipe is not None:
                        completed, _value, error = _bounded_call(
                            pipe.close,
                            cleanup_deadline,
                        )
                        if not completed or error is not None:
                            pipe_cleanup_complete = False
            else:
                pipe_cleanup_complete = False

            containment: dict[str, object] = {}
            if windows_job_required:
                completed, _value, error = _bounded_call(
                    process.close,
                    cleanup_deadline,
                )
                job_cleanup_complete = completed and error is None
                if job_cleanup_complete:
                    containment = process.containment_evidence()
                    job_cleanup_complete = (
                        containment.get("real_windows_job") is True
                        and containment.get("assignment_proven") is True
                        and containment.get("active_process_limit") == 1
                        and containment.get("kill_on_close") is True
                        and containment.get("resume_after_assignment") is True
                        and containment.get("final_active_processes") == 0
                        and containment.get("handles_closed") is True
                        and containment.get("cleanup_certain") is True
                    )

            raw_stdout = b""
            raw_stderr = b""
            if reader_cleanup_complete:
                with stdout_path.open("rb") as captured_stdout:
                    raw_stdout = captured_stdout.read(limits.stdout_bytes)
                with stderr_path.open("rb") as captured_stderr:
                    raw_stderr = captured_stderr.read(limits.stderr_bytes)

            if reader_cleanup_complete and pipe_cleanup_complete:
                completed, _value, error = _bounded_call(
                    lambda: shutil.rmtree(temporary_root),
                    cleanup_deadline,
                )
                temporary_cleanup_complete = completed and error is None
            else:
                temporary_cleanup_complete = False
        except Exception:
            if not readers_started:
                for destination in (stdout_file, stderr_file):
                    if destination is not None:
                        destination.close()
                if temporary_root.exists():
                    shutil.rmtree(temporary_root)
            raise

        cleanup_complete = (
            process_cleanup_complete
            and job_cleanup_complete
            and reader_cleanup_complete
            and pipe_cleanup_complete
            and temporary_cleanup_complete
        )
        cleanup_deferred = not cleanup_complete and temporary_root.exists()
        deferred_cleanup_status = None
        if cleanup_deferred:
            deferred_cleanup_status = _schedule_deferred_capture_cleanup(
                threads=threads,
                pipes=tuple(
                    pipe
                    for pipe in (
                        getattr(process, "stdout", None),
                        getattr(process, "stderr", None),
                    )
                    if pipe is not None
                ),
                temporary_root=temporary_root,
                temporary_paths=temporary_paths,
            )
        if not cleanup_complete:
            raw_stdout = b""
            raw_stderr = b""
        observed_stdout = int(stdout_state["observed"])
        observed_stderr = int(stderr_state["observed"])
        stdout_overflow = observed_stdout > limits.stdout_bytes
        stderr_overflow = observed_stderr > limits.stderr_bytes
        errors: list[str] = []
        if stdout_overflow:
            errors.append("stdout_capture_limit_exceeded")
        if stderr_overflow:
            errors.append("stderr_capture_limit_exceeded")
        if not errors and timed_out:
            errors.append(f"timeout:{timeout}s")
        for state in (stdout_state, stderr_state):
            if state.get("reader_error"):
                errors.append(f"capture_reader_error:{state['reader_error']}")
        if not process_cleanup_complete:
            errors.append("process_cleanup_incomplete")
        if not job_cleanup_complete:
            errors.append("windows_job_cleanup_incomplete")
        if not reader_cleanup_complete:
            errors.append(
                "capture_cleanup_incomplete:task5_containment_required"
            )
        elif not pipe_cleanup_complete:
            errors.append("capture_pipe_cleanup_incomplete")
        if not temporary_cleanup_complete and reader_cleanup_complete:
            errors.append("temporary_capture_cleanup_incomplete")
        exit_code = process.returncode
        if not errors and exit_code != 0:
            errors.append(f"exit_code:{exit_code}")
        return CapturedProcess(
            exit_code=exit_code,
            stdout=raw_stdout.decode("utf-8", errors="replace"),
            stderr=raw_stderr.decode("utf-8", errors="replace"),
            completed=exit_code == 0 and not errors,
            timed_out=timed_out,
            overflowed=stdout_overflow or stderr_overflow,
            observed_stdout_bytes=observed_stdout,
            observed_stderr_bytes=observed_stderr,
            limits=limits,
            errors=tuple(errors),
            temporary_paths=() if cleanup_deferred else temporary_paths,
            raw_stdout=raw_stdout,
            raw_stderr=raw_stderr,
            cleanup_complete=cleanup_complete,
            cleanup_deferred=cleanup_deferred,
            deferred_cleanup_status=deferred_cleanup_status,
            containment=containment,
        )
    finally:
        pass

def _is_loopback(hostname: str | None) -> bool:
    if hostname is None:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _validate_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.username is not None
        or parsed.password is not None
        or not _is_loopback(parsed.hostname)
        or parsed.port != 11434
        or parsed.query
        or parsed.fragment
        or parsed.path not in _OLLAMA_ENDPOINTS
    ):
        raise ValueError("capture URL must be an unauthenticated loopback Ollama endpoint")


def bounded_http_json(
    url: str,
    *,
    method: str,
    body: object | None,
    timeout: float,
    max_bytes: int,
    open_fn=urlopen,
    capture_metadata: dict[str, object] | None = None,
) -> dict:
    """Fetch one local JSON object without ever performing an unbounded read."""
    _validate_url(url)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(float(timeout))
        or timeout <= 0
    ):
        raise ValueError("timeout must be positive and finite")
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError("max_bytes must be a non-negative integer")
    expected = {
        "/api/version": ("GET", None),
        "/api/tags": ("GET", None),
        "/api/show": ("POST", {"verbose": False}),
        "/api/chat": ("POST", {"stream": False}),
    }
    expected_method, expected_body = expected[urlsplit(url).path]
    if method != expected_method:
        raise ValueError("capture method is invalid for endpoint")
    if expected_body is None:
        if body is not None:
            raise ValueError("GET capture requests cannot include a body")
    elif urlsplit(url).path == "/api/show":
        if (
            not isinstance(body, dict)
            or set(body) != {"model", "verbose"}
            or not isinstance(body.get("model"), str)
            or not body["model"]
            or body.get("verbose") is not False
        ):
            raise ValueError("show capture body must be an exact model/verbose object")
    elif (
        not isinstance(body, dict)
        or set(body) != {"model", "messages", "stream", "options"}
        or not isinstance(body.get("model"), str)
        or not body["model"]
        or body.get("stream") is not False
        or not isinstance(body.get("options"), dict)
        or set(body["options"]) != {"seed", "temperature"}
        or type(body["options"].get("seed")) is not int
        or body["options"]["seed"] != 0
        or type(body["options"].get("temperature")) is not int
        or body["options"]["temperature"] != 0
        or not isinstance(body.get("messages"), list)
        or len(body["messages"]) != 1
        or not isinstance(body["messages"][0], dict)
        or set(body["messages"][0]) != {"role", "content"}
        or body["messages"][0].get("role") != "user"
        or not isinstance(body["messages"][0].get("content"), str)
        or not body["messages"][0]["content"]
    ):
        raise ValueError("chat capture body must match the exact evaluation contract")
    payload = None
    headers = {"Accept": "application/json"}
    if body is not None:
        payload = json.dumps(
            body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=payload, method=method, headers=headers)
    with open_fn(request, timeout) as response:
        final_url = getattr(response, "geturl", lambda: url)()
        if final_url != url:
            raise ValueError("capture redirect is not allowed")
        content_length = getattr(response, "headers", {}).get("Content-Length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError as exc:
                raise ValueError("response Content-Length is invalid") from exc
            if declared_length < 0:
                raise ValueError("response Content-Length is invalid")
            if declared_length > max_bytes:
                raise CaptureLimitExceeded(
                    f"response exceeds {max_bytes} byte capture limit",
                    allowed_bytes=max_bytes,
                    observed_bytes=max_bytes + 1,
                )
        raw = capture_bounded_response(response, max_bytes)
    if capture_metadata is not None:
        capture_metadata.update(
            {
                "allowed_bytes": max_bytes,
                "observed_bytes": len(raw),
                "overflowed": False,
            }
        )
    try:
        decoded = raw.decode("utf-8")
        value = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("response is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("response JSON must be an object")
    return value
