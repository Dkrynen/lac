# Research Findings — 8 Blockers for Apt v2.2.0

Compiled 2026-07-01. Each section includes root cause, solutions with API signatures, and recommended approach.

---

## 1. MCP Cross-Task Cancel-Scope Error

### Root Cause
AnyIO cancel scopes are **task-local** — `AsyncExitStack.__aenter__()` and `__aexit__()` must happen in the same asyncio task. The MCP SDK's `stdio_client`, `sse_client`, and `streamablehttp_client` all create anyio `TaskGroup`s / `CancelScope`s internally. When `MCPManager.connect()` is called from task A but `close_all()` / `aclose()` from task B (e.g. during TUI shutdown or reconnection), AnyIO raises:

```
RuntimeError: Attempted to exit cancel scope in a different task than it was entered in
```

This is a known MCP SDK issue (#521, #577, #79, #831) with no planned SDK-side fix as of 2026.

### Solutions (verified in production)

**A. Microsoft agent-framework pattern — `_safe_close_exit_stack()` (recommended)**
```python
async def _safe_close_exit_stack(self) -> None:
    try:
        await self._exit_stack.aclose()
    except RuntimeError as e:
        error_msg = str(e).lower()
        # Known anyio cross-task patterns:
        # - "Attempted to exit cancel scope in a different task than it was entered in"
        # - "Attempted to exit a cancel scope that isn't the current task's current cancel scope"
        logger.warning(f"MCP cleanup cross-task boundary: {e}")
        # Allow GC to clean up — the cancel scope is orphaned in the original task
```

Source: `microsoft/agent-framework` PR #3277 (commit `6b5437e`), later superseded by PR #4687.

**B. Lifecycle owner tracking** (Microsoft PR #4687)
```python
async def _ensure_lifecycle_owner(self) -> None:
    async with self._lifecycle_lock:
        if self._lifecycle_owner_task is not None and not self._lifecycle_owner_task.done():
            return
        self._lifecycle_owner_task = asyncio.current_task()
```
Ensures cleanup always runs in the task that created the connection.

**C. Single shared AsyncExitStack** (MCP SDK issue #577)
```python
# One stack for ALL servers — LIFO order guaranteed
async with AsyncExitStack() as stack:
    for name, cfg in servers.items():
        read, write = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read, write))
```
Rigid — all servers must share the same lifetime scope.

**D. asyncio.Event shutdown signal** (Chainlit #2182)
```python
stop_event = asyncio.Event()

# Owning task loop:
await stop_event.wait()
await exit_stack.aclose()  # same task that entered the stack

# External shutdown call:
stop_event.set()
await owning_task
```

### Recommended Fix for MCPManager
Use **pattern A** (`_safe_close_exit_stack`) as the simplest fix. Our `MCPManager` already has a single `_stack` per instance — the issue is only when `connect()` runs in a `@work(thread=True)` or different async context from `close_all()`. Wrap `_stack.aclose()` in a try/except RuntimeError.

---

## 2. Ollama Tool-Calling Reliability

### Root Cause
Small local models (llama3.2:3b, gemma3:12b) have inconsistent template processing for tool calls. The Ollama template system must produce model-specific control tokens (`<tool_call>`, `[TOOL_CALLS]`, etc.), but small models often:

- Emit tool calls as plain JSON text in `message.content` instead of `message.tool_calls` (Ollama #13519, #11608, #10552)
- Return malformed JSON with missing quotes or incorrect structure (Ollama #11185)
- Emit partial tool call tokens as streaming fragments when stream=True (Ollama #11407)

### Verified Behavior
- **phi4:14b**: Most reliable, consistent `tool_calls` field
- **gemma3:12b**: Works with `/api/chat`, less reliable with `/v1/chat/completions` (Ollama #9802)
- **llama3.2:3b**: Consistently puts tool calls in `content` as JSON text — never uses structured `tool_calls`

### Solutions

**A. Defensive tool call parser in Ollama provider** (recommended)
```python
def _extract_tool_calls(self, message: dict) -> list[dict]:
    # 1. Structured field (phi4, reliable models)
    tc = message.get("tool_calls", [])
    if tc:
        return tc
    # 2. Parse content for JSON function calls
    content = message.get("content", "")
    if not content:
        return []
    # Try parsing content as JSON array of tool calls
    try:
        data = json.loads(content)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "name" in data:
            return [{"function": data}]
    except json.JSONDecodeError:
        pass
    # 3. Regex for [TOOL_CALLS][{...}] pattern (mistral-nemo style)
    m = re.search(r'\[TOOL_CALLS\]\s*\[({.*?})\]', content, re.DOTALL)
    if m:
        try:
            return [{"function": json.loads(m.group(1))}]
        except json.JSONDecodeError:
            pass
    # 4. Regex for <tool_call>{...}</tool_call> (hermes2pro style)
    tool_calls = re.findall(r'<tool_call>\s*({.*?})\s*</tool_call>', content, re.DOTALL)
    if tool_calls:
        return [{"function": json.loads(tc)} for tc in tool_calls if tc]
    return []
```

**B. Streaming with tool detection** — When `stream=True`, some models emit `[TOOL_CALLS][` prefix as content chunks. Accumulate content during streaming, then run the parser at `done=True`.

**C. Prompt engineering** — Add system prompt instruction: *"Respond with valid JSON only. If calling a tool, use the JSON format {\"name\": \"tool_name\", \"arguments\": {...}}. Never include explanatory text."*

**D. Format constraint** — Do NOT use `"format": "json"` in the request body when `tools` is specified; they conflict (Ollama #11185 confirmed fix).

### Recommended Implementation
Add `_extract_tool_calls()` to `backend/provider/ollama.py`. In the `chat()` method, after receiving each message chunk or at `done=True`, run the parser. Accumulate `content` during streaming and only parse tool calls at stream end.

---

## 3. Textual 8.2.8 Theme System

### API (confirmed from source)

**Theme creation:**
```python
from textual.theme import Theme

my_theme = Theme(
    name="my-theme",
    primary="#004578",
    secondary="#FFA500",
    accent="#00FF00",
    warning="#FF0000",
    error="#FF0000",
    success="#00FF00",
    surface="#1a1d27",
    background="#0f1117",
    foreground="#e1e4ec",
    dark=True,
    variables={
        "my-custom-var": "#abcdef",
    },
)
```

**Registration:**
```python
class MyApp(App):
    def on_mount(self) -> None:
        self.register_theme(my_theme)
        self.theme = "my-theme"  # sets immediately, re-renders CSS
```

**Runtime switching:**
```python
self.app.theme = "apt-light"  # reactive attribute — CSS vars auto-update
```

**Listen for changes:**
```python
self.app.theme_changed_signal.subscribe(self, handler)
# handler receives Theme object
```

**Built-in themes available:** `textual-dark`, `textual-light`, `nord`, `gruvbox`, `tokyo-night`, `solarized-light`, `atom-one-dark`, `atom-one-light`.

**Available methods:**
- `App.register_theme(theme: Theme)` — registers theme, overwrites if exists
- `App.unregister_theme(theme: Theme)` — removes from available
- `App.get_theme(name: str) -> Theme` — retrieve registered theme
- `App.available_themes -> dict[str, Theme]` — all registered themes
- `App.search_themes()` — opens command palette for theme switching
- `App.get_theme_variable_defaults() -> dict[str, str]` — override for app-specific vars

**CSS variables auto-generated from Theme base colors:**
- `$primary`, `$primary-muted`, `$primary-lighten-1`, `$primary-darken-1`, etc.
- `$secondary`, `$accent`, `$surface`, `$panel`, `$background`, `$foreground`
- `$text-primary`, `$text-secondary`, `$text-muted`, `$text-disabled`
- `$success`, `$warning`, `$error`
- `$border`, `$boost`, `$hover`, `$cursor-highlight`
- Custom vars from `Theme.variables` dict

### How to Wire 3 Apt Themes
Source: `textualize/textual` PR #5087 (themes + command palette improvements), merged in Textual 0.60+.

1. In `AptApp.on_mount()`:
   ```python
   from backend.tui.themes.apt_dark import apt_dark_theme
   from backend.tui.themes.apt_light import apt_light_theme
   from backend.tui.themes.apt_high_contrast import apt_high_contrast_theme

   self.register_theme(apt_dark_theme)
   self.register_theme(apt_light_theme)
   self.register_theme(apt_high_contrast_theme)

   # Set from config
   from backend.config import resolve_config
   cfg = resolve_config()
   self.theme = cfg.theme if cfg.theme in self.available_themes else "apt-dark"
   ```

2. Theme switching: Since `ENABLE_COMMAND_PALETTE = False` (Ctrl+P used for model pick), add a `/theme` slash command or Ctrl+T binding:
   ```python
   @on(Input.Submitted)
   async def _theme_slash(self, name: str) -> None:
       if name in self.available_themes:
           self.theme = name
           self._bar()
   ```

3. CSS must use theme variables (change app.py inline CSS):
   ```css
   Screen { background: $background; color: $foreground; }
   #bar { background: $surface; color: $text-muted; }
   #inp { background: $surface; border: tall $border; color: $foreground; }
   #inp:focus { border: tall $primary; }
   #dialog { background: $surface; border: solid $border; }
   Select { background: $surface; border: tall $border; color: $foreground; }
   Select:focus { border: tall $primary; }
   ```

### Note
The 3 theme files (`apt_dark.py`, `apt_light.py`, `apt_high_contrast.py`) are already created in `backend/tui/themes/` — they just need to be imported and registered in `on_mount`, and the inline CSS in `app.py` needs to switch from hardcoded hex colors to `$variable` references.

---

## 4. Permission Engine Data Model

### Design (synthesized from OpenCode, PraisonAI, AgentScope, yakAgent, LangChain)

**3-way action model:**
| Action | Meaning | Effect |
|--------|---------|--------|
| `allow` | Permit | Execute without prompt |
| `deny` | Reject | Block with PermissionDenied error |
| `ask` | Prompt | Show user Y/N prompt (default) |

**Permission names** (mirror tool types):
`bash`, `read`, `write`, `edit`, `glob`, `grep`, `webfetch`, `websearch`, `task`, `skill`, `todowrite`, `todoread`, `codesearch`, `external_directory`, `doom_loop`

**Rule structure (OpenCode-inspired):**
```python
@dataclass
class PermissionRule:
    permission: str           # Permission name (e.g. "bash", "read")
    pattern: str              # Wildcard pattern ("*", "ls *", "src/**/*.py")
    action: str               # "allow" | "deny" | "ask"
    agent_name: str | None = None  # Per-agent scoping
    priority: int = 0         # Higher = evaluated first
```

**Evaluation strategies (choose one):**
- **OpenCode (last-match-wins)**: `findLast()` — later rules override earlier ones. Natural for user config where explicit overrides come last.
- **LangChain (first-match-wins)**: First matching rule decides. Natural for deny-by-default with explicit allow overrides.
- **AgentScope (DENY-beats-ALLOW at same level, child-beats-parent across levels)**: Hierarchical cascading.

**Recommended: OpenCode last-match-wins** (simplest, most intuitive for users):
```python
def evaluate(self, permission: str, target: str, agent: str | None) -> str:
    """Returns 'allow', 'deny', or 'ask'."""
    matches = [r for r in self.rules if r.matches(permission, target, agent)]
    if not matches:
        return "ask"  # default
    last = matches[-1]  # last-match-wins
    return last.action
```

**Pattern matching:**
- Bash: prefix wildcard (`npm run:*` matches `npm run build`, `npm run test`)
- File paths: glob (`src/**/*.py` matches any .py under src/)
- Other: exact or prefix match

**Doom loop detection:**
```python
class DoomLoopDetector:
    def __init__(self, threshold: int = 3, window_seconds: int = 60):
        self.threshold = threshold
        self.history: list[tuple[str, str, float]] = []  # (tool_name, args_hash, timestamp)

    def record(self, tool_name: str, arguments: dict) -> None:
        self.history.append((tool_name, self._hash(arguments), time.time()))
        self._expire()

    def check(self, tool_name: str, arguments: dict) -> bool:
        """Returns True if this call would be a loop."""
        recent = [h for h in self.history if h[0] == tool_name and h[1] == self._hash(arguments)]
        return len(recent) >= self.threshold
```

**SQLite schema:**
```sql
CREATE TABLE permission_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    permission TEXT NOT NULL,
    pattern TEXT NOT NULL DEFAULT '*',
    action TEXT NOT NULL CHECK(action IN ('allow','deny','ask')),
    agent_name TEXT,
    priority INTEGER DEFAULT 0,
    created_at REAL DEFAULT (julianday('now'))
);

CREATE TABLE permission_approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT NOT NULL,
    approved INTEGER NOT NULL,
    scope TEXT NOT NULL CHECK(scope IN ('once','session','always')),
    agent_name TEXT,
    created_at REAL DEFAULT (julianday('now'))
);
```

**Integration point:** PermissionEngine lives in `backend/permission/engine.py`, consumed by `AgentRunner._execute_tool()` in `backend/agent/runner.py`.

---

## 5. Resilience for Streaming LLM

### 3-Layer Architecture
```
Provider Call -> Retry -> Circuit Breaker -> Fallback Chain -> Actual API
```

### Layer 1: Retry (exponential backoff + jitter)
- Retry only on retryable errors: 429 (rate limit), 500/502/503/504 (server), timeout, connection error
- Do NOT retry on: 400, 401, 403, 422 (client errors — same request will fail again)
- **Streaming limitation**: Can only retry BEFORE first token is emitted. Once `delta.content` starts flowing, abort mid-stream is unsafe (partial state).
- Implementation: `tenacity` or custom wrapper around provider `chat()`

```python
def is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    msg = str(exc)
    if any(code in msg for code in ["429", "500", "502", "503", "504"]):
        return True
    if any(kw in msg.lower() for kw in ["rate_limit", "overloaded", "service_unavailable"]):
        return True
    return False
```

### Layer 2: Circuit Breaker (per-provider)
State machine: `CLOSED -> OPEN (after N failures) -> HALF_OPEN (after timeout) -> CLOSED (on probe success)`

```python
class CircuitBreaker:
    def __init__(self, failure_threshold=5, recovery_timeout_s=60.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout_s = recovery_timeout_s
        self._failures = 0
        self._state = "CLOSED"  # CLOSED | OPEN | HALF_OPEN
        self._last_failure_time = 0.0
        self._lock = asyncio.Lock()

    @property
    async def is_available(self) -> bool:
        async with self._lock:
            if self._state == "OPEN" and (time.time() - self._last_failure_time) > self.recovery_timeout_s:
                self._state = "HALF_OPEN"
            return self._state in ("CLOSED", "HALF_OPEN")

    async def record_success(self):
        async with self._lock:
            self._failures = 0
            self._state = "CLOSED"

    async def record_failure(self):
        async with self._lock:
            self._failures += 1
            self._last_failure_time = time.time()
            if self._failures >= self.failure_threshold:
                self._state = "OPEN"
```

### Layer 3: Fallback Chain (provider failover)
Ordered list: e.g. `[Ollama, OpenAI, Anthropic]`. Try first, fall through on retryable errors.

```python
class FallbackChain:
    def __init__(self, providers: list[tuple[str, LLMProvider]]):
        self.providers = providers  # ordered list of (name, provider)

    async def chat(self, model, messages, **kwargs):
        last_error = None
        for name, provider in self.providers:
            try:
                if not await provider.circuit_breaker.is_available:
                    continue
                return await provider.chat(model, messages, **kwargs)
            except Exception as e:
                if not is_retryable(e):
                    raise  # non-retryable: re-raise immediately
                last_error = e
                await provider.circuit_breaker.record_failure()
                continue
        raise AllProvidersFailedError(last_error)
```

### Integration with AgentRunner
Current `AgentRunner.run_stream()` calls `self.provider.chat()` directly. Wrap with:
```python
# In AgentRunner.run_stream():
for delta in self.resilient_provider.chat(model, messages, stream=True, tools=tools_param):
    ...
```
Where `self.resilient_provider` is a `ResilientProvider` that composes retry + circuit breaker + fallback around the raw `LLMProvider`.

### Streaming-specific concern
For streaming (`stream=True`), the retry layer must detect failure before yielding the first delta. If the initial connect fails (connection refused, timeout), retry normally. If a stream disconnects mid-response:
- Option A: Abort and report error (user must re-ask)
- Option B: Buffer output and attempt reconnect (complex, risk of duplicate tokens)
- **Recommendation**: Option A — log a warning, report error to user, let them re-submit. Mid-stream recovery is fragile and rarely worth the complexity.

---

## 6. Self-Update for Python CLI

### rustup-inspired UX
Three modes: `enable` (default on pip/uv install), `disable`, `check-only` (default for bundled exe)

```toml
# ~/.model-hub/config.json
{
    "update": {
        "auto_update": "check-only"
    }
}
```

### Implementation paths

**Path A: pip/uv install (simplest)**
```python
import subprocess
import json
import urllib.request
from packaging.version import Version

def check_update() -> dict | None:
    """Check PyPI for newer version. Returns update info or None."""
    resp = urllib.request.urlopen("https://pypi.org/pypi/apt/json")
    data = json.loads(resp.read())
    latest = Version(data["info"]["version"])
    current = Version(__version__)
    if latest > current:
        return {"latest": str(latest), "current": str(current), "url": data["info"]["release_url"]}
    return None

def do_update() -> bool:
    """Run uv tool install --upgrade or pip install --upgrade."""
    result = subprocess.run(["uv", "tool", "install", "--upgrade", "apt"], capture_output=True)
    return result.returncode == 0
```

**Path B: PyInstaller/standalone exe (Windows)**
The Windows file-lock problem: can't overwrite a running exe. Solutions:

1. **Launcher + Updater pattern** (most reliable):
   - Small `apt-launcher.exe` (~3MB, Rust or C) that:
     a. Checks for updates
     b. Downloads new `apt.exe` to temp dir
     c. Launches updater script that copies new exe, then starts it
     d. Exits (allowing file lock to release)
   - Reference: `rustup self update` uses `setup.exe --self-replace`

2. **tufup library** (Python-based, works with any bundle):
   ```python
   from tufup.client import Client
   client = Client(metadata_dir, target_dir)
   client.update()  # downloads delta/patch, applies via install script
   ```
   - Uses TUF (The Update Framework) for security
   - Supports delta updates (patch/diff between versions)
   - Windows: spawns new process for install, current process exits

3. **PyUpdater** — archived/maintenance mode as of 2026. Don't use for new projects.

**Path C: Git-based (pyappify pattern)**
- Launcher clones git repo, creates venv, installs deps on first launch
- Updates are `git pull` + `uv sync`
- ~3MB Rust launcher, universal binary

### Recommended for Apt
Check-only by default (non-invasive). Install mode detection:
```python
def _detect_install_method() -> str:
    """Returns 'pip', 'uv', 'pyinstaller', or 'source'."""
    import sys
    if getattr(sys, 'frozen', False):
        return 'pyinstaller'
    if hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
        # Running in a venv managed by uv
        return 'uv'
    return 'pip'
```

For phase 1: implement `apt update` CLI command with `check` and `install` subcommands. Only support pip/uv install (not PyInstaller). Add `apt config set update.auto_update check-only` to control behavior.

---

## 7. Session Export Interop with OpenCode

### OpenCode Session Format (JSON)
Schema from `opencode export <sessionID>` and local storage at `~/.local/share/opencode/storage/`:

```json
{
    "info": {
        "id": "ses_01J...",
        "slug": "abc123",
        "projectID": "pro_01J...",
        "workspaceID": "wrk_01J...",
        "directory": "/path/to/project",
        "parentID": null,
        "title": "New session - 2026-01-19T10:46:33.000Z",
        "version": "1.17.8",
        "summary": {
            "additions": 150,
            "deletions": 30,
            "files": 5,
            "diffs": [{"path": "src/main.py", "patch": "@@ -1,3 +1,5 @@\\n..."}]
        },
        "time": {
            "created": 1737283593,
            "updated": 1737283800
        }
    },
    "messages": [
        {
            "info": {
                "id": "msg_01K...",
                "role": "user",
                "time": {"created": 1737283593},
                "parentID": null
            },
            "parts": [
                {"type": "text", "text": "Hello, can you help me with...", "time": {"start": 1737283593}}
            ]
        },
        {
            "info": {
                "id": "msg_01L...",
                "role": "assistant",
                "time": {"created": 1737283593, "completed": 1737283800},
                "parentID": "msg_01K...",
                "modelID": "claude-sonnet-4-20250514",
                "providerID": "anthropic",
                "agent": "build",
                "cost": 0.0015,
                "tokens": {"total": 450, "input": 300, "output": 150, "reasoning": 0, "cache": {"read": 0, "write": 0}}
            },
            "parts": [
                {"type": "reasoning", "text": "Let me think about this...", "time": {"start": 1737283600, "end": 1737283610}},
                {"type": "text", "text": "Here's the solution...", "time": {"start": 1737283610}},
                {"type": "tool", "tool": "read", "callID": "call_01M...", "state": "completed",
                 "input": {"path": "src/main.py"}, "output": {"text": "def hello():..."}}
            ]
        }
    ]
}
```

### Key Schema Points
- **Part types**: `text`, `reasoning`, `tool`, `file`, `agent`, `subtask`, `compaction`, `step-start`, `step-finish`, `snapshot`, `patch`, `retry`
- **Tool states**: `completed`, `error`, `running`
- **IDs**: ULID-based with prefix (`ses_`, `msg_`, `pro_`, `wrk_`)
- **Summary**: `summary.diffs[*].patch` is a unified diff string (NOT `before/after` objects — those are legacy)
- Encoding: UTF-8 without BOM (UTF-16 BOM will cause import failures in opencode v1.17.8+)

### Apt Export Implementation
Two formats:

**A. Markdown export** (human-readable):
```
# Apt Session Export (2026-07-01)
## Model: llama3.2:3b | Provider: ollama

### User
Hello, can you help me...

### Assistant
Here's the solution...

### Tool Call: read
Arguments: {"path": "src/main.py"}
Result: def hello():...
```

**B. OpenCode JSON export** (interop):
```python
def to_opencode_json(apt_session: dict) -> dict:
    """Convert Apt session format to OpenCode-compatible JSON."""
    return {
        "info": {
            "id": f"ses_{apt_session['id']}",
            "slug": apt_session['id'][:8],
            "projectID": f"pro_{apt_session.get('workspace_id', 'default')}",
            "directory": apt_session.get('workspace', str(Path.cwd())),
            "title": f"Apt session - {apt_session.get('created_at', '')}",
            "version": __version__,
            "time": {
                "created": apt_session.get('created_at', 0),
                "updated": apt_session.get('updated_at', 0),
            }
        },
        "messages": [_convert_msg(m) for m in apt_session.get('messages', [])]
    }
```

CLI integration:
```
apt session export [session_id] [-f markdown|json] [-o output_path]
```

---

## 8. CLI Restructure Patterns

### Current State
`cli.py` at root — single file, flat argparse. All commands in one place.

### Target: Subpackage Pattern
```
backend/
├── cli/
│   ├── __init__.py          # Re-export main CLI entry point
│   ├── main.py              # Parent parser, argument setup, dispatch
│   ├── cmd_list.py          # apt list
│   ├── cmd_pull.py          # apt pull
│   ├── cmd_scan.py          # apt scan
│   ├── cmd_session.py       # apt session export/import/list
│   ├── cmd_update.py        # apt update (self-update)
│   ├── cmd_config.py        # apt config get/set
│   └── cmd_tui.py           # apt tui (launch textual app)
```

### Pattern from uv/pip/poetry

**uv's approach** (Python subpackage + pyproject.toml scripts):
```python
# backend/cli/__init__.py
from .main import cli

# backend/cli/main.py
def cli():
    parser = argparse.ArgumentParser(prog="apt")
    subparsers = parser.add_subparsers(dest="command")
    # Register subcommands from modules
    from .cmd_list import register as register_list
    from .cmd_pull import register as register_pull
    register_list(subparsers)
    register_pull(subparsers)
    args = parser.parse_args()
    if args.command == "list":
        from .cmd_list import run as run_list
        run_list(args)
    elif args.command == "pull":
        ...
```

Each `cmd_*.py` module exports two functions:
```python
# backend/cli/cmd_list.py
def register(subparsers):
    p = subparsers.add_parser("list", help="List installed models")
    p.add_argument("--format", choices=["table", "json"], default="table")

def run(args):
    from backend.cookbook.config import load_config
    ...
```

**pyproject.toml entry point:**
```toml
[project.scripts]
apt = "backend.cli:main"
```

### Migration Steps
1. Create `backend/cli/` directory with `__init__.py`
2. Move argparse setup from `cli.py` to `backend/cli/main.py`
3. Split command handlers into `cmd_*.py` files
4. `backend/cli/__init__.py` re-exports `main` function
5. Keep root `cli.py` as thin wrapper: `from backend.cli import main; main()`

### Migration to Click (optional, recommended for Phase 2)
Click provides nested groups, auto-help, type coercion, and is more maintainable for many subcommands:
```python
import click

@click.group()
@click.version_option(__version__, prog_name="Apt")
def cli():
    pass

@cli.command()
@click.argument("model")
def pull(model):
    """Download a model."""
    ...

@cli.group()
def session():
    """Manage sessions."""
    pass

@session.command()
@click.argument("session_id")
@click.option("-f", "--format", type=click.Choice(["markdown", "json"]), default="markdown")
def export(session_id, format):
    """Export a session."""
    ...
```

### Filesystem Pattern
No monorepo. Flat package structure keeps things simple. Use namespace packages only if publishing sub-packages separately. For a single-installable CLI app, one `backend/` package with `cli/` subpackage is sufficient.
