# LAC Pro Autopilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Every future subagent dispatch for this plan must be told explicitly: work in the foreground, do NOT spawn agents.** (A delegation-loop bug bit this project earlier — do not repeat it.)

**Goal:** Wire one new optional core hook (`on_model_installed`) so LAC Pro can automatically benchmark, GPU-offload-sweep, and apply the fastest config after every model install — with zero user action — while removing free-tier benchmarking entirely so the free/Pro boundary is real, not just documented.

**Architecture:** Core (`model-hub`) gains a single generic extension point in the existing `lac.plugins` seam — `on_model_installed(model_name: str) -> None` — called from `cli.py::cmd_pull` (synchronously) and `backend/api.py::ollama_pull()` (backgrounded in a thread, mirroring the existing `_refresh_library_background()` pattern, so the HTTP response never blocks). `lac-pro` implements the hook: license-gate via the existing `check()` contract, then reuse `run_sweep()`/`apply_config()` verbatim plus one new dedicated benchmark step that feeds core's `results.jsonl` "measured" tier. The web path additionally gets a status file + a Pro-mounted polling route; the CLI path just prints inline like `lac pro tune` already does. All free/Pro marketing decisions (upsell toast, first-run explainer) live in the frontend via `localStorage` — core and the hook never know Pro's marketing exists.

