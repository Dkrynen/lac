from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half-open"


class CircuitOpenError(Exception):
    def __init__(self, name: str, recovery_in: float):
        super().__init__(f"circuit breaker '{name}' is open (retry in {recovery_in:.1f}s)")
        self.name = name
        self.recovery_in = recovery_in


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    _state: CircuitState = CircuitState.CLOSED
    _failures: int = 0
    _opened_at: float = 0.0
    _lock: threading.Lock = None  # type: ignore[assignment]

    def __post_init__(self):
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state is CircuitState.OPEN:
                if time.monotonic() - self._opened_at >= self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
            return self._state

    def before_call(self) -> None:
        st = self.state
        if st is CircuitState.OPEN:
            wait = self.recovery_timeout - (time.monotonic() - self._opened_at)
            raise CircuitOpenError(self.name, max(wait, 0.0))

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state is CircuitState.HALF_OPEN or self._failures >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()

    def reset(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = CircuitState.CLOSED
            self._opened_at = 0.0
