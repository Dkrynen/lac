from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .permissions import Permissions


@dataclass
class Agent:
    name: str
    type: str = "build"
    description: str = ""
    model: str | None = None
    system_prompt: str = ""
    permissions: Permissions = field(default_factory=lambda: Permissions.from_dict({}))
    tools: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "model": self.model,
            "system_prompt": self.system_prompt,
            "permissions": self.permissions.to_dict(),
            "tools": list(self.tools),
        }
