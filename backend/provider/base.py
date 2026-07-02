from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass
class ModelInfo:
    name: str
    size: int = 0
    modified: str = ""
    context_length: int = 0
    quant: str = ""
    family: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def size_gb(self) -> float:
        return self.size / 1_073_741_824 if self.size else 0.0


@dataclass
class ChatDelta:
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    done: bool = False
    raw: dict = field(default_factory=dict)


class ProviderError(RuntimeError):
    pass


class LLMProvider(ABC):
    type: str = "abstract"
    display_name: str = "Abstract"

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def list_models(self) -> list[ModelInfo]: ...

    @abstractmethod
    def chat(
        self,
        model: str,
        messages: list[dict],
        stream: bool = True,
        tools: list[dict] | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatDelta]: ...

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        raise ProviderError(f"{self.name} does not support embeddings")

    def pull(self, model: str) -> Iterator[dict]:
        raise ProviderError(f"{self.name} does not support pull")

    def delete(self, model: str) -> bool:
        raise ProviderError(f"{self.name} does not support delete")

    def running(self) -> list[dict]:
        return []

    def is_available(self) -> bool:
        try:
            self.list_models()
            return True
        except Exception:
            return False
