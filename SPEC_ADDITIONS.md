# Apt v2.2.0 — Enterprise Spec Additions

Deep research findings from OpenCode (181k stars), VS Code extension API,
rustup, and enterprise Python CLI patterns. These are the detailed specs
for what gets built beyond the basic foundation.

---

## 1. Permission System

Based on OpenCode's permission architecture. Controls what each agent can do
at three levels: `allow` (auto-execute), `ask` (prompt user), `deny` (block).

### 1.1 Config Format

In `.apt/apt.jsonc` or `~/.model-hub/config.json`:

```jsonc
{
  "permission": {
    "build": {
      "read": "allow",
      "edit": {
        "src/**": "allow",
        "*.secret": "deny",
        "*": "ask"
      },
      "glob": "allow",
      "grep": "allow",
      "list": "allow",
      "bash": "ask",
      "task": "ask",
      "webfetch": "allow",
      "websearch": "allow",
      "external_directory": "ask",
      "doom_loop": "ask"
    },
    "plan": {
      "*": "ask",               // Default: ask for everything
      "read": "allow",           // Read is always OK
      "glob": "allow",
      "grep": "allow",
      "list": "allow",
      "edit": "deny",            // Plan never edits
      "bash": "deny"             // Plan never runs commands
    },
    "explore": {
      "read": "allow",
      "glob": "allow",
      "grep": "allow",
      "list": "allow",
      "webfetch": "allow",
      "websearch": "allow",
      "edit": "deny",
      "bash": "deny",
      "task": "allow"
    }
  }
}
```

### 1.2 Permission Keys (from OpenCode research)

| Key | Tools it gates |
|-----|---------------|
| `read` | `read` |
| `edit` | `write`, `edit`, `apply_patch`, `multiedit` |
| `glob` | `glob` |
| `grep` | `grep` |
| `list` | `list` |
| `bash` | `bash` (parsed commands like `git status`) |
| `task` | `task` (subagent spawning) |
| `skill` | `skill` (skill loading) |
| `webfetch` | `webfetch` (URL fetching) |
| `websearch` | `websearch` |
| `external_directory` | Any tool touching paths outside CWD |
| `doom_loop` | Same tool call repeated 3× with identical input |
| `todowrite` | `todowrite`, `todoread` |
| `question` | `question` |
| `lsp` | Language server tools |

### 1.3 Rule Evaluation

- **Last-match-wins**: Declare broad rules first, specific overrides after.
- **Wildcard patterns**: `*` matches any sequence, `?` matches single char.
  Paths are normalized before matching.
- **Agent merging**: Agent rules are merged with global config. Agent rules
  take precedence over global rules.

### 1.4 Doom Loop Detection

When the same tool call repeats 3+ times with identical input, the system
triggers a permission `ask` event. The user can then provide corrective
feedback, which is returned to the agent as a `CorrectedError`.

### 1.5 Session Persistence

- `always` decisions (user says "always allow") are written to SQLite,
  bound to `project_id`.
- Decisions in project A do NOT apply to project B.
- Session-level approvals expire when the session ends.

---

## 2. Plugin API Specification

Based on VS Code's extension architecture (activation events, contribution
points, lifecycle) adapted for Python.

### 2.1 Plugin Types

| Type | Purpose | Example |
|------|---------|---------|
| `tool` | Custom agent tools | web_search, db_query |
| `tui` | TUI extensions | custom panels, keybindings |
| `theme` | Textual themes | apt-dark, catppuccin |
| `command` | New CLI subcommands | `apt analytics` |
| `provider` | LLM provider adapters | groq, together |
| `skill` | Reusable agent skills | code_review, debug |

### 2.2 Plugin Lifecycle

Each plugin is a Python package with a standard interface:

```
my_plugin/
├── __init__.py          # Plugin metadata + setup()
├── apt.plugin.json      # Manifest (optional)
└── ...                  # Plugin code
```

**`__init__.py`**:
```python
__plugin_name__ = "my-tool"
__version__ = "1.0.0"
__description__ = "Does something useful"
__plugin_type__ = "tool"  # tool | tui | theme | command | provider | skill

# Optional: trigger conditions (VS Code "activation events")
__activation__ = ["on_command:my_tool", "on_startup"]

tools = [...]   # Tool definitions (for tool plugins)
theme = ...     # Theme object (for theme plugins)


def setup(apt: AptApp) -> None:
    """Called when the plugin is loaded. Receives the Apt app instance.
    Register tools, commands, keybindings, etc. here."""
    apt.register_tool("my_tool", my_tool_fn)
```

