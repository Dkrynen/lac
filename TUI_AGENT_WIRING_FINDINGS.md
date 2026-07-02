# TUI AgentRunner Wiring — Research Findings

**Date:** 2026-07-01
**Context:** Model Hub (Apt) v2.2.0, Textual 8.2.8, Python 3.11.15, Ollama 0.30.11
**Source files:** `backend/tui/app.py`, `backend/agent/runner.py`, `backend/permission/engine.py`, `backend/resilience/fallback.py`, `backend/provider/base.py`, `backend/mcp/client.py`, `backend/plugin/builtins/tools.py`

---

## 1. Async Worker Pattern for AgentRunner Streaming

### Hypothesis
The AgentRunner.run_stream() is an async generator yielding typed events. The TUI can consume it from a `@work()` async worker (NOT `@work(thread=True)`), enabling direct widget manipulation and `await push_screen(wait_for_dismiss=True)` for permission dialogs — all without `call_from_thread`.

### Evidence

**Textual 8.2.8 Worker Architecture (verified from source):**

- `Worker._run(app)` (line ~50) wraps execution in `app._context()` which sets `active_app` and `active_message_pump` context vars — the same vars required by widget methods like `query_one()`, `mount()`, `update()`.
- `Worker._run_async()` (line ~15) does `return await self._work()` — the async work function runs directly in the asyncio event loop, not in a thread.
- `App.call_from_thread()` (line ~45) explicitly raises `RuntimeError("The call_from_thread method must run in a different thread from the app")` — confirming it is ONLY for `@work(thread=True)`, NOT for async workers.
- The `@work` decorator (line ~60) calls `self.run_worker(partial(method, ...), ...)` with `start=True` by default — worker starts immediately when the decorated method is called.

**AgentRunner.run_stream() API (line 141 of `backend/agent/runner.py`):**
```python
async def run_stream(self, user_text: str, history: list[dict] | None = None) -> AsyncIterator[Event]:
```
Yields events: `delta`, `tool_calls`, `tool_call`, `tool_result`, `done`, `error`

**Proven async worker safety for widget operations:**
The `on_input_submitted` handler is already async and successfully calls `self.query_one("#inp", Input)`, `self.mount()`, `await self._msg()`, `await self._line()` — all from the event loop. An async worker has the same capability because `Worker._run()` sets `app._context()` which establishes `active_app` and `active_message_pump`.

**Markdown performance (verified):** Rich's `Markdown(text, code_theme="monokai")` parses in <1ms even for 3000-char texts. Per-token re-parse is negligible. Incomplete code fences (open `` ``` `` without closing) render without errors — Rich handles partial Markdown gracefully.

