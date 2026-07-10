# M2 Plan 2: Ask Bridge + Runner Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make blocking user approval possible over SSE: restructure the web agent stream to a worker-thread + queue pump (all agent modes), add a run registry + answer endpoint, and change the runner's ask contract so allow-once vs always-allow is explicit and doom-loop asks are never persisted.

**Architecture:** The current `generate()` in `agent_chat` drives the async stream with `loop.run_until_complete(...)` on the request thread — while the runner awaits `on_ask`, the sync generator is suspended *inside* `run_until_complete`, not at a `yield`, so a blocking ask can never reach the browser (deadlock by construction). Fix: a worker `threading.Thread` runs the runner loop via `asyncio.run()` and puts events on a `queue.Queue`; the Flask generator pulls with a 15 s timeout, yielding SSE comment heartbeats while paused. `on_ask` blocks the worker on a `threading.Event` that `POST /api/agent/runs/<run_id>/answer` sets. Runner-side, `AskCallback` gains the computed permission key and returns `AskResult(decision, remember)` — the runner persists an always-allow only when `remember=True` and the key is not `doom_loop` (fixes today's silent remember-everything bug).

**Tech Stack:** Python 3.11, stdlib `threading`/`queue`/`asyncio`, Flask, pytest. No new dependencies.

## Global Constraints

- Repo: `C:\Users\User\repos\model-hub`, branch `master`. Plan 1 (staged-change store) is already merged; this plan does NOT touch staging.
- Run tests with the repo venv: `.venv\Scripts\python.exe -m pytest <file> -v`. Full suite green after every task.
- ALL web agent modes (plan/explore, and build in Plan 3) move to the single worker-pump `generate()` — one code path, one persist/finally block, heartbeats for all. Plain "ask" chat mode (`/api/ollama/chat`) is untouched.
- `on_ask` is passed to the runner for all web modes; `permission_engine` is NOT wired here (Plan 3). Without an engine the runner never calls `on_ask`, so plan/explore behavior is unchanged — but the bridge is fully integration-testable dark via a FakeRunner.
- Ask timeout: module constant `ASK_TIMEOUT = 300.0` seconds; heartbeat interval: `HEARTBEAT_INTERVAL = 15.0` seconds (module constants so tests can monkeypatch).
- The answer endpoint accepts ONLY `"allow"` / `"deny"` — never feed `Decision.parse`'s ASK fallback into a run (400 otherwise).
- `threading.Event` (not `asyncio.Event`) for the ask gate — the setter is a different thread; `run_in_executor` keeps the worker loop schedulable.
- Post-disconnect output is deliberately discarded; the session persists up to the disconnect; never `yield` after `GeneratorExit` (guard the final `[DONE]` with a `disconnected` flag).
- Doom-loop asks are never rememberable: the runner never calls `engine.remember` when `key == "doom_loop"`, regardless of the flag.
- model-hub never imports `lac_pro`.

---

### Task 1: `AskResult` contract in the runner (+ migrate TUI and existing test callbacks)

**Files:**
- Modify: `backend/agent/runner.py:14-15` (types), `backend/agent/runner.py:103-113` (ask branch)
- Modify: `backend/tui/app.py:461-468` (`_permission_ask`)
- Modify: `tests/test_permission.py` (migrate every `async def ask(...)` callback — grep `on_ask` and `async def ask` across `tests/`; `cli.py` constructs no AgentRunner, so there is no CLI caller)
- Test: `tests/test_permission.py` (append new tests)

**Interfaces:**
- Produces (frozen for Plans 3-4 and the TUI):

```python
@dataclass
class AskResult:
    decision: Decision
    remember: bool = False

AskCallback = Callable[[str, str, str | None, str], Awaitable["AskResult"]]
# called as: on_ask(agent.name, tool_name, target, key) — key is the computed permission key
```

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_permission.py` (it already imports `asyncio`, `Decision`, `PermissionEngine`, `AlwaysAllowStore`, `parse_rules` at top — reuse them):

```python
def test_ask_remember_false_does_not_persist(mock_provider, isolated_home):
    from backend.agent import AgentRunner, get_agent
    from backend.agent.runner import AskResult
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {"function": {"name": "list_files", "arguments": '{"path":"."}'}}
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True), ChatDelta(content="done", done=True)])
    engine = PermissionEngine(rules=parse_rules({"build": {"list": "ask"}}), project_id="t", store=AlwaysAllowStore())
    seen_keys = []

    async def ask(agent_name, tool, target, key):
        seen_keys.append(key)
        return AskResult(decision=Decision.ALLOW, remember=False)

    runner = AgentRunner(mock_provider, agent, TOOL_HANDLERS, TOOL_SCHEMAS, permission_engine=engine, on_ask=ask, max_iterations=2)
    asyncio.run(runner.run("list"))
    assert seen_keys and seen_keys[0] == "list"
    assert engine.evaluate("build", "list", ".") is Decision.ASK  # NOT persisted


