"""Bounded process and local HTTP capture primitives for evaluation evidence."""
from __future__ import annotations

import ipaddress
import json
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
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
            or self.cleanup_grace_seconds < 0
        ):
            raise ValueError("cleanup_grace_seconds must be non-negative")


DEFAULT_CAPTURE_LIMITS = CaptureLimits()


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


def _terminate_process(process: object, grace_seconds: float) -> None:
    terminate_tree = getattr(process, "terminate_tree", None)
    if callable(terminate_tree):
        terminate_tree()
    else:
        process.terminate()
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        kill_tree = getattr(process, "kill_tree", None)
        if callable(kill_tree):
            kill_tree()
        else:
            process.kill()
        process.wait()


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
        destination.flush()
        state["observed"] = min(observed, limit + 1)


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
    try:
        with stdout_path.open("xb") as stdout_file, stderr_path.open("xb") as stderr_file:
            process = launch(
                list(argv),
                cwd=cwd,
                env=None if env is None else dict(env),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
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

            deadline = time.monotonic() + float(timeout)
            while process.poll() is None:
                if overflow_event.wait(timeout=min(0.01, max(0.0, deadline - time.monotonic()))):
                    _terminate_process(process, float(limits.cleanup_grace_seconds))
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    _terminate_process(process, float(limits.cleanup_grace_seconds))
                    break
            if process.poll() is None:
                process.wait()
            for thread in threads:
                thread.join(timeout=float(limits.cleanup_grace_seconds))
            if any(thread.is_alive() for thread in threads):
                raise RuntimeError("capture reader did not stop during cleanup")

        with stdout_path.open("rb") as captured_stdout:
            raw_stdout = captured_stdout.read(limits.stdout_bytes)
        with stderr_path.open("rb") as captured_stderr:
            raw_stderr = captured_stderr.read(limits.stderr_bytes)
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
            if state.get("reader_error") and not errors:
                errors.append(f"capture_reader_error:{state['reader_error']}")
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
            temporary_paths=temporary_paths,
            raw_stdout=raw_stdout,
            raw_stderr=raw_stderr,
        )
    finally:
        if process is not None:
            for pipe_name in ("stdout", "stderr"):
                pipe = getattr(process, pipe_name, None)
                if pipe is not None:
                    pipe.close()
        if temporary_root.exists():
            shutil.rmtree(temporary_root)

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
) -> dict:
    """Fetch one local JSON object without ever performing an unbounded read."""
    _validate_url(url)
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
    try:
        decoded = raw.decode("utf-8")
        value = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("response is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("response JSON must be an object")
    return value
