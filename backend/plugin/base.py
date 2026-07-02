from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

PLUGIN_TYPES = {"tool", "tui", "theme", "command", "provider"}


@dataclass
class PluginManifest:
    name: str
    type: str = "tool"
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    entry: str = "setup"
    path: str = ""
    enabled: bool = True
    options: dict = field(default_factory=dict)


@runtime_checkable
class PluginHost(Protocol):
    def register_tool(self, name: str, description: str, parameters: dict, handler: Any) -> None: ...
    def register_command(self, name: str, handler: Any) -> None: ...
    def register_provider(self, name: str, factory: Any) -> None: ...
    def register_theme(self, theme_id: str, theme: Any) -> None: ...