### 2.3 Discovery Sources (ordered by priority)

1. **Built-in**: `backend/plugins/builtins/` (shipped with Apt)
2. **User**: `~/.model-hub/plugins/` (user-wide)
3. **Project**: `.apt/plugins/` (project-specific)
4. **Package**: `importlib.metadata.entry_points(group='apt.plugins')`
   (pip-installed packages)

### 2.4 Activation Events (VS Code pattern)

Plugins declare when they should be activated. Lazy loading — the plugin
module is NOT imported until the activation event fires.

```python
__activation__ = [
    "on_startup",                    # After Apt initializes
    "on_command:my_tool",            # When a specific command runs
    "on_language:python",            # When working with a language
    "on_view:chat",                  # When a specific view opens
]
```

Default (empty list = `[]`): activates when setup() is called during
plugin discovery at startup.

### 2.5 Contribution Points

Plugins can contribute:

- **Tools**: `{"name": "my_tool", "description": "...", "input_schema": {...}}`
- **Commands**: `{"id": "my.command", "title": "My Command", "handler": fn}`
- **Keybindings**: `{"keys": "ctrl+shift+m", "command": "my.command"}`
- **Themes**: `Theme(name="my-theme", primary="#...", ...)`
- **Views**: Custom TUI panels
- **Menus**: Items for the command palette or context menu

### 2.6 VS Code-style Extension Manifest

Plugins can optionally include an `apt.plugin.json` for declarative config:

```json
{
  "name": "my-tool",
  "version": "1.0.0",
  "description": "Does something useful",
  "type": "tool",
  "activation": ["on_startup"],
  "contributes": {
    "tools": [{"name": "my_tool", "description": "...", "input_schema": {...}}],
    "commands": [{"id": "my.command", "title": "My Command"}],
    "keybindings": [{"keys": "ctrl+shift+m", "command": "my.command"}]
  }
}
```

---

## 3. Session Export / Import Format

Based on OpenCode's `/export` (Markdown) and `opencode export` (JSON),
plus community tools like ace and SessionSync.

### 3.1 Export Commands

```
apt session export <session_id>              # Default: Markdown
apt session export <session_id> --format md  # Markdown (human-readable)
apt session export <session_id> --format json  # JSON (machine, importable)
apt session export <session_id> --format yaml  # YAML (alt human-readable)
apt session export <session_id> --format html  # HTML (self-contained)
apt session list                              # List all sessions
apt session export --all --out ./exports      # Export all sessions
apt session import ./session.json             # Import a session
```

### 3.2 Markdown Format (default, human-readable)

```markdown
---
session_id: abc123def45678
model: gemma3:12b
created: 2026-07-01T14:30:00Z
duration: 15m
tool: apt
workspace: default
---

# Apt Session: abc123def4

**Model:** gemma3:12b
**Created:** 2026-07-01 14:30
**Duration:** 15 minutes
**Messages:** 8

---

## User
Write a fibonacci function in Python.

## gemma3:12b
Here's a recursive Fibonacci implementation:

\```python
def fib(n):
    return n if n < 2 else fib(n-1) + fib(n-2)
\```

**Note:** This has O(2^n) complexity. For larger values, use an
iterative approach with memoization.

---

## User
Make it iterative.

## gemma3:12b
\```python
def fib(n):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a
\```

Time complexity: O(n), Space complexity: O(1).
```

### 3.3 JSON Format (machine, import-safe)

```json
{
  "format": "apt-session/v1",
  "session": {
    "id": "abc123def45678",
    "model": "gemma3:12b",
    "provider": "ollama",
    "name": "",
    "system_prompt": "You are a helpful coding assistant.",
    "workspace": "default",
    "created_at": 1780309800.0,
    "updated_at": 1780310700.0,
    "messages": [
      {
        "role": "user",
        "content": "Write a fibonacci function in Python.",
        "timestamp": 1780309810.0,
        "tool_calls": null
      },
      {
        "role": "assistant",
        "content": "Here's a recursive...",
        "timestamp": 1780309900.0,
        "finish_reason": "stop",
        "usage": {
          "prompt_tokens": 45,
          "completion_tokens": 120
        }
      }
    ]
  },
  "subagent_sessions": []
}
```

### 3.4 YAML Format

Same structure as JSON but YAML-serialized for human editing.

### 3.5 Output Directory Structure

