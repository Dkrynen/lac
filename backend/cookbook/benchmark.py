"""Shared benchmark logic, fed exclusively by LAC Pro's autopilot
(``lac_pro.autopilot.run_benchmark``) — the free CLI command and web route
that used to call this were removed when benchmarking became Pro-only.

Keeps the Ollama-response → log-entry transform, the results.jsonl append, and
the history read in one place so calibration data is written identically no
matter which surface launched the run.
"""

from __future__ import annotations

import json
import time
from pathlib import Path


def build_metrics(
    result: dict,
    model: str,
    prompt: str,
    num_predict: int,
    temperature: float,
    fingerprint: str | None = None,
    stack: dict | None = None,
) -> dict:
    """Turn an Ollama /api/generate response into a benchmark log entry.

    tokens_per_second = generated tokens / generation (eval) time.
    time_to_first_token = model load + prompt prefill — NOT generation duration.
    """
    eval_count = result.get("eval_count", 0)
    eval_duration_ns = result.get("eval_duration", 0)
    total_duration_ns = result.get("total_duration", 0)
    load_duration_ns = result.get("load_duration", 0)
    prompt_eval_duration_ns = result.get("prompt_eval_duration", 0)
    ttft_ms = (load_duration_ns + prompt_eval_duration_ns) / 1_000_000
    tokens_per_second = eval_count / (eval_duration_ns / 1e9) if eval_duration_ns > 0 else 0
    entry = {
        "model": model,
        "prompt": prompt,
        "prompt_len": len(prompt),
        "num_predict": num_predict,
        "temperature": temperature,
        "eval_count": eval_count,
        "eval_duration_ns": eval_duration_ns,
        "eval_duration_ms": round(eval_duration_ns / 1_000_000, 1),
        "total_duration_ns": total_duration_ns,
        "total_duration_ms": round(total_duration_ns / 1_000_000, 1),
        "tokens_per_second": round(tokens_per_second, 2),
        "time_to_first_token_ms": round(ttft_ms, 1),
        "response": result.get("response", ""),
    }
    if fingerprint:
        entry["fingerprint"] = fingerprint
    if stack:
        entry["stack"] = stack
    return entry


def log_result(entry: dict) -> Path | None:
    """Append a benchmark result to ~/.model-hub/benchmarks/results.jsonl.

    Returns the log path on success, None on failure (never raises).
    """
    try:
        log_dir = Path.home() / ".model-hub" / "benchmarks"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "results.jsonl"
        entry["timestamp"] = time.time()
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
        return log_file
    except Exception:
        return None


def history() -> list[dict]:
    """Read all benchmark results from results.jsonl (newest last)."""
    log_file = Path.home() / ".model-hub" / "benchmarks" / "results.jsonl"
    if not log_file.exists():
        return []
    out: list[dict] = []
    try:
        with open(log_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception:
        pass
    return out