def test_ask_remember_true_persists(mock_provider, isolated_home):
    from backend.agent import AgentRunner, get_agent
    from backend.agent.runner import AskResult
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {"function": {"name": "list_files", "arguments": '{"path":"."}'}}
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True), ChatDelta(content="done", done=True)])
    engine = PermissionEngine(rules=parse_rules({"build": {"list": "ask"}}), project_id="t", store=AlwaysAllowStore())

    async def ask(agent_name, tool, target, key):
        return AskResult(decision=Decision.ALLOW, remember=True)

    runner = AgentRunner(mock_provider, agent, TOOL_HANDLERS, TOOL_SCHEMAS, permission_engine=engine, on_ask=ask, max_iterations=2)
    asyncio.run(runner.run("list"))
    assert engine.evaluate("build", "list", ".") is Decision.ALLOW  # persisted


def test_doom_loop_allow_with_remember_never_persists(mock_provider, isolated_home):
    from backend.agent import AgentRunner, get_agent
    from backend.agent.runner import AskResult
    from backend.plugin.builtins.tools import TOOL_HANDLERS, TOOL_SCHEMAS
    from backend.provider.base import ChatDelta

    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {"function": {"name": "list_files", "arguments": '{"path":"."}'}}
    # mock provider replays the same script every iteration -> identical calls -> doom loop on the 3rd
    mock_provider.set_script([ChatDelta(content="", tool_calls=[call], done=True)])
    store = AlwaysAllowStore()
    engine = PermissionEngine(rules=parse_rules({"build": {"list": "allow"}}), project_id="t", store=store)
    doom_asks = []

    async def ask(agent_name, tool, target, key):
        doom_asks.append(key)
        return AskResult(decision=Decision.ALLOW, remember=True)  # user ticks the box anyway

    runner = AgentRunner(mock_provider, agent, TOOL_HANDLERS, TOOL_SCHEMAS, permission_engine=engine, on_ask=ask, max_iterations=4)
    asyncio.run(runner.run("list"))
    assert "doom_loop" in doom_asks
    assert not store.is_allowed("t", "build", "doom_loop", ".")  # belt and braces: never persisted
    assert engine.evaluate("build", "doom_loop", ".") is Decision.ASK
```

- [ ] **Step 2: Migrate existing callbacks in the same file, then run to verify failure**

In `tests/test_permission.py`, find every existing ask callback (grep `async def ask`) — each currently has the 3-arg signature `(agent, tool, target)` returning a bare `Decision`. Change each to the 4-arg signature returning `AskResult`, e.g. the one at ~line 172:

```python
    from backend.agent.runner import AskResult

    async def ask(agent, tool, target, key):
        return AskResult(decision=Decision.ALLOW)
```

If any existing test asserts the OLD auto-persist behavior (an always-allow row existing after a plain ALLOW), update it to `remember=True` explicitly — the old behavior is the bug this task fixes.

Run: `.venv\Scripts\python.exe -m pytest tests/test_permission.py -v`
Expected: new tests FAIL (`ImportError: cannot import name 'AskResult'`).

- [ ] **Step 3: Implement the runner contract**

In `backend/agent/runner.py`, replace line 15:

```python
AskCallback = Callable[[str, str, str | None], Awaitable["Decision"]]
```

with:

```python
@dataclass
class AskResult:
    decision: "Decision"
    remember: bool = False


AskCallback = Callable[[str, str, str | None, str], Awaitable["AskResult"]]
```

(`dataclass` is already imported at line 5.)

In `_check_permission` (lines 103-114), replace the ask branch:

```python
        if self.on_ask is not None:
            try:
                result = await self.on_ask(self.agent.name, tool_name, target, key)
            except Exception as e:
                return False, f"[permission ask failed: {e}]"
            if result.decision == Decision.ALLOW:
                if result.remember and key != "doom_loop":
                    self.permission_engine.remember(self.agent.name, key, target)
                return True, ""
            if result.decision == Decision.DENY:
                return False, f"[permission denied by user: {tool_name}]"
            return False, f"[permission denied: {tool_name} (ask returned no decision)]"
        return False, f"[permission denied: {tool_name} ({key}) requires approval (no ask handler)]"
