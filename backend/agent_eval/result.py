"""Shared machine-readable result contract for evaluation arms."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ArmResult:
    arm: str
    model: str
    runtime: str
    completed: bool
    timed_out: bool
    response: str
    wall_time_ms: float
    metrics: dict[str, Any] = field(default_factory=dict)
    errors: tuple[str, ...] = ()
    exit_code: int | None = None
    raw_stdout: str = ""
    raw_stderr: str = ""
    events: tuple[dict[str, Any], ...] = ()
    unknown_event_types: tuple[str, ...] = ()
