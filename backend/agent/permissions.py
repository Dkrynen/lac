from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class FilesystemPerm:
    read: bool = True
    write: bool = False
    delete: bool = False


@dataclass
class NetworkPerm:
    fetch: bool = True
    post: bool = False


@dataclass
class BashPerm:
    run: bool = False
    read_output: bool = True


@dataclass
class MCPPerm:
    connect: bool = True


@dataclass
class Permissions:
    filesystem: FilesystemPerm
    network: NetworkPerm
    bash: BashPerm
    mcp: MCPPerm

    def can_read(self) -> bool:
        return self.filesystem.read

    def can_write(self) -> bool:
        return self.filesystem.write

    def can_delete(self) -> bool:
        return self.filesystem.delete

    def can_run_bash(self) -> bool:
        return self.bash.run

    def can_fetch(self) -> bool:
        return self.network.fetch

    def can_post(self) -> bool:
        return self.network.post

    def can_mcp(self) -> bool:
        return self.mcp.connect

    def allows_tool(self, tool_name: str, write_tools: set[str], delete_tools: set[str], net_tools: set[str]) -> bool:
        if tool_name in write_tools:
            return self.can_write()
        if tool_name in delete_tools:
            return self.can_delete()
        if tool_name in net_tools:
            return self.can_fetch()
        return True

    def to_dict(self) -> dict:
        return {
            "filesystem": {"read": self.filesystem.read, "write": self.filesystem.write, "delete": self.filesystem.delete},
            "network": {"fetch": self.network.fetch, "post": self.network.post},
            "bash": {"run": self.bash.run, "read_output": self.bash.read_output},
            "mcp": {"connect": self.mcp.connect},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Permissions":
        fs = data.get("filesystem", {}) or {}
        nw = data.get("network", {}) or {}
        bs = data.get("bash", {}) or {}
        mc = data.get("mcp", {}) or {}
        return cls(
            filesystem=FilesystemPerm(
                read=fs.get("read", True), write=fs.get("write", False), delete=fs.get("delete", False)
            ),
            network=NetworkPerm(fetch=nw.get("fetch", True), post=nw.get("post", False)),
            bash=BashPerm(run=bs.get("run", False), read_output=bs.get("read_output", True)),
            mcp=MCPPerm(connect=mc.get("connect", True)),
        )


FULL_PERMISSIONS = Permissions(
    filesystem=FilesystemPerm(read=True, write=True, delete=True),
    network=NetworkPerm(fetch=True, post=True),
    bash=BashPerm(run=True, read_output=True),
    mcp=MCPPerm(connect=True),
)

READONLY_PERMISSIONS = Permissions(
    filesystem=FilesystemPerm(read=True, write=False, delete=False),
    network=NetworkPerm(fetch=True, post=False),
    bash=BashPerm(run=False, read_output=True),
    mcp=MCPPerm(connect=True),
)
