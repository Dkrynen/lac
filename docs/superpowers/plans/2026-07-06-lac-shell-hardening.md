# LAC Shell Hardening (S1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn LAC from a headless server that pops a browser tab into a real, single-instance native desktop window that never flashes a console and can never act destructively on a process or file it does not own.

**Architecture:** Two independent workstreams. **Part A** (backend safety) routes every shell-out through one wrapper that hides the Windows console and tracks the PIDs we spawn, then scopes all process-kills to our own processes and confirms filesystem writes stay sandboxed. **Part B** (native window) adds `backend/desktop.py` which boots the existing Flask app on `127.0.0.1:5050` in a daemon thread and opens a pywebview (WebView2) window over it, with a single-instance guard, taskbar identity, a graceful WebView2-absence fallback, and proven PyInstaller bundling.

**Tech Stack:** Python 3.11, Flask (existing `run_server`), pywebview 5.x (EdgeChromium/WebView2 backend), PyInstaller (`build.spec`), Inno Setup (`installer.iss`), React/Vite web UI (existing), pytest.

## Reconciliation note (read first)

An earlier locked-direction design (`docs/superpowers/specs/2026-07-03-apt-v2-overhaul-design.md` §5.3) sketched the desktop shell as `backend/desktop.py` on an **ephemeral** loopback port. **Nothing from it is built** (`backend/desktop.py` absent, pywebview not a dependency). This plan **adopts** the `backend/desktop.py` module name and its non-goals (no tray, no autostart, no multi-window) but **keeps a fixed port 5050 + single-instance guard** (from the approved S1 spec) rather than ephemeral ports — because single-instance is exactly what fixes the "orphan servers stack" bug, and the whole app/CLI already assumes 5050.

## Global Constraints

- **Platform:** Windows-first. The subprocess/safety layer (Part A) is cross-platform, but the native window (Part B) ships Windows-only this slice; every window/mutex/taskbar path must no-op cleanly on non-Windows.
- **Python:** 3.11 (ABI-locked build environment). Run tests with `.venv\Scripts\python.exe -m pytest -q -m "not live"`.
- **`lac-pro` is untouched.** S1 is open-core only. No Pro plumbing, no license logic, no Pro UI (those are S2/S3).
- **No latency work** (S4) and **no GUI activate-that-licenses** (S2) in this slice.
- **Suite discipline:** the full non-live suite must stay green with zero regressions after every task. New tests are added RED-first.
- **Nothing is pushed or published without Duan's explicit go.** Commit locally per task; do not `git push`.
- **Do not bundle the WebView2 runtime installer** — rely on Win11 Evergreen + the B3 fallback.
- **Blast-radius rule (non-negotiable):** LAC may only ever kill a process it spawned or that is verifiably a LAC process; it must never kill a foreign process, even to free its own port. Writes stay under `~/.model-hub`.

---

# PART A — Subprocess & safety layer (backend, fully unit-testable)

Part A and Part B share no code and can be executed in parallel.

### Task A1: Central subprocess wrapper (`proc.py`)

**Files:**
- Create: `backend/cookbook/proc.py`
- Test: `tests/test_proc.py`

**Interfaces:**
- Produces:
  - `run(cmd, **kwargs) -> subprocess.CompletedProcess` — `subprocess.run` with the console hidden on Windows.
  - `popen(cmd, **kwargs) -> subprocess.Popen` — `subprocess.Popen` with the console hidden; records the child PID as ours.
  - `register_spawned(pid: int) -> None` — record a PID we own.
  - `is_ours(pid) -> bool` — True if `pid` is in our spawn registry.
  - `_win_kwargs() -> dict` — internal; the Windows console-hiding kwargs.
  - Module-level set `_spawned_pids: set[int]` (tests clear it between runs).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_proc.py
import os
import subprocess
import pytest
from backend.cookbook import proc


@pytest.fixture(autouse=True)
def _clear_registry():
    proc._spawned_pids.clear()
    yield
    proc._spawned_pids.clear()


def test_win_kwargs_empty_off_windows(monkeypatch):
    monkeypatch.setattr(proc, "_IS_WINDOWS", False)
    assert proc._win_kwargs() == {}


@pytest.mark.skipif(os.name != "nt", reason="Windows console-hiding flags")
def test_win_kwargs_hides_console_on_windows():
    kw = proc._win_kwargs()
    assert kw["creationflags"] & subprocess.CREATE_NO_WINDOW
    assert kw["startupinfo"].wShowWindow == subprocess.SW_HIDE
    assert kw["startupinfo"].dwFlags & subprocess.STARTF_USESHOWWINDOW


def test_popen_records_pid_as_ours(monkeypatch):
    class FakeProc:
        pid = 4242
    monkeypatch.setattr(proc.subprocess, "Popen", lambda *a, **k: FakeProc())
    p = proc.popen(["anything"])
    assert p.pid == 4242
    assert proc.is_ours(4242)
    assert not proc.is_ours(9999)


def test_register_and_is_ours_coerce_int():
    proc.register_spawned("777")
    assert proc.is_ours(777)
    assert proc.is_ours("777")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_proc.py -q`
Expected: FAIL — `ModuleNotFoundError: backend.cookbook.proc`.

- [ ] **Step 3: Write the implementation**

```python
# backend/cookbook/proc.py
"""Central subprocess wrapper.

Two jobs:
1. On Windows, always hide the console window (CREATE_NO_WINDOW + a hidden
   STARTUPINFO). On a windowed PyInstaller exe, a raw subprocess call pops a
   console; routing every shell-out through here kills the terminal-flash bug.
2. Track the PIDs we spawn so kill logic (server.clear_port) can prove a target
   is ours before ever terminating it — LAC must never kill a foreign process.
"""
import os
import subprocess
import threading