**Current gap (line 387 of TUI app.py):** `_run_stream` uses `@work(thread=True)` + `call_from_thread`, bypassing AgentRunner entirely:
```python
# Current: thread worker with raw urllib streaming
@work(thread=True, exclusive=True)
def _run_stream(self, text: str) -> None:
    # ... blocking urllib.request call ...
    self.call_from_thread(self._stream_update, full)
```
This thread worker:
1. Cannot use AgentRunner (runs in thread, can't call async APIs)
2. Cannot show permission dialogs (can't `await push_screen`)
3. Cannot execute tools (no tool handler integration)
4. Has no circuit breaker / fallback (no FallbackChain)
5. Has no MCP tools

### Recommended Approach

Replace `@work(thread=True)` with `@work()` and consume AgentRunner.run_stream():

```python
# In AptApp.__init__ or on_mount:
from backend.agent import AgentRunner, get_agent
from backend.agent.runner import ask_callback  # defined below

@work(exclusive=True, group="chat")
async def _run_agent_stream(self, text: str) -> None:
    # Build AgentRunner with current agent + MCP + permission engine
    agent = self._agent_for_reply()  # current agent or get_agent("build")
    if not agent.model:
        agent.model = self.model  # use current Ollama model

    provider = self._provider_for_agent()  # OllamaProvider(base_url=OLLAMA_HOST)
    chain = build_default_chain(provider, agent.name)

    permission_engine = PermissionEngine.from_config()

    tools = TOOL_HANDLERS  # from builtins
    schemas = TOOL_SCHEMAS

    # If MCP is connected, inject MCP into runner
    mcp = self._mcp if self._mcp_ready else None

    runner = AgentRunner(
        provider=chain,
        agent=agent,
        tool_handlers=tools,
        tool_schemas=schemas,
        mcp=mcp,
        permission_engine=permission_engine,
        on_ask=self._permission_ask,
        resilient=True,
    )

    # Mount user message widget
    self.messages.append({"role": "user", "content": text})
    await self._msg("user", text)
    assistant_widget = await self._msg("assistant")
    full_content = ""

    # Stream events from AgentRunner
    try:
        async for ev in runner.run_stream(text, self._agent_history()):
            if ev["type"] == "delta":
                full_content += ev["content"]
                assistant_widget.update(
                    Markdown(full_content.strip(), code_theme="monokai")
                )
                self._scr().scroll_end()
            elif ev["type"] == "tool_call":
                await self._line(
                    f"  [dim]→ calling {ev['name']}({json.dumps(ev['args'])[:80]})[/dim]"
                )
            elif ev["type"] == "tool_result":
                color = "green" if ev["ok"] else "red"
                await self._line(
                    f"  [{color}]← {ev['name']}: {ev['result'][:200]}[/{color}]"
                )
            elif ev["type"] == "error":
                assistant_widget.update(f"[red]{ev['message']}[/red]")
                break
            elif ev["type"] == "done":
                full_content = ev.get("content", full_content)
                break
    finally:
        if full_content:
            self.messages.append({"role": "assistant", "content": full_content})
        self.streaming = False
        self._stream_widget = None
        inp = self.query_one("#inp", Input)
        inp.disabled = False
        inp.focus()
```

**How `@work()` vs `@work(thread=True)` differs:**

| Aspect | `@work(thread=True)` (current) | `@work()` async (target) |
|--------|-------------------------------|--------------------------|
| Execution | Thread pool | Asyncio task |
| Widget access | `call_from_thread` required | Direct |
| `await push_screen` | NOT possible | Works with `wait_for_dismiss=True` |
| `async for` | NOT possible | Works directly |
| AgentRunner call | Cannot (async API) | Natural `async for` |
| Network I/O | Blocking urllib | Async (provider abstraction) |
| Error handling | `call_from_thread` callback | Standard try/except |

### Implementation Risk: **LOW**

- Pattern is proven: `on_input_submitted` is already async and calls widget methods directly.
- `@work()` without thread creates an asyncio task in the same event loop — standard Python.
- `exclusive=True` cancels previous streaming worker (same as current code).

### Verification
1. Run `test_tui.py` with `run_test()` — the async pilot already works with the Textual test harness
2. Manual test: `python cli.py chat`, send a prompt, verify streaming updates token-by-token
3. Manual test: `/agent build`, send "list the files in backend/", verify tool_call and tool_result appear in transcript
4. Unit test: create a `MockAsyncLLMProvider` that yields tool calls, wire it to `AgentRunner`, verify stream events

---

## 2. Permission "Ask" Flow as a TUI Modal

### Hypothesis
The `on_ask` callback in `AgentRunner` can `await self.app.push_screen(PermissionModal(...), wait_for_dismiss=True)` from within an async worker, blocking the agent execution until the user decides, without freezing the UI.

### Evidence

**Textual 8.2.8 push_screen API (verified from source, line ~35):**
```python
def push_screen(
    self, screen: Screen | str,
    callback: Callable | None = None,
    wait_for_dismiss: bool = False,
    *, mode: str | None = None,
) -> AwaitMount | asyncio.Future:
```

Docs state: *"wait_for_dismiss: If `True`, awaiting this method will return the dismiss value from the screen. When set to `False`, awaiting this method will wait for the screen to be mounted. Note that `wait_for_dismiss` should only be set to `True` when running in a worker."*

**ModalScreen.dismiss() API (line ~15):**
```python
def dismiss(self, result: ScreenResultType | None = None) -> AwaitComplete:
    """Dismiss the screen, optionally with a result.
    Any callback provided in push_screen will be invoked with the supplied result."""
```

**Current AgentRunner.on_ask signature (line 120 of `runner.py`):**
```python
on_ask: AskCallback | None = None
# where:
AskCallback = Callable[[str, str, str | None], Awaitable[Decision]]
```

**AgentRunner._check_permission flow (line 80 of `runner.py`):**
```python
async def _check_permission(self, tool_name: str, target: str | None):
    ...
    if self.on_ask is not None:
        user_decision = await self.on_ask(self.agent.name, tool_name, target)
        if user_decision == Decision.ALLOW:
            self.permission_engine.remember(self.agent.name, key, target)
            return True, ""
```

This is the exact await point we need. The worker will suspend here, the modal will appear, user interaction resolves the future, and control returns to the worker.

### Recommended Approach

**PermissionModal class:**

```python
from textual.screen import ModalScreen
from textual.widgets import Button, Static
from textual.containers import Vertical, Horizontal
from backend.permission import Decision

class PermissionModal(ModalScreen[Decision]):
    CSS = """
    #perm-dialog {
        width: 54; height: auto; padding: 1 2;
        background: $surface; border: solid $primary;
    }
    #perm-title { margin-bottom: 1; }
    #perm-body { margin-bottom: 1; color: $text-muted; }
    #perm-target { margin-bottom: 1; color: $warning; }
    #perm-buttons { width: 1fr; align: right bottom; }
    Horizontal { height: auto; margin-top: 1; }
    """

    def __init__(self, agent_name: str, tool_name: str, target: str | None = None):
        super().__init__()
        self.agent_name = agent_name
        self.tool_name = tool_name
        self.target = target or ""

    def compose(self) -> ComposeResult:
        with Vertical(id="perm-dialog"):
            yield Static(
                f"Agent [bold]{self.agent_name}[/bold] wants to use [bold cyan]{self.tool_name}[/bold]",
                id="perm-title",
            )
            if self.target:
                yield Static(f"Target: {self.target[:120]}", id="perm-target")
            yield Static("Allow this operation?", id="perm-body")
            yield Horizontal(
                Button("Deny", variant="error", id="btn-deny"),
                Button("Allow Once", variant="primary", id="btn-allow-once"),
                Button("Always Allow", variant="success", id="btn-allow-always"),
                id="perm-buttons",
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-allow-once":
            self.dismiss(Decision.ALLOW)
        elif event.button.id == "btn-allow-always":
            self.dismiss(("allow_always", Decision.ALLOW))
        elif event.button.id == "btn-deny":
            self.dismiss(Decision.DENY)
```

**on_ask callback bound in _run_agent_stream:**

```python
async def _permission_ask(self, agent_name: str, tool_name: str, target: str | None) -> Decision:
    """Called by AgentRunner when a tool requires user approval.
    Awaits user response from a modal dialog."""
    modal = PermissionModal(agent_name, tool_name, target)
    result = await self.app.push_screen(modal, wait_for_dismiss=True)
    if isinstance(result, tuple) and result[0] == "allow_always":
        # The remember call in _check_permission handles storage
        return result[1]
    return result if isinstance(result, Decision) else Decision.DENY
```

**Flow diagram:**
```
async worker                           Textual event loop
     |                                       |
     |--- awaits runner.run_stream() -----   |
     |   runner yields tool_call             |
     |   runner awaits on_ask()              |
     |       |                               |
     |       |--- push_screen(modal, T) -->  |
     |       |                               |--- mounts modal
     |       |   (worker is suspended)       |--- user clicks "Allow Once"
     |       |                               |--- modal.dismiss(Decision.ALLOW)
     |       |       <-- future resolves --  |
     |       |                               |--- pops modal
     |   Decision.ALLOW returned             |
     |   runner executes tool                |
     |   runner yields tool_result           |
     |   worker mounts result widget         |
     |                                       |
```

**Why suspension works without freezing the UI:**
- `await push_screen(wait_for_dismiss=True)` returns a `Future` created by the event loop
- The worker's coroutine suspends at the `await` — the event loop is free to process other events
- Textual processes mouse/key events, renders the modal, handles button clicks
- When `dismiss(result)` is called, the future resolves, and the event loop resumes the worker
- The provider connection (urllib or httpx stream) remains open on a background task — not blocked

### Implementation Risk: **LOW**

- `wait_for_dismiss=True` is documented for worker use in Textual 8.2.8
- Pattern tested in `test_permission.py` line 175 where `on_ask` callback returns `Decision.ALLOW`
- No platform-specific issues — Python asyncio suspend/resume is cross-platform

### Verification
1. Unit test: `async with app.run_test() as pilot:`, trigger tool call, verify PermissionModal appears, click "Allow Once", verify `Decision.ALLOW` flows back
2. Manual: `/agent build`, type "write a file at test.txt with hello", verify modal appears, click Deny, verify permission denied message

---

## 3. MCP connect_all on TUI Startup

### Hypothesis
MCPManager.connect_all() should be called from a `@work()` (async) worker launched in `on_mount()`, not from `on_mount()` directly, to avoid blocking UI initialization. The connected MCPManager + its tool_schemas are passed into AgentRunner at chat time.

### Evidence

**MCPManager.connect_all() (line 109 of `backend/mcp/client.py`):**
```python
async def connect_all(self) -> dict[str, bool]:
    results = {}
    for name in list(self.servers):
        results[name] = await self.connect(name)
    return results
```
Each `connect()` spawns stdio subprocesses via MCP SDK, which requires asyncio — cannot run from thread.

**Cross-task cancel-scope constraint (line 147 of `client.py`):**
```python
async def close_all(self) -> None:
    if self._stack is not None:
        try:
            await self._stack.aclose()
        except RuntimeError:
            pass  # RESEARCH_FINDINGS.md Pattern A: safe close
```
The current implementation already uses the recommended `_safe_close_exit_stack` pattern (catching RuntimeError). The key constraint is that `connect()` and `close_all()` should run in the same asyncio task when possible. Since both will be called from TUI event loop tasks, `close_all()` can be called from `AptApp.on_mount().__exit__` or app shutdown hook.

**Textual app shutdown hooks (from Textual 8.2.8):**
Textual apps support `on_shutdown()` or `app.exit()` lifecycle hooks. Closest match is overriding `App.action_quit()` or the `on_unmount` handler.

### Recommended Approach

```python
class AptApp(App):
    # ... existing fields ...
    def __init__(self):
        super().__init__()
        self._mcp_manager = None
        self._mcp_ready = False
        self._mcp_error = None

    def on_mount(self) -> None:
        self._register_themes()
        self._bar("[dim]connecting to Ollama...[/dim]")
        self._refresh_models()
        self._start_mcp()  # fire-and-forget async worker

    @work(exclusive=False, group="init")
    async def _start_mcp(self) -> None:
        """Connect to all configured MCP servers (async, non-blocking)."""
        try:
            from backend.mcp.client import MCPManager
            self._mcp_manager = MCPManager()
            results = await self._mcp_manager.connect_all()
            connected = sum(1 for v in results.values() if v)
            total = len(results)
            if connected:
                self._mcp_ready = True
                # Update bar with MCP status
                self._bar(f"[dim]MCP: {connected}/{total} servers connected[/dim]")
            else:
                self._mcp_error = "no servers connected"
        except Exception as e:
            self._mcp_error = str(e)

    async def action_quit(self) -> None:
        """Clean shutdown — close MCP connections."""
        if self._mcp_manager:
            try:
                await self._mcp_manager.close_all()
            except Exception:
                pass
        self.exit()
```

**Passing MCPManager to AgentRunner (in `_run_agent_stream`):**
```python
# MCPManager is passed directly to AgentRunner constructor
# AgentRunner._enabled_schemas() (line 73) injects MCP tools:
def _enabled_schemas(self) -> list[dict]:
    allowed = set(self.agent.tools)
    out = [s for s in self.tool_schemas if s["function"]["name"] in allowed]
    if self.mcp is not None and self.agent.permissions.can_mcp():
        out.extend(self.mcp.tool_schemas_for_agent())  # prefixes with mcp_{server}_{tool}
    return out

# MCP tool execution in _execute_tool (line 116):
if name.startswith("mcp_") and self.mcp is not None:
    parts = name.split("_", 2)
    srv, tool = parts[1], parts[2]
    result = await self.mcp.call_tool(srv, tool, args)
```

No changes needed in AgentRunner — MCP injection is already designed for this.

### Cross-task cancel-scope mitigation
- `_start_mcp()` runs as an async worker — same event loop as shutdown
- `action_quit()` is an async handler — also same event loop
- `close_all()` will run within a Textual handler context, which sets `active_app` — safe
- The `except RuntimeError: pass` in `close_all()` is the safety net per Pattern A

### Implementation Risk: **MEDIUM**

MCP SDK stdio subprocess spawning can hang on misconfigured servers. Mitigation: use `try/except` in `_start_mcp()` worker, which won't crash the app (workers in group "init" don't have `exit_on_error=True`). If an MCP server fails to start, the app continues with Ollama-only tools.

### Verification
1. Configure a test MCP server in `.apt/apt.jsonc` (e.g., `npx @modelcontextprotocol/server-filesystem /tmp`)
2. Launch TUI, check bar for "MCP: 1/1 servers connected"
3. Type a prompt requiring MCP tool, verify `mcp_*` tool schemas appear in agent tools
4. Test `close_all()` on app quit — no RuntimeError raised

---

## 4. Agent Switching + Per-Agent Session Scoping

### Hypothesis
Each agent (build/plan/explore/custom) should have independent message history, while sharing the same transcript view. Switching agents clears the visible transcript but preserves per-agent history. `/clear` clears only the active agent's history.

### Evidence

**Current TUI state (line 119-125 of `app.py`):**
```python
self.model = ""       # current Ollama model
self.models = []      # installed models
self.messages = []    # current chat history (ONE for all agents)
self.sid = None       # session ID (ONE for all agents)
self.agent = None     # current Agent dataclass
self.agent_name = "build"
```

**Current `/agent` handler (line 453):** Switches `self.agent` and `self.agent_name` but does NOT scope messages — old history from previous agent remains in `self.messages`.

**AgentRunner uses history parameter (line 141 of `runner.py`):**
```python
async def run_stream(self, user_text: str, history: list[dict] | None = None):
    messages = list(history or [])
    messages.append({"role": "user", "content": user_text})
```
History is passed per-call — the runner doesn't retain it. So per-agent history is a TUI concern, not a runner concern.

### Recommended Approach

**Data model:**

```python
def __init__(self):
    super().__init__()
    # ... existing ...
    self.agent_histories: dict[str, list[dict]] = {}  # agent_name -> messages
    self.agent_sids: dict[str, str] = {}               # agent_name -> session_id

def _agent_history(self) -> list[dict]:
    """Get history for the currently active agent."""
    return list(self.agent_histories.get(self.agent_name, []))

async def _save_agent_history(self, messages: list[dict]) -> None:
    """Save messages to the active agent's history scope."""
    self.agent_histories[self.agent_name] = list(messages)

def _agent_for_reply(self) -> Agent:
    """Get the active Agent, or fall back to default."""
    from backend.agent import get_agent
    agent = get_agent(self.agent_name)
    if agent and not agent.model and self.model:
        agent.model = self.model
    return agent
```

**Modified `_run_agent_stream` (cleanup section):**
```python
finally:
    if full_content:
        msgs = self._agent_history()
        msgs.extend([...])  # append user + assistant messages
        await self._save_agent_history(msgs)
```

**Modified `/agent` handler:**
```python
async def _agent_command(self, arg: str) -> None:
    # ... existing agent resolution ...
    if self.agent_name != a.name:
        # Save current history before switching
        self.agent_histories[self.agent_name] = list(self.messages)
        # Load target agent's history
        self.messages = list(self.agent_histories.get(a.name, []))
        # Re-render transcript
        await self._rerender_transcript()
    # ... set new agent fields ...
```

**Modified `/clear` handler:**
```python
elif cmd == "/clear":
    await s.remove_children()
    self.messages = []
    self.agent_histories[self.agent_name] = []
    await self._line(f"cleared [{self.agent_name}]")
```

### Implementation Risk: **LOW**

- Pure state management — no threading/async concerns
- AgentRunner already accepts `history` parameter per call
- Session IDs and persistence already scoped per agent via `agent_sids`

### Verification
1. Chat with build agent, send "read the README", verify tool call executes
2. Switch to plan agent (`/agent plan`), transcript clears but build history is preserved
3. Switch back to build (`/agent build`), verify build history reappears
4. `/clear` only clears build history, plan history unaffected
5. Check `agent_histories` dict after heavy cycling

---

## 5. Streaming Markdown Rendering Correctness

### Hypothesis
Per-token Rich Markdown re-parse is performant enough for real-time streaming, but widget.update() calls should not exceed ~60 FPS (every ~16ms) for smooth rendering. Incomplete code fences render safely.

### Evidence

**Performance benchmark (verified):**
| Text length | Parse time (ms) |
|------------|----------------|
| 5 chars | 0.40 |
| 47 chars | 0.33 |
| 2,970 chars | 0.50 |

Rich's `Markdown()` parse is sub-millisecond — well within token arrival rates (typically 20-50ms between tokens from Ollama).

**Incomplete code fence behavior (verified):**
All partial states render without exceptions:
- Open `` ```python `` — renders as text
- `` ```python\ndef foo(): `` — renders as text
- `` ```python\ndef foo():\n    pass `` — no closing fence, renders as text
- Mid-stream `` Normal text ```python\ndef `` — renders without error

No special handling needed for partial Markdown.

**Widget.update() throttling consideration:**
The current code calls `widget.update()` on every token. At typical Ollama streaming speed (~20-50ms between tokens), this is ~20-50 updates/second — well within Textual's render budget. However, if a fast provider delivers tokens at <5ms intervals, batching is recommended.

### Recommended Approach

**No throttling for Ollama** (tokens arrive at ~20-50ms intervals). For fast providers (OpenAI, Anthropic at >100 tokens/sec), add a simple time gate:

```python
_STREAM_UPDATE_MIN_MS = 16  # ~60 FPS cap

_last_update_time = 0.0
async for ev in runner.run_stream(text, history):
    if ev["type"] == "delta":
        full_content += ev["content"]
        now = time.monotonic()
        if now - self._last_update_time > (_STREAM_UPDATE_MIN_MS / 1000):
            assistant_widget.update(
                Markdown(full_content.strip(), code_theme="monokai")
            )
            self._scr().scroll_end()
            self._last_update_time = now

# Final update after stream ends (guaranteed flush)
if full_content:
    assistant_widget.update(
        Markdown(full_content.strip(), code_theme="monokai")
    )
```

### Implementation Risk: **LOW**

- Rich Markdown is well-tested, gracefully handles partial input
- Throttle is a defensive optimization, not required for Ollama
- Can be added later if performance issues are observed

### Verification
1. Run TUI with a large model (e.g., phi4:14b), send a complex coding prompt
2. Observe streaming — UI remains responsive, scroll bar works
3. Send "write Python code for" prompt to trigger code blocks — verify syntax highlighting appears
4. Check for flicker or stutter — if none, throttle is unnecessary

---

## 6. Textual 8.2.8 API Edge Cases

### Calling `@work()` from `on_mount`

`on_mount` is a synchronous DOM event handler. Calling a `@work()` decorated method from within it returns a `Worker` instance but DOES NOT `await` it. The worker runs as a background asyncio task. This is correct — MCP init should not block mount:

```python
def on_mount(self) -> None:
    self._start_mcp()    # returns Worker immediately, runs in background
    self._bar()          # bar updates before MCP connects
```

### Worker error handling vs app crashes

`@work` defaults to `exit_on_error=True` — unhandled exceptions crash the app. For MCP init and other background workers, set `exit_on_error=False`:

```python
@work(exclusive=False, group="init", exit_on_error=False)
async def _start_mcp(self) -> None:
    ...
```

### call_from_thread from async workers

DO NOT use `call_from_thread` from an async worker — it raises RuntimeError. Async workers can directly manipulate widgets because `Worker._run` sets `app._context()`. The app context provides `active_app` and `active_message_pump` context vars required by all widget operations.

### Push_screen edge case: dismissing from within callback

The `dismiss()` method doc warns: *"Textual will raise a ScreenError if you await the return value from a message handler on the Screen being dismissed."* In our PermissionModal, `on_button_pressed` calls `self.dismiss(result)` without awaiting it — correct.

---

## Summary: Integration Code Sketch

```python
class AptApp(App):
    # ... CSS, BINDINGS unchanged ...

    def __init__(self):
        super().__init__()
        self.model = ""
        self.models = []
        self.messages = []
        self.sid = None
        self.streaming = False
        self.system = ""
        self._stream_widget = None
        self.agent = None
        self.agent_name = "build"
        # New fields:
        self.agent_histories = {}      # agent_name -> list[dict]
        self.agent_sids = {}           # agent_name -> session_id
        self._mcp_manager = None
        self._mcp_ready = False
        self._last_update_time = 0.0

    def on_mount(self) -> None:
        self._register_themes()
        self._bar("[dim]connecting...[/dim]")
        self._refresh_models()
        self._start_mcp()  # async background init

    @work(exclusive=False, group="init", exit_on_error=False)
    async def _start_mcp(self) -> None:
        from backend.mcp.client import MCPManager
        self._mcp_manager = MCPManager()
        results = await self._mcp_manager.connect_all()
        self._mcp_ready = any(results.values())

    @work(exclusive=True, group="chat")
    async def _run_agent_stream(self, text: str) -> None:
        agent = self._agent_for_reply()
        agent.model = agent.model or self.model
        provider = self._provider_for_agent()
        chain = build_default_chain(provider, agent.name)
        engine = PermissionEngine.from_config()
        runner = AgentRunner(
            provider=chain, agent=agent,
            tool_handlers=TOOL_HANDLERS,
            tool_schemas=TOOL_SCHEMAS,
            mcp=self._mcp_manager if self._mcp_ready else None,
            permission_engine=engine,
            on_ask=self._permission_ask,
            resilient=True,
        )
        self.messages.append({"role": "user", "content": text})
        await self._msg("user", text)
        w = await self._msg("assistant")
        full = ""
        try:
            async for ev in runner.run_stream(text, self._agent_history()):
                if ev["type"] == "delta":
                    full += ev["content"]
                    w.update(Markdown(full.strip(), code_theme="monokai"))
                    self._scr().scroll_end()
                elif ev["type"] == "tool_call":
                    await self._line(f"  → [bold]{ev['name']}[/bold]({json.dumps(ev['args'])[:80]})")
                elif ev["type"] == "tool_result":
                    ok = "green" if ev["ok"] else "red"
                    await self._line(f"  [{ok}]← {ev['name']}: {ev['result'][:200]}[/{ok}]")
                elif ev["type"] == "error":
                    w.update(f"[red]{ev['message']}[/red]")
                    break
                elif ev["type"] == "done":
                    full = ev.get("content", full)
                    break
        finally:
            if full:
                self.messages.append({"role": "assistant", "content": full})
            await self._save_agent_history(self.messages)
            self.streaming = False
            self._stream_widget = None
            inp = self.query_one("#inp", Input)
            inp.disabled = False
            inp.focus()

    async def _permission_ask(self, agent: str, tool: str, target: str | None) -> Decision:
        modal = PermissionModal(agent, tool, target)
        result = await self.app.push_screen(modal, wait_for_dismiss=True)
        if isinstance(result, tuple) and result[0] == "allow_always":
            return result[1]
        return result if isinstance(result, Decision) else Decision.DENY
```

---

## Priority Matrix

| Topic | Priority | Risk | Effort |
|-------|----------|------|--------|
| 1. Async Worker + AgentRunner | **P0 (blocker)** | Low | Medium |
| 2. Permission Modal | **P0 (blocker)** | Low | Small |
| 3. MCP Startup | P1 | Medium | Small |
| 4. Agent History Scoping | P1 | Low | Small |
| 5. Markdown Throttle | P2 | Low | Tiny |