```

- [ ] **Step 4: Migrate the TUI caller**

In `backend/tui/app.py`, replace `_permission_ask` (lines 461-468):

```python
    async def _permission_ask(self, agent_name: str, tool_name: str, target: str | None, key: str):
        from backend.agent.runner import AskResult
        from backend.permission import Decision
        from backend.tui.permission_modal import PermissionModal

        modal = PermissionModal(agent_name, tool_name, target)
        result = await self.app.push_screen(modal, wait_for_dismiss=True)
        if isinstance(result, tuple) and result[0] == "allow_always":
            # the modal's always-allow choice maps to an explicit remember
            return AskResult(decision=result[1], remember=(key != "doom_loop"))
        if isinstance(result, Decision):
            return AskResult(decision=result)
        return AskResult(decision=Decision.DENY)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_permission.py tests/test_agent.py -v`
Expected: PASS (fix any other 3-arg callbacks the run surfaces).

- [ ] **Step 6: Run the full suite, then commit**

Run: `.venv\Scripts\python.exe -m pytest -q` — expected green.

```bash
git add backend/agent/runner.py backend/tui/app.py tests/test_permission.py
git commit -m "feat(permission): AskResult contract - explicit allow-once vs always-allow, doom-loop never persisted"
```

---

### Task 2: Widen `is_dangerous` for SSH targets

**Files:**
- Modify: `backend/permission/engine.py:149`
- Test: `tests/test_permission.py` (append)

**Interfaces:**
- Consumes/produces: `is_dangerous(tool: str, target: str | None) -> bool` (signature unchanged; `evaluate()` already downgrades dangerous ALLOW targets to ASK via `engine.py:201-204`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_permission.py`:

```python
def test_is_dangerous_covers_ssh_paths():
    from backend.permission.engine import is_dangerous

    assert is_dangerous("write_file", "C:\\Users\\u\\.ssh\\config")
    assert is_dangerous("write_file", "/home/u/.ssh/authorized_keys")
    assert is_dangerous("write_file", "keys/id_ed25519")
    assert is_dangerous("write_file", "backup/id_ecdsa.pub")
    assert is_dangerous("write_file", "id_rsa")           # pre-existing, still covered
    assert is_dangerous("write_file", "prod/.env")        # pre-existing, still covered
    assert not is_dangerous("write_file", "src/main.py")
    assert not is_dangerous("write_file", "docs/ssh-guide.md")  # 'ssh' substring alone is not a hit
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_permission.py::test_is_dangerous_covers_ssh_paths -v`
Expected: FAIL on `keys/id_ed25519` (only `id_rsa` and the literal `.ssh/authorized_keys` match today).

- [ ] **Step 3: Implement**

In `backend/permission/engine.py:149`, replace the tuple (note `p` is already lowercased with `\\`→`/`; the `.ssh/` segment subsumes the old `authorized_keys` literal):

```python
        return any(s in p for s in (".bashrc", ".zshrc", ".profile", "id_rsa", "id_ed25519", "id_ecdsa", ".ssh/", "credentials", ".env"))
```

- [ ] **Step 4: Run tests, full suite, commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_permission.py -v` then `.venv\Scripts\python.exe -m pytest -q` — expected green.

```bash
git add backend/permission/engine.py tests/test_permission.py
git commit -m "feat(permission): widen is_dangerous to .ssh/ segment + ed25519/ecdsa keys"
```

---

### Task 3: Worker-pump `generate()` — run registry, heartbeats, disconnect semantics

**Files:**
- Modify: `backend/api.py` (top imports; new module-level registry after `PULL_PROGRESS` block ~line 45; rewrite the `generate()` closure inside `agent_chat`, lines 1058-1141; `run_server` line 2635-2640)
- Test: `tests/test_api_agent_chat.py` (modify event-order assertions + append)

**Interfaces:**
- Consumes: `AgentRunner.run_stream` (unchanged), Task 1's `AskCallback` shape (the runner receives `on_ask=None` until Task 4 — this task wires the pump only).
- Produces (Task 4 and Plan 3 rely on these): module constants `ASK_TIMEOUT = 300.0`, `HEARTBEAT_INTERVAL = 15.0`, `_RUN_SENTINEL`; dataclass `_AgentRun(ask_event, queue, session_id, created_at, answer=None, remember=False, pending_ask=None, cancelled=False, thread=None)`; registry `_AGENT_RUNS: dict[str, _AgentRun]` + `_AGENT_RUNS_LOCK`; `_register_agent_run(run_id, run)` (lazy 1 h dead-thread sweep); SSE event `{"type": "run", "run_id": ...}` emitted right after the `session` event.

- [ ] **Step 1: Update the event-order test + add pump tests (failing first)**

In `tests/test_api_agent_chat.py`, `test_agent_chat_streams_and_persists_tool_events` line 72, change:

```python
    assert [e["type"] for e in events] == ["session", "status", "delta", "tool_call", "tool_result", "done"]
```

to:

```python
    assert [e["type"] for e in events] == ["session", "run", "status", "delta", "tool_call", "tool_result", "done"]
```

Append to `tests/test_api_agent_chat.py`:

```python
import time