**Tech Stack:** Python 3.10+ / Flask (core + lac-pro), TypeScript/React + Vite + sonner (web), pytest (both repos share `model-hub`'s `.venv`), argparse (CLI).

## Global Constraints

- New hook signature, exact and unchanging everywhere it appears: `on_model_installed(model_name: str) -> None`.
- Hook is mounted through the existing `lac.plugins` entry-point group (`backend/plugins.py`, `GROUP = "lac.plugins"`) — no new plugin mechanism.
- Every hook call site is wrapped in per-plugin `try/except Exception` isolation matching `backend/plugins.py::discover()`'s existing convention — a raising or absent hook must never break an install that already succeeded.
- The hook's license check uses `lac_pro.license.check()` — **never** `require()` (which calls `sys.exit(3)` and would abort the whole CLI/web process right after a successful install). The hook is a bystander to the install, never a gate on it.
- `lac benchmark` (free CLI) is removed entirely, including its web-UI equivalent (the `/api/benchmark` route + the `BenchmarkDialog` browser dialog) — decision 1 says "**Both** `tune` and `benchmark` become Pro-gated," and both a CLI command and a web button currently let a free user manually benchmark, so both must go. `lac pro benchmark` (Pro-gated, via `require("benchmark")`) replaces the free CLI command for on-demand re-runs.
- The calibration "measured" tier is now populated **exclusively** by LAC Pro's autopilot benchmark step, via the same `backend/cookbook/benchmark.py::build_metrics()`/`log_result()` functions core already uses — same file (`~/.model-hub/benchmarks/results.jsonl`), same format, just a different (Pro-gated) producer. No change to calibration scoring itself.
- Web status file: `~/.model-hub/pro_optimize_status.json`, a JSON object keyed by model name (matches the existing best-effort, no-locking convention already used by `tune.jsonl`/`results.jsonl` appends).
- Web polling route: `GET /api/pro/optimize-status?model=<name>`, registered via the existing `register_api(app)` plugin capability. States, exact literal strings used everywhere: `idle`, `running`, `done`, `failed_silent`, `not_licensed`.
- Free-tier upsell toast and the first-run "here's what Pro just did" explainer toast are **frontend-only**, gated by `localStorage` flags so each fires exactly once ever. Core and the hook implementation never make or know about this decision (spec decision 3: "this decision lives in the frontend, not core or the hook").
- Sweep/benchmark/apply failures degrade silently to `failed_silent` — never block, delay, or show a scary error toast. The model is already installed and usable regardless of what autopilot does afterward.
- No change to the sweep algorithm, scoring, or split-plan logic — `run_sweep()` (`lac_pro/tune.py`) and `apply_config()` (`lac_pro/apply.py`) are reused verbatim, not modified.
- Polar.sh checkout href and `$3/month billed annually` price copy on the landing page must stay byte-identical.
- No new task-queue/job system — a background thread + status file is sufficient for this scope.
- Commits land directly on `master` in both repos (both are already on `master`), one commit per task, never pushed to origin without Duan's separate explicit go-ahead. Stage only the specific files each task touches — never `git add -A` (the model-hub working tree currently has an unrelated stray modification to `backend/cookbook/data/library_cache.json` from a prior test run; do not stage or commit it).

---

## Verified baselines (2026-07-04 — do not trust any other number)

- **model-hub** (`C:\Users\User\repos\model-hub`, branch `master`, HEAD `8e53f39c112c0224e7333c4a39119207210b1835`, 19 commits ahead of origin): `.venv\Scripts\python.exe -m pytest -q` → **210 collected, 205 passed, 5 skipped** ("Ollama not running" — live-Ollama tests, expected in this environment), **0 failed**, exit 0. `cd web && npm run typecheck` → exit 0. `npm run build` → exit 0 (`dist/assets/index-*.js` 386.07 kB, built in 3.65s). No frontend test runner is configured (no vitest/jest, no `*.test.*` files under `web/src`) — frontend verification for this plan is `npm run typecheck && npm run build` passing cleanly, per the environment brief.
- **lac-pro** (`C:\Users\User\repos\lac-pro`, branch `master`, HEAD `fdba3b766c090bd07ecd5092ba92d897001578b2`, working tree clean): `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q` (run from the lac-pro directory) → **47 passed**, 0 failed, exit 0 in 0.51s. `model-hub` is installed editable into that same `.venv` (confirmed: `import backend, cli` resolves to `C:\Users\User\repos\model-hub\...` from any cwd), and `lac-pro` is registered as the real `lac.plugins` entry point `pro = lac_pro.plugin:PLUGIN` in that same venv — so cross-repo imports (`from backend.cookbook.benchmark import ...` inside `lac_pro/*.py`) work with no `sys.path` manipulation needed in new code.

## Flagged gap (read before executing)

The design spec's §5 "concrete changes" list only names `cli.py::cmd_benchmark` for removal. But decision 1 in §2 says **"Both `tune` and `benchmark` become Pro-gated"** and §5 says the measured tier is now fed **"exclusively"** by the Pro autopilot. Investigation found a second, currently-free benchmarking surface the spec doesn't explicitly mention: `backend/api.py`'s `/api/benchmark` SSE route, wired to a `BenchmarkDialog` "Benchmark" button that sits directly next to the calibration badges on `web/src/pages/scan.tsx` (`web/src/lib/api.ts::api.benchmark()`). It calls the exact same `build_metrics`/`log_result` core functions and would let any free user manually populate "measured" calibration data forever, directly falsifying decision 5 and the "exclusively" claim. **Task 3 below removes this surface** as a necessary consequence of decision 1 + decision 5, even though §5's literal list doesn't name it. Flagging this explicitly rather than silently deciding — if Duan disagrees, skip Task 3 and keep the web benchmark button free.

Two further stale-copy items were found (README.md's `## Features` list documents "Benchmark from the browser" and a `lac benchmark` quick-start line; `site/index.html`'s FAQ says "benchmarking" is part of the free core) — folded into Task 9 as part of the copy-review pass, since they're directly falsified by Tasks 2/3's removal and are the same kind of "Duan reviews final wording" decision as the Pro-section bullets.

---

### Task 1: Core — `on_model_installed` hook call sites (CLI sync, web backgrounded)

**Files:**
- Modify: `C:\Users\User\repos\model-hub\cli.py:138-154` (insert new helper after `_download_history`), `cli.py:470-477` (`cmd_pull` success branch)
- Modify: `C:\Users\User\repos\model-hub\backend\api.py:894-909` (insert new helpers after `_discover_plugins_safe`), `backend\api.py:243-277` (`ollama_pull`'s `generate()`)
- Test: `C:\Users\User\repos\model-hub\tests\test_cli_plugins.py`, `C:\Users\User\repos\model-hub\tests\test_api_plugins.py`

**Interfaces:**
- Consumes: `backend.plugins.discover() -> list[LoadedPlugin]` (existing), `LoadedPlugin.obj`/`.ok` (existing).
- Produces: `cli._notify_model_installed(model_name: str) -> None` (synchronous). `backend.api._notify_model_installed(model_name: str) -> None` and `backend.api._notify_model_installed_async(model_name: str) -> None` (spawns a daemon thread). Every later task's `on_model_installed` implementation (Task 4) must be callable as `getattr(plugin.obj, "on_model_installed", None)(model_name)` for these to find it.

- [ ] Step 1: Write the failing tests

In `C:\Users\User\repos\model-hub\tests\test_cli_plugins.py`, append:

```python
def test_notify_model_installed_calls_hook(monkeypatch):
    calls = []
    plug = SimpleNamespace(name="fake", version="1.0", on_model_installed=lambda m: calls.append(m))
    _fake_discover(monkeypatch, [LoadedPlugin("fake", "1.0", plug)])

    import cli
    cli._notify_model_installed("llama3.2:3b")
    assert calls == ["llama3.2:3b"]


def test_notify_model_installed_isolates_raising_hook(monkeypatch, capsys):
    def boom(model_name):
        raise RuntimeError("sweep exploded")

    plug = SimpleNamespace(name="bad", version="0.0", on_model_installed=boom)
    _fake_discover(monkeypatch, [LoadedPlugin("bad", "0.0", plug)])

    import cli
    cli._notify_model_installed("m:1b")  # must not raise
    assert "on_model_installed failed" in capsys.readouterr().err


def test_notify_model_installed_skips_plugin_without_hook(monkeypatch):
    plug = SimpleNamespace(name="fake", version="1.0")  # no on_model_installed attr
    _fake_discover(monkeypatch, [LoadedPlugin("fake", "1.0", plug)])

    import cli
    cli._notify_model_installed("m:1b")  # must not raise


def test_cmd_pull_fires_hook_on_success(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    import cli
    calls = []
    monkeypatch.setattr(cli, "ollama_stream",
                         lambda path, body, timeout=3600: iter([{"status": "success", "total": 0}]))
    monkeypatch.setattr(cli, "_notify_model_installed", lambda m: calls.append(m))

    args = SimpleNamespace(model="m:1b")
    cli.cmd_pull(args)
    assert calls == ["m:1b"]
```

In `C:\Users\User\repos\model-hub\tests\test_api_plugins.py`, append:

```python
def test_notify_model_installed_calls_hook(monkeypatch):
    calls = []
    plug = SimpleNamespace(name="fake", version="1.0", on_model_installed=lambda m: calls.append(m))
    monkeypatch.setattr(plugins_mod, "discover", lambda: [LoadedPlugin("fake", "1.0", plug)])

    from backend.api import _notify_model_installed
    _notify_model_installed("m:1b")
    assert calls == ["m:1b"]


def test_notify_model_installed_isolates_raising_hook(monkeypatch, capsys):
    def boom(model_name):
        raise RuntimeError("boom")

    plug = SimpleNamespace(name="bad", version="0.0", on_model_installed=boom)
    monkeypatch.setattr(plugins_mod, "discover", lambda: [LoadedPlugin("bad", "0.0", plug)])

    from backend.api import _notify_model_installed
    _notify_model_installed("m:1b")  # must not raise
    assert "on_model_installed failed" in capsys.readouterr().out


def test_notify_model_installed_async_runs_in_background_thread(monkeypatch):
    import threading
    calls = []
    done = threading.Event()

    def hook(model_name):
        calls.append(model_name)
        done.set()

    plug = SimpleNamespace(name="fake", version="1.0", on_model_installed=hook)
    monkeypatch.setattr(plugins_mod, "discover", lambda: [LoadedPlugin("fake", "1.0", plug)])

    from backend.api import _notify_model_installed_async
    _notify_model_installed_async("m:1b")
    assert done.wait(timeout=2)
    assert calls == ["m:1b"]


def test_ollama_pull_fires_hook_on_success(monkeypatch, flask_app):
    import json as _json
    import urllib.request
    from backend import api as api_mod

    lines = [
        _json.dumps({"status": "pulling manifest"}).encode(),
        _json.dumps({"status": "success"}).encode(),
    ]
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: iter(lines))

    calls = []
    monkeypatch.setattr(api_mod, "_notify_model_installed_async", lambda m: calls.append(m))

    client = flask_app.test_client()
    r = client.post("/api/ollama/pull", json={"model": "m:1b"})
    assert r.status_code == 200
    _ = r.data  # fully consume the streamed SSE response
    assert calls == ["m:1b"]
```

- [ ] Step 2: Run tests to verify they fail

```
C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q tests/test_cli_plugins.py tests/test_api_plugins.py
```
Expected: FAIL — `AttributeError: module 'cli' has no attribute '_notify_model_installed'` (and the equivalent for `backend.api`).

- [ ] Step 3: Implement the hook call sites

In `C:\Users\User\repos\model-hub\cli.py`, insert this function between `_download_history` and `_update_session` (i.e. right after the existing block ending `    return history` at line 154):

```python
def _notify_model_installed(model_name: str) -> None:
    """Call every plugin's on_model_installed(model_name), isolated per-plugin
    (mirrors the register_cli mounting loop in build_parser()). Runs
    synchronously — a CLI session is already a blocking, watch-it-happen
    context, so a Pro autopilot hook prints its own progress inline."""
    from backend import plugins as _plugins
    try:
        found = _plugins.discover()
    except Exception as e:  # noqa: BLE001 — discovery failure must not kill the CLI
        eprint(f"[plugins] discovery failed: {e}")
        return
    for p in found:
        hook = getattr(p.obj, "on_model_installed", None)
        if not p.ok or hook is None:
            continue
        try:
            hook(model_name)
        except Exception as e:  # noqa: BLE001
            eprint(f"[plugin:{p.name}] on_model_installed failed: {e}")
```

Then in `cmd_pull`, change:

```python
        if chunk.get("status") == "success":
            success = True
            size_gb = 0
            if chunk.get("total"):
                size_gb = round(chunk["total"] / (1024**3), 2)
            print(f"\n\n{C['green']}✓ {model} installed successfully!{C['reset']}")
            _log_download(model, "completed", size_gb)

    if not success:
```

to:

```python
        if chunk.get("status") == "success":
            success = True
            size_gb = 0
            if chunk.get("total"):
                size_gb = round(chunk["total"] / (1024**3), 2)
            print(f"\n\n{C['green']}✓ {model} installed successfully!{C['reset']}")
            _log_download(model, "completed", size_gb)
            _notify_model_installed(model)

    if not success:
```

In `C:\Users\User\repos\model-hub\backend\api.py`, insert these two functions right after `_discover_plugins_safe()`'s body (between it and the `@app.route("/api/plugins")` decorator):

```python
    except Exception as e:  # noqa: BLE001 — discovery failure must not kill the API
        print(f"[plugins] discovery failed: {e}")
        return []


def _notify_model_installed(model_name: str) -> None:
    """Call every plugin's on_model_installed(model_name), isolated per-plugin
    (mirrors _mount_plugins()'s isolation). A missing hook, a plugin that
    isn't installed, or a raising hook must never affect the install that
    already succeeded."""
    for p in _discover_plugins_safe():
        hook = getattr(p.obj, "on_model_installed", None)
        if not p.ok or hook is None:
            continue
        try:
            hook(model_name)
        except Exception as e:  # noqa: BLE001
            print(f"[plugin:{p.name}] on_model_installed failed: {e}")


def _notify_model_installed_async(model_name: str) -> None:
    """Fire _notify_model_installed in a background thread so a slow plugin
    hook (e.g. LAC Pro's benchmark+sweep+apply autopilot) never delays the
    pull's HTTP response. Mirrors _refresh_library_background()'s pattern."""
    threading.Thread(target=_notify_model_installed, args=(model_name,), daemon=True).start()


@app.route("/api/plugins")
```

Then in `ollama_pull()`, change:

```python
        try:
            resp = urllib.request.urlopen(req, timeout=3600)
            for line in resp:
                decoded = line.decode().strip()
                if decoded:
                    yield f"data: {decoded}\n\n"
        except urllib.error.HTTPError as e:
```

to:

```python
        try:
            resp = urllib.request.urlopen(req, timeout=3600)
            for line in resp:
                decoded = line.decode().strip()
                if decoded:
                    yield f"data: {decoded}\n\n"
                    try:
                        chunk = json.loads(decoded)
                    except json.JSONDecodeError:
                        chunk = {}
                    if chunk.get("status") == "success":
                        _notify_model_installed_async(model_name)
        except urllib.error.HTTPError as e:
```

- [ ] Step 4: Run tests to verify they pass

```
C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q tests/test_cli_plugins.py tests/test_api_plugins.py
```
Expected: PASS, all tests including the 4 new ones (7 + 3 pre-existing in test_cli_plugins.py = 8 passed; 3 + 4 new in test_api_plugins.py = 7 passed — exact counts don't matter, 0 failed does).

- [ ] Step 5: Commit

```bash
cd "C:\Users\User\repos\model-hub"
git add cli.py backend/api.py tests/test_cli_plugins.py tests/test_api_plugins.py
git commit -m "feat(core): add on_model_installed plugin hook call sites (cli.py sync, api.py backgrounded)"
```

---

### Task 2: Core — remove `lac benchmark` from the free CLI

**Files:**
- Modify: `C:\Users\User\repos\model-hub\cli.py:17` (docstring), `cli.py:23,27` (unused imports), `cli.py:841-954` (`cmd_benchmark`), `cli.py:1199-1209` (subparser), `cli.py:1291` (commands dict)
- Test: `C:\Users\User\repos\model-hub\tests\test_benchmark.py:107-121` (remove), same file (add replacement)

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing new — `backend/cookbook/benchmark.py`'s `build_metrics`/`log_result`/`history` stay in core untouched (Task 4 and Task 5 reuse them from `lac-pro`).

- [ ] Step 1: Write the failing test

In `C:\Users\User\repos\model-hub\tests\test_benchmark.py`, replace the existing `test_cli_help_includes_benchmark` (lines 107-121):

```python
def test_cli_help_includes_benchmark():
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "-m", "cli", "benchmark", "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert r.returncode == 0
    assert "benchmark" in r.stdout
    assert "--prompt PROMPT" in r.stdout
    assert "--num-predict" in r.stdout
    assert "--temperature" in r.stdout
    assert "--list" in r.stdout
    assert "--export FILE" in r.stdout
```

with:

```python
def test_benchmark_subcommand_removed_from_free_cli():
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "-m", "cli", "benchmark", "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert r.returncode != 0
    assert "invalid choice" in r.stderr.lower()
```

- [ ] Step 2: Run test to verify it fails

```
C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q tests/test_benchmark.py::test_benchmark_subcommand_removed_from_free_cli
```
Expected: FAIL — `assert 0 != 0` (the `benchmark` subcommand still exists and exits 0 today).

- [ ] Step 3: Remove the free CLI benchmark command

In `C:\Users\User\repos\model-hub\cli.py`:

1. Remove line 17 from the module docstring: `  benchmark [model]     Benchmark a model's tok/s via Ollama`
2. Remove the now-unused imports (only used inside `cmd_benchmark`, confirmed via grep — no other call sites in the file): line 23 `import csv` and line 27 `import statistics`.
3. Delete the entire `cmd_benchmark` function (lines 841-954, from `def cmd_benchmark(args):` through the line `print(f"\n{C['dim']}Logged {logged} of {len(entries)} result(s) to ~/.model-hub/benchmarks/results.jsonl{C['reset']}")`), keeping the two blank lines that already separate it from `def cmd_browse(args):`.
4. Remove the subparser block (lines 1199-1209):

```python
    p_bench = sub.add_parser("benchmark", help="Benchmark model tok/s via Ollama")
    p_bench.add_argument("model", nargs="?", help="Model name to benchmark")
    p_bench.add_argument("--prompt", default=None, help="Prompt text (default: fibonacci function)")
    p_bench.add_argument("--num-predict", type=int, default=128, help="Tokens to generate (default: 128)")
    p_bench.add_argument("--temperature", type=float, default=0.0, help="Sampling temperature (default: 0)")
    p_bench.add_argument("--timeout", type=int, default=300, help="Request timeout in seconds (default: 300)")
    p_bench.add_argument("--no-cache", action="store_true", help="Disable prompt cache for fresh eval")
    p_bench.add_argument("--repeat", type=int, default=1, help="Run N times and report median tok/s (default: 1)")
    p_bench.add_argument("--list", action="store_true", help="Show benchmark history")
    p_bench.add_argument("--export", metavar="FILE", help="Export results to CSV/JSON/JSONL")

```
(delete the whole block, leaving exactly one blank line between the `p_rec.add_argument("--no-calibration", ...)` line and `p_browse = sub.add_parser("browse", ...)`)

5. Remove `"benchmark": cmd_benchmark,` from the `commands` dict (currently line 1291).

- [ ] Step 4: Run tests to verify they pass

```
C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q tests/test_benchmark.py
```
Expected: PASS (6 tests: the 5 pre-existing `build_metrics`/`log_result`/`history` tests, unaffected, plus the new `test_benchmark_subcommand_removed_from_free_cli`).

Then run the full suite to confirm nothing else broke:

```
C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q
```
Expected: PASS, 0 failed (test count drops by 0 net — one test replaced in place, not removed).

- [ ] Step 5: Commit

```bash
cd "C:\Users\User\repos\model-hub"
git add cli.py tests/test_benchmark.py
git commit -m "feat(cli): remove lac benchmark from the free CLI (moves to lac pro benchmark)"
```

---

### Task 3: Core — remove the free-tier web benchmark surface (gap-closing, flagged above)

**Files:**
- Modify: `C:\Users\User\repos\model-hub\backend\api.py:326-410` (remove `api_benchmark` route + `_sse`/`_sse_done` helpers)
- Modify: `C:\Users\User\repos\model-hub\tests\test_api.py:98-149` (remove the two `/api/benchmark` tests, add one regression test)
- Modify: `C:\Users\User\repos\model-hub\web\src\lib\api.ts:120-123` (remove `benchmark()` method)
- Modify: `C:\Users\User\repos\model-hub\web\src\pages\scan.tsx:11,173` (remove `BenchmarkDialog` import + usage)
- Delete: `C:\Users\User\repos\model-hub\web\src\components\benchmark-dialog.tsx`

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing new — this is pure removal. `backend/cookbook/benchmark.py` (the underlying module) is untouched; only the free-tier HTTP surface and its one caller go away.

- [ ] Step 1: Write the failing test

In `C:\Users\User\repos\model-hub\tests\test_api.py`, delete `test_benchmark_requires_model` and `test_benchmark_streams_runs_and_median` (lines 98-149) and replace with:

```python
def test_api_benchmark_route_removed(flask_app):
    """The free-tier web benchmark surface is gone entirely — benchmarking
    only happens through LAC Pro's autopilot from now on (spec decision 1)."""
    r = flask_app.test_client().post("/api/benchmark", json={"model": "m:1b"})
    assert r.status_code == 404
```

- [ ] Step 2: Run test to verify it fails

```
C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q tests/test_api.py::test_api_benchmark_route_removed
```
Expected: FAIL — `assert 200 == 404` (the route still exists and responds today).

- [ ] Step 3: Remove the route and its frontend caller

In `C:\Users\User\repos\model-hub\backend\api.py`, delete the entire block from `@app.route("/api/benchmark", methods=["POST"])` through `def _sse_done() -> str:\n    return "data: [DONE]\n\n"` (lines 326-410 — the whole `api_benchmark()` function plus its two only-used-there `_sse`/`_sse_done` helpers), keeping the two blank lines that already separate it from `@app.route("/api/ollama/check-install")`.

In `C:\Users\User\repos\model-hub\web\src\lib\api.ts`, remove:

```typescript
  /** Stream a benchmark run. Yields {run,tokens_per_second,...} frames then {done:true,median_tps,runs}. */
  benchmark(model: string, opts: { repeat?: number } = {}, signal?: AbortSignal) {
    return sse("/api/benchmark", { model, repeat: opts.repeat ?? 2 }, signal);
  },
```

In `C:\Users\User\repos\model-hub\web\src\pages\scan.tsx`, remove the import (line 11):

```typescript
import { BenchmarkDialog } from "@/components/benchmark-dialog";
```

and remove the usage (line 173):

```tsx
        <BenchmarkDialog onDone={() => recs.reload()} />
```

leaving the surrounding `<div className="mb-3 mt-6 flex items-center justify-between">...</div>` wrapper around just the "Top picks" label.

Delete the now-dead component file:

```bash
cd "C:\Users\User\repos\model-hub"
git rm web/src/components/benchmark-dialog.tsx
```

- [ ] Step 4: Run tests to verify they pass

```
C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q tests/test_api.py
```
Expected: PASS, 0 failed.

```
cd C:\Users\User\repos\model-hub\web && npm run typecheck && npm run build
```
Expected: both exit 0, no dangling references to `BenchmarkDialog` or `api.benchmark`.

- [ ] Step 5: Commit

```bash
cd "C:\Users\User\repos\model-hub"
git add backend/api.py tests/test_api.py web/src/lib/api.ts web/src/pages/scan.tsx
git commit -m "feat(web): remove free-tier /api/benchmark route and Benchmark dialog (Pro-only from here)"
```

---

### Task 4: lac-pro — shared benchmark step + `on_model_installed` autopilot hook

**Files:**
- Create: `C:\Users\User\repos\lac-pro\lac_pro\autopilot.py`
- Modify: `C:\Users\User\repos\lac-pro\lac_pro\plugin.py:54-55` (add `on_model_installed` method before the `register_api` stub)
- Test: `C:\Users\User\repos\lac-pro\tests\test_autopilot.py` (new), `C:\Users\User\repos\lac-pro\tests\test_plugin.py` (extend)

**Interfaces:**
- Consumes: `lac_pro.tune.PROMPT`, `lac_pro.tune.http_generate`, `lac_pro.tune.http_show`, `lac_pro.tune.run_sweep(model, ollama_generate, ollama_show, repeat=2, num_predict=128) -> dict` (existing, unmodified), `lac_pro.apply.apply_config(model, num_gpu, num_ctx=None, create_fn=None) -> str` (existing, unmodified), `lac_pro.license.check() -> Grant | None` (existing, unmodified), `backend.cookbook.benchmark.build_metrics(...)`/`log_result(entry) -> Path | None` (core, unmodified), `backend.cookbook.hardware.detect()`, `backend.cookbook.calibration.detect_stack(info=...)`/`machine_fingerprint(info, stack)` (core, unmodified).
- Produces: `lac_pro.autopilot.STATUS_PATH: Path`, `lac_pro.autopilot._read_status() -> dict`, `lac_pro.autopilot._write_status(model: str, entry: dict) -> None`, `lac_pro.autopilot.run_benchmark(model: str, prompt: str | None = None, num_predict: int = 128, temperature: float = 0.0, repeat: int = 1, no_cache: bool = False) -> list[dict]` (Task 5 reuses this — do not duplicate benchmark logic), `lac_pro.autopilot.run_autopilot(model: str) -> None`, `ProPlugin.on_model_installed(self, model_name: str) -> None` (this is what Task 1's core hook call sites find via `getattr(plugin.obj, "on_model_installed", None)`).

- [ ] Step 1: Write the failing tests

Create `C:\Users\User\repos\lac-pro\tests\test_autopilot.py`:

```python
"""Autopilot: benchmark -> sweep -> apply after a model install, license-gated.
Reuses run_sweep()/apply_config() verbatim (spec §3/§8 — no new sweep
algorithm) plus one new dedicated benchmark step that feeds core's
results.jsonl "measured" tier via core's own build_metrics/log_result."""
import json

import pytest

import lac_pro.autopilot as autopilot_mod
from lac_pro.autopilot import run_autopilot, run_benchmark


def _fake_generate_factory(tps_by_num_gpu):
    def gen(model, prompt, options, num_predict):
        tps = tps_by_num_gpu[options.get("num_gpu", "auto")]
        return {"eval_count": 100, "eval_duration": int(100 / tps * 1e9),
                "total_duration": 1, "load_duration": 0, "prompt_eval_duration": 0,
                "response": "x"}
    return gen


def _fake_show(model):
    return {"model_info": {"llama.block_count": 4}}


@pytest.fixture(autouse=True)
def isolated_status(tmp_path, monkeypatch):
    status = tmp_path / "pro_optimize_status.json"
    monkeypatch.setattr(autopilot_mod, "STATUS_PATH", status)
    return status


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def test_run_benchmark_logs_measured_entry_to_core_results(monkeypatch):
    monkeypatch.setattr(autopilot_mod, "http_generate",
                         lambda model, prompt, options, num_predict: {
                             "eval_count": 100, "eval_duration": 5_000_000_000,
                             "load_duration": 0, "prompt_eval_duration": 0,
                             "total_duration": 5_000_000_000, "response": "x",
                         })
    entries = run_benchmark("m:1b")
    assert len(entries) == 1
    assert entries[0]["tokens_per_second"] == 20.0

    from backend.cookbook.benchmark import history
    rows = history()
    assert len(rows) == 1
    assert rows[0]["model"] == "m:1b"
    assert rows[0]["tokens_per_second"] == 20.0
    assert "fingerprint" in rows[0]


def test_run_autopilot_sweeps_and_applies_winner(monkeypatch, isolated_status):
    monkeypatch.setattr(autopilot_mod, "http_generate", _fake_generate_factory(
        {"auto": 20.0, 4: 50.0, 3: 40.0, 2: 10.0}))
    monkeypatch.setattr(autopilot_mod, "http_show", _fake_show)

    applied = {}
    monkeypatch.setattr(autopilot_mod, "apply_config",
                         lambda model, num_gpu: applied.setdefault("num_gpu", num_gpu) or f"{model}-tuned")

    run_autopilot("m:1b")

    status = json.loads(isolated_status.read_text())
    assert status["m:1b"]["state"] == "done"
    assert status["m:1b"]["tokens_per_second"] == pytest.approx(50.0)
    assert applied["num_gpu"] == 4


def test_run_autopilot_degrades_to_failed_silent_on_error(monkeypatch, isolated_status):
    def boom(model, prompt, options, num_predict):
        raise ConnectionError("ollama unreachable")
    monkeypatch.setattr(autopilot_mod, "http_generate", boom)

    run_autopilot("m:1b")  # must not raise

    status = json.loads(isolated_status.read_text())
    assert status["m:1b"]["state"] == "failed_silent"
```

Append to `C:\Users\User\repos\lac-pro\tests\test_plugin.py`:

```python
def test_on_model_installed_noop_when_unlicensed(monkeypatch, tmp_path):
    import lac_pro.license as lic
    import lac_pro.autopilot as autopilot_mod
    monkeypatch.delenv("LAC_PRO_DEV", raising=False)
    monkeypatch.setattr(lic, "GRANT_PATH", tmp_path / "nope.json")
    calls = []
    monkeypatch.setattr(autopilot_mod, "run_autopilot", lambda m: calls.append(m))

    PLUGIN.on_model_installed("m:1b")  # must not raise SystemExit — check(), not require()
    assert calls == []


def test_on_model_installed_runs_autopilot_when_licensed(monkeypatch):
    import lac_pro.autopilot as autopilot_mod
    monkeypatch.setenv("LAC_PRO_DEV", "1")
    calls = []
    monkeypatch.setattr(autopilot_mod, "run_autopilot", lambda m: calls.append(m))

    PLUGIN.on_model_installed("m:1b")
    assert calls == ["m:1b"]
```

- [ ] Step 2: Run tests to verify they fail

```
cd "C:\Users\User\repos\lac-pro"
C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q tests/test_autopilot.py tests/test_plugin.py
```
Expected: FAIL — `ModuleNotFoundError: No module named 'lac_pro.autopilot'` and `AttributeError: 'ProPlugin' object has no attribute 'on_model_installed'`.

- [ ] Step 3: Implement the shared benchmark step + autopilot pipeline + hook

Create `C:\Users\User\repos\lac-pro\lac_pro\autopilot.py`:

```python
"""Autopilot: the automatic benchmark -> sweep -> apply pipeline that fires
after any successful model install, via the on_model_installed(model_name)
hook core calls. Reuses lac_pro.tune's run_sweep/http_generate/http_show and
lac_pro.apply's apply_config verbatim — no new sweep algorithm, just a new
automatic entry point into the existing one (spec §3/§8).

The dedicated benchmark step reuses CORE's own build_metrics/log_result
(backend.cookbook.benchmark) — the exact functions cli.cmd_benchmark and
api.api_benchmark used before they were removed — so results.jsonl gets a
real, fingerprinted "measured" entry: same file, same format, just a
Pro-gated producer (spec §5).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from lac_pro.apply import apply_config
from lac_pro.tune import PROMPT, http_generate, http_show, run_sweep

STATUS_PATH = Path.home() / ".model-hub" / "pro_optimize_status.json"


def _read_status() -> dict:
    try:
        return json.loads(STATUS_PATH.read_text())
    except Exception:  # noqa: BLE001 — missing/corrupt == empty
        return {}


def _write_status(model: str, entry: dict) -> None:
    try:
        STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = _read_status()
        data[model] = entry
        STATUS_PATH.write_text(json.dumps(data))
    except Exception:  # noqa: BLE001 — status tracking must never break autopilot
        pass


def run_benchmark(model: str, prompt: str | None = None, num_predict: int = 128,
                   temperature: float = 0.0, repeat: int = 1, no_cache: bool = False) -> list[dict]:
    """Run `repeat` benchmark generations, logging each to core's
    results.jsonl (the "measured" calibration tier) via core's own
    build_metrics/log_result. Returns the logged entries. Shared by the
    autopilot hook (below) and `lac pro benchmark`'s manual on-demand
    re-run (lac_pro/benchmark_cli.py) — exactly one implementation."""
    from backend.cookbook.benchmark import build_metrics, log_result
    from backend.cookbook.calibration import detect_stack, machine_fingerprint
    from backend.cookbook.hardware import detect

    prompt = prompt or PROMPT
    info = detect()
    stack = detect_stack(info=info)
    fp = machine_fingerprint(info, stack)

    options: dict = {"temperature": temperature}
    if no_cache:
        options["prompt_cache_disable"] = True

    entries = []
    for _ in range(max(1, repeat)):
        result = http_generate(model, prompt, options, num_predict)
        entry = build_metrics(result, model, prompt, num_predict, temperature,
                               fingerprint=fp, stack=stack)
        log_result(entry)
        entries.append(entry)
    return entries


def run_autopilot(model: str) -> None:
    """Benchmark -> sweep -> apply, in that order. Never raises: any failure
    degrades to failed_silent (spec §6) — the install already succeeded
    before this ever runs, so this must never look like an error."""
    print(f"[lac-pro] auto-optimizing {model} in the background…")
    _write_status(model, {"state": "running", "started_at": time.time()})
    try:
        run_benchmark(model)
        sweep = run_sweep(model, http_generate, http_show)
        winner = sweep["winner"]
        print(f"[lac-pro]   winner: {winner['label']} at {winner['median_tps']:.1f} tok/s")
        if winner["num_gpu"] is not None:
            name = apply_config(model, winner["num_gpu"])
            print(f"[lac-pro]   applied as {name}")
        _write_status(model, {
            "state": "done",
            "tokens_per_second": winner["median_tps"],
            "updated_at": time.time(),
        })
    except Exception as e:  # noqa: BLE001 — degrade silently, per spec §6
        print(f"[lac-pro]   autopilot failed silently: {e}")
        _write_status(model, {"state": "failed_silent", "updated_at": time.time()})
```

In `C:\Users\User\repos\lac-pro\lac_pro\plugin.py`, change:

```python
    def register_api(self, app) -> None:  # Pro API surface lands in spec Phase 2
        return


PLUGIN = ProPlugin()
```

to:

```python
    def on_model_installed(self, model_name: str) -> None:
        """Core's optional lac.plugins hook (backend/plugins.py): fires after
        any successful model install (backend/api.py's ollama_pull() and
        cli.py's cmd_pull()). Unlicensed -> silent no-op; the free-tier
        upsell decision lives entirely in the frontend (spec decision 3) --
        this hook must never know Pro's marketing exists. Uses check(), NOT
        require(): require() calls sys.exit(3), which would abort the CLI
        process right after a successful install -- this hook is a
        bystander, never a gate on the install itself.
        """
        from lac_pro.license import check
        if check() is None:
            return
        from lac_pro import autopilot
        autopilot.run_autopilot(model_name)

    def register_api(self, app) -> None:  # Pro API surface lands in spec Phase 2
        return


PLUGIN = ProPlugin()
```

- [ ] Step 4: Run tests to verify they pass

```
cd "C:\Users\User\repos\lac-pro"
C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q tests/test_autopilot.py tests/test_plugin.py
```
Expected: PASS, 0 failed (3 new in test_autopilot.py, 2 new + existing in test_plugin.py).

Then run the full lac-pro suite:

```
C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q
```
Expected: PASS, 0 failed (47 + 5 new = 52).

- [ ] Step 5: Commit

```bash
cd "C:\Users\User\repos\lac-pro"
git add lac_pro/autopilot.py lac_pro/plugin.py tests/test_autopilot.py tests/test_plugin.py
git commit -m "feat: on_model_installed autopilot hook — benchmark -> sweep -> apply, license-gated via check()"
```

---

### Task 5: lac-pro — `lac pro benchmark` manual on-demand command

**Files:**
- Create: `C:\Users\User\repos\lac-pro\lac_pro\benchmark_cli.py`
- Modify: `C:\Users\User\repos\lac-pro\lac_pro\plugin.py:12-19` (register the new subcommand)
- Modify: `C:\Users\User\repos\lac-pro\lac_pro\insights.py:50,60` (stale `lac benchmark` references — the command it points users to no longer exists in the free CLI)
- Test: `C:\Users\User\repos\lac-pro\tests\test_benchmark_cli.py` (new)

**Interfaces:**
- Consumes: `lac_pro.autopilot.run_benchmark(...)` (Task 4 — do not reimplement), `lac_pro.license.require(feature: str) -> Grant` (existing), `backend.cookbook.benchmark.history() -> list[dict]` (core, unmodified).
- Produces: `lac_pro.benchmark_cli.cmd_benchmark(args) -> None`, `lac_pro.benchmark_cli.configure_parser(parser) -> None`, registered as the `lac pro benchmark` subcommand.

- [ ] Step 1: Write the failing tests

Create `C:\Users\User\repos\lac-pro\tests\test_benchmark_cli.py`:

```python
"""`lac pro benchmark` -- Pro-gated manual on-demand benchmark, reusing the
same run_benchmark() step the autopilot hook uses internally (spec §2.1:
"a manual `lac pro benchmark` for on-demand re-runs, Pro-gated the same as
`lac pro tune`")."""
import argparse

import pytest

from lac_pro.plugin import PLUGIN


def _build_sub():
    parser = argparse.ArgumentParser(prog="lac")
    return parser, parser.add_subparsers(dest="command")


def test_benchmark_is_license_gated(monkeypatch, tmp_path):
    import lac_pro.license as lic
    monkeypatch.delenv("LAC_PRO_DEV", raising=False)
    monkeypatch.setattr(lic, "GRANT_PATH", tmp_path / "nope.json")

    parser, sub = _build_sub()
    PLUGIN.register_cli(sub)
    args = parser.parse_args(["pro", "benchmark", "m:1b"])
    with pytest.raises(SystemExit) as e:
        args.func(args)
    assert e.value.code == 3


def test_benchmark_runs_and_prints_median(monkeypatch, capsys, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LAC_PRO_DEV", "1")

    import lac_pro.autopilot as autopilot_mod
    monkeypatch.setattr(autopilot_mod, "http_generate",
                         lambda model, prompt, options, num_predict: {
                             "eval_count": 100, "eval_duration": 5_000_000_000,
                             "load_duration": 0, "prompt_eval_duration": 0,
                             "total_duration": 5_000_000_000, "response": "x",
                         })

    parser, sub = _build_sub()
    PLUGIN.register_cli(sub)
    args = parser.parse_args(["pro", "benchmark", "m:1b", "--repeat", "2"])
    args.func(args)

    out = capsys.readouterr().out
    assert "20.0" in out


def test_benchmark_list_shows_history(monkeypatch, capsys, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LAC_PRO_DEV", "1")

    from backend.cookbook.benchmark import log_result
    log_result({"model": "m:1b", "tokens_per_second": 42.0, "eval_count": 100})

    parser, sub = _build_sub()
    PLUGIN.register_cli(sub)
    args = parser.parse_args(["pro", "benchmark", "--list"])
    args.func(args)

    out = capsys.readouterr().out
    assert "m:1b" in out and "42.0" in out
```

- [ ] Step 2: Run tests to verify they fail

```
cd "C:\Users\User\repos\lac-pro"
C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q tests/test_benchmark_cli.py
```
Expected: FAIL — `argparse.ArgumentError` / `SystemExit(2)` from `parse_args` (`benchmark` isn't a registered `pro` subcommand yet).

- [ ] Step 3: Implement the manual command

Create `C:\Users\User\repos\lac-pro\lac_pro\benchmark_cli.py`:

```python
"""`lac pro benchmark` -- manual on-demand benchmark re-run, Pro-gated the
same as `lac pro tune`. Reuses lac_pro.autopilot.run_benchmark() -- the exact
same benchmark step the autopilot hook runs automatically after every
install -- so there is exactly one implementation of "how LAC Pro benchmarks
a model", not two.
"""
from __future__ import annotations

import statistics

from lac_pro.autopilot import run_benchmark
from lac_pro.license import require


def cmd_benchmark(args) -> None:
    require("benchmark")

    if args.list:
        from backend.cookbook.benchmark import history
        rows = history()
        if not rows:
            print("No benchmark results yet.")
            return
        print(f"  {'model':<28} {'tok/s':>8} {'eval_count':>11}")
        for e in rows[-30:]:
            print(f"  {e.get('model', '?'):<28} {e.get('tokens_per_second', 0):>8.1f} {e.get('eval_count', 0):>11}")
        return

    model = args.model
    if not model:
        print("model required (or use --list)")
        raise SystemExit(1)

    print(f"Benchmarking {model} (repeat={args.repeat})…")
    entries = run_benchmark(
        model,
        prompt=args.prompt,
        num_predict=args.num_predict,
        temperature=args.temperature,
        repeat=args.repeat,
        no_cache=args.no_cache,
    )
    tps_values = [e["tokens_per_second"] for e in entries]
    median_tps = statistics.median(tps_values)
    print(f"  tok/s: {median_tps:.1f} (median of {len(entries)})")


def configure_parser(parser) -> None:
    parser.add_argument("model", nargs="?", help="Installed Ollama model to benchmark")
    parser.add_argument("--prompt", default=None, help="Prompt text (default: LAC Pro's tuning prompt)")
    parser.add_argument("--num-predict", type=int, default=128, help="Tokens to generate (default: 128)")
    parser.add_argument("--temperature", type=float, default=0.0, help="Sampling temperature (default: 0)")
    parser.add_argument("--no-cache", action="store_true", help="Disable prompt cache for fresh eval")
    parser.add_argument("--repeat", type=int, default=1, help="Run N times and report median tok/s (default: 1)")
    parser.add_argument("--list", action="store_true", help="Show benchmark history")
    parser.set_defaults(func=cmd_benchmark)
```

In `C:\Users\User\repos\lac-pro\lac_pro\plugin.py`, change:

```python
from lac_pro import tune as _tune  # noqa: E402 — after _SUBCOMMANDS; tune never imports plugin
from lac_pro import activate as _activate  # noqa: E402
from lac_pro import insights as _insights  # noqa: E402

_SUBCOMMANDS.append(("tune", "Sweep offload configs and find the fastest for this rig", _tune.configure_parser))
_SUBCOMMANDS.append(("activate", "Activate a LAC Pro license key on this machine", _activate.configure_activate))
_SUBCOMMANDS.append(("deactivate", "Deactivate this machine's license seat", _activate.configure_deactivate))
_SUBCOMMANDS.append(("insights", "Calibration history + regression detection", _insights.configure_parser))
```

to:

```python
from lac_pro import tune as _tune  # noqa: E402 — after _SUBCOMMANDS; tune never imports plugin
from lac_pro import activate as _activate  # noqa: E402
from lac_pro import insights as _insights  # noqa: E402
from lac_pro import benchmark_cli as _benchmark_cli  # noqa: E402

_SUBCOMMANDS.append(("tune", "Sweep offload configs and find the fastest for this rig", _tune.configure_parser))
_SUBCOMMANDS.append(("activate", "Activate a LAC Pro license key on this machine", _activate.configure_activate))
_SUBCOMMANDS.append(("deactivate", "Deactivate this machine's license seat", _activate.configure_deactivate))
_SUBCOMMANDS.append(("insights", "Calibration history + regression detection", _insights.configure_parser))
_SUBCOMMANDS.append(("benchmark", "Benchmark a model's tok/s via Ollama (on-demand re-run)", _benchmark_cli.configure_parser))
```

In `C:\Users\User\repos\lac-pro\lac_pro\insights.py`, fix the two stale references to the now-removed free CLI command:

Line 50, change:
```python
        print("No benchmark history yet — run `lac benchmark <model>` a few times first.")
```
to:
```python
        print("No benchmark history yet — run `lac pro benchmark <model>` a few times first.")
```

Line 60, change:
```python
        print(f"\n{len(regs)} model(s) slower than baseline — driver update, background load, "
              f"or a stack change (re-run `lac benchmark` to recalibrate).")
```
to:
```python
        print(f"\n{len(regs)} model(s) slower than baseline — driver update, background load, "
              f"or a stack change (re-run `lac pro benchmark` to recalibrate).")
```

- [ ] Step 4: Run tests to verify they pass

```
cd "C:\Users\User\repos\lac-pro"
C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q tests/test_benchmark_cli.py
```
Expected: PASS, 0 failed.

```
C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q
```
Expected: PASS, 0 failed (52 + 3 new = 55).

- [ ] Step 5: Commit

```bash
cd "C:\Users\User\repos\lac-pro"
git add lac_pro/benchmark_cli.py lac_pro/plugin.py lac_pro/insights.py tests/test_benchmark_cli.py
git commit -m "feat: lac pro benchmark -- Pro-gated manual on-demand re-run, reuses autopilot's run_benchmark()"
```

---

### Task 6: lac-pro — `/api/pro/optimize-status` polling route

**Files:**
- Modify: `C:\Users\User\repos\lac-pro\lac_pro\autopilot.py` (add `optimize_status()`)
- Modify: `C:\Users\User\repos\lac-pro\lac_pro\plugin.py` (implement `register_api`, replacing the stub)
- Test: `C:\Users\User\repos\lac-pro\tests\test_autopilot.py` (extend)

**Interfaces:**
- Consumes: `lac_pro.autopilot._read_status()`, `lac_pro.autopilot.STATUS_PATH` (Task 4), `lac_pro.license.check()` (existing).
- Produces: `lac_pro.autopilot.optimize_status(model: str) -> tuple[dict, int]` (framework-agnostic, testable without Flask), Flask route `GET /api/pro/optimize-status?model=<name>` returning JSON `{"state": "idle" | "running" | "done" | "failed_silent" | "not_licensed", "tokens_per_second"?: float}` — this is exactly what Task 7's frontend polling consumes.

- [ ] Step 1: Write the failing tests

Append to `C:\Users\User\repos\lac-pro\tests\test_autopilot.py`:

```python
def test_optimize_status_requires_model():
    from lac_pro.autopilot import optimize_status
    body, code = optimize_status("")
    assert code == 400


def test_optimize_status_not_licensed(monkeypatch, tmp_path):
    import lac_pro.license as lic
    monkeypatch.delenv("LAC_PRO_DEV", raising=False)
    monkeypatch.setattr(lic, "GRANT_PATH", tmp_path / "nope.json")

    from lac_pro.autopilot import optimize_status
    body, code = optimize_status("m:1b")
    assert code == 200
    assert body == {"state": "not_licensed"}


def test_optimize_status_idle_when_licensed_but_no_entry(monkeypatch):
    monkeypatch.setenv("LAC_PRO_DEV", "1")
    from lac_pro.autopilot import optimize_status
    body, code = optimize_status("m:1b")
    assert code == 200
    assert body == {"state": "idle"}


def test_optimize_status_returns_recorded_entry(monkeypatch):
    monkeypatch.setenv("LAC_PRO_DEV", "1")
    autopilot_mod._write_status("m:1b", {"state": "done", "tokens_per_second": 73.0})

    from lac_pro.autopilot import optimize_status
    body, code = optimize_status("m:1b")
    assert code == 200
    assert body == {"state": "done", "tokens_per_second": 73.0}


def test_register_api_mounts_optimize_status_route(monkeypatch):
    import flask
    app = flask.Flask(__name__)
    monkeypatch.setenv("LAC_PRO_DEV", "1")

    from lac_pro.plugin import PLUGIN
    PLUGIN.register_api(app)

    client = app.test_client()
    r = client.get("/api/pro/optimize-status?model=m:1b")
    assert r.status_code == 200
    assert r.get_json() == {"state": "idle"}
```

(These 5 tests use the `isolated_status`/`isolated_home` autouse fixtures already defined at the top of `test_autopilot.py` from Task 4 — no new fixtures needed.)

- [ ] Step 2: Run tests to verify they fail

```
cd "C:\Users\User\repos\lac-pro"
C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q tests/test_autopilot.py
```
Expected: FAIL — `ImportError: cannot import name 'optimize_status' from 'lac_pro.autopilot'`.

- [ ] Step 3: Implement the route

In `C:\Users\User\repos\lac-pro\lac_pro\autopilot.py`, append:

```python
def optimize_status(model: str) -> tuple[dict, int]:
    """GET /api/pro/optimize-status?model=<name> business logic, framework-
    agnostic so it's testable without Flask. State machine: not_licensed
    (checked live -- always wins over a stale file entry, since a license
    can lapse) -> idle (licensed, autopilot hasn't recorded anything for
    this model yet) -> running -> done | failed_silent (spec §4/§6)."""
    if not model:
        return {"error": "model required"}, 400

    from lac_pro.license import check
    if check() is None:
        return {"state": "not_licensed"}, 200

    entry = _read_status().get(model)
    if entry is None:
        return {"state": "idle"}, 200
    return entry, 200
```

In `C:\Users\User\repos\lac-pro\lac_pro\plugin.py`, change:

```python
    def register_api(self, app) -> None:  # Pro API surface lands in spec Phase 2
        return


PLUGIN = ProPlugin()
```

to:

```python
    def register_api(self, app) -> None:
        from flask import jsonify, request
        from lac_pro.autopilot import optimize_status

        @app.route("/api/pro/optimize-status")
        def _pro_optimize_status():
            model = request.args.get("model", "").strip()
            body, code = optimize_status(model)
            return jsonify(body), code


PLUGIN = ProPlugin()
```

- [ ] Step 4: Run tests to verify they pass

```
cd "C:\Users\User\repos\lac-pro"
C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q tests/test_autopilot.py
```
Expected: PASS, 0 failed (8 tests in this file: 3 from Task 4 + 5 new).

```
C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q
```
Expected: PASS, 0 failed (55 + 5 new = 60).

- [ ] Step 5: Commit

```bash
cd "C:\Users\User\repos\lac-pro"
git add lac_pro/autopilot.py lac_pro/plugin.py tests/test_autopilot.py
git commit -m "feat: /api/pro/optimize-status polling route (idle/running/done/failed_silent/not_licensed)"
```

---

### Task 7: Web — installer.ts/api.ts autopilot status polling + toasts

**Files:**
- Modify: `C:\Users\User\repos\model-hub\web\src\lib\api.ts` (add `proOptimizeStatus`, `plugins`)
- Modify: `C:\Users\User\repos\model-hub\web\src\lib\installer.ts` (poll after `pullWithToast`'s success path)

**Interfaces:**
- Consumes: `GET /api/pro/optimize-status?model=<name>` (Task 6), `GET /api/plugins` (pre-existing core route, `backend/api.py:909-914`).
- Produces: `pollProOptimizeStatus(model: string): Promise<void>` (internal to `installer.ts`, invoked automatically from `pullWithToast` — every existing caller of `pullWithToast` across `scan.tsx`, `browse.tsx`, `dashboard.tsx`, `installed.tsx` gets this behavior with zero changes to those 4 files).

No automated frontend test suite exists in this repo (confirmed: no vitest/jest, no `*.test.*` under `web/src`, `package.json` has only `dev`/`build`/`preview`/`typecheck` scripts). Verification for this task is `npm run typecheck && npm run build` passing cleanly plus a manual read-through of the logic against the 5 states.

- [ ] Step 1: Add the two new API bindings

In `C:\Users\User\repos\model-hub\web\src\lib\api.ts`, add these two methods right after `chat(...)` (where `benchmark(...)` used to sit before Task 3 removed it):

```typescript
  /** Poll LAC Pro's autopilot status for a just-installed model. */
  proOptimizeStatus: (model: string) =>
    getJSON<{ state: "idle" | "running" | "done" | "failed_silent" | "not_licensed"; tokens_per_second?: number }>(
      `/api/pro/optimize-status?model=${encodeURIComponent(model)}`
    ),
  /** List installed plugins (e.g. to check whether "pro" is present/licensed). */
  plugins: () => getJSON<{ name: string; version: string; ok: boolean; error: string | null }[]>("/api/plugins"),
```

- [ ] Step 2: Verify the new bindings typecheck

```
cd C:\Users\User\repos\model-hub\web && npm run typecheck
```
Expected: exit 0 (the new methods compile; nothing calls them yet so this step just proves the types are sound before wiring the caller).

- [ ] Step 3: Wire the polling + toasts into `pullWithToast`

Replace the full contents of `C:\Users\User\repos\model-hub\web\src\lib\installer.ts` with:

```typescript
import { toast } from "sonner";
import { api } from "@/lib/api";

/** Track active pulls so they can be cancelled from the UI. */
const activePulls = new Map<string, AbortController>();

const PRO_UPSELL_TOAST_KEY = "lac.pro_upsell_toast_shown";
const PRO_AUTOPILOT_EXPLAINER_KEY = "lac.pro_autopilot_explainer_shown";
const OPTIMIZE_POLL_MS = 2000;
const OPTIMIZE_POLL_TIMEOUT_MS = 5 * 60 * 1000;

/**
 * Pull a model from Ollama via the streaming /api/ollama/pull endpoint,
 * surfacing live progress as a Sonner toast with a Cancel button.
 * Calls onDone when complete (not when cancelled).
 */
export function pullWithToast(model: string, onDone?: () => void) {
  // If already pulling this model, ignore duplicate.
  if (activePulls.has(model)) return;

  const controller = new AbortController();
  activePulls.set(model, controller);

  const id = toast.loading(`Pulling ${model}…`, {
    action: {
      label: "Cancel",
      onClick: () => controller.abort(),
    },
  });

  let pct = 0;

  (async () => {
    try {
      for await (const ev of api.pull(model, controller.signal)) {
        if (ev.error) throw new Error(String(ev.error));
        const c = Number(ev.completed ?? 0);
        const t = Number(ev.total ?? 0);
        const status = String(ev.status ?? "");
        if (t > 0) {
          pct = Math.max(pct, Math.round((c / t) * 100));
          toast.loading(`Pulling ${model} — ${pct}%`, {
            id,
            description: status,
            action: {
              label: "Cancel",
              onClick: () => controller.abort(),
            },
          });
        } else {
          toast.loading(`Pulling ${model}…`, {
            id,
            description: status,
            action: {
              label: "Cancel",
              onClick: () => controller.abort(),
            },
          });
        }
      }
      toast.success(`Installed ${model}`, { id });
      onDone?.();
      pollProOptimizeStatus(model);
    } catch (e) {
      if (controller.signal.aborted) {
        toast.info(`Cancelled pull of ${model}`, { id });
      } else {
        toast.error(`Failed to pull ${model}`, {
          id,
          description: e instanceof Error ? e.message : String(e),
        });
      }
    } finally {
      activePulls.delete(model);
    }
  })();
}

/** Cancel all active pulls (e.g. on page unload). */
export function cancelAllPulls() {
  for (const controller of activePulls.values()) {
    controller.abort();
  }
  activePulls.clear();
}

/**
 * Second phase after an install: poll LAC Pro's autopilot (benchmark + sweep
 * + apply, fired by the on_model_installed hook) and surface its result.
 * Free users (no Pro, or Pro unlicensed) get a single one-time upsell toast
 * instead of a polling toast — gated by localStorage so it only ever fires
 * once, per spec decision 3 (this lives entirely in the frontend; core and
 * the hook never know Pro's marketing exists).
 */
async function pollProOptimizeStatus(model: string) {
  const started = Date.now();
  const toastId = toast.loading(`Optimizing ${model}…`);

  while (Date.now() - started < OPTIMIZE_POLL_TIMEOUT_MS) {
    let status: { state: string; tokens_per_second?: number };
    try {
      status = await api.proOptimizeStatus(model);
    } catch {
      // Route unreachable (404 = Pro not installed at all, or transient) --
      // stop silently and offer the upsell, same as an explicit not_licensed.
      toast.dismiss(toastId);
      maybeShowUpsellToast();
      return;
    }

    if (status.state === "not_licensed") {
      toast.dismiss(toastId);
      maybeShowUpsellToast();
      return;
    }
    if (status.state === "done") {
      const tps = Math.round(status.tokens_per_second ?? 0);
      toast.success(`${model}: ${tps} tok/s ✓`, { id: toastId });
      maybeShowAutopilotExplainerToast();
      return;
    }
    if (status.state === "failed_silent") {
      // Never a scary error toast -- the model is already installed and
      // usable; it just stayed at Ollama's default config (spec §6).
      toast.dismiss(toastId);
      return;
    }
    // "idle" or "running" -> keep polling.
    await new Promise((resolve) => setTimeout(resolve, OPTIMIZE_POLL_MS));
  }
  toast.dismiss(toastId);
}

function maybeShowUpsellToast() {
  if (localStorage.getItem(PRO_UPSELL_TOAST_KEY)) return;
  localStorage.setItem(PRO_UPSELL_TOAST_KEY, "1");
  toast.info("LAC Pro auto-benchmarks and tunes every model you install for your exact hardware.", {
    action: {
      label: "Get Pro",
      onClick: () => window.open("https://dkrynen.github.io/lac/#pro", "_blank"),
    },
  });
}

function maybeShowAutopilotExplainerToast() {
  if (localStorage.getItem(PRO_AUTOPILOT_EXPLAINER_KEY)) return;
  localStorage.setItem(PRO_AUTOPILOT_EXPLAINER_KEY, "1");
  toast.info(
    "LAC Pro just optimized this model automatically — benchmarked it, swept GPU-offload configs, and applied the fastest. It'll keep doing this for every model you install, silently, from now on."
  );
}
```

- [ ] Step 4: Verify the whole web app still typechecks and builds

```
cd C:\Users\User\repos\model-hub\web && npm run typecheck && npm run build
```
Expected: both exit 0. Manually confirm the 5 states are all handled (`idle`/`running` -> keep polling, `done` -> success toast + one-time explainer, `failed_silent` -> silent dismiss, `not_licensed` -> one-time upsell, unreachable/404 -> same as `not_licensed`), and that all 4 existing `pullWithToast` callers (`scan.tsx`, `browse.tsx`, `dashboard.tsx`, `installed.tsx`) needed zero changes.

- [ ] Step 5: Commit

```bash
cd "C:\Users\User\repos\model-hub"
git add web/src/lib/api.ts web/src/lib/installer.ts
git commit -m "feat(web): poll LAC Pro autopilot status after install, one-time upsell + explainer toasts"
```

---

### Task 8: Web — calibration badge copy reflects the new free/Pro boundary

**Files:**
- Modify: `C:\Users\User\repos\model-hub\web\src\pages\scan.tsx` (originally lines 311-336, `SOURCE_META`/`SourceBadge` — now at lines 309-334 after Task 3 removed 2 earlier lines from this same file)

**Interfaces:**
- Consumes: nothing new (existing `Recommendation.speed_source`/`speed_band_pct` from `backend/api.py::api_recommend`, unchanged).
- Produces: nothing new — copy/tooltip only.

No automated test exists for this (pure JSX/copy change in a page with no test coverage). Verification is `npm run typecheck && npm run build` plus a visual/manual check that the tooltip text is accurate.

- [ ] Step 1: Update the `SourceBadge` tooltip copy

In `C:\Users\User\repos\model-hub\web\src\pages\scan.tsx`, change:

```tsx
function SourceBadge({ source, band }: { source: "measured" | "calibrated" | "estimated"; band: number }) {
  const meta = SOURCE_META[source];
  const tip =
    source === "measured"
      ? "Real tok/s from your benchmarks"
      : source === "calibrated"
      ? `Adjusted by your machine's regime factor (±${Math.round(band)}%)`
      : `Theoretical estimate (±${Math.round(band)}%)`;
```

to:

```tsx
function SourceBadge({ source, band }: { source: "measured" | "calibrated" | "estimated"; band: number }) {
  const meta = SOURCE_META[source];
  const tip =
    source === "measured"
      ? "Real tok/s — auto-benchmarked by LAC Pro on your exact hardware"
      : source === "calibrated"
      ? `Adjusted by your machine's regime factor (±${Math.round(band)}%)`
      : `Theoretical estimate (±${Math.round(band)}%). LAC Pro auto-benchmarks every model you install for measured accuracy.`;
```

(`SOURCE_META`'s labels — `measured`/`calibrated`/`estimated` — stay unchanged; only the tooltip copy changes, since the badge text itself is a plain, accurate state name in all three tiers.)

- [ ] Step 2: Verify it typechecks and builds

```
cd C:\Users\User\repos\model-hub\web && npm run typecheck && npm run build
```
Expected: both exit 0.

- [ ] Step 3: Commit

```bash
cd "C:\Users\User\repos\model-hub"
git add web/src/pages/scan.tsx
git commit -m "feat(web): calibration badge copy reflects free tops out at calibrated, Pro reaches measured"
```

---

### Task 9: Landing page + README copy pass (PROPOSED copy — Duan reviews before final)

**Files:**
- Modify: `C:\Users\User\repos\model-hub\site\index.html:107-112` (the "Dyno" free-tier feature card), `site\index.html:129-131` (the `#pro` section's 3 bullets — bullet 3 stays unchanged), `site\index.html:165` (FAQ "Is it actually free?")
- Modify: `C:\Users\User\repos\model-hub\README.md:5,15,18,33` (top pitch line, feature list, quick-start), `README.md:45` (LAC Pro section — add an autopilot bullet)

**Interfaces:** none — copy only. The Polar.sh checkout `href` (`site/index.html:134`) and the `$3/month billed annually` price line (`site/index.html:133`) are **not** touched — hard constraint, byte-identical.

Per the spec's explicit deferral ("exact copy is Duan's call at review time, not locked here"), this task's deliverable is the proposed new copy below, applied as a single commit — but the acceptance criterion is Duan's sign-off on wording, not "task done" the moment it's typed. Do not treat this commit as final until Duan has seen the before/after diff. There is no automated test for marketing copy; verification is a visual read-through in a browser plus `git diff` for the exact wording review.

- [ ] Step 1: Rewrite the "Dyno" feature card (free tier no longer does one-click benchmarks)

In `C:\Users\User\repos\model-hub\site\index.html`, change:

```html
    <div class="card">
      <div class="k">Dyno</div>
      <h3>Real numbers, not vibes</h3>
      <p>One-click benchmarks measure actual tokens/second, and LAC calibrates its own
         predictions against them. The more you benchmark, the smarter it gets about your machine.</p>
    </div>
```

to:

```html
    <div class="card">
      <div class="k">Dyno</div>
      <h3>Real numbers, not vibes</h3>
      <p>Every recommendation is tagged <code>measured</code>, <code>calibrated</code>, or <code>estimated</code>
         with a confidence band, so you always know how sure LAC is. LAC Pro turns every model you
         install into a real, measured data point — automatically.</p>
    </div>
```

- [ ] Step 2: Rewrite the `#pro` section's bullets (autopilot framing, not manual tune/insights)

In `C:\Users\User\repos\model-hub\site\index.html`, change:

```html
        <li><strong>Stop guessing your GPU offload.</strong> <code>lac pro tune</code> benchmarks every layer-split config on your exact rig and applies the fastest one. No more afternoon wasted manually tweaking <code>num_gpu</code>.</li>
        <li><strong>Save optimal settings per model.</strong> Switch between models without re-tuning. Layer splits, context presets, iGPU control — one profile per model, applied automatically.</li>
        <li><strong>Know when something changed.</strong> Insights tracks your tok/s baseline over time and flags the moment a driver update, OS patch, or model swap costs you performance.</li>
```

to:

```html
        <li><strong>It just works — automatically.</strong> Install any model and LAC Pro benchmarks it, sweeps every GPU-offload config, and applies the fastest — the moment the download finishes. No command to run, no button to click.</li>
        <li><strong>Every recommendation, actually measured.</strong> Free LAC estimates and calibrates. Pro turns every model you install into a real, measured data point on your exact rig — no more guessing bands.</li>
        <li><strong>Know when something changed.</strong> Insights tracks your tok/s baseline over time and flags the moment a driver update, OS patch, or model swap costs you performance.</li>
```

(Historical public checkout CTA preservation note redacted; current public copy should route to the waitlist until Duan-gated launch approval.)

- [ ] Step 3: Fix the FAQ answer that still claims benchmarking is free

In `C:\Users\User\repos\model-hub\site\index.html`, change:

```html
        <p>The core — hardware scan, recommendations, benchmarking, chat, web UI, TUI — is MIT open source and always will be. Pro features are a paid add-on.</p>
```

to:

```html
        <p>The core — hardware scan, recommendations, chat, web UI, TUI — is MIT open source and always will be. Benchmarking and auto-tuning are LAC Pro, a paid add-on.</p>
```

- [ ] Step 4: Fix README.md's now-stale free-tier benchmark claims

In `C:\Users\User\repos\model-hub\README.md`, change line 5:
```markdown
**Scans your hardware. Recommends models that actually fit. Benchmarks real tok/s — not guesses.**
```
to:
```markdown
**Scans your hardware. Recommends models that actually fit. LAC Pro benchmarks and auto-tunes every one you install.**
```

Change line 15:
```markdown
- **Real-speed calibration** — `lac benchmark` measures actual tok/s and feeds a per-machine calibration loop; recs are tagged `measured` / `calibrated` / `estimated` with confidence bands
```
to:
```markdown
- **Real-speed calibration** — recs are tagged `measured` / `calibrated` / `estimated` with confidence bands; LAC Pro's autopilot feeds the `measured` tier automatically on every install
```

Remove line 18 entirely (the feature no longer exists):
```markdown
- **Benchmark from the browser** — one dialog, live per-run tok/s, recommendations recalibrate on completion
```

Change line 33 (the CLI quick-start block):
```bash
lac benchmark llama3.2:3b   # real tok/s -> calibrates future recs
```
to:
```bash
lac pull llama3.2:3b        # installs it -- LAC Pro (if licensed) auto-tunes it for your rig
```

In the "## LAC Pro — the Tuning Cockpit" section, change:
```markdown
- **`lac pro tune <model>`** — sweeps GPU-offload configurations (auto / all layers / 75% / 50%), benchmarks each on *your* hardware, and bakes the fastest into a ready-to-use `<model>-tuned` variant
- **Offload controls** — per-model layer splits, iGPU control, context presets
- **Insights** — calibration history and regression detection ("your tok/s dropped 12% since that driver update")
```
to:
```markdown
- **Autopilot** — every model you install is automatically benchmarked, GPU-offload swept, and tuned to your exact rig, with zero commands
- **`lac pro tune <model>` / `lac pro benchmark <model>`** — manual on-demand re-runs of the same sweep and benchmark steps autopilot uses
- **Offload controls** — per-model layer splits, iGPU control, context presets
- **Insights** — calibration history and regression detection ("your tok/s dropped 12% since that driver update")
```

- [ ] Step 5: Present the diff for review, then commit

```bash
cd "C:\Users\User\repos\model-hub"
git diff site/index.html README.md
```

Show this diff to Duan and get explicit sign-off on the wording before treating it as final (per the spec's deferral — this is a proposal, not a locked decision). Once approved (or edited to Duan's preferred wording):

```bash
git add site/index.html README.md
git commit -m "docs(site): landing page + README copy pass for Pro autopilot (proposed wording, reviewed)"
```

---

## Self-review

**Spec coverage** — every numbered decision in §2 maps to a task:
- §2.1 (both tune and benchmark Pro-gated, `lac benchmark` removed, `lac pro benchmark` added) → Tasks 2, 3 (the flagged gap-closing removal), 5.
- §2.2 (autopilot, not a button; fires on both CLI and web installs) → Tasks 1, 4.
- §2.3 (free users see nothing extra from the backend; one-time upsell toast) → Task 4 (silent no-op, no backend flag) + Task 7 (frontend-only, localStorage-gated).
- §2.4 (first-run-on-Pro explainer toast, one-time, silent after) → Task 7.
- §2.5 (free tops out at calibrated/estimated; Pro reaches measured) → Task 4 (`run_benchmark` feeds `results.jsonl`) + Task 8 (badge copy).
- §2.6 (optimization strictly additive; failures degrade silently) → Task 4 (`run_autopilot`'s try/except → `failed_silent`) + Task 7 (`failed_silent` → silent dismiss, no error toast).
- §2.7 (pricing/checkout unchanged) → Task 9 (explicit byte-identical constraint called out, verified via `git diff` on the price/CTA lines).
- §3 (hook signature, plugin isolation, license-gate via `check()`, reuse `run_sweep`/`apply_config`) → Tasks 1, 4.
- §4 (CLI synchronous / web backgrounded + status file + route + frontend polling + upsell-decision-in-frontend-only) → Tasks 1, 4, 6, 7.
- §5 (concrete boundary changes: `cmd_benchmark` removed, measured tier now Pro-exclusive, landing page copy) → Tasks 2, 3 (gap), 9.
- §6 (error handling: silent degrade, Ollama-unreachable same path, concurrent installs via per-model status keys, raising hook isolated) → Tasks 1, 4, 7.
- §7 (testing approach: plugin call/isolation tests, license-gate + route state-transition tests) → Tasks 1, 4, 5, 6.
- §8 (out of scope — verified NOT touched: `run_sweep`'s algorithm/scoring/split-plan logic, W1/W2/W5, no task-queue system) → confirmed by design; `lac_pro/tune.py` and `lac_pro/apply.py` are only ever *called*, never modified, across all 9 tasks.

**Placeholder scan** — no "TBD"/"add appropriate handling"/"similar to Task N" found in the final draft; every step has real, complete code or an exact command.

**Type/interface consistency checked:**
- Hook signature `on_model_installed(model_name: str) -> None` is identical in Task 1 (core call sites), Task 4 (`ProPlugin` implementation), and the tests in both.
- Status-file state literals (`idle`, `running`, `done`, `failed_silent`, `not_licensed`) are the same 5 strings in Task 4 (`run_autopilot`'s writes), Task 6 (`optimize_status`'s reads/synthesis + route), and Task 7 (frontend's `if` chain) — no drift (e.g. no `"failed"` vs `"failed_silent"` mismatch).
- `run_benchmark(model, prompt=None, num_predict=128, temperature=0.0, repeat=1, no_cache=False) -> list[dict]` has the identical signature everywhere it's called: `run_autopilot` (Task 4, defaults only) and `cmd_benchmark` (Task 5, full args passthrough).
- `STATUS_PATH`, `_read_status()`, `_write_status()` are defined once in Task 4 and only ever consumed (never redefined) in Task 6.
- `/api/pro/optimize-status` response shape (`{"state": ..., "tokens_per_second"?: ...}`) matches between Task 6's Python route and Task 7's TypeScript type.

**Gaps flagged explicitly (not guessed):** the `/api/benchmark` web route + `BenchmarkDialog` removal (Task 3) is not literally named in the spec's §5 list — flagged at the top of this document and in Task 3's own header. README.md/CHANGELOG.md/HANDOFF.md staleness beyond what Task 9 covers: `CHANGELOG.md` and `HANDOFF.md` are historical/point-in-time records and were deliberately left untouched (not living docs); if Duan wants those corrected too, that's a follow-up, not part of this plan.

The exact SQL/threading-safety nuance worth calling out again: the status file (`~/.model-hub/pro_optimize_status.json`) uses read-modify-write with no file lock (Task 4's `_write_status`), which has a narrow race window if two different models install at the *exact* same instant — this is an intentional, spec-sanctioned trade-off ("no locking needed beyond what already exists for tune.jsonl appends"), not an oversight; flagging it here so it isn't mistaken for one during review.
