from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

from ..permission import Decision, PermissionEngine
from ..provider.base import ChatDelta, LLMProvider, ProviderError
from ..resilience import FallbackChain, build_default_chain
from .base import Agent
from .permissions import Permissions

ToolHandler = Callable[[dict, dict], str]
RememberAction = Callable[[], Any]
AskCommit = Callable[
    [RememberAction | None, RememberAction | None, str | None],
    "Decision",
]


@dataclass
class AskResult:
    decision: "Decision"
    remember: bool = False
    _commit: AskCommit | None = field(default=None, repr=False, compare=False)

    def consume(
        self,
        remember_action: RememberAction | None = None,
        rollback_action: RememberAction | None = None,
        grant_id: str | None = None,
    ) -> "Decision":
        """Commit an answer before its decision can authorize tool execution.

        Ordinary callers use the default path, which applies the optional
        remembered permission. The web bridge supplies a commit callback that
        couples permission persistence, audit persistence, and HTTP
        acknowledgement under its run-state lock.
        """

        if self._commit is not None:
            return self._commit(remember_action, rollback_action, grant_id)
        if self.decision is Decision.ALLOW and remember_action is not None:
            try:
                remember_action()
            except Exception:
                # SQLite/networked stores can fail after an ambiguous partial
                # write. Best-effort exact rollback keeps a failed approval
                # from silently becoming permanent.
                if rollback_action is not None:
                    rollback_action()
                raise
        return self.decision


AskCallback = Callable[[str, str, Any, str], Awaitable["AskResult | Decision"]]

Event = dict


@dataclass
class RunResult:
    content: str
    messages: list[dict] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    iterations: int = 0
    error: str | None = None


