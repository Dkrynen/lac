from __future__ import annotations

import logging
from typing import Any, Iterator

from ..provider.base import ChatDelta, LLMProvider, ModelInfo, ProviderError
from .circuit_breaker import CircuitBreaker, CircuitOpenError
from .retry import RetryPolicy, retry_stream

log = logging.getLogger("apt.resilience")


class FallbackChain(LLMProvider):
    type = "fallback"
    display_name = "Fallback Chain"

    def __init__(
        self,
        steps: list[tuple[str, LLMProvider, CircuitBreaker]],
        retry_policy: RetryPolicy | None = None,
    ):
        if not steps:
            raise ValueError("FallbackChain requires at least one step")
        self.steps = steps
        self.retry_policy = retry_policy
        self._last_used: str | None = None
        self._last_fallback_reason: str | None = None

    @property
    def name(self) -> str:
        return "fallback"

    @property
    def last_used(self) -> str | None:
        return self._last_used

    @property
    def last_fallback_reason(self) -> str | None:
        return self._last_fallback_reason

    def list_models(self) -> list[ModelInfo]:
        last_exc: Exception | None = None
        for provider_name, provider, breaker in self.steps:
            try:
                breaker.before_call()
                models = provider.list_models()
                breaker.record_success()
                self._last_used = provider_name
                return models
            except CircuitOpenError as e:
                self._last_fallback_reason = str(e)
                last_exc = e
                continue
            except Exception as e:
                breaker.record_failure()
                self._last_fallback_reason = f"{provider_name}: {e}"
                last_exc = e
                continue
        raise ProviderError(f"all providers failed in list_models: {last_exc}")

    def chat(
        self,
        model: str,
        messages: list[dict],
        stream: bool = True,
        tools: list[dict] | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatDelta]:
        last_exc: Exception | None = None
        for provider_name, provider, breaker in self.steps:
            try:
                breaker.before_call()
            except CircuitOpenError as e:
                self._last_fallback_reason = str(e)
                last_exc = e
                continue

            def _factory(p=provider, m=model, msgs=messages, st=stream, tl=tools, sys_=system, kw=kwargs):
                return p.chat(m, msgs, stream=st, tools=tl, system=sys_, **kw)

            try:
                if stream:
                    yield from retry_stream(_factory, policy=self.retry_policy)
                else:
                    result = retry(lambda f=_factory: list(f()), policy=self.retry_policy)
                    for item in result:
                        yield item
                breaker.record_success()
                self._last_used = provider_name
                self._last_fallback_reason = None
                return
            except CircuitOpenError as e:
                self._last_fallback_reason = str(e)
                last_exc = e
                continue
            except Exception as e:
                breaker.record_failure()
                reason = f"{provider_name}: {e}"
                self._last_fallback_reason = reason
                log.warning("provider %s failed, falling back: %s", provider_name, e)
                last_exc = e
                continue
        raise ProviderError(f"all providers failed in chat: {last_exc}")

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        last_exc: Exception | None = None
        for provider_name, provider, breaker in self.steps:
            try:
                breaker.before_call()
                result = provider.embed(model, texts)
                breaker.record_success()
                self._last_used = provider_name
                return result
            except Exception as e:
                breaker.record_failure()
                self._last_fallback_reason = f"{provider_name}: {e}"
                last_exc = e
                continue
        raise ProviderError(f"all providers failed in embed: {last_exc}")


def build_default_chain(provider: LLMProvider, name: str = "primary") -> FallbackChain:
    return FallbackChain([(name, provider, CircuitBreaker(name))])
