from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..config import ProviderConfig, resolve_config
from .base import LLMProvider, ProviderError
from .ollama import OllamaProvider

_BUILTIN_FACTORIES: dict[str, Callable[..., LLMProvider]] = {}


def _register_factory(name: str, factory: Callable[..., LLMProvider]) -> None:
    _BUILTIN_FACTORIES[name] = factory


def _load_builtin_factories() -> None:
    _register_factory("ollama", lambda **kw: OllamaProvider(**kw))
    try:
        from .openai import OpenAIProvider

        _register_factory("openai", lambda **kw: OpenAIProvider(**kw))
    except Exception:
        pass
    try:
        from .anthropic import AnthropicProvider

        _register_factory("anthropic", lambda **kw: AnthropicProvider(**kw))
    except Exception:
        pass


_load_builtin_factories()


_PLUGIN_FACTORIES: dict[str, Callable[..., LLMProvider]] = {}


def register_provider(name: str, factory: Callable[..., LLMProvider]) -> None:
    _PLUGIN_FACTORIES[name] = factory


def known_providers() -> list[str]:
    return sorted(set(_BUILTIN_FACTORIES) | set(_PLUGIN_FACTORIES))


def create_provider(
    name: str | None = None,
    *,
    start: Path | None = None,
    **overrides: Any,
) -> LLMProvider:
    cfg = resolve_config(start)
    providers = cfg.providers

    if name is None:
        if not providers:
            name = "ollama"
        else:
            name = next(iter(providers))

    if name in _PLUGIN_FACTORIES:
        return _PLUGIN_FACTORIES[name](**overrides)
    if name in _BUILTIN_FACTORIES:
        pcfg = providers.get(name)
        params: dict = {}
        if pcfg is not None:
            params.update(
                {
                    "base_url": pcfg.base_url,
                    "api_key": pcfg.api_key,
                    "api_key_env": pcfg.api_key_env,
                    "default_model": pcfg.default_model,
                }
            )
        params = {k: v for k, v in params.items() if v is not None}
        params.update(overrides)
        if "base_url" not in params and name == "ollama":
            params.setdefault("base_url", cfg.ollama_host)
        return _BUILTIN_FACTORIES[name](**params)

    raise ProviderError(f"Unknown provider: {name!r}. Known: {known_providers()}")


def list_providers(start: Path | None = None) -> list[dict]:
    cfg = resolve_config(start)
    out = []
    for name in known_providers():
        out.append(
            {
                "name": name,
                "configured": name in cfg.providers,
                "type": (cfg.providers[name].type if name in cfg.providers else name),
            }
        )
    return out


def default_provider(start: Path | None = None) -> LLMProvider:
    return create_provider(None, start=start)