_IS_WINDOWS = os.name == "nt"

# PIDs of processes THIS process launched. Consulted by kill logic.
_spawned_pids: set[int] = set()
_lock = threading.Lock()


def _win_kwargs() -> dict:
    if not _IS_WINDOWS:
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": si}


def run(cmd, **kwargs):
    """subprocess.run with the console window always hidden on Windows."""
    merged = {**_win_kwargs(), **kwargs}
    return subprocess.run(cmd, **merged)


def popen(cmd, **kwargs):
    """subprocess.Popen with the console hidden; records the child PID as ours."""
    merged = {**_win_kwargs(), **kwargs}
    p = subprocess.Popen(cmd, **merged)
    register_spawned(p.pid)
    return p


def register_spawned(pid) -> None:
    with _lock:
        _spawned_pids.add(int(pid))


def is_ours(pid) -> bool:
    with _lock:
        return int(pid) in _spawned_pids
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_proc.py -q`
Expected: PASS (the non-Windows-only test is skipped off Windows; on this dev box all run).

- [ ] **Step 5: Commit**

```bash
git add backend/cookbook/proc.py tests/test_proc.py
git commit -m "feat(shell): central subprocess wrapper (no-window + spawn registry)"
```

---

### Task A2: Migrate every shell-out through `proc.run`

**Files:**
- Modify: `backend/cookbook/hardware.py:65` (in `_run_cmd`), `backend/cookbook/hardware.py:291` (the PowerShell call)
- Modify: `backend/api.py:410` (ollama `--version`)
- Modify: `backend/plugin/builtins/tools.py:65` (the `_run_bash` tool)
- Modify: `backend/update.py:124,133` (git pull / pip upgrade)
- Modify: `server.py:56` (`netstat` in `find_port_pids`)
- Test: `tests/test_no_raw_subprocess.py`

**Interfaces:**
- Consumes: `backend.cookbook.proc.run` (A1).
- Produces: nothing new; behavior is unchanged, only the console-hiding is added. The grep-gate test below is the completeness proof.

**Note on behavior:** this is a pure re-route. `except subprocess.TimeoutExpired` / `subprocess.CalledProcessError` clauses stay as-is (they reference the exception classes, not calls) so `import subprocess` remains where those are used. The regex gate only forbids `subprocess.run/Popen/check_output/check_call/call` *calls*.

- [ ] **Step 1: Write the failing grep-gate test**

```python
# tests/test_no_raw_subprocess.py
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CALL = re.compile(r"subprocess\.(run|Popen|check_output|check_call|call)\b")
ALLOWED = {ROOT / "backend" / "cookbook" / "proc.py"}  # the wrapper itself


def _sources():
    files = list((ROOT / "backend").rglob("*.py")) + [ROOT / "server.py"]
    for f in files:
        if "__pycache__" in f.parts or f in ALLOWED:
            continue
        yield f


def test_no_raw_subprocess_calls_outside_proc():
    offenders = []
    for f in _sources():
        text = f.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if CALL.search(line):
                offenders.append(f"{f.relative_to(ROOT)}:{i}")
    assert offenders == [], (
        "raw subprocess calls must route through backend.cookbook.proc: "
        + ", ".join(offenders)
    )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_no_raw_subprocess.py -q`
Expected: FAIL — offenders list includes `hardware.py:65`, `hardware.py:291`, `api.py:410`, `tools.py:65`, `update.py:124`, `update.py:133`, `server.py:56`.

- [ ] **Step 3: Migrate `hardware.py`**

In `backend/cookbook/hardware.py`, add near the top imports: `from backend.cookbook import proc`. Then:

`_run_cmd` (line ~65) — change `r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)` to:

```python
        r = proc.run(cmd, capture_output=True, text=True, timeout=timeout)