class AgentRunner:
    def __init__(
        self,
        provider: LLMProvider | FallbackChain,
        agent: Agent,
        tool_handlers: dict[str, ToolHandler] | None = None,
        tool_schemas: list[dict] | None = None,
        mcp: Any | None = None,
        ctx: dict | None = None,
        max_iterations: int = 12,
        permission_engine: PermissionEngine | None = None,
        on_ask: AskCallback | None = None,
        chat_options: dict[str, Any] | None = None,
        resilient: bool = True,
    ):
        self.agent = agent
        self.tool_handlers = tool_handlers or {}
        self.tool_schemas = list(tool_schemas or [])
        self.mcp = mcp
        self.ctx = ctx or {}
        self.max_iterations = max_iterations
        self.permission_engine = permission_engine
        self.on_ask = on_ask
        self.chat_options = dict(chat_options or {})
        self.provider = provider if (isinstance(provider, FallbackChain) or not resilient) else build_default_chain(provider)

        from ..plugin.builtins.tools import WRITE_TOOLS, DELETE_TOOLS, NETWORK_TOOLS

        self._write_tools = WRITE_TOOLS
        self._delete_tools = DELETE_TOOLS
        self._network_tools = NETWORK_TOOLS
        self._filesystem_tools = {"read_file", "write_file", "list_files"} | self._delete_tools
        self._chat_chain: FallbackChain = build_default_chain(self.provider, "primary")

    def _perm_key_for(self, tool_name: str) -> str:
        if tool_name in {"run_bash"}:
            return "bash"
        if tool_name in self._write_tools:
            return "edit"
        if tool_name in self._network_tools:
            return "webfetch"
        if tool_name in {"read_file"}:
            return "read"
        if tool_name in {"list_files"}:
            return "list"
        return "task"

    def _enabled_schemas(self) -> list[dict]:
        allowed = set(self.agent.tools)
        out = [s for s in self.tool_schemas if s["function"]["name"] in allowed]
        if self.mcp is not None and self.agent.permissions.can_mcp():
            out.extend(self.mcp.tool_schemas_for_agent())
        return out

    def _canonical_permission_target(self, tool_name: str, target: Any) -> Any:
        """Canonicalize local path policy input without changing handler arguments."""

        if tool_name not in self._filesystem_tools or not isinstance(target, str):
            return target
        base = Path(self.ctx.get("cwd", ".")).resolve()
        path = Path(target)
        resolved = path.resolve() if path.is_absolute() else (base / path).resolve()
        try:
            relative = resolved.relative_to(base)
        except ValueError:
            # Handlers retain their own workspace jail, but an outside target
            # must fail before policy evaluation, prompting, or remembering.
            raise ValueError(
                f"filesystem target resolves outside workspace: {target}"
            )
        return relative.as_posix()

    async def _check_permission(
        self, tool_name: str, target: Any, tool_args: dict
    ) -> tuple[bool, str]:
        if not tool_name.startswith("mcp_") and tool_name not in set(self.agent.tools):
            return False, f"[permission denied: {tool_name} not enabled for agent '{self.agent.name}']"

        if self.permission_engine is None:
            allowed = self.agent.permissions.allows_tool(
                tool_name, self._write_tools, self._delete_tools, self._network_tools
            )
            if not allowed:
                return False, f"[permission denied: {tool_name} not permitted for agent '{self.agent.name}']"
            return True, ""

        try:
            target = self._canonical_permission_target(tool_name, target)
        except (OSError, RuntimeError, ValueError) as e:
            return False, f"[permission denied: {tool_name} invalid filesystem target: {e}]"
        base_key = self._perm_key_for(tool_name)
        loop_detected = self.permission_engine.record_tool_call(
            self.agent.name, tool_name, tool_args
        )
        base_decision = self.permission_engine.evaluate(
            self.agent.name, base_key, target, tool_name=tool_name
        )
        key = base_key
        decision = base_decision
        if loop_detected and base_decision is not Decision.DENY:
            loop_decision = self.permission_engine.evaluate(
                self.agent.name, "doom_loop", target, tool_name=tool_name
            )
            if loop_decision is Decision.DENY:
                key = "doom_loop"
                decision = Decision.DENY
            else:
                # The third identical call always asks. A configured ALLOW may
                # never disable loop protection, and the doom key prevents the
                # answer from becoming a permanent approval.
                key = "doom_loop"
                decision = Decision.ASK
        if decision == Decision.ALLOW:
            return True, ""
        if decision == Decision.DENY:
            return False, f"[permission denied: {tool_name} ({key}) denied for agent '{self.agent.name}']"
        if self.on_ask is not None:
            try:
                result = await self.on_ask(self.agent.name, tool_name, target, key)
                if isinstance(result, Decision):
                    # Backward-compatible callers are treated as allow-once;
                    # only the explicit AskResult contract can persist.
                    result = AskResult(decision=result)
                if not isinstance(result, AskResult):
                    raise TypeError("ask callback must return AskResult")
            except Exception as e:
                return False, f"[permission ask failed: {e}]"
            remember_requested = (
                result.decision is Decision.ALLOW
                and result.remember is True
                and key != "doom_loop"
            )
            remember_action = None
            rollback_action = None
            owner_token = None
            if remember_requested:
                owner_token = self.permission_engine.new_remember_token()
                remember_action = lambda: self.permission_engine.remember(
                    self.agent.name,
                    key,
                    target,
                    tool_name=tool_name,
                    owner_token=owner_token,
                )
                rollback_action = lambda: self.permission_engine.forget_remembered(
                    self.agent.name,
                    key,
                    target,
                    tool_name=tool_name,
                    owner_token=owner_token,
                )
            try:
                final_decision = result.consume(
                    remember_action,
                    rollback_action,
                    owner_token,
                )
            except Exception as e:
                return False, f"[permission ask failed: {e}]"
            if final_decision == Decision.ALLOW:
                return True, ""
            if final_decision == Decision.DENY:
                return False, f"[permission denied by user: {tool_name}]"
            return False, f"[permission denied: {tool_name} (ask returned no decision)]"
        return False, f"[permission denied: {tool_name} ({key}) requires approval (no ask handler)]"

    async def _execute_tool(self, name: str, args: dict) -> tuple[bool, str]:
        if name == "list_files":
            list_path = args.get("path", ".")
            if not isinstance(list_path, str):
                return False, "[tool error: list_files path must be a string]"
            target = list_path or "."
        else:
            target = args.get("path") or args.get("command") or args.get("query") or args.get("url")
        ok, reason = await self._check_permission(name, target, args)
        if not ok:
            return False, reason

        if name.startswith("mcp_") and self.mcp is not None:
            parts = name.split("_", 2)
            if len(parts) == 3:
                srv, tool = parts[1], parts[2]
                try:
                    result = await self.mcp.call_tool(srv, tool, args)
                    text_parts = []
                    for c in getattr(result, "content", []) or []:
                        text = getattr(c, "text", None)
                        if text:
                            text_parts.append(text)
                    return True, "\n".join(text_parts) or "(no content)"
                except Exception as e:
                    return False, f"[mcp error: {e}]"
            return False, "[malformed mcp tool name]"

        handler = self.tool_handlers.get(name)
        if handler is None:
            return False, f"[unknown tool: {name}]"
        try:
            return True, handler(args, self.ctx)
        except Exception as e:
            return False, f"[tool error: {e}]"

    async def run_stream(self, user_text: str, history: list[dict] | None = None) -> AsyncIterator[Event]:
        messages = list(history or [])
        messages.append({"role": "user", "content": user_text})
        schemas = self._enabled_schemas()
        tools_param = schemas if schemas else None

        for i in range(self.max_iterations):
            content = ""
            tool_calls: list[dict] = []
            try:
                for delta in self._chat_chain.chat(
                    self.agent.model or "",
                    messages,
                    stream=True,
                    tools=tools_param,
                    system=self.agent.system_prompt or None,
                    **self.chat_options,
                ):
                    if delta.content:
                        content += delta.content
                        yield {"type": "delta", "content": delta.content}
                    if delta.tool_calls:
                        tool_calls.extend(delta.tool_calls)
                    if delta.done:
                        break
            except ProviderError as e:
                yield {"type": "error", "message": str(e)}
                return

            if tool_calls:
                asst = {"role": "assistant", "content": content, "tool_calls": tool_calls}
                messages.append(asst)
                yield {"type": "tool_calls", "calls": tool_calls}

                for call in tool_calls:
                    if not isinstance(call, dict):
                        name = "<invalid>"
                        args = {}
                        result = "[tool error: tool call must be a JSON object]"
                        yield {"type": "tool_call", "name": name, "args": args}
                        yield {"type": "tool_result", "name": name, "ok": False, "result": result}
                        messages.append({"role": "tool", "name": name, "content": result})
                        continue
                    fn = call.get("function", call)
                    if not isinstance(fn, dict):
                        name = "<invalid>"
                        args = {}
                        result = "[tool error: function must be a JSON object]"
                        yield {"type": "tool_call", "name": name, "args": args}
                        yield {"type": "tool_result", "name": name, "ok": False, "result": result}
                        messages.append({"role": "tool", "name": name, "content": result})
                        continue
                    name = fn.get("name", "")
                    if not isinstance(name, str) or not name:
                        safe_name = "<invalid>"
                        args = {}
                        result = "[tool error: tool name must be a non-empty string]"
                        yield {"type": "tool_call", "name": safe_name, "args": args}
                        yield {"type": "tool_result", "name": safe_name, "ok": False, "result": result}
                        messages.append({"role": "tool", "name": safe_name, "content": result})
                        continue
                    raw_args = fn.get("arguments", "{}")
                    parse_error = None
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except json.JSONDecodeError:
                        args = {}
                        parse_error = "[tool error: arguments must be valid JSON]"
                    yield {"type": "tool_call", "name": name, "args": args}
                    if parse_error is not None or not isinstance(args, dict):
                        result = parse_error or "[tool error: arguments must be a JSON object]"
                        yield {
                            "type": "tool_result",
                            "name": name,
                            "ok": False,
                            "result": result,
                        }
                        messages.append({"role": "tool", "name": name, "content": result})
                        continue
                    ok, result = await self._execute_tool(name, args)
                    yield {"type": "tool_result", "name": name, "ok": ok, "result": result[:4000]}
                    messages.append({"role": "tool", "name": name, "content": result})
                continue

            messages.append({"role": "assistant", "content": content})
            yield {"type": "done", "content": content, "messages": messages, "iterations": i + 1}
            return

        yield {"type": "error", "message": f"agent exceeded {self.max_iterations} tool iterations"}

    async def run(self, user_text: str, history: list[dict] | None = None) -> RunResult:
        content = ""
        events: list[Event] = []
        messages: list[dict] = []
        async for ev in self.run_stream(user_text, history):
            events.append(ev)
            if ev["type"] == "delta":
                content += ev["content"]
            elif ev["type"] == "done":
                content = ev.get("content", content)
                messages = ev.get("messages", [])
                return RunResult(content=content, messages=messages, events=events, iterations=ev.get("iterations", 0))
            elif ev["type"] == "error":
                return RunResult(content=content, messages=messages, events=events, error=ev["message"])
        return RunResult(content=content, messages=messages, events=events)
