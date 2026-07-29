"""Raw-model evaluation arm using only a loopback Ollama endpoint."""
from __future__ import annotations

import time
import urllib.request
from typing import Any, Callable
from urllib.parse import urlsplit

from .capture import (
    OLLAMA_RESPONSE_MAX_BYTES,
    CaptureLimitExceeded,
    bounded_http_json,
)
from .result import ArmResult
from .schedule import GenerationSettings, TrialSpec
from .task import EvalTask, snapshot_fixture


RequestFn = Callable[[str, dict[str, Any], int], dict[str, Any]]
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _request_json(
    ollama_host: str,
    body: dict[str, Any],
    timeout: int,
    *,
    capture_metadata: dict[str, object] | None = None,
    expected_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    host = ollama_host.rstrip("/")
    return bounded_http_json(
        host + "/api/chat",
        method="POST",
        body=body,
        timeout=timeout,
        max_bytes=OLLAMA_RESPONSE_MAX_BYTES,
        open_fn=urllib.request.urlopen,
        capture_metadata=capture_metadata,
        expected_origin=host,
        expected_chat_options=expected_options,
    )


def _is_loopback_ollama_host(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "http"
            and parsed.hostname in _LOOPBACK_HOSTS
            and parsed.username is None
            and parsed.password is None
            and not parsed.path.rstrip("/")
            and not parsed.query
            and not parsed.fragment
        )
    except (TypeError, ValueError):
        return False


def _milliseconds(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return round(float(value) / 1_000_000, 3)


def _metrics(data: dict[str, Any]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for key in ("prompt_eval_count", "eval_count"):
        value = data.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            metrics[key] = value
    for source, target in (
        ("prompt_eval_duration", "prompt_eval_duration_ms"),
        ("eval_duration", "eval_duration_ms"),
        ("total_duration", "total_duration_ms"),
        ("load_duration", "load_duration_ms"),
    ):
        value = _milliseconds(data.get(source))
        if value is not None:
            metrics[target] = value

    count = metrics.get("eval_count")
    duration_ms = metrics.get("eval_duration_ms")
    if count and duration_ms and duration_ms > 0:
        metrics["tokens_per_second"] = round(count / (duration_ms / 1000), 2)
    return metrics


def build_raw_prompt(task: EvalTask) -> str:
    """Return the exact prompt used when the raw arm has no file tools."""

    return (
        f"{task.prompt}\n\n"
        "The complete project fixture follows. Treat it as read-only input.\n\n"
        f"{snapshot_fixture(task.fixture_root)}"
    )


def run_raw(
    task: EvalTask,
    model: str,
    ollama_host: str,
    *,
    generation: GenerationSettings | None = None,
    trial: TrialSpec | None = None,
    request_fn: RequestFn = _request_json,
) -> ArmResult:
    started = time.perf_counter()
    if (generation is None) != (trial is None):
        raise ValueError("generation and trial must be provided together")
    options = (
        {"seed": 0, "temperature": 0}
        if generation is None
        else {
            "seed": trial.seed,
            "temperature": generation.temperature,
            "num_predict": generation.max_output_tokens,
        }
    )
    request_metadata = {
        "stream": False,
        "options": dict(options),
        "response_max_bytes": OLLAMA_RESPONSE_MAX_BYTES,
    }
    if trial is not None:
        request_metadata["trial_index"] = trial.index
    if not _is_loopback_ollama_host(ollama_host):
        return ArmResult(
            arm="raw",
            model=model,
            runtime="ollama",
            completed=False,
            timed_out=False,
            response="",
            wall_time_ms=round((time.perf_counter() - started) * 1000, 3),
            errors=("non_loopback_ollama_host",),
            request_metadata=request_metadata,
        )

    body = {
        "model": model,
        "messages": [{"role": "user", "content": build_raw_prompt(task)}],
        "stream": False,
        "options": dict(options),
    }
    response_capture: dict[str, object] = {
        "allowed_bytes": OLLAMA_RESPONSE_MAX_BYTES,
        "observed_bytes": None,
        "overflowed": False,
    }
    try:
        if request_fn is _request_json:
            data = _request_json(
                ollama_host.rstrip("/"),
                body,
                task.timeout_seconds,
                capture_metadata=response_capture,
                expected_options=dict(options) if generation is not None else None,
            )
        else:
            data = request_fn(
                ollama_host.rstrip("/"),
                body,
                task.timeout_seconds,
            )
    except CaptureLimitExceeded as exc:
        return ArmResult(
            arm="raw",
            model=model,
            runtime="ollama",
            completed=False,
            timed_out=False,
            response="",
            wall_time_ms=round((time.perf_counter() - started) * 1000, 3),
            errors=("response_body_overflow",),
            capture={
                "response": {
                    "allowed_bytes": exc.allowed_bytes,
                    "observed_bytes": exc.observed_bytes,
                    "overflowed": True,
                }
            },
            request_metadata=request_metadata,
        )
    except TimeoutError as exc:
        return ArmResult(
            arm="raw",
            model=model,
            runtime="ollama",
            completed=False,
            timed_out=True,
            response="",
            wall_time_ms=round((time.perf_counter() - started) * 1000, 3),
            errors=(f"timeout: {exc}",),
            request_metadata=request_metadata,
        )
    except Exception as exc:
        return ArmResult(
            arm="raw",
            model=model,
            runtime="ollama",
            completed=False,
            timed_out=False,
            response="",
            wall_time_ms=round((time.perf_counter() - started) * 1000, 3),
            errors=(f"{type(exc).__name__}: {exc}",),
            request_metadata=request_metadata,
        )

    message = data.get("message") if isinstance(data, dict) else None
    response = (
        message.get("content", "")
        if isinstance(message, dict) and isinstance(message.get("content", ""), str)
        else ""
    )
    errors: tuple[str, ...] = ()
    completed = bool(response.strip()) and data.get("done") is True
    if not response.strip():
        errors = ("empty_response",)
    elif data.get("done") is not True:
        errors = ("incomplete_response",)
    return ArmResult(
        arm="raw",
        model=model,
        runtime="ollama",
        completed=completed,
        timed_out=False,
        response=response,
        wall_time_ms=round((time.perf_counter() - started) * 1000, 3),
        metrics=_metrics(data if isinstance(data, dict) else {}),
        errors=errors,
        capture={
            "response": response_capture
        },
        request_metadata=request_metadata,
    )