```

The PowerShell block (line ~291) — change `r = subprocess.run(` to `r = proc.run(`:

```python
        r = proc.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=15
        )
```

- [ ] **Step 4: Migrate `api.py`**

In `backend/api.py`, add `from backend.cookbook import proc` with the other imports (if not already importable there). Line ~410:

```python
            r = proc.run([path, "--version"], capture_output=True, text=True, timeout=5)
```

- [ ] **Step 5: Migrate `tools.py`**

In `backend/plugin/builtins/tools.py`, add `from backend.cookbook import proc`. In `_run_bash` (line ~65):

```python
        proc_result = proc.run(
            cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=60
        )
        out = (proc_result.stdout or "") + (proc_result.stderr or "")
        return f"[exit {proc_result.returncode}]\n{out.strip()}"
```

(Rename the local `proc` result variable to `proc_result` to avoid shadowing the imported `proc` module.)

- [ ] **Step 6: Migrate `update.py`**

In `backend/update.py`, add `from backend.cookbook import proc`. Line ~124:

```python
            proc.run(["git", "pull"], check=True, capture_output=True, text=True, timeout=60)
```

Line ~133:

```python
            proc_result = proc.run(cmd, check=False, capture_output=True, text=True, timeout=120)
            result["applied"] = proc_result.returncode == 0
            if proc_result.returncode != 0:
                result["error"] = (proc_result.stderr or proc_result.stdout or "")[:300]
```

(Rename the local `proc` variable to `proc_result` here too.)

- [ ] **Step 7: Migrate `server.py`**

In `server.py`, replace the local `import subprocess` in `find_port_pids` with a module-level `from backend.cookbook import proc`, and change line ~56:

```python
        out = proc.run(["netstat", "-ano"], capture_output=True, text=True, timeout=10).stdout
```

Leave `kill_pids` for Task A3 (it is rewritten there, not just re-routed).

- [ ] **Step 8: Run the grep gate + full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_no_raw_subprocess.py -q`
Expected: PASS.
Run: `.venv\Scripts\python.exe -m pytest -q -m "not live"`
Expected: PASS, no regressions.

- [ ] **Step 9: Commit**

```bash
git add backend/cookbook/hardware.py backend/api.py backend/plugin/builtins/tools.py backend/update.py server.py tests/test_no_raw_subprocess.py
git commit -m "refactor(shell): route all shell-outs through proc wrapper (no-window)"
```

---

### Task A3: PID-scoped kill safety in `server.py`

**Files:**
- Modify: `server.py` — `kill_pids`, `clear_port`; add `_process_is_ours`
- Test: `tests/test_kill_safety.py`

**Interfaces:**
- Consumes: `backend.cookbook.proc.is_ours` (A1); `proc.run` (A2).
- Produces: `_process_is_ours(pid: str) -> bool` in `server.py`.
- Behavior contract: `clear_port` only kills PIDs for which `_process_is_ours` is True. A foreign PID holding the port → no kill, return False (honest failure). `kill_pids` filters to our own PIDs before killing.

**Policy:** In the normal desktop path the single-instance guard (B2) means we focus the existing window instead of killing anything. `clear_port`'s kill path now fires only on explicit `--force` / `--kill-port`, and even then only against a process that is in our spawn registry OR whose image name is `lac.exe` (a stale LAC from a prior launch). Anything else is refused.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_kill_safety.py
import server


def test_clear_port_refuses_foreign_process(monkeypatch):
    monkeypatch.setattr(server, "find_port_pids", lambda port: ["9999"])
    monkeypatch.setattr(server, "_process_is_ours", lambda pid: False)
    killed = []
    monkeypatch.setattr(server, "kill_pids", lambda pids: killed.extend(pids) or killed)
    ok = server.clear_port(5050, force=True)
    assert killed == []        # never touched the foreign process
    assert ok is False         # refused, honest failure


def test_clear_port_kills_our_stale_lac(monkeypatch):
    monkeypatch.setattr(server, "find_port_pids", lambda port: ["1234"])
    monkeypatch.setattr(server, "_process_is_ours", lambda pid: True)
    monkeypatch.setattr(server, "kill_pids", lambda pids: pids)
    monkeypatch.setattr(server, "find_port_pids", lambda port: ["1234"])
    # after "kill", pretend the port frees:
    calls = {"n": 0}

    def _pids(port):
        calls["n"] += 1
        return ["1234"] if calls["n"] == 1 else []
    monkeypatch.setattr(server, "find_port_pids", _pids)
    ok = server.clear_port(5050, force=True)
    assert ok is True


def test_kill_pids_filters_to_ours(monkeypatch):
    monkeypatch.setattr(server, "_process_is_ours", lambda pid: pid == "111")
    ran = []
    monkeypatch.setattr(server.proc, "run", lambda *a, **k: ran.append(a) or None)
    monkeypatch.setattr(server.os, "name", "nt")
    killed = server.kill_pids(["111", "222"])
    assert killed == ["111"]   # 222 was foreign → never killed


def test_process_is_ours_true_for_registry(monkeypatch):
    server.proc.register_spawned(555)
    assert server._process_is_ours("555") is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_kill_safety.py -q`
Expected: FAIL — `_process_is_ours` does not exist; `kill_pids` does not filter.

- [ ] **Step 3: Implement the safety helpers**

In `server.py`, ensure `from backend.cookbook import proc` and `import os` are present at module level. Add:

```python
def _process_is_ours(pid: str) -> bool:
    """True only if we can prove this PID belongs to LAC.

    Either we spawned it (in-memory registry) or its image name is our shipped
    exe (a stale LAC from a previous launch). Anything else is treated as a
    foreign process we must never kill.
    """
    try:
        if proc.is_ours(pid):
            return True
    except (ValueError, TypeError):
        pass
    if os.name != "nt":
        return False
    try:
        out = proc.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        ).stdout.lower()
    except Exception:
        return False
    return "lac.exe" in out
```

Rewrite `kill_pids` to filter:

```python
def kill_pids(pids: list[str]) -> list[str]:
    killed = []
    for pid in pids:
        if not _process_is_ours(pid):
            print(f"  ! Refusing to kill PID {pid}: not a LAC process.")
            continue
        try:
            if os.name == "nt":
                proc.run(["taskkill", "/F", "/T", "/PID", pid], capture_output=True, timeout=10)
            else:
                os.kill(int(pid), 9)
            killed.append(pid)
        except Exception:
            pass
    return killed
