from __future__ import annotations

from typing import Any, Iterator

from .base import ChatDelta, LLMProvider, ModelInfo, ProviderError

try:
    import openai  # type: ignore
    _HAS_OPENAI = True
except Exception:
    _HAS_OPENAI = False


class OpenAIProvider(LLMProvider):
    type = "openai"
    display_name = "OpenAI-compatible"

    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 api_key_env: str | None = None, default_model: str | None = None, **_: Any):
        if not _HAS_OPENAI:
            raise ProviderError("openai package not installed. Run: uv pip install openai")
        import os

        key = api_key or (os.environ.get(api_key_env) if api_key_env else None) or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ProviderError("No API key for OpenAI provider (set OPENAI_API_KEY or api_key_env)")
        self._client = openai.OpenAI(base_url=base_url, api_key=key)
        self._default_model = default_model

    @property
    def name(self) -> str:
        return "openai"

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
        if system and (not msgs or msgs[0].get("role") != "system"):
            msgs.insert(0, {"role": "system", "content": system})
        params: dict = {"model": model, "messages": msgs, "stream": stream}
        if tools:
            params["tools"] = tools
        params.update(kwargs)
        if not stream:
            resp = self._client.chat.completions.create(**params)
            msg = resp.choices[0].message
            yield ChatDelta(
                content=msg.content or "",
                tool_calls=[tc.model_dump() for tc in (msg.tool_calls or [])],
                done=True,
                raw=resp.model_dump(),
            )
            return
        for chunk in self._client.chat.completions.create(**params):
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            yield ChatDelta(
                content=delta.content or "",
                tool_calls=[tc.model_dump() for tc in (delta.tool_calls or [])] if delta.tool_calls else [],
                done=chunk.choices[0].finish_reason is not None,
                raw=chunk.model_dump(),
            )
