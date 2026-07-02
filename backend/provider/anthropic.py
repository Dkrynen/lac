from __future__ import annotations

from typing import Any, Iterator

from .base import ChatDelta, LLMProvider, ModelInfo, ProviderError

try:
    import anthropic  # type: ignore
    _HAS_ANTHROPIC = True
except Exception:
    _HAS_ANTHROPIC = False


class AnthropicProvider(LLMProvider):
    type = "anthropic"
    display_name = "Anthropic Claude"

    def __init__(self, api_key: str | None = None, api_key_env: str | None = None,
                 default_model: str | None = None, **_: Any):
        if not _HAS_ANTHROPIC:
            raise ProviderError("anthropic package not installed. Run: uv pip install anthropic")
        import os

        key = api_key or (os.environ.get(api_key_env) if api_key_env else None) or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ProviderError("No API key for Anthropic provider (set ANTHROPIC_API_KEY or api_key_env)")
        self._client = anthropic.Anthropic(api_key=key)
        self._default_model = default_model

    @property
    def name(self) -> str:
        return "anthropic"

    def list_models(self) -> list[ModelInfo]:
        models = []
        for m in self._client.models.list().data:
            models.append(ModelInfo(name=m.id, raw={"id": m.id}))
        return models

    def chat(
        self,
        model: str,
        messages: list[dict],
        stream: bool = True,
        tools: list[dict] | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatDelta]:
        msgs = list(messages)
        if msgs and msgs[0].get("role") == "system":
            system = msgs[0]["content"] if system is None else system
            msgs = msgs[1:]
        params: dict = {"model": model, "messages": msgs, "max_tokens": kwargs.pop("max_tokens", 4096)}
        if system:
            params["system"] = system
        if tools:
            params["tools"] = tools
        params.update(kwargs)

        if not stream:
            resp = self._client.messages.create(**params)
            content = "".join(b.text for b in resp.content if b.type == "text")
            yield ChatDelta(content=content, done=True, raw=resp.model_dump())
            return

        with self._client.messages.stream(**params) as stream_resp:
            for text in stream_resp.text_stream:
                yield ChatDelta(content=text, done=False)
        yield ChatDelta(done=True)