```

Update `clear_port` so a refusal is an honest failure (it already returns `not find_port_pids(port)` after killing; with foreign PIDs `kill_pids` returns `[]`, the port stays held, and it returns False):

```python
def clear_port(port: int, force: bool) -> bool:
    pids = find_port_pids(port)
    if not pids:
        return True
    ours = [p for p in pids if _process_is_ours(p)]
    foreign = [p for p in pids if p not in ours]
    if foreign:
        print(f"  ! Port {port} is held by another application (PID {', '.join(foreign)}).")
        print(f"  ! LAC will not terminate a process it does not own. Free the port and retry.")
        return False
    print(f"  ! Port {port} is held by a stale LAC process (PID {', '.join(ours)}).")
    if not force:
        print(f"  ! Re-run with --force to reclaim it.")
        return False
    kill_pids(ours)
    time.sleep(0.5)
    return not find_port_pids(port)
```

- [ ] **Step 4: Run the tests + full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_kill_safety.py -q`
Expected: PASS.
Run: `.venv\Scripts\python.exe -m pytest -q -m "not live"`
Expected: PASS, no regressions.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_kill_safety.py
git commit -m "feat(shell): scope process kills to LAC-owned PIDs only (never kill foreign)"
```

---

### Task A4: Filesystem blast-radius audit + guard

**Files:**
- Create: `docs/superpowers/specs/2026-07-06-lac-shell-safety-audit.md` (written deliverable)
- Modify: `backend/cookbook/config.py` (or wherever the `~/.model-hub` root constant lives — confirm during the task) — add a `resolve_under_data_root(path)` guard helper if writes anywhere accept caller-influenced names
- Test: `tests/test_fs_sandbox.py`

**Interfaces:**
- Produces: `resolve_under_data_root(name: str) -> Path` — resolves `name` under `~/.model-hub` and raises `ValueError` if it escapes.

**Scope note:** The import-path traversal is already hardened (audit-fix Task 1 + hf_import path guard). A4 is the *general sweep*: enumerate every write site, confirm each stays under `~/.model-hub`, and add a reusable guard for any that build a path from non-constant input. If the audit finds every write already uses a fixed constant path, the guard is still added and unit-tested for future use, and the audit records that finding.

- [ ] **Step 1: Enumerate write sites**

Run (record results in the audit doc): 
`grep -rniE "open\(.+[\"']w|\.write_text|\.write_bytes|os\.makedirs|\.mkdir\(|shutil\.(copy|move|rmtree)|os\.remove|os\.unlink" backend server.py`
For each hit, note: what it writes, whether the path is a fixed constant under `~/.model-hub` or built from input, and the residual risk.

- [ ] **Step 2: Write the failing guard test**

```python
# tests/test_fs_sandbox.py
import pytest
from pathlib import Path
from backend.cookbook import config


def test_resolve_under_data_root_allows_child():
    p = config.resolve_under_data_root("downloads/history.jsonl")
    assert config.DATA_ROOT in p.resolve().parents or p.resolve() == config.DATA_ROOT


def test_resolve_under_data_root_rejects_escape():
    with pytest.raises(ValueError):
        config.resolve_under_data_root("../../Windows/System32/evil.dll")


def test_resolve_under_data_root_rejects_absolute():
    with pytest.raises(ValueError):
        config.resolve_under_data_root("C:/Windows/evil.dll")
```

(If the data-root constant is named differently than `DATA_ROOT`/`config`, adjust the import to the real location found in Step 1 and keep the same three assertions.)

- [ ] **Step 3: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_fs_sandbox.py -q`
Expected: FAIL — `resolve_under_data_root` not defined.

- [ ] **Step 4: Implement the guard**

Add to the module that owns the `~/.model-hub` root (mirror the resolve-then-`Path.parents`-contain pattern already used by the workspace/import guards):

```python
def resolve_under_data_root(name: str) -> Path:
    """Resolve `name` under the LAC data root, rejecting any path that escapes it."""
    root = DATA_ROOT.resolve()
    candidate = (root / name).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"path escapes the LAC data root: {name!r}")
    return candidate
```