def _iter_events(resp):
    """Incrementally parse SSE data frames from a streaming test-client response.

    resp.response is Werkzeug's LAZY app iterator; .get_data() would drain the
    whole stream (and hang on a paused run) - never use it in bridge tests.
    """
    for chunk in resp.response:
        text = chunk.decode() if isinstance(chunk, bytes) else chunk
        for line in text.splitlines():
            if line.startswith("data:"):
                data = line.removeprefix("data:").strip()
                if data != "[DONE]":
                    yield json.loads(data)


def test_agent_chat_emits_run_event_and_registers_run(flask_app, isolated_home, monkeypatch, tmp_path):
    import backend.api as api_mod

    class FakeRunner:
        def __init__(self, *args, **kwargs):
            pass

        async def run_stream(self, user_text, history=None):
            yield {"type": "done", "content": "ok", "messages": [], "iterations": 1}

    monkeypatch.setattr(api_mod, "AgentRunner", FakeRunner)
    monkeypatch.setattr(api_mod, "default_provider", lambda: object())
    project = tmp_path / "project"
    project.mkdir()

    resp = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "plan", "model": "mock:1b", "message": "go", "cwd": str(project)},
    )
    events = list(_iter_events(resp))
    run_events = [e for e in events if e["type"] == "run"]
    assert len(run_events) == 1 and run_events[0]["run_id"]
    # run completed -> registry swept clean by the finally block
    assert api_mod._AGENT_RUNS == {}


def test_agent_chat_heartbeats_while_runner_is_slow(flask_app, isolated_home, monkeypatch, tmp_path):
    import asyncio as aio

    import backend.api as api_mod

    monkeypatch.setattr(api_mod, "HEARTBEAT_INTERVAL", 0.05)

    class SlowRunner:
        def __init__(self, *args, **kwargs):
            pass

        async def run_stream(self, user_text, history=None):
            await aio.sleep(0.4)
            yield {"type": "done", "content": "ok", "messages": [], "iterations": 1}

    monkeypatch.setattr(api_mod, "AgentRunner", SlowRunner)
    monkeypatch.setattr(api_mod, "default_provider", lambda: object())
    project = tmp_path / "project"
    project.mkdir()

    resp = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "plan", "model": "mock:1b", "message": "go", "cwd": str(project)},
    )
    raw = b"".join(c if isinstance(c, bytes) else c.encode() for c in resp.response)
    assert b": ping" in raw  # SSE comment heartbeat reached the wire
    assert b'"type": "done"' in raw or b'"type":"done"' in raw


def test_agent_chat_disconnect_cancels_worker(flask_app, isolated_home, monkeypatch, tmp_path):
    import asyncio as aio

    import backend.api as api_mod

    started = {"flag": False}

    class DripRunner:
        def __init__(self, *args, **kwargs):
            pass

        async def run_stream(self, user_text, history=None):
            started["flag"] = True
            for i in range(1000):
                yield {"type": "delta", "content": f"chunk{i}"}
                await aio.sleep(0.01)
            yield {"type": "done", "content": "never", "messages": [], "iterations": 1}

    monkeypatch.setattr(api_mod, "AgentRunner", DripRunner)
    monkeypatch.setattr(api_mod, "default_provider", lambda: object())
    project = tmp_path / "project"
    project.mkdir()

    resp = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "plan", "model": "mock:1b", "message": "go", "cwd": str(project)},
    )
    events = _iter_events(resp)
    for ev in events:
        if ev["type"] == "delta":
            break  # stream is live
    resp.response.close()  # simulate client disconnect -> GeneratorExit in generate()

    assert started["flag"]
    deadline = time.time() + 5
    while time.time() < deadline and api_mod._AGENT_RUNS:
        time.sleep(0.05)
    assert api_mod._AGENT_RUNS == {}  # registry entry dropped by the finally block
```

Run: `.venv\Scripts\python.exe -m pytest tests/test_api_agent_chat.py -v`
Expected: FAIL (no `run` event, no `_AGENT_RUNS`, no `HEARTBEAT_INTERVAL`).

- [ ] **Step 2: Implement the registry + pump**

In `backend/api.py` top imports, add `import queue` and `import uuid` to the stdlib block, and `from dataclasses import dataclass, field` after them.

After the `_pull_progress_snapshot` function (~line 80), add:

```python
ASK_TIMEOUT = 300.0
HEARTBEAT_INTERVAL = 15.0
_RUN_SENTINEL = object()


@dataclass
class _AgentRun:
    """Registry entry bridging a streaming agent run and the answer endpoint."""

    ask_event: threading.Event
    queue: "queue.Queue"
    session_id: str
    created_at: float
    answer: object | None = None       # Decision | None; None at timeout/disconnect => DENY
    remember: bool = False
    pending_ask: dict | None = None
    cancelled: bool = False
    thread: threading.Thread | None = None