```
exports/
├── apt-session-abc123def4.md       # Markdown (default)
├── apt-session-abc123def4.json     # JSON (always, for import safety)
└── apt-session-abc123def4.yaml     # YAML (if requested)
```

For `--all` exports, each session gets its own file, organized:

```
exports/
├── YYYY-MM-DD/
│   ├── abc123def4.md
│   └── abc123def4.json
└── ...
```

---

## 4. Error Handling Architecture

Three-layer resilience pattern (retry → circuit breaker → fallback chain),
based on production LLM apps research.

### 4.1 Layer 1: Retry with Exponential Backoff

```python
from backend.resilience import retry

@retry(max_attempts=3, base_delay=1.0, jitter=True, retryable=[408, 429, 500, 502, 503])
def call_ollama(model, messages):
    ...
```

- **Retryable codes**: 408 (timeout), 429 (rate limit), 5xx (server error)
- **Non-retryable**: 400, 401, 403, 404 (client errors — fail fast)
- **Backoff**: `delay = min(base * 2^attempt + jitter, max_delay)`
- **Jitter**: Full jitter (`random.uniform(0, delay)`) to avoid thundering herd
- **Honor Retry-After**: Parse `Retry-After` header when available

### 4.2 Layer 2: Circuit Breaker

```python
from backend.resilience import CircuitBreaker

# One breaker per provider
ollama_breaker = CircuitBreaker("ollama", failure_threshold=5, recovery_timeout=30)
openai_breaker = CircuitBreaker("openai", failure_threshold=5, recovery_timeout=30)
```

State machine: **Closed** (normal) → **Open** (failing, reject fast) →
**Half-Open** (probe) → **Closed** (recovered)