- [ ] **Step 5: Run the tests + full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_fs_sandbox.py -q`
Expected: PASS.
Run: `.venv\Scripts\python.exe -m pytest -q -m "not live"`
Expected: PASS.

- [ ] **Step 6: Write the audit doc**

Fill `docs/superpowers/specs/2026-07-06-lac-shell-safety-audit.md` with a table: `site | file:line | what it does | blast radius | mitigation / residual risk`, covering every shell-out (now via `proc`), every kill (now PID-scoped, A3), and every write (Step 1). State residual/accepted risks explicitly.

- [ ] **Step 7: Commit**

```bash
git add backend/cookbook/config.py tests/test_fs_sandbox.py docs/superpowers/specs/2026-07-06-lac-shell-safety-audit.md
git commit -m "feat(shell): filesystem blast-radius audit + data-root path guard"
```

---

### Task A5: Fix the Ollama guardrail false-negative

**Files:**
- Modify: `installer.iss:60-72` (remove the `CurStepChanged` Ollama registry check)
- Modify: `web/src/components/topbar.tsx` (make the "Ollama offline" indicator link to the download page)
- Test: `tests/test_installer_no_ollama_check.py`

**Interfaces:**
- Consumes: existing `api.ollamaStatus()` (already polled in topbar).
- Produces: nothing programmatic; the installer no longer shows a false "Ollama was not detected" prompt, and the runtime offline indicator becomes an actionable install link.

- [ ] **Step 1: Write the failing guard test**

```python
# tests/test_installer_no_ollama_check.py
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_installer_has_no_ollama_registry_check():
    text = (ROOT / "installer.iss").read_text(encoding="utf-8", errors="ignore")
    assert "Services\\Ollama" not in text
    assert "Ollama was not detected" not in text
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_installer_no_ollama_check.py -q`
Expected: FAIL — both strings are present.

- [ ] **Step 3: Remove the installer check**

In `installer.iss`, delete the `[Code]` `CurStepChanged` block (lines ~56-72: the `var OpenResult`, the `procedure CurStepChanged`, and its `RegKeyExists`/`MsgBox`/`Exec` body). The install no longer probes for Ollama. (If nothing else uses `[Code]`, remove the now-empty `[Code]` section header too.)

- [ ] **Step 4: Make the offline indicator actionable**

In `web/src/components/topbar.tsx`, when `online` is false, render the pill as a link to the download page (keep the exact existing pill styling; only wrap/target it):

```tsx
{online ? (
  <div className={/* existing online pill classes */}>
    <Activity className="h-3.5 w-3.5" />
    <span className={cn("h-1.5 w-1.5 rounded-full", "bg-success")} />
    Ollama online
  </div>
) : (
  <a
    href="https://ollama.com/download"
    target="_blank"
    rel="noreferrer"
    className={/* existing offline pill classes */}
    title="Ollama offline — click to install"
  >
    <Activity className="h-3.5 w-3.5" />
    <span className={cn("h-1.5 w-1.5 rounded-full", "bg-warning")} />
    Ollama offline — install
  </a>
)}
```

(Match the real class names / structure in the file; the change is: offline state becomes an `<a href="https://ollama.com/download">` with an install affordance. Do not alter the online branch's behavior.)

- [ ] **Step 5: Run the guard test + web build**

Run: `.venv\Scripts\python.exe -m pytest tests/test_installer_no_ollama_check.py -q`
Expected: PASS.
Run (in `web/`): `npm run typecheck && npm run build`
Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add installer.iss web/src/components/topbar.tsx tests/test_installer_no_ollama_check.py
git commit -m "fix(shell): drop false-negative installer Ollama check; link offline pill to install"
```

---

# PART B — Native window (packaging + lifecycle)

### Task B1: `backend/desktop.py` — window over daemon-thread Flask + `server.py` routing

**Files:**
- Create: `backend/desktop.py`
- Modify: `server.py` — add `--window` / `--no-window` args; route to the window when frozen or `--window`
- Test: `tests/test_desktop.py`

**Interfaces:**
- Consumes: `backend.api.run_server(host, port)` (existing).
- Produces:
  - `launch_desktop(host: str = "127.0.0.1", port: int = 5050) -> int` — starts Flask (daemon), waits for readiness, opens the window; returns process exit code.
  - `_wait_until_serving(host, port, timeout=20.0) -> bool`
  - `_set_taskbar_identity() -> None`
  - module constants `HOST`, `PORT`, `WINDOW_TITLE = "LAC"`, `APP_USER_MODEL_ID = "Acend.LAC"`
  - `server._should_use_window(args) -> bool`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_desktop.py
import sys
import types
import pytest
from backend import desktop


def test_wait_until_serving_true_when_up(monkeypatch):
    monkeypatch.setattr(desktop, "_serving", lambda h, p: True)
    assert desktop._wait_until_serving("127.0.0.1", 5050, timeout=1.0) is True


def test_wait_until_serving_false_on_timeout(monkeypatch):
    monkeypatch.setattr(desktop, "_serving", lambda h, p: False)
    assert desktop._wait_until_serving("127.0.0.1", 5050, timeout=0.3) is False


def test_launch_desktop_creates_window_when_serving(monkeypatch):
    calls = {}
    fake = types.ModuleType("webview")
    fake.create_window = lambda *a, **k: calls.setdefault("create", (a, k))
    fake.start = lambda *a, **k: calls.setdefault("start", True)
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(desktop, "_set_taskbar_identity", lambda: None)
    monkeypatch.setattr(desktop, "acquire_single_instance", lambda: True)
    monkeypatch.setattr(desktop, "_start_server_thread", lambda h, p: None)
    monkeypatch.setattr(desktop, "_wait_until_serving", lambda h, p, timeout=20.0: True)
    rc = desktop.launch_desktop("127.0.0.1", 5050)
    assert rc == 0
    assert calls["create"][0][0] == "LAC"
    assert calls["create"][0][1] == "http://127.0.0.1:5050"
    assert calls.get("start") is True


def test_launch_desktop_returns_1_when_server_never_starts(monkeypatch):
    monkeypatch.setattr(desktop, "_set_taskbar_identity", lambda: None)
    monkeypatch.setattr(desktop, "acquire_single_instance", lambda: True)
    monkeypatch.setattr(desktop, "_start_server_thread", lambda h, p: None)
    monkeypatch.setattr(desktop, "_wait_until_serving", lambda h, p, timeout=20.0: False)
    monkeypatch.setattr(desktop, "_show_startup_error", lambda *a, **k: None)
    assert desktop.launch_desktop("127.0.0.1", 5050) == 1