_AGENT_RUNS: dict[str, _AgentRun] = {}
_AGENT_RUNS_LOCK = threading.Lock()


def _register_agent_run(run_id: str, run: _AgentRun) -> None:
    now = time.time()
    with _AGENT_RUNS_LOCK:
        stale = [
            rid
            for rid, r in _AGENT_RUNS.items()
            if now - r.created_at > 3600 and (r.thread is None or not r.thread.is_alive())
        ]
        for rid in stale:
            _AGENT_RUNS.pop(rid, None)
        _AGENT_RUNS[run_id] = run
```

- [ ] **Step 3: Rewrite `generate()` in `agent_chat`**

In `agent_chat` (`backend/api.py`), replace everything from `def generate():` (line 1058) through the end of the closure (line 1141) — the runner construction moves OUT of the closure, above it:

```python
    run_id = uuid.uuid4().hex
    run = _AgentRun(
        ask_event=threading.Event(),
        queue=queue.Queue(),
        session_id=session_id,
        created_at=time.time(),
    )
    _register_agent_run(run_id, run)

    runner = AgentRunner(
        provider,
        agent,
        TOOL_HANDLERS,
        TOOL_SCHEMAS,
        ctx={"cwd": str(cwd)},
        max_iterations=max_iterations,
        chat_options=chat_options,
    )

    def pump():
        async def _drive():
            stream = runner.run_stream(message, runner_history)
            try:
                async for ev in stream:
                    if run.cancelled:
                        break
                    run.queue.put(ev)
            finally:
                await stream.aclose()

        try:
            asyncio.run(_drive())
        except Exception as e:
            run.queue.put({"type": "error", "message": str(e)})
        finally:
            run.queue.put(_RUN_SENTINEL)

    worker = threading.Thread(target=pump, daemon=True)
    run.thread = worker

    def generate():
        assistant_content = ""
        saved_done = False
        disconnected = False
        started_at = time.time()
        persisted_messages = [
            {
                **m,
                "timestamp": m.get("timestamp") if isinstance(m.get("timestamp"), (int, float)) else started_at + (i * 0.000001),
            }
            for i, m in enumerate(history)
        ]
        persisted_messages.append({
            "role": "user",
            "content": message,
            "timestamp": started_at + (len(persisted_messages) * 0.000001),
        })

        yield _agent_sse({"type": "session", "session_id": session_id})
        yield _agent_sse({"type": "run", "run_id": run_id})
        yield _agent_sse({"type": "status", "message": f"{agent_name.title()} agent started"})
        worker.start()

        try:
            while True:
                try:
                    ev = run.queue.get(timeout=HEARTBEAT_INTERVAL)
                except queue.Empty:
                    # keeps disconnect detectable while the run is paused on an ask
                    yield ": ping\n\n"
                    continue
                if ev is _RUN_SENTINEL:
                    break

                ev_type = ev.get("type")
                if ev_type == "delta":
                    assistant_content += str(ev.get("content") or "")
                elif ev_type == "done":
                    assistant_content = str(ev.get("content") or assistant_content)
                    if assistant_content:
                        persisted_messages.append({
                            "role": "assistant",
                            "content": assistant_content,
                            "timestamp": started_at + (len(persisted_messages) * 0.000001),
                        })
                    save_session(
                        session_id=session_id,
                        model=model,
                        messages=persisted_messages,
                        name=session_name,
                        workspace=workspace,
                    )
                    saved_done = True
                elif ev_type in _PERSISTED_AGENT_EVENT_TYPES:
                    add_session_event(session_id, str(ev_type), ev)

                yield _agent_sse(ev)
        except GeneratorExit:
            disconnected = True
            raise
        except Exception as e:
            err = {"type": "error", "message": str(e)}
            add_session_event(session_id, "error", err)
            yield _agent_sse(err)
        finally:
            # cancel-by-disconnect AND normal completion both land here
            run.cancelled = True
            run.answer = None  # a pending ask unblocks as DENY
            run.ask_event.set()
            worker.join(timeout=2)
            with _AGENT_RUNS_LOCK:
                _AGENT_RUNS.pop(run_id, None)
            if not saved_done:
                if assistant_content:
                    persisted_messages.append({
                        "role": "assistant",
                        "content": assistant_content,
                        "timestamp": started_at + (len(persisted_messages) * 0.000001),
                    })
                save_session(
                    session_id=session_id,
                    model=model,
                    messages=persisted_messages,
                    name=session_name,
                    workspace=workspace,
                )
            if not disconnected:
                # yielding after GeneratorExit is a RuntimeError - only emit on normal end
                yield "data: [DONE]\n\n"