- When 5 consecutive failures occur, breaker opens for 30s
- During open: requests are rejected immediately (no retry wasted)
- After 30s: one probe request allowed through (half-open)
- If probe succeeds: breaker closes
- If probe fails: breaker re-opens for another window
- Separate breaker PER PROVIDER (OpenAI down shouldn't block Ollama)

### 4.3 Layer 3: Provider Fallback Chain

```python
from backend.resilience import FallbackChain

chain = FallbackChain([
    ("ollama", "gemma3:12b", ollama_breaker),
    ("openai", "gpt-4o-mini", openai_breaker),
    ("ollama", "phi4:14b", ollama_breaker),       # Larger local model
])
```

- Try primary first. If it fails (breaker open or error), try fallback.
- Fallback to a different provider entirely (e.g., Ollama → OpenAI)
- Or fallback to a different model on the same provider
- Log the fallback reason so users know why it switched

### 4.4 Provider-Specific Error Handling

| Provider | Errors | Handling |
|----------|--------|----------|
| **Ollama** | Connection refused | Show "Ollama not running. Start it with `ollama serve`" |
| **Ollama** | Model not found | Show "Pull the model first: `apt pull {name}`" |
| **Ollama** | GPU OOM | Detect, suggest smaller model or lower quantization |
| **OpenAI** | 401 | Show "OPENAI_API_KEY not set or invalid" |
| **OpenAI** | 429 | Parse Retry-After, wait, suggest fallback |
| **OpenAI** | 500 | Retry (transient) |
| **Anthropic** | overloaded_error | Circuit breaker trigger |
| **Any** | Timeout (>30s) | Retry with backoff, then fallback |
| **Any** | Rate limited | Parse Retry-After, honor wait |

### 4.5 User-Facing Error Messages

```python
# instead of:
Error: HTTP 500: Internal Server Error

# show:
Ollama is overloaded. Retrying in 5s... (attempt 2/3)
```

If ALL providers fail:
```
All providers are currently unavailable. Your message has been saved.
Try again later, or run `apt provider list` to check provider status.
```

---

## 5. Self-Update System

Based on rustup's auto-update with enable/disable/check-only modes and
install method auto-detection.

### 5.1 Update Commands

```
apt update                  # Check for + apply updates
apt update --check          # Check only, report available version
apt update --no-self-update # Skip apt itself, only update model library
```

### 5.2 Install Method Detection

```python
def detect_install_method():
    if Path(sys.executable).parent / "pip" in PATH:
        return "pip"
    if "brew" in sys.executable or Path(sys.executable).parent.match("*/Cellar/*"):
        return "brew"
    if getattr(sys, "frozen", False):
        return "pyinstaller"  # Standalone EXE
    return "unknown"
```

| Method | Upgrade Command |
|--------|----------------|
| pip | `uv pip install --upgrade apt` or `pip install --upgrade apt` |
| brew | `brew upgrade apt` |
| pyinstaller | Download latest EXE from releases |
| unknown | "Download the latest version from https://apt.ai/download" |

### 5.3 Self-Update Config

```jsonc
{
  "update": {
    "auto_update": "check-only",
    // "enable" | "disable" | "check-only"
    "channel": "stable"
    // "stable" | "beta" | "nightly"
  }
}
```

- **enable**: `apt update` also upgrades Apt itself
- **disable**: Never self-update
- **check-only**: Check and report, don't install (default)

### 5.4 `--no-self-update` Flag

Every command that could trigger an update accepts `--no-self-update`:

```
apt update --no-self-update          # Update models only, not apt
apt pull model --no-self-update
```

### 5.5 Update Flow

1. Check current version (`backend/version.py`)
2. Fetch latest release from GitHub API / PyPI
3. If newer available:
   - Show changelog excerpt
   - Ask: "Update available: v2.3.0. Install now? [Y/n]"
   - If yes: download and apply
   - If check-only: print message and return
4. On update: download binary/package, verify signature, apply,
   restart if needed

---

## 6. Directory Structure Summary

After all phases, the project should look like:

```
model-hub/
├── .apt/                           # Project-level config (auto-created)
│   ├── apt.jsonc                   # Main config
│   ├── agent/
│   ├── command/
│   ├── plugins/
│   ├── skills/
│   ├── themes/
│   └── mcp/
├── backend/
│   ├── __init__.py
│   ├── version.py
│   ├── api.py                      # Flask API
│   ├── cli/                        # CLI commands (split from cli.py)
│   │   ├── __init__.py
│   │   ├── chat.py
│   │   ├── list.py
│   │   ├── pull.py
│   │   ├── session.py
│   │   ├── update.py
│   │   └── ...
│   ├── tui/
│   │   ├── __init__.py
│   │   ├── app.py                  # Main TUI app
│   │   └── themes/
│   │       ├── __init__.py
│   │       ├── apt_dark.py
│   │       ├── apt_light.py
│   │       └── apt_high_contrast.py
│   ├── provider/
│   │   ├── __init__.py
│   │   ├── base.py                 # LLMProvider ABC
│   │   ├── ollama.py
│   │   ├── openai.py
│   │   ├── anthropic.py
│   │   └── registry.py
│   ├── mcp/
│   │   ├── __init__.py
│   │   └── client.py               # MCP client
│   ├── plugin/
│   │   ├── __init__.py
│   │   ├── registry.py             # Plugin discovery + loading
│   │   └── builtins/
│   │       ├── __init__.py
│   │       └── web_search.py
│   ├── schema/
│   │   ├── __init__.py
│   │   └── apt_config.py           # Config loading + validation
│   ├── resilience/
│   │   ├── __init__.py
│   │   ├── retry.py                # Retry with backoff
│   │   ├── circuit_breaker.py      # Circuit breaker
│   │   └── fallback.py             # Provider fallback chain
│   ├── permission/
│   │   ├── __init__.py
│   │   └── engine.py               # Permission evaluation engine
│   └── cookbook/
│       ├── config.py
│       ├── persistence.py
│       ├── hardware.py
│       └── recommend.py
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── script.js
├── cli.py                          # Entry point (thin, delegates to backend/)
├── server.py
├── requirements.txt
├── SPEC_ADDITIONS.md               # This file
└── README.md
```

---

## 7. Implementation Priority (Refined)

### P0 — Must ship
- [ ] Permission system (backend/permission/engine.py + .apt/apt.jsonc schema)
- [ ] Plugin system (already drafted in backend/plugin/registry.py)
- [ ] Provider fallback chain (backend/resilience/)

### P1 — Core experience
- [ ] MCP client (already drafted in backend/mcp/client.py)
- [ ] Session export/import (CLI commands + Markdown/JSON serializers)
- [ ] Self-update system (backend/version.py → GitHub/PyPI check)

### P2 — Enterprise polish
- [ ] Circuit breaker per provider
- [ ] Theme switching in TUI (Ctrl+P command palette)
- [ ] Plugin activation events (lazy loading)
- [ ] VS Code-style extension manifest (apt.plugin.json)

### P3 — Nice to have
- [ ] HTML session export (self-contained page)
- [ ] `--no-self-update` flag on all commands
- [ ] Doom loop detection and correction feedback
- [ ] External directory permission boundary