def test_should_use_window_defaults_to_frozen(monkeypatch):
    import server
    ns = lambda **k: type("NS", (), {"window": False, "no_window": False, **k})()
    monkeypatch.setattr(server.sys, "frozen", True, raising=False)
    assert server._should_use_window(ns()) is True
    assert server._should_use_window(ns(no_window=True)) is False
    monkeypatch.setattr(server.sys, "frozen", False, raising=False)
    assert server._should_use_window(ns()) is False
    assert server._should_use_window(ns(window=True)) is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_desktop.py -q`
Expected: FAIL — `backend.desktop` missing; `server._should_use_window` missing.

- [ ] **Step 3: Implement `backend/desktop.py`**

```python
# backend/desktop.py
"""Native desktop window for LAC.

Boots the existing Flask app on a fixed loopback port in a daemon thread,
waits until it is serving, then opens a pywebview (WebView2) window over it.
Closing the window exits the process and the daemon server dies with it, so
orphan servers cannot accumulate. Windows-only window; no-ops elsewhere.

Non-goals (locked): no tray, no autostart, no in-window auto-update, no
multi-window.
"""
import sys
import threading
import time
import urllib.request

HOST = "127.0.0.1"
PORT = 5050
WINDOW_TITLE = "LAC"
APP_USER_MODEL_ID = "Acend.LAC"


