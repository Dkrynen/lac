from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Type

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


class RetryableError(Exception):
    def __init__(self, message: str, *, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    jitter: bool = True
    retryable_statuses: set[int] = field(default_factory=lambda: set(RETRYABLE_STATUS))
    retry_on: tuple[Type[Exception], ...] = (RetryableError,)

    def is_retryable(self, exc: BaseException) -> bool:
        if isinstance(exc, RetryableError):
            if exc.status is not None and exc.status not in self.retryable_statuses:
                return False
            return True
        return isinstance(exc, self.retry_on)

    def delay_for(self, attempt: int, exc: BaseException | None = None) -> float:
        retry_after = getattr(exc, "retry_after", None) if exc is not None else None
        if isinstance(exc, RetryableError) and exc.retry_after is not None:
            retry_after = exc.retry_after
        if retry_after is not None:
            return min(float(retry_after), self.max_delay)
        raw = self.base_delay * (2 ** attempt)
        capped = min(raw, self.max_delay)
        if self.jitter:
            return random.uniform(0, capped)
        return capped


def retry(fn: Callable, *args, policy: RetryPolicy | None = None, sleep: Callable[[float], None] = time.sleep, **kwargs) -> Any:
    policy = policy or RetryPolicy()
    last_exc: BaseException | None = None
    for attempt in range(policy.max_attempts):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if not policy.is_retryable(exc) or attempt == policy.max_attempts - 1:
                raise
            time.sleep(0)
            sleep(policy.delay_for(attempt, exc))
    if last_exc:
        raise last_exc
    raise RuntimeError("retry exhausted without exception")


def retry_stream(
    gen_factory: Callable[[], Iterable],
    *,
    policy: RetryPolicy | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator:
    policy = policy or RetryPolicy()
    last_exc: BaseException | None = None
    for attempt in range(policy.max_attempts):
        started = False
        try:
            it = iter(gen_factory())
            try:
                first = next(it)
            except StopIteration:
                return
            started = True
            yield first
            yield from it
            return
        except Exception as exc:
            last_exc = exc
            if started or not policy.is_retryable(exc) or attempt == policy.max_attempts - 1:
                raise
            sleep(policy.delay_for(attempt, exc))
    if last_exc:
        raise last_exc