```

Also in `run_server` (line 2635-2640), make the threading invariant explicit — the answer POST must be servable while an SSE response streams; if this ever becomes a single-threaded server, the ask bridge deadlocks:

```python
def run_server(host="127.0.0.1", port=5050, debug=False):
    print(f"  LAC running at http://{host}:{port}")
    print(f"  Open your browser to that address.\n")
    # Pre-warm the library cache in the background so Browse loads instantly.
    threading.Thread(target=_fetch_library, daemon=True).start()
    # threaded=True is a LOAD-BEARING invariant: /api/agent/runs/<id>/answer must be
    # servable while /api/agent/chat streams (the ask bridge deadlocks otherwise).
    app.run(host=host, port=port, debug=debug, threaded=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_api_agent_chat.py -v`
Expected: all PASS (including the three pre-existing FakeRunner tests — their fakes accept `**kwargs`).

- [ ] **Step 5: Run the full suite, then commit**

Run: `.venv\Scripts\python.exe -m pytest -q` — expected green.

```bash
git add backend/api.py tests/test_api_agent_chat.py
git commit -m "feat(workbench): worker-pump SSE for agent runs - run registry, heartbeats, disconnect cancel"
```

---

### Task 4: The ask bridge — `on_ask`, answer endpoint, persisted approval timeline

**Files:**
- Modify: `backend/api.py` (`_make_web_ask` beside the registry; wire `on_ask` into the `AgentRunner(...)` construction from Task 3; new answer route after `agent_chat`; `_PERSISTED_AGENT_EVENT_TYPES` line ~939)
- Test: `tests/test_api_agent_chat.py` (append)

**Interfaces:**
- Consumes: Task 1's `AskResult`, Task 3's registry/pump/constants.
- Produces:
  - `_make_web_ask(run_id: str, run: _AgentRun) -> AskCallback` — SSE `ask` event, blocking wait, timeout ⇒ DENY + `ask_timeout` event.
  - `POST /api/agent/runs/<run_id>/answer`, body `{"decision": "allow"|"deny", "remember": bool}` → 200 `{"ok": true}`; 400 invalid decision; 404 unknown run; 409 no pending ask. Pushes `{"type": "ask_resolved", run_id, tool, decision, remember}` onto the run queue (that is what makes the approval timeline replayable).
  - SSE/persisted event shapes: `{"type": "ask", run_id, tool, target, key, doom_loop}`, `{"type": "ask_timeout", run_id, tool}`, `{"type": "ask_resolved", run_id, tool, decision, remember}`.
  - `_PERSISTED_AGENT_EVENT_TYPES` gains `"ask"`, `"ask_resolved"`, `"ask_timeout"`, `"staged_change"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api_agent_chat.py`:

```python
def _ask_fake_runner(captured):
    """FakeRunner whose stream asks for bash approval and reacts to the verdict."""
    from backend.agent.runner import AskResult
    from backend.permission import Decision

    class AskingRunner:
        def __init__(self, *args, **kwargs):
            captured["on_ask"] = kwargs.get("on_ask")

        async def run_stream(self, user_text, history=None):
            result = await captured["on_ask"]("build", "run_bash", "pytest -q", "bash")
            captured["ask_result"] = result
            assert isinstance(result, AskResult)
            ok = result.decision == Decision.ALLOW
            yield {"type": "tool_result", "name": "run_bash", "ok": ok, "result": "[exit 0]" if ok else "[denied]"}
            yield {"type": "done", "content": "finished", "messages": [], "iterations": 1}

    return AskingRunner


def test_ask_bridge_allow_flow_and_replay(flask_app, isolated_home, monkeypatch, tmp_path):
    import backend.api as api_mod
    from backend.cookbook import persistence
    from backend.permission import Decision

    captured: dict = {}
    monkeypatch.setattr(api_mod, "AgentRunner", _ask_fake_runner(captured))
    monkeypatch.setattr(api_mod, "default_provider", lambda: object())
    project = tmp_path / "project"
    project.mkdir()

    client = flask_app.test_client()
    resp = client.post(
        "/api/agent/chat",
        json={"agent": "plan", "model": "mock:1b", "message": "run tests", "cwd": str(project)},
    )
    events = _iter_events(resp)
    run_id = session_id = ask = None
    for ev in events:
        if ev["type"] == "session":
            session_id = ev["session_id"]
        if ev["type"] == "run":
            run_id = ev["run_id"]
        if ev["type"] == "ask":
            ask = ev
            break
    assert ask is not None
    assert ask["tool"] == "run_bash"
    assert ask["target"] == "pytest -q"
    assert ask["key"] == "bash"
    assert ask["doom_loop"] is False

    answer = flask_app.test_client().post(
        f"/api/agent/runs/{run_id}/answer", json={"decision": "allow", "remember": True}
    )
    assert answer.status_code == 200

    rest = list(events)
    types = [e["type"] for e in rest]
    assert "ask_resolved" in types
    resolved = [e for e in rest if e["type"] == "ask_resolved"][0]
    assert resolved["decision"] == "allow" and resolved["remember"] is True
    tool_result = [e for e in rest if e["type"] == "tool_result"][0]
    assert tool_result["ok"] is True
    assert captured["ask_result"].decision == Decision.ALLOW
    assert captured["ask_result"].remember is True

    # the approval timeline is persisted for session replay
    stored = [e["type"] for e in persistence.list_session_events(session_id)]
    assert "ask" in stored and "ask_resolved" in stored


def test_ask_bridge_deny_flow(flask_app, isolated_home, monkeypatch, tmp_path):
    import backend.api as api_mod
    from backend.permission import Decision

    captured: dict = {}
    monkeypatch.setattr(api_mod, "AgentRunner", _ask_fake_runner(captured))
    monkeypatch.setattr(api_mod, "default_provider", lambda: object())
    project = tmp_path / "project"
    project.mkdir()

    resp = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "plan", "model": "mock:1b", "message": "run", "cwd": str(project)},
    )
    events = _iter_events(resp)
    run_id = None
    for ev in events:
        if ev["type"] == "run":
            run_id = ev["run_id"]
        if ev["type"] == "ask":
            break
    flask_app.test_client().post(f"/api/agent/runs/{run_id}/answer", json={"decision": "deny"})
    rest = list(events)
    assert captured["ask_result"].decision == Decision.DENY
    tool_result = [e for e in rest if e["type"] == "tool_result"][0]
    assert tool_result["ok"] is False


