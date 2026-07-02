from __future__ import annotations

import pytest

from backend.provider.base import ChatDelta, LLMProvider, ModelInfo, ProviderError
from backend.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    FallbackChain,
    RetryPolicy,
    RetryableError,
    retry,
    retry_stream,
)


def make_policy(**kw):
    base = dict(max_attempts=3, base_delay=0, max_delay=0, jitter=False)
    base.update(kw)
    return RetryPolicy(**base)


def test_retry_succeeds_after_transient():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RetryableError("boom", status=503)
        return "ok"

    assert retry(fn, policy=make_policy()) == "ok"
    assert calls["n"] == 3


def test_retry_non_retryable_fails_fast():
    def fn():
        raise RetryableError("bad", status=400)

    with pytest.raises(RetryableError):
        retry(fn, policy=make_policy())


def test_retry_exhausts_attempts():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise RetryableError("always", status=500)

    with pytest.raises(RetryableError):
        retry(fn, policy=make_policy(max_attempts=2))
    assert calls["n"] == 2


def test_retry_honors_retry_after():
    delays = []
    policy = make_policy(max_delay=10)
    policy.retry_on = (ValueError,)

    class RA(ValueError):
        retry_after = 5

    def fn():
        raise RA()

    def sleep(d):
        delays.append(d)

    with pytest.raises(RA):
        retry(fn, policy=policy, sleep=sleep)
    assert all(d == 5 for d in delays)


def test_retry_stream_retries_before_first_token():
    attempts = {"n": 0}

    def factory():
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise RetryableError("conn", status=502)

        def gen():
            yield ChatDelta(content="hi", done=True)

        return gen()

    out = [d.content for d in retry_stream(factory, policy=make_policy())]
    assert out == ["hi"]
    assert attempts["n"] == 2


def test_retry_stream_no_retry_after_first_token():
    attempts = {"n": 0}

    def factory():
        attempts["n"] += 1

        def gen():
            yield ChatDelta(content="hi")
            raise RetryableError("mid", status=500)

        return gen()

    with pytest.raises(RetryableError):
        list(retry_stream(factory, policy=make_policy()))
    assert attempts["n"] == 1


def test_circuit_breaker_opens_after_threshold():
    cb = CircuitBreaker("x", failure_threshold=3, recovery_timeout=30)
    assert cb.state is CircuitState.CLOSED
    cb.record_failure()
    cb.record_failure()
    assert cb.state is CircuitState.CLOSED
    cb.record_failure()
    assert cb.state is CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        cb.before_call()


def test_circuit_breaker_half_open_after_timeout():
    cb = CircuitBreaker("x", failure_threshold=1, recovery_timeout=0.05)
    cb.record_failure()
    assert cb.state is CircuitState.OPEN
    import time

    time.sleep(0.2)
    assert cb.state is CircuitState.HALF_OPEN
    cb.record_success()
    assert cb.state is CircuitState.CLOSED


def test_circuit_breaker_half_open_failure_reopens():
    cb = CircuitBreaker("x", failure_threshold=1, recovery_timeout=0.05)
    cb.record_failure()
    import time

    time.sleep(0.2)
    assert cb.state is CircuitState.HALF_OPEN
    cb.record_failure()
    assert cb.state is CircuitState.OPEN


class FakeProvider(LLMProvider):
    type = "fake"
    display_name = "Fake"

    def __init__(self, name, fail=False, models=None):
        self._name = name
        self._fail = fail
        self._models = models or [ModelInfo(name="m")]

    @property
    def name(self):
        return self._name

    def list_models(self):
        if self._fail:
            raise ProviderError("down")
        return self._models

    def chat(self, model, messages, stream=True, tools=None, system=None, **kwargs):
        if self._fail:
            raise ProviderError("down")
        yield ChatDelta(content="ok", done=True)


def test_fallback_chain_uses_primary_when_healthy():
    primary = FakeProvider("p")
    cb = CircuitBreaker("p")
    chain = FallbackChain([("p", primary, cb)])
    models = chain.list_models()
    assert models and chain.last_used == "p"


def test_fallback_chain_falls_over_to_secondary():
    primary = FakeProvider("p", fail=True)
    secondary = FakeProvider("s")
    chain = FallbackChain([
        ("p", primary, CircuitBreaker("p", failure_threshold=1)),
        ("s", secondary, CircuitBreaker("s")),
    ])
    models = chain.list_models()
    assert chain.last_used == "s"
    assert "p" in (chain.last_fallback_reason or "")


def test_fallback_chain_streaming_falls_over():
    primary = FakeProvider("p", fail=True)
    secondary = FakeProvider("s")
    chain = FallbackChain([
        ("p", primary, CircuitBreaker("p", failure_threshold=1)),
        ("s", secondary, CircuitBreaker("s")),
    ])
    out = [d.content for d in chain.chat("m", [{"role": "user", "content": "hi"}], stream=True)]
    assert out == ["ok"]
    assert chain.last_used == "s"


def test_fallback_chain_all_fail_raises():
    primary = FakeProvider("p", fail=True)
    secondary = FakeProvider("s", fail=True)
    chain = FallbackChain([
        ("p", primary, CircuitBreaker("p", failure_threshold=1)),
        ("s", secondary, CircuitBreaker("s", failure_threshold=1)),
    ])
    with pytest.raises(ProviderError):
        list(chain.chat("m", [{"role": "user", "content": "hi"}], stream=True))


def test_fallback_chain_skips_open_breaker():
    primary = FakeProvider("p", fail=True)
    secondary = FakeProvider("s")
    p_cb = CircuitBreaker("p", failure_threshold=1, recovery_timeout=60)
    p_cb.record_failure()
    assert p_cb.state is CircuitState.OPEN
    chain = FallbackChain([("p", primary, p_cb), ("s", secondary, CircuitBreaker("s"))])
    out = [d.content for d in chain.chat("m", [{"role": "user", "content": "hi"}], stream=True)]
    assert out == ["ok"]
    assert chain.last_used == "s"