def _serving(host: str, port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://{host}:{port}/", timeout=1)
        return True
    except Exception:
        return False


def _wait_until_serving(host: str, port: int, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _serving(host, port):
            return True
        time.sleep(0.15)
    return False


def _start_server_thread(host: str, port: int) -> None:
    from backend.api import run_server
    t = threading.Thread(target=lambda: run_server(host=host, port=port), daemon=True)
    t.start()


def _set_taskbar_identity() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def _show_startup_error(host: str, port: int) -> None:
    msg = f"LAC could not start its local server on {host}:{port}."
    print(f"  ! {msg}")
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg, "LAC", 0x10)
        except Exception:
            pass


def launch_desktop(host: str = HOST, port: int = PORT) -> int:
    # Single-instance FIRST: never even start a server if one is running.
    if not acquire_single_instance():
        focus_existing_window()
        return 0

    _set_taskbar_identity()
    _start_server_thread(host, port)

    if not _wait_until_serving(host, port):
        _show_startup_error(host, port)
        return 1

    return _open_window(host, port)


def _open_window(host: str, port: int) -> int:
    # Real implementation of window open + WebView2 fallback lands in Task B3.
    import webview
    webview.create_window(WINDOW_TITLE, f"http://{host}:{port}", min_size=(1024, 700))
    webview.start()
    return 0
```

**Note:** `acquire_single_instance` / `focus_existing_window` are defined in Task B2; `_open_window`'s fallback is hardened in Task B3. For B1's tests they are monkeypatched. Add temporary module-level stubs so B1 imports cleanly if B2 runs after:

```python
def acquire_single_instance() -> bool:  # replaced/expanded in Task B2
    return True


def focus_existing_window() -> None:  # replaced/expanded in Task B2
    return None
```

- [ ] **Step 4: Wire `server.py` routing**

In `server.py`, add the args in `main()`'s parser:

```python
    parser.add_argument("--window", action="store_true", help="Open the native desktop window")
    parser.add_argument("--no-window", action="store_true", help="Force headless server (no window)")
```

Add the helper and the branch (before the `clear_port`/browser path):

```python
def _should_use_window(args) -> bool:
    if getattr(args, "no_window", False):
        return False
    if getattr(args, "window", False):
        return True
    return getattr(sys, "frozen", False)
```

In `main()`, right after `args = parser.parse_args()` and the `--kill-port` handling:

```python
    if _should_use_window(args):
        from backend import desktop
        sys.exit(desktop.launch_desktop(host=args.host, port=args.port))
```

- [ ] **Step 5: Run the tests + full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_desktop.py -q`
Expected: PASS.
Run: `.venv\Scripts\python.exe -m pytest -q -m "not live"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/desktop.py server.py tests/test_desktop.py
git commit -m "feat(shell): native desktop window over daemon-thread Flask + server routing"
```

---

### Task B2: Single-instance guard + focus-existing

**Files:**
- Modify: `backend/desktop.py` — replace the `acquire_single_instance`/`focus_existing_window` stubs
- Test: `tests/test_single_instance.py`

**Interfaces:**
- Produces:
  - `acquire_single_instance(name: str = "LAC_SINGLE_INSTANCE") -> bool` — True if we're the first instance; False if another holds the named mutex. Non-Windows → always True.
  - `focus_existing_window(title: str = WINDOW_TITLE) -> None` — best-effort raise of the running window; never raises.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_single_instance.py
import sys
import types
import pytest
from backend import desktop


def test_acquire_true_off_windows(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    assert desktop.acquire_single_instance() is True


def _fake_ctypes(last_error):
    fake = types.SimpleNamespace()
    k32 = types.SimpleNamespace()
    k32.CreateMutexW = lambda *a: 12345
    k32.GetLastError = lambda: last_error
    fake.windll = types.SimpleNamespace(kernel32=k32)
    fake.wintypes = types.SimpleNamespace(BOOL=lambda v: v)
    return fake


def test_acquire_true_first_instance(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "ctypes", _fake_ctypes(0))
    monkeypatch.setitem(sys.modules, "ctypes.wintypes", _fake_ctypes(0).wintypes)
    assert desktop.acquire_single_instance() is True


def test_acquire_false_when_already_running(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    ERROR_ALREADY_EXISTS = 183
    monkeypatch.setitem(sys.modules, "ctypes", _fake_ctypes(ERROR_ALREADY_EXISTS))
    monkeypatch.setitem(sys.modules, "ctypes.wintypes", _fake_ctypes(ERROR_ALREADY_EXISTS).wintypes)
    assert desktop.acquire_single_instance() is False


def test_focus_existing_never_raises(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    desktop.focus_existing_window()  # no exception off-Windows
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_single_instance.py -q`
Expected: FAIL — the stubs always return True / do nothing, so `test_acquire_false_when_already_running` fails.

- [ ] **Step 3: Implement the guard**

Replace the B1 stubs in `backend/desktop.py`:

```python
_MUTEX_HANDLE = None  # kept alive for the process lifetime


def acquire_single_instance(name: str = "LAC_SINGLE_INSTANCE") -> bool:
    """True if we are the first instance; False if another already holds the mutex."""
    global _MUTEX_HANDLE
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        ERROR_ALREADY_EXISTS = 183
        handle = kernel32.CreateMutexW(None, wintypes.BOOL(True), name)
        if not handle:
            return True  # fail-open: a mutex error must not block launch
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            return False
        _MUTEX_HANDLE = handle
        return True
    except Exception:
        return True  # fail-open


def focus_existing_window(title: str = WINDOW_TITLE) -> None:
    """Best-effort raise of the already-running LAC window. Never raises."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            user32.ShowWindow(hwnd, 9)          # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
    except Exception:
        pass
```

- [ ] **Step 4: Run the tests + full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_single_instance.py tests/test_desktop.py -q`
Expected: PASS.
Run: `.venv\Scripts\python.exe -m pytest -q -m "not live"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/desktop.py tests/test_single_instance.py
git commit -m "feat(shell): single-instance mutex + focus-existing-window (no orphan servers)"
```

---

### Task B3: WebView2-absence graceful fallback

**Files:**
- Modify: `backend/desktop.py` — harden `_open_window` with a browser fallback
- Test: `tests/test_desktop_fallback.py`

**Interfaces:**
- Produces: `_fallback_to_browser(host, port, reason) -> int` (returns 0 — the app remains usable via the browser). `_open_window` now catches a failed WebView2 import/start and delegates to it.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_desktop_fallback.py
import sys
import types
import pytest
from backend import desktop


def test_open_window_falls_back_when_webview_import_fails(monkeypatch):
    # Simulate no webview module available
    monkeypatch.setitem(sys.modules, "webview", None)
    opened = {}
    monkeypatch.setattr(desktop.webbrowser, "open", lambda url: opened.setdefault("url", url))
    rc = desktop._open_window("127.0.0.1", 5050)
    assert rc == 0
    assert opened["url"] == "http://127.0.0.1:5050"


def test_open_window_falls_back_when_start_raises(monkeypatch):
    fake = types.ModuleType("webview")
    fake.create_window = lambda *a, **k: None
    def _boom(*a, **k):
        raise RuntimeError("WebView2 runtime missing")
    fake.start = _boom
    monkeypatch.setitem(sys.modules, "webview", fake)
    opened = {}
    monkeypatch.setattr(desktop.webbrowser, "open", lambda url: opened.setdefault("url", url))
    rc = desktop._open_window("127.0.0.1", 5050)
    assert rc == 0
    assert opened["url"] == "http://127.0.0.1:5050"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_desktop_fallback.py -q`
Expected: FAIL — `_open_window` raises instead of falling back; `webbrowser` not imported in `desktop.py`.

- [ ] **Step 3: Implement the fallback**

At the top of `backend/desktop.py` add `import webbrowser`. Replace `_open_window`:

```python
WEBVIEW2_RUNTIME_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"


def _open_window(host: str, port: int) -> int:
    url = f"http://{host}:{port}"
    try:
        import webview
        if webview is None:
            raise ImportError("webview unavailable")
        webview.create_window(WINDOW_TITLE, url, min_size=(1024, 700))
        webview.start()
        return 0
    except Exception as e:
        return _fallback_to_browser(host, port, str(e))


def _fallback_to_browser(host: str, port: int, reason: str) -> int:
    print(f"  ! Native window unavailable ({reason}).")
    print(f"  ! Opening LAC in your browser instead.")
    print(f"  ! For the desktop app, install the WebView2 runtime: {WEBVIEW2_RUNTIME_URL}")
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                "The desktop window needs the Microsoft WebView2 runtime.\n\n"
                "LAC will open in your browser now. Install WebView2 for the app window:\n"
                + WEBVIEW2_RUNTIME_URL,
                "LAC", 0x40,
            )
        except Exception:
            pass
    webbrowser.open(f"http://{host}:{port}")
    return 0
```

- [ ] **Step 4: Run the tests + full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_desktop_fallback.py -q`
Expected: PASS.
Run: `.venv\Scripts\python.exe -m pytest -q -m "not live"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/desktop.py tests/test_desktop_fallback.py
git commit -m "feat(shell): graceful browser fallback when WebView2 runtime is absent"
```

---

### Task B4: Packaging — bundle pywebview into the exe

**Files:**
- Modify: `build.spec` — `collect_all("webview")` (and pythonnet/`clr` if the real build needs it)
- Modify: `requirements.txt`, `pyproject.toml` — add `pywebview>=5.0`
- Test: `tests/test_build_spec_webview.py`

**Interfaces:**
- Produces: a shipped exe that opens the native window. The unit test is a static guard; the real proof is the manual build in Step 5.

- [ ] **Step 1: Add the dependency**

Add `pywebview>=5.0` to `requirements.txt` and to `pyproject.toml`'s dependencies. Install into the venv:
`.venv\Scripts\python.exe -m pip install "pywebview>=5.0"`

- [ ] **Step 2: Write the failing guard test**

```python
# tests/test_build_spec_webview.py
import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_build_spec_collects_webview():
    text = (ROOT / "build.spec").read_text(encoding="utf-8")
    assert 'collect_all("webview")' in text


def test_pywebview_importable():
    assert importlib.util.find_spec("webview") is not None
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_build_spec_webview.py -q`
Expected: FAIL — `build.spec` has no `collect_all("webview")` yet (Step 1's pip install makes the import test pass).

- [ ] **Step 4: Wire `build.spec`**

Below the `crypto_* = collect_all("cryptography")` line, add:

```python
# pywebview + its WebView2 (EdgeChromium) backend are imported only at runtime
# by backend/desktop.py; PyInstaller's graph from server.py cannot fully
# discover the native loader/assemblies, so collect them explicitly. Same class
# of ship-blocker as the cryptography omission above.
webview_datas, webview_binaries, webview_hidden = collect_all("webview")
```

Merge into `Analysis(...)`:

```python
    binaries=crypto_binaries + webview_binaries,
    datas=datas + crypto_datas + webview_datas,
    hiddenimports=[
        "flask",
        "json", "os", "platform", "subprocess",
        "threading", "time", "webbrowser", "urllib",
        "shutil", "pathlib", "dataclasses", "re", "typing",
        *crypto_hidden,
        *webview_hidden,
    ],
```

- [ ] **Step 5: Prove it on a real build (manual, record in the ledger)**

This is the highest-risk step — the exact native bits (pythonnet/`clr`, `WebView2Loader.dll`, `Microsoft.Web.WebView2.*`) may not all be caught by `collect_all("webview")` alone, exactly as `cryptography` needed its native `_rust` extension. Do:

```bash
cd web && npm run build && cd ..
.venv\Scripts\pyinstaller build.spec
```

Then run `dist\lac.exe` and confirm: the **native window opens** (not a browser tab), and the PyInstaller build log shows **zero missing-import warnings** for `webview`/`clr`. If the window fails to open or DLLs are missing, add the discovered pieces to `build.spec` (e.g. `collect_all("clr")` / a pythonnet hook / an explicit `WebView2Loader.dll` binary) and rebuild until the window opens clean. Record the exact final set + SHA of the exe in `.superpowers/sdd/progress.md`.

- [ ] **Step 6: Run the guard test + full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_build_spec_webview.py -q`
Expected: PASS.
Run: `.venv\Scripts\python.exe -m pytest -q -m "not live"`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add build.spec requirements.txt pyproject.toml tests/test_build_spec_webview.py
git commit -m "build(shell): bundle pywebview/WebView2 into the exe (proven on real build)"
```

---

## Final: manual smoke checklist (record results in `.superpowers/sdd/progress.md`)

A GUI window is not unit-testable; run this on the packaged exe after B4:

1. [ ] App opens as a real native window titled "LAC" (not a browser tab).
2. [ ] **Zero console flashes** — launch, then click through every page (scan, browse, installed, downloads, settings); no console window ever appears.
3. [ ] Second launch focuses the existing window and exits (no second server; verify `netstat -ano | findstr :5050` shows one PID; `tasklist | findstr lac` shows one process).
4. [ ] Taskbar shows the LAC icon + "LAC" label (not "python").
5. [ ] Close the window → `tasklist | findstr lac` shows no lingering process (no orphan server).
6. [ ] Kill-safety: with a foreign process on 5050, `lac.exe --window` refuses to kill it and reports the port is held by another app.
7. [ ] (If a WebView2-absent machine is available) app shows the install-runtime dialog and falls back to the browser.

---

## Self-review (completed by plan author)

**Spec coverage** — every §4 design item maps to a task: A1 wrapper → A1; A2 migrate sites → A2; A3 blast-radius kills → A3; A4 FS sandbox + audit doc → A4; A5 Ollama guardrail → A5; B1 entry/daemon/readiness → B1; B2 single-instance → B2; B3 taskbar identity → B1 (`_set_taskbar_identity`); B4 WebView2 absence → B3; B5 packaging → B4. Verification strategy §5: automated A-tests (A1–A5), manual smoke (final checklist), build-graph test (B4).

**Placeholder scan** — no TBD/TODO; every code step shows real code. The two spots that legitimately defer to the real environment (A4's write-site enumeration; B4 Step 5's exact native-DLL set) are explicitly framed as discovery with a concrete method and a recorded outcome, not vague hand-waving.

**Type consistency** — `launch_desktop`/`_open_window`/`_wait_until_serving`/`acquire_single_instance`/`focus_existing_window`/`_fallback_to_browser`/`_should_use_window`/`_process_is_ours`/`resolve_under_data_root` names are used identically across the tasks that define and consume them. `proc.run`/`proc.popen`/`proc.is_ours`/`proc.register_spawned` are consistent A1→A2→A3→B1.

**Known assumptions to verify during execution** (flagged, not blocking): the `~/.model-hub` root constant's exact module/name (A4 Step 1 confirms it before wiring `resolve_under_data_root`); the precise topbar pill markup (A5 Step 4 matches the real classes); whether pywebview 5.x on this box pulls pythonnet (B4 Step 5 discovers it on the real build).