def test_ask_timeout_denies_and_run_continues(flask_app, isolated_home, monkeypatch, tmp_path):
    import backend.api as api_mod
    from backend.permission import Decision

    monkeypatch.setattr(api_mod, "ASK_TIMEOUT", 0.05)
    captured: dict = {}
    monkeypatch.setattr(api_mod, "AgentRunner", _ask_fake_runner(captured))
    monkeypatch.setattr(api_mod, "default_provider", lambda: object())
    project = tmp_path / "project"
    project.mkdir()

    resp = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "plan", "model": "mock:1b", "message": "run", "cwd": str(project)},
    )
    events = list(_iter_events(resp))  # nobody answers; timeout path drains fully
    types = [e["type"] for e in events]
    assert "ask" in types and "ask_timeout" in types and "done" in types
    assert captured["ask_result"].decision == Decision.DENY


def test_answer_endpoint_validation(flask_app, isolated_home):
    import queue as queue_mod
    import threading
    import time as time_mod

    import backend.api as api_mod

    client = flask_app.test_client()
    assert client.post("/api/agent/runs/nosuch/answer", json={"decision": "allow"}).status_code == 404
    assert client.post("/api/agent/runs/nosuch/answer", json={"decision": "maybe"}).status_code == 400
    assert client.post("/api/agent/runs/nosuch/answer", json={}).status_code == 400

    run = api_mod._AgentRun(
        ask_event=threading.Event(),
        queue=queue_mod.Queue(),
        session_id="s",
        created_at=time_mod.time(),
    )
    with api_mod._AGENT_RUNS_LOCK:
        api_mod._AGENT_RUNS["testrun"] = run
    try:
        # registered run but nothing pending -> 409
        assert client.post("/api/agent/runs/testrun/answer", json={"decision": "allow"}).status_code == 409
    finally:
        with api_mod._AGENT_RUNS_LOCK:
            api_mod._AGENT_RUNS.pop("testrun", None)


def test_disconnect_while_ask_pending_denies(flask_app, isolated_home, monkeypatch, tmp_path):
    import backend.api as api_mod
    from backend.permission import Decision

    captured: dict = {}
    monkeypatch.setattr(api_mod, "AgentRunner", _ask_fake_runner(captured))
    monkeypatch.setattr(api_mod, "default_provider", lambda: object())
    project = tmp_path / "project"
    project.mkdir()

    resp = flask_app.test_client().post(
        "/api/agent/chat",
        json={"agent": "plan", "model": "mock:1b", "message": "run", "cwd": str(project)},
    )
    events = _iter_events(resp)
    for ev in events:
        if ev["type"] == "ask":
            break
    resp.response.close()  # disconnect while the worker is blocked on the ask

    deadline = time.time() + 5
    while time.time() < deadline and api_mod._AGENT_RUNS:
        time.sleep(0.05)
    assert api_mod._AGENT_RUNS == {}
    assert captured["ask_result"].decision == Decision.DENY
```

Run: `.venv\Scripts\python.exe -m pytest tests/test_api_agent_chat.py -v`
Expected: new tests FAIL (`captured["on_ask"]` is None — the route doesn't pass `on_ask`; the answer route 404s).

- [ ] **Step 2: Implement**

In `backend/api.py`:

1. After `_register_agent_run`, add:

```python
def _make_web_ask(run_id: str, run: _AgentRun):
    """Blocking ask bridge: SSE ask event out, threading.Event wait, answer POST in."""

    async def on_ask(agent_name: str, tool_name: str, target, key: str):
        from .agent.runner import AskResult
        from .permission import Decision

        run.answer = None
        run.remember = False
        run.ask_event.clear()
        run.pending_ask = {"tool": tool_name, "target": target, "key": key}
        run.queue.put({
            "type": "ask",
            "run_id": run_id,
            "tool": tool_name,
            "target": target,
            "key": key,
            "doom_loop": key == "doom_loop",
        })
        loop = asyncio.get_running_loop()
        answered = await loop.run_in_executor(None, run.ask_event.wait, ASK_TIMEOUT)
        run.pending_ask = None
        if not answered:
            run.queue.put({"type": "ask_timeout", "run_id": run_id, "tool": tool_name})
            return AskResult(decision=Decision.DENY)
        decision = run.answer if run.answer is not None else Decision.DENY
        return AskResult(decision=decision, remember=run.remember)

    return on_ask
```

2. In the Task 3 `AgentRunner(...)` construction inside `agent_chat`, add the kwarg:

```python
        on_ask=_make_web_ask(run_id, run),
```

(Passed for ALL web modes: without a `permission_engine` the runner never calls it — plan/explore behavior is unchanged; Plan 3 wires the engine for build.)

3. Update line ~939:

```python
_PERSISTED_AGENT_EVENT_TYPES = {
    "tool_calls", "tool_call", "tool_result", "error",
    "ask", "ask_resolved", "ask_timeout", "staged_change",
}
```

4. Add the answer route directly after the `agent_chat` route:

```python
@app.route("/api/agent/runs/<run_id>/answer", methods=["POST"])
def agent_run_answer(run_id):
    from .permission import Decision

    data = request.get_json(silent=True) or {}
    decision_raw = str(data.get("decision") or "").strip().lower()
    if decision_raw not in ("allow", "deny"):
        # never feed Decision.parse's ASK fallback into a run
        return jsonify({"error": "decision must be 'allow' or 'deny'"}), 400
    remember = bool(data.get("remember", False))
    with _AGENT_RUNS_LOCK:
        run = _AGENT_RUNS.get(run_id)
    if run is None:
        return jsonify({"error": "Run not found"}), 404
    pending = run.pending_ask
    if pending is None or run.ask_event.is_set():
        return jsonify({"error": "No pending ask"}), 409
    run.answer = Decision.ALLOW if decision_raw == "allow" else Decision.DENY
    run.remember = remember
    run.queue.put({
        "type": "ask_resolved",
        "run_id": run_id,
        "tool": pending.get("tool"),
        "decision": decision_raw,
        "remember": remember,
    })
    run.ask_event.set()
    return jsonify({"ok": True})
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_api_agent_chat.py -v`
Expected: all PASS.

- [ ] **Step 4: Run the full suite, then commit**

Run: `.venv\Scripts\python.exe -m pytest -q` — expected green.

```bash
git add backend/api.py tests/test_api_agent_chat.py
git commit -m "feat(workbench): blocking ask bridge over SSE - answer endpoint + persisted approval timeline"
```

---

## Self-review checklist (run after all tasks)

- Spec §3.2 coverage: worker pump for ALL web modes ✓ (Task 3), registry + lazy sweep ✓, `run` SSE event ✓, heartbeats ✓, `on_ask` blocking wait + 300 s timeout ⇒ DENY ✓ (Task 4), answer endpoint incl. 400-on-invalid ✓, disconnect ⇒ pending ask DENIED + worker aborts within one event + post-disconnect output discarded ✓, `threaded=True` explicit ✓, no `ask_cancelled` event (reviewed out) ✓.
- Spec §3.3 coverage: `AskResult` contract + key param ✓ (Task 1), remember only when `remember and key != "doom_loop"` ✓, TUI `_permission_ask` migrated ✓, `is_dangerous` widened ✓ (Task 2). `BUILD_WEB_PERMISSIONS` / build agent definition / engine wiring = Plan 3, deliberately absent here.
- Spec §3.4 partial (this plan's slice): `ask`/`ask_resolved`/`ask_timeout`/`staged_change` persisted ✓; replay reconstructs the approval timeline ✓ (allow-flow test asserts stored events).
- Type consistency: `AskResult` defined once in `runner.py`; api/tui/tests import it from there. `_AgentRun.queue` is the one queue used by pump, on_ask, and the answer route.
