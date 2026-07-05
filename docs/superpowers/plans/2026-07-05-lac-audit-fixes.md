# LAC Pre-Launch Audit Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all 16 Critical/Important findings (plus cheap Minor fixes folded in) from LAC's completed pre-launch audit, across the `model-hub` (core) and `lac-pro` (Pro plugin) repos, in the priority order Duan specified: security + broken payment flow, then broken user-facing happy paths, then Pro-pitch integrity + API error handling, then backend VRAM correctness, then frontend polish + rebrand leaks, then `docs.html` cleanup, then the remaining minor sweep.

**Architecture:** Root causes are already diagnosed per-finding (this is a bug-fix plan, not an investigation). Each task is a targeted, test-first fix touching only the specific file(s) the audit named — no new subsystems, no speculative refactors, beyond the two small DRY extractions the audit itself calls for (a shared library-enrichment helper for `lac browse`, and a shared download-history logger). Two independent git repos are involved and are committed to separately: `model-hub` (core, has an `origin` but pushes are gated on Duan) and `lac-pro` (the Pro plugin, no git remote, never gets one).

**Tech Stack:** Python 3.11 (Flask backend + argparse CLI) in `model-hub`; a matching Python plugin package in `lac-pro` (Polar.sh license API client, Ollama HTTP client, using core's venv — it has none of its own); React + TypeScript + Vite web app in `model-hub/web`; `pytest` for both Python suites; `tsc --noEmit` + `vite build` for the web suite.

## Global Constraints

- Core venv: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe` — use this interpreter for every Python command in **both** repos (`lac-pro` has no venv of its own).
- Core test command: `.venv\Scripts\python.exe -m pytest -q` run from `C:\Users\User\repos\model-hub`.
- `lac-pro` test command: the **same** venv's python, `-m pytest -q`, run from `C:\Users\User\repos\lac-pro`.
- Web test command: `cd C:\Users\User\repos\model-hub\web && npm run typecheck && npm run build` — bare, unpiped, so exit codes are real.
- Windows environment; Git Bash is available for POSIX-style commands (used for all shell snippets below).
- One commit per task, landed directly on `master` in whichever repo the task touches (both repos are currently on `master`). Never push to origin without Duan's separate, explicit go-ahead.
- Never spawn agents/subagents while executing a task in this plan — work in the foreground.
- Do not edit anything under `docs/superpowers/specs/` or `docs/superpowers/plans/` other than this new plan file.
- A live LAC server may currently be running at `http://127.0.0.1:5050` with `LAC_PRO_DEV=1` set, and Ollama online with `qwen3:0.6b` + `qwen2.5:0.5b` installed. Each task below notes whether it needs a server restart to take effect live — do not restart the server yourself as part of executing this plan.
- **Manual, non-code cleanup:** `C:\Temp\lac-audit-traversal-probe` is a real directory the audit's path-traversal proof created (confirmed still present, contains a 133-byte `workspace.json`). It must be deleted using normal OS tools, **never** through the LAC app itself (whose vulnerable code created it). Task 1, Step 1 covers this explicitly.

---

### Task 1: Security — path traversal in workspace create/delete

**Files:**
- Modify: `backend/cookbook/config.py:120-150` (adds a new `_resolve_within_workspaces` helper; rewrites `create_workspace` and `delete_workspace`)
- Modify: `backend/api.py:703-711` (`api_create_workspace` — catch the new `ValueError`)
- Modify: `cli.py:944-951` (`cmd_workspace`'s `create` action — catch the new `ValueError`)
- Test: `tests/test_workspace_path_safety.py` (new)

**Interfaces:**
- Consumes: `backend.cookbook.config._workspaces_dir() -> Path` (existing, unchanged)
- Produces: `backend.cookbook.config._resolve_within_workspaces(ws_id: str) -> Path` — raises `ValueError` if `ws_id` would resolve outside `_workspaces_dir()`. `create_workspace()` and `delete_workspace()` now route through it; `delete_workspace()` still returns `bool` (never raises — a traversal attempt is treated as "not found"), `create_workspace()` now raises `ValueError` for a traversal attempt (callers must catch it).

- [ ] Step 1: Manually delete the audit's proof-of-concept artifact (not a code change)

Run (Git Bash), verifying first, then deleting with plain OS tools — never through the LAC app:

```bash
ls -la "/c/Temp/lac-audit-traversal-probe"
rm -rf "/c/Temp/lac-audit-traversal-probe"
ls "/c/Temp/" | grep -i traversal
```

Expected: the first `ls` shows the directory (containing `workspace.json`); the final `ls | grep` prints nothing (directory gone).

- [ ] Step 2: Write the failing tests

Create `tests/test_workspace_path_safety.py`:

```python
from __future__ import annotations

import pytest

from backend.cookbook.config import (
    create_workspace,
    delete_workspace,
    _workspaces_dir,
)


def test_create_workspace_rejects_path_traversal(isolated_home):
    with pytest.raises(ValueError):
        create_workspace("../../../../Temp/x")


def test_create_workspace_sane_name_still_works(isolated_home):
    ws = create_workspace("My Project")
    assert ws.id == "my-project"
    assert (_workspaces_dir() / "my-project").is_dir()


def test_delete_workspace_rejects_path_traversal(isolated_home, tmp_path):
    _workspaces_dir()  # ensure the sandbox dir exists
    outside = tmp_path / "outside-target"
    outside.mkdir()
    (outside / "keepme.txt").write_text("still here")

    # _workspaces_dir() is tmp_path/home/.model-hub/workspaces (3 levels
    # under tmp_path) -- this purely-relative id climbs out to tmp_path
    # then back down into the planted sibling, exactly like the proven
    # exploit's shape ("../../../../Temp/x").
    result = delete_workspace("../../../outside-target")

    assert result is False
    assert outside.exists()
    assert (outside / "keepme.txt").exists()


def test_delete_workspace_still_works_for_real_workspace(isolated_home):
    ws = create_workspace("Scratch")
    assert delete_workspace(ws.id) is True
    assert not (_workspaces_dir() / ws.id).exists()


def test_api_create_workspace_traversal_returns_400(flask_app, isolated_home):
    client = flask_app.test_client()
    r = client.post("/api/workspaces", json={"name": "../../../../Temp/evil"})
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_cli_workspace_create_traversal_exits_clean(isolated_home):
    import cli as cli_mod

    parser = cli_mod.build_parser()
    args = parser.parse_args(["workspace", "create", "../../../../Temp/evil"])
    with pytest.raises(SystemExit) as e:
        cli_mod.cmd_workspace(args)
    assert e.value.code == 1
```

- [ ] Step 3: Run tests to verify they fail

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_workspace_path_safety.py` (from `C:\Users\User\repos\model-hub`)
Expected: `test_create_workspace_rejects_path_traversal`, `test_delete_workspace_rejects_path_traversal`, `test_api_create_workspace_traversal_returns_400`, and `test_cli_workspace_create_traversal_exits_clean` FAIL (no `ValueError` raised yet — traversal currently succeeds). `test_create_workspace_sane_name_still_works` and `test_delete_workspace_still_works_for_real_workspace` PASS already (baseline behavior).

- [ ] Step 4: Fix `backend/cookbook/config.py` — add the sandbox guard

Replace lines 120-150 (from `def get_workspace` through the end of `delete_workspace`) with:

```python
def get_workspace(workspace_id: str) -> Optional[Workspace]:
    for w in list_workspaces():
        if w.id == workspace_id:
            return w
    return None


def _resolve_within_workspaces(ws_id: str) -> Path:
    """Resolve ws_id under _workspaces_dir() and refuse a path that would
    escape the sandbox via '/', '\\', '..', or an absolute path.

    Proven exploit (pre-launch audit): POST /api/workspaces
    {"name": "../../../../Temp/x"} created a real directory outside
    ~/.model-hub/workspaces (invisible to list_workspaces(), which only
    lists direct children). delete_workspace() had the identical
    unsanitized join before shutil.rmtree() -- an equally real arbitrary
    recursive-delete primitive. Raises ValueError if ws_id would not land
    strictly inside _workspaces_dir().
    """
    base = _workspaces_dir().resolve()
    candidate = (base / ws_id).resolve()
    if candidate == base or base not in candidate.parents:
        raise ValueError(f"invalid workspace id: {ws_id!r}")
    return candidate


def create_workspace(name: str, description: str = "") -> Workspace:
    import time
    ws_id = name.lower().replace(" ", "-").replace("_", "-")
    ws_dir = _resolve_within_workspaces(ws_id)
    ws_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    meta = ws_dir / "workspace.json"
    meta.write_text(json.dumps({
        "name": name,
        "description": description,
        "created_at": now,
    }, indent=2))
    return Workspace(id=ws_id, name=name, description=description, created_at=now)


def delete_workspace(workspace_id: str) -> bool:
    if workspace_id == DEFAULT_WORKSPACE:
        return False
    try:
        ws_dir = _resolve_within_workspaces(workspace_id)
    except ValueError:
        return False
    if ws_dir.exists():
        import shutil
        shutil.rmtree(ws_dir)
        return True
    return False
```

- [ ] Step 5: Fix `backend/api.py` — catch the traversal `ValueError` in the create route

Replace lines 703-711:

```python
@app.route("/api/workspaces", methods=["POST"])
def api_create_workspace():
    from .cookbook.config import create_workspace
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Workspace name required"}), 400
    ws = create_workspace(name, data.get("description", ""))
    return jsonify({"id": ws.id, "name": ws.name, "description": ws.description}), 201
```

with:

```python
@app.route("/api/workspaces", methods=["POST"])
def api_create_workspace():
    from .cookbook.config import create_workspace
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Workspace name required"}), 400
    try:
        ws = create_workspace(name, data.get("description", ""))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"id": ws.id, "name": ws.name, "description": ws.description}), 201
```

- [ ] Step 6: Fix `cli.py` — catch the traversal `ValueError` in the CLI create action

Replace lines 944-951:

```python
    elif args.action == "create":
        name = args.name
        desc = args.description or ""
        if not name:
            eprint(f"{C['red']}Workspace name required.{C['reset']}")
            sys.exit(1)
        ws = create_workspace(name, desc)
        print(f"{C['green']}✓ Created workspace '{ws.name}' (id: {ws.id}){C['reset']}")
```

with:

```python
    elif args.action == "create":
        name = args.name
        desc = args.description or ""
        if not name:
            eprint(f"{C['red']}Workspace name required.{C['reset']}")
            sys.exit(1)
        try:
            ws = create_workspace(name, desc)
        except ValueError as e:
            eprint(f"{C['red']}{e}{C['reset']}")
            sys.exit(1)
        print(f"{C['green']}✓ Created workspace '{ws.name}' (id: {ws.id}){C['reset']}")
```

- [ ] Step 7: Run tests to verify they pass

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_workspace_path_safety.py`
Expected: all 6 tests PASS.

Then run the full suite to confirm no regressions: `.venv\Scripts\python.exe -m pytest -q`
Expected: all tests pass (baseline was green before this task).

Note: this fix is in the running-server's code path (`/api/workspaces` POST/DELETE) — if the live server at `127.0.0.1:5050` is still running, it needs a restart to serve the fixed behavior. Do not restart it yourself.

- [ ] Step 8: Commit

```bash
cd "C:\Users\User\repos\model-hub"
git add backend/cookbook/config.py backend/api.py cli.py tests/test_workspace_path_safety.py
git commit -m "fix(security): sandbox workspace create/delete against path traversal

POST /api/workspaces and workspace create/delete could escape
~/.model-hub/workspaces via '/', '\\', or '..' in the name/id, both for
mkdir (proven) and shutil.rmtree (identical unsanitized join, treated as
equally real). Both routes now resolve through a shared guard that
refuses anything outside the sandbox."
```

---

### Task 2: Security — Pro license activate/deactivate blocked by Cloudflare

**Files:**
- Modify: `lac_pro/ls.py:28-53` (`_json_post`)
- Test: `lac_pro/tests/test_ls.py` (append)

**Interfaces:**
- Consumes: none new
- Produces: `_json_post` now sends a real `User-Agent` header (`"LAC-Pro/2.2.0"`) on every Polar.sh request; `activate()`/`validate()`/`deactivate()` signatures unchanged.

- [ ] Step 1: Write the failing test

Append to `C:\Users\User\repos\lac-pro\tests\test_ls.py`:

```python
def test_json_post_sets_a_real_user_agent(monkeypatch):
    """Polar.sh's Cloudflare WAF hard-blocks (403) requests using Python's
    default urllib User-Agent before they ever reach Polar's API. A real
    User-Agent header must be present on every request."""
    import lac_pro.ls as ls_mod

    captured = {}

    class FakeResponse:
        status = 200

        def read(self):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=5):
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        return FakeResponse()

    monkeypatch.setattr(ls_mod.urllib.request, "urlopen", fake_urlopen)

    ls_mod._json_post(f"{ls_mod.POLAR_BASE}/validate", {"key": "x"})

    ua = captured["headers"].get("user-agent")
    assert ua is not None and ua != ""
    assert "python-urllib" not in ua.lower()
    assert ua == "LAC-Pro/2.2.0"
```

- [ ] Step 2: Run test to verify it fails

Run: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q tests/test_ls.py::test_json_post_sets_a_real_user_agent` (from `C:\Users\User\repos\lac-pro`)
Expected: FAIL — `assert ua is not None and ua != ""` fails because no `User-Agent` header is set (`captured["headers"]` has no `"user-agent"` key, so `ua` is `None`).

- [ ] Step 3: Fix `lac_pro/ls.py`

Replace lines 28-53:

```python
def _json_post(url: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Accept": "application/json",
                 "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            raw = r.read()
            # 204 No Content (deactivate) — return empty dict, not an error.
            if r.status == 204:
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        # Polar returns JSON bodies on 4xx (invalid key, etc.) — read them.
        try:
            raw = e.read()
            if e.code == 204:
                return {}
            return json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            raise LsError(f"HTTP {e.code} with unreadable body") from exc
    except Exception as exc:  # noqa: BLE001 — DNS, timeout, refused…
        raise LsError(str(exc)) from exc
```

with:

```python
def _json_post(url: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Accept": "application/json",
                 "Content-Type": "application/json",
                 # Polar.sh's Cloudflare WAF hard-blocks Python's default
                 # urllib User-Agent (403) before the request ever reaches
                 # Polar's API. A real User-Agent is required.
                 "User-Agent": "LAC-Pro/2.2.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            raw = r.read()
            # 204 No Content (deactivate) — return empty dict, not an error.
            if r.status == 204:
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        # Polar returns JSON bodies on 4xx (invalid key, etc.) — read them.
        try:
            raw = e.read()
            if e.code == 204:
                return {}
            return json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            raise LsError(f"HTTP {e.code} with unreadable body") from exc
    except Exception as exc:  # noqa: BLE001 — DNS, timeout, refused…
        raise LsError(str(exc)) from exc
```

- [ ] Step 4: Run test to verify it passes

Run: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q tests/test_ls.py` (from `C:\Users\User\repos\lac-pro`)
Expected: all tests in `test_ls.py` PASS (5 existing + 1 new = 6).

Then run the full lac-pro suite: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q` (from `C:\Users\User\repos\lac-pro`)
Expected: all 61 tests pass (60 baseline + 1 new).

- [ ] Step 5: Commit

```bash
cd "C:\Users\User\repos\lac-pro"
git add lac_pro/ls.py tests/test_ls.py
git commit -m "fix(security): send a real User-Agent to Polar.sh (Cloudflare was blocking activate/deactivate for every real user)"
```

---

### Task 3: CLI Unicode crash on write-paths

**Files:**
- Modify: `cli.py:1151-1154` (`main()`)
- Test: `tests/test_cli_encoding.py` (new)

**Interfaces:**
- Consumes: none new
- Produces: `main()` now reconfigures `sys.stdout`/`sys.stderr` to UTF-8 with `errors="replace"` before any other output happens.

- [ ] Step 1: Write the failing test

Create `tests/test_cli_encoding.py`:

```python
from __future__ import annotations

import os
import subprocess
import sys


def test_cli_survives_a_narrow_console_codepage(tmp_path):
    """Windows' default console codepage (cp1252) can't encode the '✓'
    glyph cli.py prints on every success line (config set, workspace
    create/delete/switch, delete) or the progress-bar block glyphs
    ('█'/'░') during `lac pull`. Force PYTHONIOENCODING=cp1252 (simulating
    an un-reconfigured console) and confirm `lac config set` still exits 0
    and prints its success line -- the crash the audit found happens AFTER
    the action already succeeded, so a raw crash here would be doubly bad."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp1252"
    env["HOME"] = str(tmp_path)
    env["USERPROFILE"] = str(tmp_path)

    r = subprocess.run(
        [sys.executable, "-m", "cli", "config", "set", "theme", "dark"],
        capture_output=True, encoding="utf-8", errors="replace",
        timeout=15, env=env,
    )
    assert r.returncode == 0
    assert "Set theme = dark" in r.stdout
```

- [ ] Step 2: Run test to verify it fails

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_cli_encoding.py` (from `C:\Users\User\repos\model-hub`)
Expected: FAIL — subprocess exits with a non-zero return code (`1`) and a `UnicodeEncodeError` traceback on the `'✓'` (✓) character appears in `r.stderr`.

- [ ] Step 3: Fix `cli.py`

Replace lines 1151-1154 (the start of `main()`):

```python
def main():
    print_banner()
    parser = build_parser()
    args = parser.parse_args()
```

with:

```python
def main():
    # Windows' default console codepage (cp1252) can't encode glyphs like
    # '✓' (success lines) or '█'/'░' (the pull progress bar), crashing
    # commands AFTER their action already succeeded. Force UTF-8 with a
    # lossy fallback rather than hunting down every current and future
    # glyph.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    print_banner()
    parser = build_parser()
    args = parser.parse_args()
```

- [ ] Step 4: Run test to verify it passes

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_cli_encoding.py`
Expected: PASS.

Then run the full suite: `.venv\Scripts\python.exe -m pytest -q`
Expected: all tests pass.

This fix is CLI-only (`cli.py`'s `main()` entry point) — it does not affect the running web server process, so no server restart consideration applies.

- [ ] Step 5: Commit

```bash
cd "C:\Users\User\repos\model-hub"
git add cli.py tests/test_cli_encoding.py
git commit -m "fix: reconfigure CLI stdout/stderr to UTF-8 so success glyphs don't crash on cp1252 consoles"
```

---

### Task 4: `lac browse` always returns zero results + `--top-k` validation

**Files:**
- Create: `backend/cookbook/library.py`
- Modify: `backend/api.py:514-608` (`api_library_browse` — replace the inline enrichment block with a call to the new shared helper)
- Modify: `cli.py:860-925` (`cmd_browse` — enrich before filtering) and `cli.py:805-857` (`cmd_recommend` — validate `--top-k`)
- Test: `tests/test_library_enrich.py` (new), `tests/test_cli_browse_recommend.py` (new)

**Interfaces:**
- Consumes: `backend.cookbook.recommend.load_models() -> list[ModelEntry]` (existing)
- Produces: `backend.cookbook.library.enrich_library_models(models: list[dict], system_vram: float | None) -> list[dict]` — mutates and returns `models`, adding `vram_q4`/`params_b`/`fit` keys. Both `api_library_browse` and `cmd_browse` call this before filtering/sorting.

- [ ] Step 1: Write the failing tests

Create `tests/test_library_enrich.py`:

```python
from __future__ import annotations

from backend.cookbook.library import enrich_library_models


def test_enrich_matches_catalog_family_and_sets_vram_q4():
    models = [{"name": "qwen3", "description": "", "capabilities": [],
               "sizes": ["8B"], "pulls": "1M", "tag_count": "5"}]
    out = enrich_library_models(models, system_vram=16.0)
    assert out[0]["vram_q4"] > 0
    assert out[0]["fit"] in ("gpu", "offload", "too_big")


def test_enrich_falls_back_to_advertised_size_when_no_catalog_match():
    models = [{"name": "totally-unknown-model", "description": "",
               "capabilities": [], "sizes": ["7B"], "pulls": "0", "tag_count": "1"}]
    out = enrich_library_models(models, system_vram=16.0)
    assert out[0]["vram_q4"] > 0
    assert out[0]["params_b"] == 7.0


def test_enrich_unknown_without_sizes_gets_unknown_fit():
    models = [{"name": "mystery", "description": "", "capabilities": [],
               "sizes": [], "pulls": "0", "tag_count": "0"}]
    out = enrich_library_models(models, system_vram=16.0)
    assert out[0]["fit"] == "unknown"
    assert "vram_q4" not in out[0]
```

Create `tests/test_cli_browse_recommend.py`:

```python
from __future__ import annotations

import pytest


def test_cli_browse_returns_results_against_real_cache(monkeypatch, capsys):
    """The headline bug: cmd_browse filtered models = [m for m in models if
    m.get("vram_q4", 0) > 0] against the RAW scraped library_cache.json,
    which has no vram_q4 field at all -- nuking the entire 236-model
    result set every time. This runs against the real on-disk cache."""
    import cli as cli_mod
    import backend.cookbook.hardware as hw_mod
    from backend.cookbook.hardware import SystemInfo, GPUInfo

    monkeypatch.setattr(hw_mod, "detect", lambda: SystemInfo(
        os="Test", cpu="Test", cpu_cores=8, ram_gb=32.0,
        gpus=[GPUInfo("Test GPU", 16.0, backend="cuda")], total_vram_gb=16.0,
    ))

    parser = cli_mod.build_parser()
    args = parser.parse_args(["browse", "qwen"])
    cli_mod.cmd_browse(args)

    out = capsys.readouterr().out
    assert "Model Library (0 variants)" not in out
    assert "GB Q4" in out


def test_cli_recommend_rejects_zero_top_k(capsys):
    import cli as cli_mod

    parser = cli_mod.build_parser()
    args = parser.parse_args(["recommend", "--top-k", "0"])
    with pytest.raises(SystemExit) as e:
        cli_mod.cmd_recommend(args)
    assert e.value.code == 1
    assert "--top-k must be a positive integer" in capsys.readouterr().err


def test_cli_recommend_rejects_negative_top_k(capsys):
    import cli as cli_mod

    parser = cli_mod.build_parser()
    args = parser.parse_args(["recommend", "--top-k", "-5"])
    with pytest.raises(SystemExit) as e:
        cli_mod.cmd_recommend(args)
    assert e.value.code == 1
```

- [ ] Step 2: Run tests to verify they fail

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_library_enrich.py tests/test_cli_browse_recommend.py` (from `C:\Users\User\repos\model-hub`)
Expected: `tests/test_library_enrich.py` FAILS to even collect (`ModuleNotFoundError: No module named 'backend.cookbook.library'`). `test_cli_browse_returns_results_against_real_cache` FAILS (`Model Library (0 variants)` IS in the output). `test_cli_recommend_rejects_zero_top_k`/`_negative_top_k` FAIL (no `SystemExit` raised — `--top-k 0` silently becomes 10, `--top-k -5` silently slices).

- [ ] Step 3: Create `backend/cookbook/library.py`

```python
"""Shared library-browse enrichment: cross-reference scraped Ollama
library entries against the curated catalog to populate real VRAM/params
and a hardware-fit verdict. Used by both the web API's
/api/library/browse route and the CLI's `lac browse` command so there is
exactly one implementation of "what does this library entry look like on
my hardware" (the CLI path previously duplicated nothing -- it just
skipped enrichment entirely, which is why every result got filtered out)."""
from __future__ import annotations

import re
from typing import Optional

from .recommend import load_models


def enrich_library_models(models: list[dict], system_vram: Optional[float]) -> list[dict]:
    """Mutate + return models in place, adding vram_q4/params_b/fit fields
    from the curated catalog (or a rough estimate from advertised sizes
    when there's no catalog match)."""
    catalog_by_family: dict[str, list] = {}
    try:
        for cm in load_models():
            catalog_by_family.setdefault(cm.id.split(":")[0], []).append(cm)
    except Exception:
        pass

    sv = system_vram or 0
    for m in models:
        fam = m.get("name", "")
        variants = catalog_by_family.get(fam)
        if variants:
            variants = sorted(variants, key=lambda v: v.vram_q4 or 0)
            fitting = [v for v in variants if (v.vram_q4 or 0) <= sv * 0.9]
            if fitting:
                rep = fitting[-1]
                m["fit"] = "gpu"
            else:
                rep = variants[0]
                m["fit"] = "offload" if (rep.vram_q4 or 0) <= sv * 2 else "too_big"
            m["vram_q4"] = rep.vram_q4
            m["params_b"] = rep.params_b
        elif m.get("sizes"):
            # No catalog match — rough estimate from advertised sizes (e.g. "3B").
            try:
                pb = float(re.sub(r"[^0-9.]", "", str(m["sizes"][0])) or 0)
                if pb:
                    vq4 = round(pb * 0.6, 1)
                    m["params_b"] = pb
                    m["vram_q4"] = vq4
                    if sv:
                        m["fit"] = "gpu" if vq4 <= sv * 0.9 else ("offload" if vq4 <= sv * 2 else "too_big")
                    else:
                        m["fit"] = "unknown"
                else:
                    m["fit"] = "unknown"
            except Exception:
                m["fit"] = "unknown"
        else:
            m["fit"] = "unknown"
    return models
```

- [ ] Step 4: Wire the shared helper into `backend/api.py`

Replace lines 525-575 of `api_library_browse` (from `# Always detect system VRAM...` through the end of the enrichment `for m in models:` loop):

```python
    # Always detect system VRAM so every card can show a real fit verdict.
    system_vram = None
    try:
        info = detect()
        system_vram = info.total_vram_gb or (info.gpus[0].vram_gb if info.gpus else 0)
    except Exception:
        system_vram = None

    # Cross-reference each library family against the curated catalog (the cookbook)
    # to populate real VRAM/params and a hardware fit verdict.
    catalog_by_family: dict[str, list] = {}
    try:
        for cm in load_models():
            catalog_by_family.setdefault(cm.id.split(":")[0], []).append(cm)
    except Exception:
        pass

    sv = system_vram or 0
    for m in models:
        fam = m.get("name", "")
        variants = catalog_by_family.get(fam)
        if variants:
            variants = sorted(variants, key=lambda v: v.vram_q4 or 0)
            fitting = [v for v in variants if (v.vram_q4 or 0) <= sv * 0.9]
            if fitting:
                rep = fitting[-1]
                m["fit"] = "gpu"
            else:
                rep = variants[0]
                m["fit"] = "offload" if (rep.vram_q4 or 0) <= sv * 2 else "too_big"
            m["vram_q4"] = rep.vram_q4
            m["params_b"] = rep.params_b
        elif m.get("sizes"):
            # No catalog match — rough estimate from advertised sizes (e.g. "3B").
            try:
                pb = float(re.sub(r"[^0-9.]", "", str(m["sizes"][0])) or 0)
                if pb:
                    vq4 = round(pb * 0.6, 1)
                    m["params_b"] = pb
                    m["vram_q4"] = vq4
                    if sv:
                        m["fit"] = "gpu" if vq4 <= sv * 0.9 else ("offload" if vq4 <= sv * 2 else "too_big")
                    else:
                        m["fit"] = "unknown"
                else:
                    m["fit"] = "unknown"
            except Exception:
                m["fit"] = "unknown"
        else:
            m["fit"] = "unknown"
```

with:

```python
    # Always detect system VRAM so every card can show a real fit verdict.
    system_vram = None
    try:
        info = detect()
        system_vram = info.total_vram_gb or (info.gpus[0].vram_gb if info.gpus else 0)
    except Exception:
        system_vram = None

    # Cross-reference each library family against the curated catalog to
    # populate real VRAM/params and a hardware fit verdict (shared with the
    # CLI's `lac browse`, which uses the exact same enrichment).
    from .cookbook.library import enrich_library_models
    models = enrich_library_models(models, system_vram)
```

- [ ] Step 5: Fix `cli.py`'s `cmd_browse` — enrich before filtering

Replace lines 883-891:

```python
    if not models:
        eprint(f"{C['yellow']}No model catalog available.{C['reset']}")
        sys.exit(1)

    if query:
        q = query.lower()
        models = [m for m in models if q in m.get("display", m.get("name", "")).lower() or q in m.get("description", "").lower()]

    models = [m for m in models if m.get("vram_q4", 0) > 0]
```

with:

```python
    if not models:
        eprint(f"{C['yellow']}No model catalog available.{C['reset']}")
        sys.exit(1)

    system_vram = None
    try:
        from backend.cookbook.hardware import detect
        info = detect()
        system_vram = info.total_vram_gb or (info.gpus[0].vram_gb if info.gpus else 0)
    except Exception:
        system_vram = None

    from backend.cookbook.library import enrich_library_models
    models = enrich_library_models(models, system_vram)

    if query:
        q = query.lower()
        models = [m for m in models if q in m.get("display", m.get("name", "")).lower() or q in m.get("description", "").lower()]

    models = [m for m in models if m.get("vram_q4", 0) > 0]
```

- [ ] Step 6: Fix `cli.py`'s `cmd_recommend` — validate `--top-k` before the (expensive, real-subprocess) hardware scan

Replace lines 808-824:

```python
    try:
        from backend.cookbook.hardware import detect
        from backend.cookbook.recommend import recommend

        print(f"{C['yellow']}Scanning hardware and computing recommendations...{C['reset']}")
        info = detect()
        use_case = args.use_case or "coding"

        if getattr(args, "no_calibration", False):
            _cal = None
        else:
            from backend.cookbook.calibration import load_calibration, detect_stack
            _stack = detect_stack(info=info)
            _results = str(Path.home() / ".model-hub" / "benchmarks" / "results.jsonl")
            _cal = load_calibration(info, _stack, _results)

        recs = recommend(info, use_case=use_case, top_k=args.top_k or 10, calibration=_cal)
```

with:

```python
    try:
        from backend.cookbook.hardware import detect
        from backend.cookbook.recommend import recommend

        top_k = args.top_k if args.top_k is not None else 10
        if top_k < 1:
            eprint(f"{C['red']}--top-k must be a positive integer (got {top_k}).{C['reset']}")
            sys.exit(1)

        print(f"{C['yellow']}Scanning hardware and computing recommendations...{C['reset']}")
        info = detect()
        use_case = args.use_case or "coding"

        if getattr(args, "no_calibration", False):
            _cal = None
        else:
            from backend.cookbook.calibration import load_calibration, detect_stack
            _stack = detect_stack(info=info)
            _results = str(Path.home() / ".model-hub" / "benchmarks" / "results.jsonl")
            _cal = load_calibration(info, _stack, _results)

        recs = recommend(info, use_case=use_case, top_k=top_k, calibration=_cal)
```

Validating before `detect()` also means a bad `--top-k` fails fast instead of first paying for a real hardware scan (`detect()` shells out to PowerShell on Windows).

- [ ] Step 7: Run tests to verify they pass

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_library_enrich.py tests/test_cli_browse_recommend.py`
Expected: all 6 tests PASS.

Then run the full suite: `.venv\Scripts\python.exe -m pytest -q`
Expected: all tests pass (this refactor also changes `api_library_browse`'s internals — confirm no regression in any existing `/api/library/browse` behavior, though no existing test covers that route directly).

The web-facing `/api/library/browse` behavior is unchanged (same output, just refactored) — if the live server is running, a restart is optional for that route, but IS required to pick up the `cmd_browse`/`cmd_recommend` CLI fixes (those only matter for a fresh `lac browse`/`lac recommend` process invocation, not the running server).

- [ ] Step 8: Commit

```bash
cd "C:\Users\User\repos\model-hub"
git add backend/cookbook/library.py backend/api.py cli.py tests/test_library_enrich.py tests/test_cli_browse_recommend.py
git commit -m "fix: lac browse always returned zero results (never enriched vram_q4 before filtering); validate --top-k

Extracted the web route's catalog-enrichment logic into a shared
backend/cookbook/library.py so cmd_browse can use the same enrichment
instead of duplicating it (or, as before, skipping it entirely). Also
rejects --top-k 0/negative instead of silently falling back to 10 or
slicing with negative indices."
```

---

### Task 5: Workspace switching 500

**Files:**
- Modify: `backend/api.py:731-736` (`api_switch_workspace`)
- Test: `tests/test_api.py` (append)

**Interfaces:**
- Consumes: `backend.cookbook.config.switch_workspace(workspace_id: str) -> bool` (existing, unchanged)
- Produces: `POST /api/workspaces/<workspace_id>/switch` now returns 200/404 instead of raising

- [ ] Step 1: Write the failing tests

Append to `tests/test_api.py`:

```python
def test_switch_workspace_succeeds_for_valid_id(flask_app, isolated_home):
    client = flask_app.test_client()
    client.get("/api/workspaces")  # ensures the default workspace exists on disk
    r = client.post("/api/workspaces/default/switch")
    assert r.status_code == 200
    assert r.get_json() == {"success": True, "workspace": "default"}


def test_switch_workspace_404_for_unknown_id(flask_app, isolated_home):
    client = flask_app.test_client()
    r = client.post("/api/workspaces/does-not-exist/switch")
    assert r.status_code == 404
```

- [ ] Step 2: Run tests to verify they fail

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_api.py::test_switch_workspace_succeeds_for_valid_id tests/test_api.py::test_switch_workspace_404_for_unknown_id`
Expected: both FAIL — the view function raises `ImportError: cannot import name 'list_sessions' from 'backend.cookbook.config'`, which surfaces as an unhandled exception in Flask's `TESTING=True` mode rather than a 200/404 response.

- [ ] Step 3: Fix `backend/api.py`

Replace lines 731-736:

```python
@app.route("/api/workspaces/<workspace_id>/switch", methods=["POST"])
def api_switch_workspace(workspace_id):
    from .cookbook.config import switch_workspace, list_sessions
    if not switch_workspace(workspace_id):
        return jsonify({"error": "Workspace not found"}), 404
    return jsonify({"success": True, "workspace": workspace_id})
```

with:

```python
@app.route("/api/workspaces/<workspace_id>/switch", methods=["POST"])
def api_switch_workspace(workspace_id):
    from .cookbook.config import switch_workspace
    if not switch_workspace(workspace_id):
        return jsonify({"error": "Workspace not found"}), 404
    return jsonify({"success": True, "workspace": workspace_id})
```

- [ ] Step 4: Run tests to verify they pass

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_api.py`
Expected: all tests in `test_api.py` PASS.

This fix is in the running server's request path — the live server at `127.0.0.1:5050` needs a restart to serve the fix.

- [ ] Step 5: Commit

```bash
cd "C:\Users\User\repos\model-hub"
git add backend/api.py tests/test_api.py
git commit -m "fix: workspace switching 500s on every call (dead import of nonexistent list_sessions from cookbook.config)"
```

---

### Task 6: Downloads page permanently empty for web-UI installs

**Files:**
- Create: `backend/cookbook/downloads.py`
- Modify: `cli.py:118-151` (`_log_download`/`_download_history` — delegate to the new shared module)
- Modify: `backend/api.py:243-282` (`ollama_pull`'s `generate()` — log on success) and `backend/api.py:652-669` (`api_config_downloads` — read via the shared module)
- Test: `tests/test_downloads_history.py` (new)

**Interfaces:**
- Consumes: none new
- Produces: `backend.cookbook.downloads.log_download(model_name: str, status: str = "completed", size_gb: float = 0) -> None` and `backend.cookbook.downloads.download_history() -> list[dict]`. Both `cli.py`'s CLI pull path and `backend/api.py`'s web install path now write to (and read from) the exact same `~/.model-hub/downloads/history.jsonl`.

- [ ] Step 1: Write the failing test

Create `tests/test_downloads_history.py`:

```python
from __future__ import annotations

import json


def test_ollama_pull_web_install_logs_download_history(monkeypatch, flask_app, isolated_home):
    """The web UI's install path (POST /api/ollama/pull) must feed the same
    history.jsonl file the CLI's `lac pull` writes -- otherwise the
    Downloads page is permanently empty for anyone who only ever installs
    through the web UI (confirmed: only cli.py's _log_download ever wrote
    to that file; api.py's install path only fired the plugin hook)."""
    import urllib.request as real_urllib_request
    from backend import api as api_mod

    class FakeResp:
        def __iter__(self):
            lines = [
                json.dumps({"status": "pulling manifest"}).encode(),
                json.dumps({"status": "downloading", "completed": 500, "total": 1000}).encode(),
                json.dumps({"status": "success"}).encode(),
            ]
            return iter(l + b"\n" for l in lines)

    monkeypatch.setattr(real_urllib_request, "urlopen", lambda req, timeout=3600: FakeResp())
    monkeypatch.setattr(api_mod, "_notify_model_installed_async", lambda model_name: None)

    client = flask_app.test_client()
    r = client.post("/api/ollama/pull", json={"model": "qwen3:0.6b"})
    assert r.status_code == 200

    r2 = client.get("/api/config/downloads")
    entries = r2.get_json()
    assert any(
        e["model"] == "qwen3:0.6b" and e["status"] == "completed" and e["size_gb"] > 0
        for e in entries
    )


def test_log_download_and_history_round_trip(isolated_home):
    from backend.cookbook.downloads import log_download, download_history

    log_download("llama3.2:3b", "completed", 1.9)
    history = download_history()
    assert any(e["model"] == "llama3.2:3b" and e["size_gb"] == 1.9 for e in history)


def test_download_history_empty_when_no_log_file(isolated_home):
    from backend.cookbook.downloads import download_history

    assert download_history() == []
```

- [ ] Step 2: Run test to verify it fails

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_downloads_history.py`
Expected: `ModuleNotFoundError: No module named 'backend.cookbook.downloads'` (collection failure — the module doesn't exist yet).

- [ ] Step 3: Create `backend/cookbook/downloads.py`

```python
"""Shared download-history logging, used by both the CLI (`lac pull`) and
the web API's install path (POST /api/ollama/pull) so both entry points
feed the same ~/.model-hub/downloads/history.jsonl file. Before this
module existed, only the CLI's own copy of this logic ever wrote to that
file -- the web UI's install button never called it, so the Downloads
page was permanently empty for anyone who only ever installs via the web
UI."""
from __future__ import annotations

import json
import time
from pathlib import Path

CONFIG_DIR = Path.home() / ".model-hub"


def log_download(model_name: str, status: str = "completed", size_gb: float = 0) -> None:
    try:
        log_dir = CONFIG_DIR / "downloads"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "history.jsonl"
        entry = {
            "model": model_name,
            "status": status,
            "size_gb": size_gb,
            "timestamp": time.time(),
        }
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def download_history() -> list[dict]:
    log_file = CONFIG_DIR / "downloads" / "history.jsonl"
    if not log_file.exists():
        return []
    history = []
    try:
        with open(log_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        history.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception:
        pass
    return history
```

- [ ] Step 4: Delegate `cli.py`'s existing functions to the shared module

Replace lines 118-151 (`_log_download` and `_download_history`):

```python
def _log_download(model_name: str, status: str = "completed", size_gb: float = 0):
    try:
        log_dir = Path.home() / ".model-hub" / "downloads"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "history.jsonl"
        entry = {
            "model": model_name,
            "status": status,
            "size_gb": size_gb,
            "timestamp": time.time(),
        }
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def _download_history() -> list[dict]:
    log_file = Path.home() / ".model-hub" / "downloads" / "history.jsonl"
    if not log_file.exists():
        return []
    history = []
    try:
        with open(log_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        history.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception:
        pass
    return history
```

with:

```python
def _log_download(model_name: str, status: str = "completed", size_gb: float = 0):
    from backend.cookbook.downloads import log_download
    log_download(model_name, status, size_gb)


def _download_history() -> list[dict]:
    from backend.cookbook.downloads import download_history
    return download_history()
```

- [ ] Step 5: Fix `backend/api.py`'s `ollama_pull()` to log on success

Replace lines 243-282:

```python
@app.route("/api/ollama/pull", methods=["POST"])
def ollama_pull():
    data = request.get_json()
    model_name = data.get("model", "")
    if not model_name:
        return jsonify({"error": "No model specified"}), 400

    def generate():
        import urllib.request
        import urllib.error
        url = f"{OLLAMA_HOST}/api/pull"
        body = json.dumps({"name": model_name}).encode()
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
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
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
```

with:

```python
@app.route("/api/ollama/pull", methods=["POST"])
def ollama_pull():
    data = request.get_json()
    model_name = data.get("model", "")
    if not model_name:
        return jsonify({"error": "No model specified"}), 400

    def generate():
        import urllib.request
        import urllib.error
        from .cookbook.downloads import log_download
        url = f"{OLLAMA_HOST}/api/pull"
        body = json.dumps({"name": model_name}).encode()
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        last_total = 0
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
                    if chunk.get("total"):
                        last_total = chunk["total"]
                    if chunk.get("status") == "success":
                        size_gb = round(last_total / (1024**3), 2) if last_total else 0
                        log_download(model_name, "completed", size_gb)
                        _notify_model_installed_async(model_name)
        except urllib.error.HTTPError as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
```

- [ ] Step 6: Fix `backend/api.py`'s `api_config_downloads` to read via the shared module

Replace lines 652-669:

```python
@app.route("/api/config/downloads")
def api_config_downloads():
    log_file = Path.home() / ".model-hub" / "downloads" / "history.jsonl"
    if not log_file.exists():
        return jsonify([])
    entries = []
    try:
        with open(log_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception:
        pass
    return jsonify(entries)
```

with:

```python
@app.route("/api/config/downloads")
def api_config_downloads():
    from .cookbook.downloads import download_history
    return jsonify(download_history())
```

- [ ] Step 7: Run tests to verify they pass

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_downloads_history.py`
Expected: all 3 tests PASS.

Then run the full suite: `.venv\Scripts\python.exe -m pytest -q`
Expected: all tests pass.

This fix changes the live install path (`POST /api/ollama/pull`) — the running server needs a restart for web-UI installs to start being logged.

- [ ] Step 8: Commit

```bash
cd "C:\Users\User\repos\model-hub"
git add backend/cookbook/downloads.py cli.py backend/api.py tests/test_downloads_history.py
git commit -m "fix: web-UI model installs never wrote to download history (only the CLI's pull path did)

Extracted the logging/reading logic into a shared backend/cookbook/downloads.py
so both entry points feed the same ~/.model-hub/downloads/history.jsonl.
Also tracks the last real 'total' seen across the pull stream instead of
re-reading it off the terminal 'success' chunk (which Ollama never
populates), so download size is no longer always 0."
```

---

### Task 7: `lac pro tune`/`lac pro benchmark` crash with raw traceback on a nonexistent model

**Files:**
- Modify: `lac_pro/tune.py:1-16` (imports) and `lac_pro/tune.py:95-117` (`cmd_tune`)
- Modify: `lac_pro/benchmark_cli.py:1-13` (imports) and `lac_pro/benchmark_cli.py:15-45` (`cmd_benchmark`)
- Test: `lac_pro/tests/test_tune.py` (append), `lac_pro/tests/test_benchmark_cli.py` (append)

**Interfaces:**
- Consumes: `urllib.error.HTTPError` (stdlib)
- Produces: `cmd_tune`/`cmd_benchmark` now catch `urllib.error.HTTPError` at the CLI-command level and exit(1) with a clean message, instead of propagating a raw traceback. `autopilot.py`'s own `run_autopilot()` hook path is untouched (it already degrades to `failed_silent` for the same underlying error).

- [ ] Step 1: Write the failing tests

Append to `C:\Users\User\repos\lac-pro\tests\test_tune.py`:

```python
def test_cli_tune_model_not_found_prints_clean_error(monkeypatch, capsys):
    """HTTP 404 from Ollama (nonexistent model) must not crash with a raw
    traceback -- autopilot.py's own hook path already degrades silently for
    this exact error; only the manual CLI command wrapper was missing the
    same handling."""
    import argparse
    import urllib.error
    from lac_pro.plugin import PLUGIN
    monkeypatch.setenv("LAC_PRO_DEV", "1")

    def raising_show(model):
        raise urllib.error.HTTPError("http://x", 404, "Not Found", {}, None)

    monkeypatch.setattr(tune_mod, "http_show", raising_show)

    parser = argparse.ArgumentParser(prog="lac")
    sub = parser.add_subparsers(dest="command")
    PLUGIN.register_cli(sub)
    args = parser.parse_args(["pro", "tune", "does-not-exist:1b"])
    with pytest.raises(SystemExit) as e:
        args.func(args)
    assert e.value.code == 1

    captured = capsys.readouterr()
    assert "does-not-exist:1b" in captured.out
    assert "Traceback" not in captured.out and "Traceback" not in captured.err
```

Append to `C:\Users\User\repos\lac-pro\tests\test_benchmark_cli.py`:

```python
def test_benchmark_model_not_found_prints_clean_error(monkeypatch, capsys, tmp_path):
    import urllib.error

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LAC_PRO_DEV", "1")

    import lac_pro.autopilot as autopilot_mod

    def raising_generate(model, prompt, options, num_predict):
        raise urllib.error.HTTPError("http://x", 404, "Not Found", {}, None)

    monkeypatch.setattr(autopilot_mod, "http_generate", raising_generate)

    parser, sub = _build_sub()
    PLUGIN.register_cli(sub)
    args = parser.parse_args(["pro", "benchmark", "does-not-exist:1b"])
    with pytest.raises(SystemExit) as e:
        args.func(args)
    assert e.value.code == 1

    captured = capsys.readouterr()
    assert "does-not-exist:1b" in captured.out
    assert "Traceback" not in captured.out and "Traceback" not in captured.err
```

- [ ] Step 2: Run tests to verify they fail

Run: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q tests/test_tune.py::test_cli_tune_model_not_found_prints_clean_error tests/test_benchmark_cli.py::test_benchmark_model_not_found_prints_clean_error` (from `C:\Users\User\repos\lac-pro`)
Expected: both FAIL — `urllib.error.HTTPError` propagates out of `args.func(args)` uncaught (pytest reports it as the test's own exception, not a `SystemExit`).

- [ ] Step 3: Fix `lac_pro/tune.py`

Add `import urllib.error` to the top imports (line 11, alongside the existing `import urllib.request`):

Replace line 11:

```python
import urllib.request
```

with:

```python
import urllib.error
import urllib.request
```

Then replace lines 95-98 (the start of `cmd_tune`):

```python
def cmd_tune(args) -> None:
    require("tune")
    print(f"Tuning {args.model} — sweeping offload configs (repeat={args.repeat})…")
    out = run_sweep(args.model, http_generate, http_show, repeat=args.repeat)
```

with:

```python
def cmd_tune(args) -> None:
    require("tune")
    print(f"Tuning {args.model} — sweeping offload configs (repeat={args.repeat})…")
    try:
        out = run_sweep(args.model, http_generate, http_show, repeat=args.repeat)
    except urllib.error.HTTPError as e:
        print(f"Model not found or Ollama error: {args.model} (HTTP {e.code}). "
              f"Is it installed? Try `ollama pull {args.model}`.")
        raise SystemExit(1)
```

- [ ] Step 4: Fix `lac_pro/benchmark_cli.py`

Add `import urllib.error` to the top imports:

Replace line 9:

```python
import statistics
```

with:

```python
import statistics
import urllib.error
```

Then replace lines 34-42 (the `run_benchmark` call in `cmd_benchmark`):

```python
    print(f"Benchmarking {model} (repeat={args.repeat})…")
    entries = run_benchmark(
        model,
        prompt=args.prompt,
        num_predict=args.num_predict,
        temperature=args.temperature,
        repeat=args.repeat,
        no_cache=args.no_cache,
    )
```

with:

```python
    print(f"Benchmarking {model} (repeat={args.repeat})…")
    try:
        entries = run_benchmark(
            model,
            prompt=args.prompt,
            num_predict=args.num_predict,
            temperature=args.temperature,
            repeat=args.repeat,
            no_cache=args.no_cache,
        )
    except urllib.error.HTTPError as e:
        print(f"Model not found or Ollama error: {model} (HTTP {e.code}). "
              f"Is it installed? Try `ollama pull {model}`.")
        raise SystemExit(1)
```

- [ ] Step 5: Run tests to verify they pass

Run: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q tests/test_tune.py tests/test_benchmark_cli.py` (from `C:\Users\User\repos\lac-pro`)
Expected: all tests PASS.

Then run the full lac-pro suite: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q` (from `C:\Users\User\repos\lac-pro`)
Expected: all tests pass (60 baseline + 2 new = 62).

This is a CLI-only fix (`lac pro tune`/`lac pro benchmark` command invocations) — no server restart consideration applies.

- [ ] Step 6: Commit

```bash
cd "C:\Users\User\repos\lac-pro"
git add lac_pro/tune.py lac_pro/benchmark_cli.py tests/test_tune.py tests/test_benchmark_cli.py
git commit -m "fix: lac pro tune/benchmark crash with a raw traceback on a nonexistent model (404 now prints a clean message + exit 1)"
```

---

### Task 8: "Measured" badge never appears after autopilot runs

**Files:**
- Modify: `backend/cookbook/calibration.py:201-218` (`apply_calibration`)
- Test: `tests/test_calibration.py` (append)

**Interfaces:**
- Consumes: `Calibration.measured: dict[tuple[str, str], MeasuredStat]` (existing)
- Produces: `apply_calibration(theoretical_tps, catalog_id, quant_name, regime, calibration)` now falls back to ANY measured quant for the same `catalog_id` if the exact `(catalog_id, quant_name)` isn't found, before falling through to the regime-level `calibrated`/`estimated` tiers. Return shape unchanged: `(tok_s: float, source: str, band_pct: float)`.

- [ ] Step 1: Write the failing test

Append to `tests/test_calibration.py`:

```python
def test_apply_calibration_falls_back_to_any_measured_quant_for_same_model():
    """recommend() scores every quant per model but returns only the
    best-SCORING one -- for a small model, F16 can win on composite score
    even though a plain `ollama pull` actually downloads (and gets
    benchmarked at) Q4_K_M. An exact (id, quant) lookup then never sees the
    Q4_K_M measured entry when scoring the F16 candidate, silently falling
    back to 'calibrated'/'estimated' even though real measured data exists
    for this model. Loosen the match: fall back to ANY measured quant for
    the same model id."""
    cal = Calibration(
        measured={("qwen3:0.6b", "Q4_K_M"): MeasuredStat(417.0, 1, 25.0)},
        regime_factor={}, regime_band_pct={}, n=1,
    )
    tps, src, band = apply_calibration(999.0, "qwen3:0.6b", "F16", "gpu", cal)
    assert (tps, src) == (417.0, "measured")
    assert band == 25.0


def test_apply_calibration_exact_match_still_wins_over_fallback():
    cal = Calibration(
        measured={
            ("m", "Q4_K_M"): MeasuredStat(50.0, 3, 4.0),
            ("m", "F16"): MeasuredStat(20.0, 1, 25.0),
        },
        regime_factor={"gpu": 0.5}, regime_band_pct={"gpu": 10.0}, n=4,
    )
    tps, src, band = apply_calibration(200.0, "m", "Q4_K_M", "gpu", cal)
    assert (tps, src) == (50.0, "measured")


def test_apply_calibration_falls_back_when_multiple_other_quants_measured():
    """When several other quants for the same model have measured data,
    fall back deterministically (most runs first) rather than raising or
    picking arbitrarily."""
    cal = Calibration(
        measured={
            ("m", "Q8"): MeasuredStat(80.0, 1, 25.0),
            ("m", "Q5_K_M"): MeasuredStat(90.0, 5, 6.0),
        },
        regime_factor={}, regime_band_pct={}, n=6,
    )
    tps, src, band = apply_calibration(999.0, "m", "F16", "gpu", cal)
    assert src == "measured"
    assert tps == 90.0  # the higher-n_runs entry wins the tie-break
```

- [ ] Step 2: Run test to verify it fails

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_calibration.py::test_apply_calibration_falls_back_to_any_measured_quant_for_same_model`
Expected: FAIL — `(tps, src) == (417.0, "measured")` fails because the exact `(catalog_id, "F16")` lookup misses, and there's no fallback, so the result is `(999.0, "estimated", 50.0)`.

- [ ] Step 3: Fix `backend/cookbook/calibration.py`

Replace lines 201-218 (`apply_calibration`):

```python
def apply_calibration(theoretical_tps, catalog_id, quant_name, regime, calibration):
    """Turn a theoretical tok/s estimate into (tok_s, source, band_pct).

    Precedence: exact measured (id,quant) > regime-level calibrated factor >
    uncalibrated estimated. A None calibration always falls through to
    "estimated".
    """
    if calibration is None:
        return round(theoretical_tps, 1), "estimated", _ESTIMATED_BAND
    stat = calibration.measured.get((catalog_id, quant_name))
    if stat is not None:
        band = stat.spread_pct if stat.n_runs > 1 else 25.0  # single-sample: flagged, not 0
        return stat.median_tps, "measured", band
    factor = calibration.regime_factor.get(regime)
    if factor is not None:
        band = calibration.regime_band_pct.get(regime, 35.0)
        return round(theoretical_tps * factor, 1), "calibrated", band
    return round(theoretical_tps, 1), "estimated", _ESTIMATED_BAND
```

with:

```python
def apply_calibration(theoretical_tps, catalog_id, quant_name, regime, calibration):
    """Turn a theoretical tok/s estimate into (tok_s, source, band_pct).

    Precedence: exact measured (id,quant) > ANY other measured quant for
    the same model id (looser fallback -- see below) > regime-level
    calibrated factor > uncalibrated estimated. A None calibration always
    falls through to "estimated".

    The fallback exists because recommend() scores every quant per model
    but returns only the single best-SCORING one: a small model can have
    F16 win on composite score even though a plain `ollama pull` actually
    installs (and LAC Pro's autopilot benchmarks) Q4_K_M. Without this
    fallback, a real measured run never surfaces as "measured" unless its
    exact quant happens to also be the highest-scoring one.
    """
    if calibration is None:
        return round(theoretical_tps, 1), "estimated", _ESTIMATED_BAND
    stat = calibration.measured.get((catalog_id, quant_name))
    if stat is None:
        candidates = [(k, v) for k, v in calibration.measured.items() if k[0] == catalog_id]
        if candidates:
            candidates.sort(key=lambda kv: (-kv[1].n_runs, kv[0][1]))
            stat = candidates[0][1]
    if stat is not None:
        band = stat.spread_pct if stat.n_runs > 1 else 25.0  # single-sample: flagged, not 0
        return stat.median_tps, "measured", band
    factor = calibration.regime_factor.get(regime)
    if factor is not None:
        band = calibration.regime_band_pct.get(regime, 35.0)
        return round(theoretical_tps * factor, 1), "calibrated", band
    return round(theoretical_tps, 1), "estimated", _ESTIMATED_BAND
```

- [ ] Step 4: Run test to verify it passes

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_calibration.py`
Expected: all tests PASS (including the pre-existing `test_apply_measured_wins`, `test_apply_calibrated_when_regime_has_factor`, `test_apply_estimated_when_no_data`, `test_apply_none_calibration_is_estimated` — the exact-match path is untouched, so these keep passing).

Then run the full suite: `.venv\Scripts\python.exe -m pytest -q`
Expected: all tests pass (this also exercises `test_recommend.py`'s calibration-integration tests, e.g. `test_recommend_surfaces_measured_source`, `test_loop_reproduces_measured_on_real_anchors` — confirm those still pass since they rely on `apply_calibration`'s exact-match behavior, which is unchanged).

This changes `backend.cookbook.calibration`, used by both `/api/recommend` and `lac recommend` — the live server needs a restart for the web UI to start surfacing this fix.

- [ ] Step 5: Commit

```bash
cd "C:\Users\User\repos\model-hub"
git add backend/cookbook/calibration.py tests/test_calibration.py
git commit -m "fix: measured badge never surfaced when recommend() picked a different quant than the benchmarked one

apply_calibration() now falls back to any measured quant for the same
model id when the exact (id, quant) isn't found, instead of silently
dropping to calibrated/estimated -- directly serves the Pro pitch's
'does the badge visibly flip to measured' promise."
```

---

### Task 9: Ollama-proxy API route correctness

**Files:**
- Modify: `backend/api.py:215-223` (`ollama_status`), `backend/api.py:243-329` (`ollama_pull`/`ollama_delete`/`ollama_chat`), `backend/api.py:789-800` (add JSON error handlers near the existing 404 handler)
- Test: `tests/test_api.py` (append)

**Interfaces:**
- Consumes: none new
- Produces: `ollama_status` reports a real version instead of always `"unknown"`. `ollama_pull`/`ollama_delete`/`ollama_chat` no longer 500 on a non-object JSON body (`null`, an array, a bare string). `ollama_delete` no longer reports `{"success": true}` when the underlying Ollama call actually failed. Malformed JSON bodies and wrong-method requests under `/api/*` now return JSON error bodies instead of Flask's default HTML error pages.

- [ ] Step 1: Write the failing tests

Append to `tests/test_api.py`:

```python
def test_ollama_status_reports_real_version(monkeypatch, flask_app):
    from backend import api as api_mod

    def fake_request(method, path, json_body=None, stream=False):
        assert path == "/api/version"
        return {"version": "0.31.1"}

    monkeypatch.setattr(api_mod, "_ollama_request", fake_request)
    client = flask_app.test_client()
    r = client.get("/api/ollama/status")
    assert r.status_code == 200
    assert r.get_json() == {"running": True, "version": "0.31.1"}


def test_ollama_pull_non_dict_body_does_not_500(flask_app):
    r = flask_app.test_client().post("/api/ollama/pull", json=["a", "b"])
    assert r.status_code == 400
    assert r.get_json()["error"] == "No model specified"


def test_ollama_delete_non_dict_body_does_not_500(flask_app):
    r = flask_app.test_client().post(
        "/api/ollama/delete", data="null", content_type="application/json"
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "No model specified"


def test_ollama_chat_non_dict_body_does_not_500(flask_app):
    r = flask_app.test_client().post("/api/ollama/chat", json="not-a-dict")
    assert r.status_code == 400
    assert r.get_json()["error"] == "Model and messages required"


def test_ollama_delete_reports_failure_when_ollama_errors(monkeypatch, flask_app):
    from backend import api as api_mod

    monkeypatch.setattr(
        api_mod, "_ollama_request",
        lambda method, path, json_body=None, stream=False: {"error": "model 'x' not found"},
    )
    r = flask_app.test_client().post("/api/ollama/delete", json={"model": "x"})
    assert r.status_code == 500
    assert r.get_json()["success"] is not True


def test_malformed_json_returns_json_error_not_html(flask_app):
    r = flask_app.test_client().put("/api/config", data="{not valid json", content_type="application/json")
    assert r.status_code == 400
    assert r.get_json() is not None
    assert "error" in r.get_json()


def test_method_not_allowed_returns_json_error_not_html(flask_app):
    r = flask_app.test_client().post("/api/benchmark", json={"model": "m:1b"})
    assert r.status_code == 405
    assert r.get_json() is not None
    assert "error" in r.get_json()
```

- [ ] Step 2: Run tests to verify they fail

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_api.py`
Expected: `test_ollama_status_reports_real_version` FAILS (`ollama_status` calls `/api/tags`, so the mock's `assert path == "/api/version"` fails). `test_ollama_pull_non_dict_body_does_not_500`/`test_ollama_chat_non_dict_body_does_not_500` FAIL with an unhandled `AttributeError` (`'list'`/`'str'` object has no attribute `'get'`). `test_ollama_delete_non_dict_body_does_not_500` FAILS the same way (`'NoneType' object has no attribute 'get'`). `test_ollama_delete_reports_failure_when_ollama_errors` FAILS (`r.get_json()["success"] is not True` — it currently IS `True`). `test_malformed_json_returns_json_error_not_html`/`test_method_not_allowed_returns_json_error_not_html` FAIL (`r.get_json() is not None` — the body is HTML, `get_json()` returns `None`).

- [ ] Step 3: Fix `ollama_status` to call `/api/version`

Replace lines 215-223:

```python
@app.route("/api/ollama/status")
def ollama_status():
    resp = _ollama_request("GET", "/api/tags")
    if resp is None or (isinstance(resp, dict) and "error" in resp):
        return jsonify({"running": False, "version": None, "error": resp.get("error") if isinstance(resp, dict) else None})
    return jsonify({
        "running": True,
        "version": resp.get("version", "unknown"),
    })
```

with:

```python
@app.route("/api/ollama/status")
def ollama_status():
    resp = _ollama_request("GET", "/api/version")
    if resp is None or (isinstance(resp, dict) and "error" in resp):
        return jsonify({"running": False, "version": None, "error": resp.get("error") if isinstance(resp, dict) else None})
    return jsonify({
        "running": True,
        "version": resp.get("version", "unknown"),
    })
```

- [ ] Step 4: Fix `ollama_pull`/`ollama_delete`/`ollama_chat` — non-dict-safe body parsing + honest delete result

Replace lines 243-329 (from `@app.route("/api/ollama/pull"...)` through the end of `ollama_chat`):

```python
@app.route("/api/ollama/pull", methods=["POST"])
def ollama_pull():
    data = request.get_json()
    model_name = data.get("model", "")
    if not model_name:
        return jsonify({"error": "No model specified"}), 400

    def generate():
        import urllib.request
        import urllib.error
        from .cookbook.downloads import log_download
        url = f"{OLLAMA_HOST}/api/pull"
        body = json.dumps({"name": model_name}).encode()
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        last_total = 0
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
                    if chunk.get("total"):
                        last_total = chunk["total"]
                    if chunk.get("status") == "success":
                        size_gb = round(last_total / (1024**3), 2) if last_total else 0
                        log_download(model_name, "completed", size_gb)
                        _notify_model_installed_async(model_name)
        except urllib.error.HTTPError as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/ollama/delete", methods=["POST"])
def ollama_delete():
    data = request.get_json()
    model_name = data.get("model", "")
    if not model_name:
        return jsonify({"error": "No model specified"}), 400

    result = _ollama_request("DELETE", f"/api/delete", {"name": model_name})
    if result is None:
        return jsonify({"error": "Failed to delete model"}), 500
    return jsonify({"success": True})


@app.route("/api/ollama/chat", methods=["POST"])
def ollama_chat():
    data = request.get_json()
    model = data.get("model", "")
    messages = data.get("messages", [])
    if not model or not messages:
        return jsonify({"error": "Model and messages required"}), 400

    def generate():
        import urllib.request
        import urllib.error
        url = f"{OLLAMA_HOST}/api/chat"
        body = json.dumps({"model": model, "messages": messages, "stream": True}).encode()
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            resp = urllib.request.urlopen(req, timeout=300)
            for line in resp:
                decoded = line.decode().strip()
                if decoded:
                    yield f"data: {decoded}\n\n"
        except urllib.error.HTTPError as e:
            yield f"data: {json.dumps({'error': f'HTTP {e.code}: {e.reason}'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

with:

```python
@app.route("/api/ollama/pull", methods=["POST"])
def ollama_pull():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    model_name = data.get("model", "")
    if not model_name:
        return jsonify({"error": "No model specified"}), 400

    def generate():
        import urllib.request
        import urllib.error
        from .cookbook.downloads import log_download
        url = f"{OLLAMA_HOST}/api/pull"
        body = json.dumps({"name": model_name}).encode()
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        last_total = 0
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
                    if chunk.get("total"):
                        last_total = chunk["total"]
                    if chunk.get("status") == "success":
                        size_gb = round(last_total / (1024**3), 2) if last_total else 0
                        log_download(model_name, "completed", size_gb)
                        _notify_model_installed_async(model_name)
        except urllib.error.HTTPError as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/ollama/delete", methods=["POST"])
def ollama_delete():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    model_name = data.get("model", "")
    if not model_name:
        return jsonify({"error": "No model specified"}), 400

    result = _ollama_request("DELETE", f"/api/delete", {"name": model_name})
    if isinstance(result, dict) and "error" in result:
        return jsonify({"error": result["error"]}), 500
    return jsonify({"success": True})


@app.route("/api/ollama/chat", methods=["POST"])
def ollama_chat():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    model = data.get("model", "")
    messages = data.get("messages", [])
    if not model or not messages:
        return jsonify({"error": "Model and messages required"}), 400

    def generate():
        import urllib.request
        import urllib.error
        url = f"{OLLAMA_HOST}/api/chat"
        body = json.dumps({"model": model, "messages": messages, "stream": True}).encode()
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            resp = urllib.request.urlopen(req, timeout=300)
            for line in resp:
                decoded = line.decode().strip()
                if decoded:
                    yield f"data: {decoded}\n\n"
        except urllib.error.HTTPError as e:
            yield f"data: {json.dumps({'error': f'HTTP {e.code}: {e.reason}'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- [ ] Step 5: Add JSON error handlers for 400/405 under `/api/*`

Replace lines 789-800:

```python
@app.errorhandler(404)
def spa_fallback(_e):
    # Client-side routes (e.g. /browse, /chat) -> index.html; API 404 -> JSON.
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found"}), 404
    index_path = Path(app.static_folder) / "index.html"
    if index_path.exists():
        return app.send_static_file("index.html")
    return (
        "Web app not built. Run `npm run build` inside web/, or `npm run dev` for development.",
        404,
    )
```

with:

```python
@app.errorhandler(404)
def spa_fallback(_e):
    # Client-side routes (e.g. /browse, /chat) -> index.html; API 404 -> JSON.
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found"}), 404
    index_path = Path(app.static_folder) / "index.html"
    if index_path.exists():
        return app.send_static_file("index.html")
    return (
        "Web app not built. Run `npm run build` inside web/, or `npm run dev` for development.",
        404,
    )


@app.errorhandler(400)
def bad_request_json(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Bad request"}), 400
    return e


@app.errorhandler(405)
def method_not_allowed_json(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Method not allowed"}), 405
    return e
```

- [ ] Step 6: Run tests to verify they pass

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_api.py`
Expected: all tests PASS, including the pre-existing `test_ollama_status` and `test_api_benchmark_route_removed` (still 405, now with a JSON body instead of HTML).

Then run the full suite: `.venv\Scripts\python.exe -m pytest -q`
Expected: all tests pass.

This changes several routes in the running server's request path — a restart is needed to serve these fixes live.

- [ ] Step 7: Commit

```bash
cd "C:\Users\User\repos\model-hub"
git add backend/api.py tests/test_api.py
git commit -m "fix: harden ollama-proxy API routes (non-dict JSON bodies, false-success on delete, wrong version endpoint, HTML error pages under /api/*)

- ollama_status now reads /api/version instead of /api/tags (which has no
  version field, so it always reported 'unknown')
- ollama_pull/delete/chat no longer 500 on a syntactically-valid-but-non-object
  JSON body (null, an array, a bare string)
- ollama_delete no longer reports {\"success\": true} when Ollama actually
  returned an error
- malformed JSON bodies and wrong-method requests under /api/* now get a
  JSON error body instead of Flask's default HTML error page"
```

---

### Task 10: VRAM accuracy — hybrid iGPU+dGPU double-counting + manual override display bug

**Files:**
- Modify: `backend/cookbook/recommend.py:282-324` (`_compute_split_plan` — add a `_tier_capacity` helper)
- Modify: `backend/api.py:120-191` (`api_recommend` — keep `combined_vram_gb` in sync with a manual VRAM override)
- Test: `tests/test_recommend.py` (append), `tests/test_api.py` (append)

**Interfaces:**
- Consumes: `TierAllocation`/`ComputeTier` (existing dataclasses, unchanged shape)
- Produces: `_compute_split_plan` no longer lets an `"integrated"` tier and the `"ram"` tier both claim the same physical RAM bytes. `GET /api/recommend?vram=<N>` now returns a `combined_vram_gb` that reflects the override, not the stale pre-override detected value.

- [ ] Step 1: Write the failing tests

Append to `tests/test_recommend.py`:

```python
def test_ram_tier_capacity_subtracts_igpu_shared_claim():
    """The iGPU shares the same physical RAM pool as the 'ram' tier -- its
    claimed VRAM must be subtracted from RAM's own ceiling before applying
    the 50% headroom, so the two tiers can't double-spend the same bytes.
    On the real hand-off rig (dGPU 16GB + iGPU 10.5GB + RAM 30.9GB): old
    (buggy) combined ceiling = 16 + 9.45 + 30.9*0.5 = 40.9GB; corrected
    ceiling = 16 + 9.45 + (30.9-10.5)*0.5 = 35.65GB. 38GB fits under the
    old (double-counting) ceiling but must now report too_big."""
    info = _sys_handoff()
    model = next(m for m in load_models() if m.id == "qwen3:32b")
    split = _compute_split_plan(38.0, info, model)
    assert split is None
```

Append to `tests/test_api.py`:

```python
def test_recommend_manual_vram_override_updates_combined_vram(monkeypatch, flask_app, isolated_home):
    from backend import api as api_mod
    from backend.cookbook.hardware import SystemInfo

    monkeypatch.setattr(api_mod, "detect", lambda: SystemInfo(
        os="Test", cpu="Test", cpu_cores=8, ram_gb=32.0,
        gpus=[], total_vram_gb=0.0, combined_vram_gb=0.0, compute_tiers=[],
    ))

    client = flask_app.test_client()
    r = client.get("/api/recommend?use_case=general&top_k=3&vram=8")
    assert r.status_code == 200
    data = r.get_json()
    assert data["vram_gb"] == 8.0
    assert data["combined_vram_gb"] == 8.0
```

- [ ] Step 2: Run tests to verify they fail

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_recommend.py::test_ram_tier_capacity_subtracts_igpu_shared_claim tests/test_api.py::test_recommend_manual_vram_override_updates_combined_vram`
Expected: `test_ram_tier_capacity_subtracts_igpu_shared_claim` FAILS (`split is None` fails — 38.0 currently fits under the double-counting 40.9GB ceiling, so a real split plan is returned). `test_recommend_manual_vram_override_updates_combined_vram` FAILS (`data["combined_vram_gb"] == 8.0` fails — it's still `0.0`, the stale pre-override value).

- [ ] Step 3: Fix `backend/cookbook/recommend.py`

Replace lines 282-324 (`_compute_split_plan`, from its `def` through the `total_capacity` check):

```python
def _compute_split_plan(vram_needed: float, info: SystemInfo,
                        model: ModelEntry) -> Optional[SplitPlan]:
    """Distribute a model's memory across compute tiers (dGPU → iGPU → RAM).

    Returns None if the model doesn't fit even across all tiers ("too_big").
    """
    tiers = info.compute_tiers
    if not tiers:
        from .hardware import ComputeTier
        tiers = []
        if info.gpus:
            gpu = info.gpus[0]
            tiers = [ComputeTier(gpu.name, gpu.vram_gb, gpu.backend, "discrete", 0)]
        elif info.total_vram_gb > 0:
            tiers = [ComputeTier("GPU", info.total_vram_gb,
                                  "rocm" if info.has_amd else "cuda", "discrete", 0)]
        if info.ram_gb > 0:
            tiers = tiers + [ComputeTier("System RAM", info.ram_gb, "cpu", "ram", -1)]
        if not tiers:
            return None

    remaining = vram_needed
    allocs: list[TierAllocation] = []
    used_kinds: set[str] = set()

    for tier in tiers:
        if remaining <= 0.01:
            break
        capacity = tier.memory_gb * TIER_HEADROOM.get(tier.kind, 0.75)
        allocated = min(remaining, capacity)
        if allocated > 0.01:
            allocs.append(TierAllocation(
                kind=tier.kind, name=tier.name, memory_gb=tier.memory_gb,
                allocated_gb=round(allocated, 2), backend=tier.backend,
                device_index=tier.device_index, bandwidth=_tier_bandwidth(tier),
            ))
            remaining -= allocated
            used_kinds.add(tier.kind)

    # Doesn't fit even with all tiers (allow small tolerance for rounding).
    total_capacity = sum(t.memory_gb * TIER_HEADROOM.get(t.kind, 0.75) for t in tiers)
    if vram_needed > total_capacity + 0.5:
        return None
```

with:

```python
def _tier_capacity(tier, igpu_claim_gb: float) -> float:
    """Usable capacity for one compute tier, after headroom.

    An integrated GPU draws its VRAM from the same physical RAM pool as
    the "ram" tier. Subtract the iGPU's claim from RAM's own ceiling
    BEFORE applying RAM's headroom, so the two shared-memory tiers can't
    both spend the same physical bytes (was: an iGPU claiming ~9.45GB plus
    RAM's 50% headroom could together spend ~80% of physical RAM, not the
    intended 50%).
    """
    memory_gb = tier.memory_gb
    if tier.kind == "ram" and igpu_claim_gb > 0:
        memory_gb = max(0.0, memory_gb - igpu_claim_gb)
    return memory_gb * TIER_HEADROOM.get(tier.kind, 0.75)


def _compute_split_plan(vram_needed: float, info: SystemInfo,
                        model: ModelEntry) -> Optional[SplitPlan]:
    """Distribute a model's memory across compute tiers (dGPU → iGPU → RAM).

    Returns None if the model doesn't fit even across all tiers ("too_big").
    """
    tiers = info.compute_tiers
    if not tiers:
        from .hardware import ComputeTier
        tiers = []
        if info.gpus:
            gpu = info.gpus[0]
            tiers = [ComputeTier(gpu.name, gpu.vram_gb, gpu.backend, "discrete", 0)]
        elif info.total_vram_gb > 0:
            tiers = [ComputeTier("GPU", info.total_vram_gb,
                                  "rocm" if info.has_amd else "cuda", "discrete", 0)]
        if info.ram_gb > 0:
            tiers = tiers + [ComputeTier("System RAM", info.ram_gb, "cpu", "ram", -1)]
        if not tiers:
            return None

    remaining = vram_needed
    allocs: list[TierAllocation] = []
    used_kinds: set[str] = set()

    igpu_claim_gb = sum(t.memory_gb for t in tiers if t.kind == "integrated")

    for tier in tiers:
        if remaining <= 0.01:
            break
        capacity = _tier_capacity(tier, igpu_claim_gb)
        allocated = min(remaining, capacity)
        if allocated > 0.01:
            allocs.append(TierAllocation(
                kind=tier.kind, name=tier.name, memory_gb=tier.memory_gb,
                allocated_gb=round(allocated, 2), backend=tier.backend,
                device_index=tier.device_index, bandwidth=_tier_bandwidth(tier),
            ))
            remaining -= allocated
            used_kinds.add(tier.kind)

    # Doesn't fit even with all tiers (allow small tolerance for rounding).
    total_capacity = sum(_tier_capacity(t, igpu_claim_gb) for t in tiers)
    if vram_needed > total_capacity + 0.5:
        return None
```

- [ ] Step 4: Fix `backend/api.py`'s `api_recommend` — keep `combined_vram_gb` in sync

Replace lines 129-137:

```python
    info = detect()
    if vram and vram > 0:
        info.total_vram_gb = vram
        for gpu in info.gpus:
            if "radeon" in gpu.name.lower() or "amd" in gpu.name.lower():
                gpu.vram_gb = vram
        if not info.gpus:
            from .cookbook.hardware import GPUInfo
            info.gpus = [GPUInfo(name=f"Manual ({vram} GB)", vram_gb=vram, backend="cuda")]
```

with:

```python
    info = detect()
    if vram and vram > 0:
        info.total_vram_gb = vram
        for gpu in info.gpus:
            if "radeon" in gpu.name.lower() or "amd" in gpu.name.lower():
                gpu.vram_gb = vram
        if not info.gpus:
            from .cookbook.hardware import GPUInfo
            info.gpus = [GPUInfo(name=f"Manual ({vram} GB)", vram_gb=vram, backend="cuda")]
        # Manual override updates fit-scoring via total_vram_gb/gpus above,
        # but combined_vram_gb is a separate display field detect() already
        # computed pre-override -- keep it in sync or the UI shows a stale
        # number next to the correctly-overridden one.
        info.combined_vram_gb = round(sum(g.vram_gb for g in info.gpus), 1)
```

- [ ] Step 5: Run tests to verify they pass

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_recommend.py tests/test_api.py`
Expected: all tests PASS, including pre-existing hand-off tests (`test_split_needs_multi_gpu`, `test_split_needs_ram_offload`, `test_split_30b_a3b_q4_needs_multi_gpu`, etc. — none of them push `vram_needed` past the corrected ceiling, so their expected `run_mode`/tier counts are unaffected) and the pre-existing `test_recommend_gpu_mask_*` tests in `test_api.py`.

Then run the full suite: `.venv\Scripts\python.exe -m pytest -q`
Expected: all tests pass.

This changes core recommendation math used by both `/api/recommend` and `lac recommend` — the live server needs a restart to serve the corrected numbers.

- [ ] Step 6: Commit

```bash
cd "C:\Users\User\repos\model-hub"
git add backend/cookbook/recommend.py backend/api.py tests/test_recommend.py tests/test_api.py
git commit -m "fix: hybrid iGPU+dGPU VRAM double-counting; manual VRAM override left combined_vram_gb stale

An integrated GPU's VRAM comes out of the same physical RAM the 'ram'
tier's 50% headroom also claims -- the two could together spend ~80% of
system RAM, not the intended 50%. Also: GET /api/recommend?vram=N updated
fit-scoring correctly but never recomputed the combined_vram_gb display
field, so it stayed at the pre-override detected value."
```

---

### Task 11: Rebrand string leaks (session exports, outbound User-Agent, update-check repo)

**Files:**
- Modify: `backend/cookbook/export.py:129-238` (`to_opencode_json`, `_filename`), `backend/cookbook/export.py:88-117` (`to_markdown`), `backend/cookbook/export.py:183-216` (`to_html`)
- Modify: `backend/plugin/builtins/tools.py:79` (`_web_search`)
- Modify: `backend/api.py:360-383` (`api_check_update`)
- Test: `tests/test_export.py` (append), `tests/test_builtin_tools_useragent.py` (new), `tests/test_api.py` (append)

**Interfaces:**
- Consumes: none new
- Produces: exported session filenames/titles use `lac-session`/`LAC Session` instead of `apt-session`/`Apt Session`; the `_web_search` tool sends `User-Agent: LAC/2.2.0`; `/api/system/check-update` hits `github.com/Dkrynen/lac` with `User-Agent: LAC/<version>` instead of the stale `Dkrynen/model-hub` + `model-hub/1.0`. (The `"format": "apt-session/v1"` JSON schema tag in `to_json`/`to_yaml` is a versioned data-format identifier, not a display string, and is explicitly out of scope — 3 existing tests hard-assert it and changing it would break exported-file forward-compat for no user-visible benefit.)

- [ ] Step 1: Write the failing tests

Append to `tests/test_export.py`:

```python
def test_filename_uses_lac_session_prefix():
    from backend.cookbook.export import _filename
    name = _filename({"id": "abc123def45678"}, "json")
    assert name == "lac-session-abc123def4.json"
    assert "apt-session" not in name


def test_markdown_heading_is_lac_session():
    md = to_markdown(_make_session())
    assert "# LAC Session:" in md
    assert "Apt Session" not in md


def test_html_title_and_heading_are_lac_session():
    h = to_html(_make_session())
    assert "<title>LAC Session" in h
    assert "<h1>LAC Session</h1>" in h
    assert "Apt Session" not in h


def test_opencode_json_default_title_is_lac_session():
    from backend.cookbook.export import to_opencode_json
    s = _make_session()
    s["name"] = ""
    s["messages"] = [{"role": "assistant", "content": "hi", "timestamp": 1.0}]
    out = json.loads(to_opencode_json(s))
    assert out["info"]["title"] == "LAC Session"
```

Create `tests/test_builtin_tools_useragent.py`:

```python
from __future__ import annotations

from backend.plugin.builtins.tools import _web_search
import backend.plugin.builtins.tools as tools_mod


def test_web_search_sends_lac_user_agent(monkeypatch):
    captured = {}

    class FakeResp:
        def read(self):
            return b"<html></html>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=15):
        captured["ua"] = req.get_header("User-agent")
        return FakeResp()

    monkeypatch.setattr(tools_mod.urllib.request, "urlopen", fake_urlopen)
    _web_search({"query": "test query"}, {})

    assert captured["ua"] == "LAC/2.2.0"
```

Append to `tests/test_api.py`:

```python
def test_check_update_uses_lac_repo_and_useragent(monkeypatch, flask_app):
    import urllib.request as real_urllib_request

    captured = {}

    class FakeResp:
        def read(self):
            return b'{"tag_name": "v9.9.9", "html_url": "x", "body": ""}'

    def fake_urlopen(req, timeout=5):
        captured["url"] = req.full_url
        captured["ua"] = req.get_header("User-agent")
        return FakeResp()

    monkeypatch.setattr(real_urllib_request, "urlopen", fake_urlopen)

    client = flask_app.test_client()
    r = client.get("/api/system/check-update?current=0.0.0")
    assert r.status_code == 200
    assert captured["url"] == "https://api.github.com/repos/Dkrynen/lac/releases/latest"
    assert captured["ua"].startswith("LAC/")
    assert captured["ua"] != "model-hub/1.0"
```

- [ ] Step 2: Run tests to verify they fail

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_export.py tests/test_builtin_tools_useragent.py tests/test_api.py`
Expected: `test_filename_uses_lac_session_prefix` FAILS (`name == "apt-session-abc123def4.json"`). `test_markdown_heading_is_lac_session` FAILS (`"# Apt Session:"` is what's actually there). `test_html_title_and_heading_are_lac_session` FAILS (`<title>Apt Session`/`<h1>Apt Session</h1>`). `test_opencode_json_default_title_is_lac_session` FAILS (`out["info"]["title"] == "Apt Session"`). `test_web_search_sends_lac_user_agent` FAILS (`captured["ua"] == "Apt/2.2"`). `test_check_update_uses_lac_repo_and_useragent` FAILS (`captured["url"]` is the `Dkrynen/model-hub` URL, `captured["ua"] == "model-hub/1.0"`).

- [ ] Step 3: Fix `backend/cookbook/export.py`

Replace line 100 (inside `to_markdown`):

```python
    lines.append(f"# Apt Session: {sid[:10] if sid else 'unknown'}")
```

with:

```python
    lines.append(f"# LAC Session: {sid[:10] if sid else 'unknown'}")
```

Replace line 131 (inside `to_opencode_json`):

```python
    title = session.get("name") or next((m.get("content", "")[:60] for m in session.get("messages", []) if m.get("role") == "user"), "Apt Session")
```

with:

```python
    title = session.get("name") or next((m.get("content", "")[:60] for m in session.get("messages", []) if m.get("role") == "user"), "LAC Session")
```

Replace lines 197-198 and 213 (inside `to_html`):

```python
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>Apt Session {html.escape(sid[:10])}</title>
```

with:

```python
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>LAC Session {html.escape(sid[:10])}</title>
```

and:

```python
<h1>Apt Session</h1>
```

with:

```python
<h1>LAC Session</h1>
```

Replace lines 234-238 (`_filename`):

```python
def _filename(session: dict, fmt: str) -> str:
    sid = session.get("id", "session") or "session"
    short = sid[:10]
    ext = {"md": "md", "markdown": "md", "json": "json", "yaml": "yaml", "yml": "yaml", "html": "html", "opencode-json": "json"}[fmt.lower()]
    return f"apt-session-{short}.{ext}"
```

with:

```python
def _filename(session: dict, fmt: str) -> str:
    sid = session.get("id", "session") or "session"
    short = sid[:10]
    ext = {"md": "md", "markdown": "md", "json": "json", "yaml": "yaml", "yml": "yaml", "html": "html", "opencode-json": "json"}[fmt.lower()]
    return f"lac-session-{short}.{ext}"
```

- [ ] Step 4: Fix `backend/plugin/builtins/tools.py`

Replace line 79:

```python
    req = urllib.request.Request(url, headers={"User-Agent": "Apt/2.2"})
```

with:

```python
    req = urllib.request.Request(url, headers={"User-Agent": "LAC/2.2.0"})
```

- [ ] Step 5: Fix `backend/api.py`'s `api_check_update`

Replace lines 360-383:

```python
@app.route("/api/system/check-update")
def api_check_update():
    current = request.args.get("current", APP_VERSION)
    try:
        import urllib.request
        import urllib.error
        import json as _json
        url = "https://api.github.com/repos/Dkrynen/model-hub/releases/latest"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "model-hub/1.0")
        resp = urllib.request.urlopen(req, timeout=5)
        data = _json.loads(resp.read().decode())
        latest = data.get("tag_name", "").lstrip("v")
        if latest and latest != current:
            return jsonify({
                "update_available": True,
                "latest_version": latest,
                "download_url": data.get("html_url", ""),
                "release_notes": (data.get("body") or "")[:500],
            })
        return jsonify({"update_available": False, "latest_version": latest, "current_version": current})
    except Exception as e:
        return jsonify({"update_available": False, "error": str(e)})
```

with:

```python
@app.route("/api/system/check-update")
def api_check_update():
    current = request.args.get("current", APP_VERSION)
    try:
        import urllib.request
        import urllib.error
        import json as _json
        url = "https://api.github.com/repos/Dkrynen/lac/releases/latest"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", f"LAC/{APP_VERSION}")
        resp = urllib.request.urlopen(req, timeout=5)
        data = _json.loads(resp.read().decode())
        latest = data.get("tag_name", "").lstrip("v")
        if latest and latest != current:
            return jsonify({
                "update_available": True,
                "latest_version": latest,
                "download_url": data.get("html_url", ""),
                "release_notes": (data.get("body") or "")[:500],
            })
        return jsonify({"update_available": False, "latest_version": latest, "current_version": current})
    except Exception as e:
        return jsonify({"update_available": False, "error": str(e)})
```

- [ ] Step 6: Run tests to verify they pass

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_export.py tests/test_builtin_tools_useragent.py tests/test_api.py`
Expected: all tests PASS, including the pre-existing `test_json_format_is_apt_session_v1`, `test_yaml_roundtrips_to_same_json`, and `test_export_session_dispatch` (which still assert `"apt-session/v1"` — untouched by this task, confirming the deliberate scope boundary).

Then run the full suite: `.venv\Scripts\python.exe -m pytest -q`
Expected: all tests pass.

This changes `/api/system/check-update` (running server) and the export/tool-call code paths — a restart is needed for the web-served update-check to reflect the fix; the export/tools fixes take effect on next CLI invocation or plugin call regardless.

- [ ] Step 7: Commit

```bash
cd "C:\Users\User\repos\model-hub"
git add backend/cookbook/export.py backend/plugin/builtins/tools.py backend/api.py tests/test_export.py tests/test_builtin_tools_useragent.py tests/test_api.py
git commit -m "fix(rebrand): session export naming/titles, web-search tool User-Agent, and the web-dashboard update-check now say LAC/lac consistently

Exported sessions were named apt-session-<id> with 'Apt Session' titles;
the built-in web-search tool sent User-Agent: Apt/2.2; and the web API's
check-update route hit the old Dkrynen/model-hub repo with User-Agent:
model-hub/1.0 -- a separate, parallel copy of the update-check that was
missed when backend/update.py (used by \`lac update\`) was fixed earlier.
The apt-session/v1 JSON format tag is left alone (data-format identifier,
not a display string; 3 tests already lock it)."
```

---

### Task 12: Downloads page hides real backend errors as an empty state

**Files:**
- Modify: `web/src/pages/downloads.tsx` (full file)
- Test: none (no component-test harness in this repo) — verify via `npm run typecheck && npm run build`

**Interfaces:**
- Consumes: `useAsync` (existing, returns `{data, error, loading, reload}`), `ErrorState` from `@/components/page` (existing)
- Produces: `Downloads` now renders `ErrorState` with a retry button when `dl.error` is set, matching every sibling page's pattern, instead of silently showing the "no history yet" empty state.

- [ ] Step 1: Show the diff

Replace the full contents of `web/src/pages/downloads.tsx`:

```tsx
import { Download as DownloadIcon, Clock } from "lucide-react";
import { PageHeader, EmptyState, ErrorState } from "@/components/page";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useAsync } from "@/lib/hooks";
import { api } from "@/lib/api";

export function Downloads() {
  const dl = useAsync(() => api.downloads());
  const rows = (dl.data ?? []).slice().reverse();

  return (
    <>
      <PageHeader title="Downloads" subtitle="History of models pulled through LAC." />

      {dl.error ? (
        <ErrorState message={`Couldn’t load download history: ${dl.error}`} onRetry={dl.reload} />
      ) : dl.loading ? (
        <Card className="p-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="my-2 h-10 w-full" />
          ))}
        </Card>
      ) : rows.length === 0 ? (
        <EmptyState
          icon={<DownloadIcon className="h-8 w-8" />}
          title="No downloads yet"
          hint="Install a model from Browse or the Dashboard and it’ll appear here."
        />
      ) : (
        <div className="overflow-hidden rounded-lg border border-line">
          <table className="w-full text-sm">
            <thead className="bg-panel-2 text-[11px] uppercase tracking-[0.06em] text-fg-faint">
              <tr>
                <th className="px-4 py-2 text-left font-semibold">Model</th>
                <th className="px-4 py-2 text-left font-semibold">Status</th>
                <th className="px-4 py-2 text-right font-semibold">When</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {rows.map((r, i) => {
                const ok = String(r.status || "").toLowerCase().includes("ok") || r.status === "success";
                const bad = String(r.status || "").toLowerCase().includes("error") || r.status === "failed";
                return (
                  <tr key={i} className="transition-colors hover:bg-panel-3/40">
                    <td className="px-4 py-2.5 font-mono text-[13px]">{r.model || "—"}</td>
                    <td className="px-4 py-2.5">
                      <Badge variant={ok ? "success" : bad ? "danger" : "neutral"} dot>
                        {r.status || "—"}
                      </Badge>
                    </td>
                    <td className="px-4 py-2.5 text-right text-[12.5px] text-fg-muted">
                      <span className="inline-flex items-center gap-1">
                        <Clock className="h-3 w-3" />
                        {r.timestamp ? new Date(r.timestamp).toLocaleString() : "—"}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
```

Note the two changes from the original: (1) the import line adds `ErrorState` and drops the unused `CheckCircle2, XCircle` lucide icons (dead imports — confirmed unused anywhere else in the file); (2) the render logic adds `dl.error ? <ErrorState .../> :` as the first branch, ahead of the loading/empty/table branches, matching the `error ? <ErrorState onRetry=.../> : ...` pattern every sibling page (`scan.tsx`, `browse.tsx`, `installed.tsx`) already uses.

- [ ] Step 2: Run verification

Run: `cd C:\Users\User\repos\model-hub\web && npm run typecheck && npm run build`
Expected: both commands exit 0 with no errors (removing the unused `CheckCircle2`/`XCircle` imports must not leave any dangling reference — confirmed none exist elsewhere in the file).

- [ ] Step 3: Commit

```bash
cd "C:\Users\User\repos\model-hub"
git add web/src/pages/downloads.tsx
git commit -m "fix: Downloads page showed the 'no history yet' empty state even when the backend request actually failed

Now uses the same error ? <ErrorState onRetry> : ... pattern every
sibling page already uses. Also drops two unused lucide icon imports
(CheckCircle2, XCircle) left over from an earlier version of this page."
```

---

### Task 13: Scan page — defensive SourceBadge fallback + remove dead Vision/Writing use cases

**Files:**
- Modify: `web/src/pages/scan.tsx:16-23` (`USE_CASES`), `web/src/pages/scan.tsx:318-334` (`SourceBadge`)
- Test: none (no component-test harness) — verify via `npm run typecheck && npm run build`

**Interfaces:**
- Consumes: none new
- Produces: `USE_CASES` drops the `"vision"`/`"writing"` entries (investigated: the 91-model catalog has zero models with `"vision"` or `"writing"` in `use_cases` — only `general`/`coding`/`reasoning`/`chat` exist anywhere in `backend/cookbook/data/models.json`, and `recommend.py`'s `USE_CASE_WEIGHTS`/`CONTEXT_TARGETS` only define behavior for those 4; selecting Vision or Writing today silently reproduces General's ranking with no indication anything is inert). `SourceBadge` no longer throws if `source` is ever something other than the 3 known literals.

**Investigation note (why "remove" and not "wire up"):** confirmed via `python -c "..."` against the real catalog that `{m.get('use_cases', []) for m in models.json}` only ever contains `{'chat', 'coding', 'general', 'reasoning'}` — no model is tagged `vision` or `writing`. The web UI's `vision` **capability** badge (shown on cards like `gemma3` in Browse) comes from a completely different system — the scraped Ollama-library `capabilities` field used by Browse's capability filter — and is unrelated to `recommend.py`'s `use_case` scoring, which is what this dropdown drives. Since no catalog model backs either option for real scoring purposes, they're removed rather than wired up.

- [ ] Step 1: Show the diff — remove dead use cases

Replace lines 16-23:

```tsx
const USE_CASES = [
  { v: "coding", l: "Coding" },
  { v: "chat", l: "Chat" },
  { v: "reasoning", l: "Reasoning" },
  { v: "vision", l: "Vision" },
  { v: "writing", l: "Writing" },
  { v: "general", l: "General" },
];
```

with:

```tsx
const USE_CASES = [
  { v: "coding", l: "Coding" },
  { v: "chat", l: "Chat" },
  { v: "reasoning", l: "Reasoning" },
  { v: "general", l: "General" },
];
```

- [ ] Step 2: Show the diff — defensive `SourceBadge` fallback

Replace lines 318-334 (`SourceBadge`):

```tsx
function SourceBadge({ source, band }: { source: "measured" | "calibrated" | "estimated"; band: number }) {
  const meta = SOURCE_META[source];
  const tip =
    source === "measured"
      ? "Real tok/s — auto-benchmarked by LAC Pro on your exact hardware"
      : source === "calibrated"
      ? `Adjusted by your machine's regime factor (±${Math.round(band)}%)`
      : `Theoretical estimate (±${Math.round(band)}%). LAC Pro auto-benchmarks every model you install for measured accuracy.`;
  return (
    <Badge variant={meta.variant} dot title={tip}>
      {meta.label}
      {source !== "estimated" && (
        <span className="font-mono text-[9px] opacity-70">±{Math.round(band)}%</span>
      )}
    </Badge>
  );
}
```

with:

```tsx
function SourceBadge({ source, band }: { source: "measured" | "calibrated" | "estimated"; band: number }) {
  // Currently unreachable (apply_calibration only ever emits these 3
  // literals) but there's no ErrorBoundary anywhere in the app, so an
  // unexpected value from the API would otherwise white-screen the whole
  // page. Matches verdict.tsx's MAP handling of its own "unknown" case.
  const meta = SOURCE_META[source] ?? { variant: "neutral" as const, label: source };
  const tip =
    source === "measured"
      ? "Real tok/s — auto-benchmarked by LAC Pro on your exact hardware"
      : source === "calibrated"
      ? `Adjusted by your machine's regime factor (±${Math.round(band)}%)`
      : source === "estimated"
      ? `Theoretical estimate (±${Math.round(band)}%). LAC Pro auto-benchmarks every model you install for measured accuracy.`
      : `Unrecognized speed source (±${Math.round(band)}%).`;
  return (
    <Badge variant={meta.variant} dot title={tip}>
      {meta.label}
      {source !== "estimated" && (
        <span className="font-mono text-[9px] opacity-70">±{Math.round(band)}%</span>
      )}
    </Badge>
  );
}
```

- [ ] Step 3: Run verification

Run: `cd C:\Users\User\repos\model-hub\web && npm run typecheck && npm run build`
Expected: both commands exit 0 with no errors.

- [ ] Step 4: Commit

```bash
cd "C:\Users\User\repos\model-hub"
git add web/src/pages/scan.tsx
git commit -m "fix: Scan page — remove inert Vision/Writing use-case options; defensive fallback for an unrecognized speed_source

Investigated first: the 91-model catalog has zero models tagged vision
or writing in use_cases, and recommend.py has no real scoring weights for
either -- selecting them silently reproduced General's ranking. Also adds
a safe SOURCE_META fallback so an unexpected speed_source value (there's
no ErrorBoundary anywhere in the app) can't white-screen the page,
matching verdict.tsx's existing pattern for its own union type."
```

---

### Task 14: `frontend/docs.html` content pass

**Files:**
- Modify: `frontend/docs.html` (multiple sections; full file read and cross-checked against `web/src/`, `backend/api.py`, and `backend/cookbook/data/models.json` before writing this task)
- Test: none (static HTML, not covered by the Vite build) — verify via `grep` + the existing `test_docs_route` test

**Interfaces:**
- Consumes: none
- Produces: every specific false claim identified in the audit is corrected; no new false claims introduced.

**Every claim cross-checked against real code, confirmed during planning:**
- "Sessions" web UI management page: does not exist (`web/src/pages/chat.tsx` has zero references to "session").
- Workspace dropdown + gear icon in the sidebar: does not exist (`web/src/components/sidebar.tsx` has no workspace UI at all; `web/src/pages/settings.tsx` only displays the current workspace name, read-only).
- "Click Run to copy to clipboard": the real button in `installed.tsx` is labeled "Chat" and navigates to `/chat?model=...` — no clipboard behavior anywhere in the codebase.
- Search/filter box on the Installed page: does not exist (`installed.tsx` has only the "Pull new" input, no filter for the existing list).
- "65+ models": real catalog is 91 (`site/index.html` already correctly says 91; confirmed via `len(load_models())`).
- `/api/recommend`'s example response and param table: missing `speed_source`, `speed_band_pct`, `split_plan`, `combined_vram_gb` (present in the real `api_recommend` JSON, `backend/api.py:163-191`) and missing `gpu_mask`/`allow_spill`/`no_calibration` params (real query params at `backend/api.py:122-127`).
- `/api/scan`'s example response: missing `combined_vram_gb` and `compute_tiers` (real fields at `backend/api.py:109-114`).
- Stale `github.com/Dkrynen/model-hub` URLs (4 occurrences): should be `Dkrynen/lac`.

- [ ] Step 1: Show the diff — `/api/models` model count

Replace lines 133-136:

```html
<div class="endpoint">
  <div class="head"><span class="method get">GET</span> <code>/api/models</code></div>
  <p>List all models in the curated database (65+ models). Returns id, name, provider, params, architecture, context, VRAM estimates.</p>
</div>
```

with:

```html
<div class="endpoint">
  <div class="head"><span class="method get">GET</span> <code>/api/models</code></div>
  <p>List all models in the curated database (91 models). Returns id, name, provider, params, architecture, context, VRAM estimates.</p>
</div>
```

- [ ] Step 2: Show the diff — `/api/system/version` example URL

Replace line 141:

```html
  <details><summary>Example</summary><pre><code>{"version": "2.2.0", "app_name": "LAC", "github_url": "https://github.com/Dkrynen/model-hub", "download_url": "https://github.com/Dkrynen/model-hub/releases"}</code></pre></details>
```

with:

```html
  <details><summary>Example</summary><pre><code>{"version": "2.2.0", "app_name": "LAC", "github_url": "https://github.com/Dkrynen/lac", "download_url": "https://github.com/Dkrynen/lac/releases"}</code></pre></details>
```

- [ ] Step 3: Show the diff — `/api/scan` example response (add `combined_vram_gb`, `compute_tiers`)

Replace lines 93-102:

```html
  <details><summary>Example response</summary><pre><code>{
  "os": "Windows 10",
  "cpu": "AMD64 Family 25 Model 33",
  "cores": 16,
  "ram_gb": 32.0,
  "gpus": [{"name": "NVIDIA GeForce RTX 4090", "vram_gb": 24.0, "backend": "cuda"}],
  "total_vram_gb": 24.0,
  "is_apple_silicon": false,
  "in_container": false
}</code></pre></details>
```

with:

```html
  <details><summary>Example response</summary><pre><code>{
  "os": "Windows 10",
  "cpu": "AMD64 Family 25 Model 33",
  "cores": 16,
  "ram_gb": 32.0,
  "gpus": [{"name": "NVIDIA GeForce RTX 4090", "vram_gb": 24.0, "backend": "cuda", "tier": "discrete", "device_index": 0}],
  "total_vram_gb": 24.0,
  "combined_vram_gb": 24.0,
  "compute_tiers": [{"name": "NVIDIA GeForce RTX 4090", "memory_gb": 24.0, "backend": "cuda", "kind": "discrete", "device_index": 0}],
  "is_apple_silicon": false,
  "in_container": false
}</code></pre></details>
```

- [ ] Step 4: Show the diff — `/api/recommend` param table + example response

Replace lines 105-131:

```html
<div class="endpoint">
  <div class="head"><span class="method get">GET</span> <code>/api/recommend?vram=24&amp;use_case=coding&amp;top_k=10</code></div>
  <p>Get model recommendations based on VRAM, use case, and count. Uses live hardware scan if <code>vram</code> omitted.</p>
  <table>
    <tr><th>Param</th><th>Type</th><th>Description</th></tr>
    <tr><td><code>vram</code></td><td>float</td><td>VRAM in GB (optional, defaults to scanned value)</td></tr>
    <tr><td><code>use_case</code></td><td>string</td><td>One of <code>coding</code>, <code>general</code>, <code>reasoning</code>, <code>chat</code></td></tr>
    <tr><td><code>top_k</code></td><td>int</td><td>Max recommendations (default 5)</td></tr>
  </table>
  <details><summary>Example response</summary><pre><code>{
  "vram_gb": 24.0,
  "ram_gb": 32.0,
  "recommendations": [{
    "name": "Qwen3 14B",
    "model_id": "qwen3:14b",
    "provider": "ollama",
    "params_b": 14.8,
    "quant": "Q4_K_M",
    "score": 92,
    "vram_gb": 9.5,
    "context": 32768,
    "run_mode": "gpu",
    "ollama_cmd": "ollama run qwen3:14b",
    "scores": {"quality": 9, "speed": 8, "fit": 10, "context": 9}
  }]
}</code></pre></details>
</div>
```

with:

```html
<div class="endpoint">
  <div class="head"><span class="method get">GET</span> <code>/api/recommend?vram=24&amp;use_case=coding&amp;top_k=10</code></div>
  <p>Get model recommendations based on VRAM, use case, and count. Uses live hardware scan if <code>vram</code> omitted.</p>
  <table>
    <tr><th>Param</th><th>Type</th><th>Description</th></tr>
    <tr><td><code>vram</code></td><td>float</td><td>Manual VRAM override in GB (optional, defaults to scanned value)</td></tr>
    <tr><td><code>use_case</code></td><td>string</td><td>One of <code>coding</code>, <code>general</code>, <code>reasoning</code>, <code>chat</code></td></tr>
    <tr><td><code>top_k</code></td><td>int</td><td>Max recommendations (default 5)</td></tr>
    <tr><td><code>gpu_mask</code></td><td>string</td><td>Comma-separated GPU device indices to include (e.g. <code>0</code> or <code>0,1</code>); a mask matching no real GPU is ignored</td></tr>
    <tr><td><code>allow_spill</code></td><td>int</td><td><code>1</code> (default) allows RAM/CPU-offload split plans; <code>0</code> restricts recommendations to GPU-resident-only</td></tr>
    <tr><td><code>no_calibration</code></td><td>int</td><td><code>1</code> ignores measured benchmarks in <code>results.jsonl</code> and always returns theoretical estimates; default <code>0</code> uses calibration when available</td></tr>
  </table>
  <details><summary>Example response</summary><pre><code>{
  "vram_gb": 24.0,
  "combined_vram_gb": 24.0,
  "ram_gb": 32.0,
  "recommendations": [{
    "name": "Qwen3 14B",
    "model_id": "qwen3:14b",
    "provider": "ollama",
    "params_b": 14.8,
    "quant": "Q4_K_M",
    "score": 92,
    "vram_gb": 9.5,
    "context": 32768,
    "run_mode": "gpu",
    "ollama_cmd": "ollama run qwen3:14b",
    "speed_source": "calibrated",
    "speed_band_pct": 12.5,
    "scores": {"quality": 9, "speed": 8, "fit": 10, "context": 9},
    "split_plan": null
  }]
}</code></pre></details>
</div>
```

- [ ] Step 5: Show the diff — Guide "Recommendations" model count

Replace line 368:

```html
<p>LAC scores 65+ models across four dimensions to find the best fit for your hardware:</p>
```

with:

```html
<p>LAC scores 91 models across four dimensions to find the best fit for your hardware:</p>
```

- [ ] Step 6: Show the diff — "Viewing installed models" (remove the nonexistent search box)

Replace line 412:

```html
<p>The <strong>Installed</strong> page lists all local models with name, size, and last modified date. Use the search box to filter. The <strong>Currently Running</strong> section shows models loaded in GPU memory.</p>
```

with:

```html
<p>The <strong>Installed</strong> page lists all local models with name, size, and last modified date. The <strong>Running now</strong> section at the top shows models currently loaded in memory.</p>
```

- [ ] Step 7: Show the diff — "Running a model" (real Chat-button behavior, not clipboard)

Replace lines 414-415:

```html
<h3>Running a model</h3>
<p>Click <kbd>Run</kbd> next to any installed model to copy the <code>ollama run &lt;name&gt;</code> command to your clipboard.</p>
```

with:

```html
<h3>Chatting with an installed model</h3>
<p>Click <kbd>Chat</kbd> next to any installed model to open it directly in the <strong>Chat</strong> page, ready to talk to.</p>
```

- [ ] Step 8: Show the diff — "Sessions" (no web UI exists; describe the real CLI/TUI surface)

Replace lines 455-456:

```html
<h3>Sessions</h3>
<p>Sessions let you save and resume conversations. In the web UI, click <kbd>Sessions</kbd> to manage saved conversations. In the CLI, use <code>/save my-conversation</code> and <code>/load my-conversation</code>. Sessions are scoped to the current workspace.</p>
```

with:

```html
<h3>Sessions</h3>
<p>Sessions are a CLI/API feature today — the web <strong>Chat</strong> page always starts a fresh conversation and has no save/load UI yet. From the TUI (<code>lac chat</code>), use <code>/save my-conversation</code>, <code>/load my-conversation</code>, and <code>/list</code>. From the shell, <code>lac session list</code>, <code>lac session export &lt;id&gt;</code>, and <code>lac session import &lt;path&gt;</code> work against the same saved sessions, scoped to the current workspace.</p>
```

- [ ] Step 9: Show the diff — Workspaces "Web UI" section (no dropdown/gear icon exists)

Replace lines 492-498:

```html
<h3>Web UI</h3>
<ul>
  <li>The workspace dropdown is in the sidebar, below the logo.</li>
  <li>Select a workspace from the dropdown to switch.</li>
  <li>Click the gear icon to create, rename, or delete workspaces.</li>
  <li>Only the active workspace's sessions are shown.</li>
</ul>
```

with:

```html
<h3>Web UI</h3>
<ul>
  <li>The <strong>Settings</strong> page shows the currently active workspace (read-only).</li>
  <li>Creating, switching, and deleting workspaces is CLI-only today — see below.</li>
</ul>
```

- [ ] Step 10: Show the diff — stale GitHub repo URLs in the troubleshooting/feedback footers

Replace line 555:

```html
Found a bug? <a href="https://github.com/Dkrynen/model-hub/issues" target="_blank">Open an issue on GitHub</a>.
```

with:

```html
Found a bug? <a href="https://github.com/Dkrynen/lac/issues" target="_blank">Open an issue on GitHub</a>.
```

Replace lines 561-562:

```html
  <a href="https://github.com/Dkrynen/model-hub/issues" target="_blank">&#128220; Report an issue</a>
  <a href="https://github.com/Dkrynen/model-hub" target="_blank">&#128279; View on GitHub</a>
```

with:

```html
  <a href="https://github.com/Dkrynen/lac/issues" target="_blank">&#128220; Report an issue</a>
  <a href="https://github.com/Dkrynen/lac" target="_blank">&#128279; View on GitHub</a>
```

- [ ] Step 11: Run verification

Run (Git Bash, from `C:\Users\User\repos\model-hub`):

```bash
grep -n "65+\|Dkrynen/model-hub\|workspace dropdown\|gear icon\|copy the.*ollama run\|Use the search box" frontend/docs.html
```

Expected: no output (all stale claims removed).

Then run the existing docs-route test to confirm nothing broke structurally:

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_api.py::test_docs_route`
Expected: PASS (still 200 for `/docs`, `/docs/api`, `/docs/guide`).

- [ ] Step 12: Commit

```bash
cd "C:\Users\User\repos\model-hub"
git add frontend/docs.html
git commit -m "docs: content pass on frontend/docs.html — remove fabricated web-UI features, fix stale repo URLs and model count

Removed claims of features that don't exist (a Sessions management page,
a workspace dropdown + gear icon in the sidebar, a Run-to-clipboard
button, an Installed-page search box); corrected '65+ models' to the real
91; added the missing speed_source/speed_band_pct/split_plan/
combined_vram_gb/compute_tiers fields and gpu_mask/allow_spill/
no_calibration params to the API examples; fixed 4 stale
github.com/Dkrynen/model-hub URLs to Dkrynen/lac."
```

---

### Task 15: CLI minor-bug sweep (inspect size, download size, session-import crash)

**Files:**
- Modify: `cli.py:539-568` (`cmd_inspect`), `cli.py:467-499` (`cmd_pull`), `cli.py:699-707` (`cmd_session`'s `import` action)
- Test: `tests/test_cli_reporting.py` (new)

**Interfaces:**
- Consumes: `cli.ollama(method, path, body=None, timeout=30) -> dict` (existing), `cli.ollama_stream(path, body, timeout=300) -> Iterator[dict]` (existing), `backend.cookbook.export.import_session(path) -> dict` (existing, raises `FileNotFoundError`)
- Produces: `lac inspect <model>` shows a real size instead of always `0.0 GB`; `lac pull <model>` logs a real `size_gb` instead of always `0`; `lac session import <missing-file>` exits cleanly with a message instead of a raw traceback.

- [ ] Step 1: Write the failing tests

Create `tests/test_cli_reporting.py`:

```python
from __future__ import annotations

import argparse

import pytest


def test_cli_inspect_shows_real_size_from_tags(monkeypatch, capsys):
    """/api/show has no top-level 'size' field (only /api/tags does), so
    cmd_inspect's result.get('size', 0) always read 0.0 GB."""
    import cli as cli_mod

    def fake_ollama(method, path, body=None, timeout=30):
        if path == "/api/show":
            return {
                "details": {"parameter_size": "8B", "quantization_level": "Q4_K_M",
                             "family": "qwen3", "format": "gguf"},
                "modified_at": "2026-01-01",
            }
        if path == "/api/tags":
            return {"models": [{"name": "qwen3:8b", "size": 5_000_000_000}]}
        return {"error": "unexpected path"}

    monkeypatch.setattr(cli_mod, "ollama", fake_ollama)
    args = argparse.Namespace(model="qwen3:8b")
    cli_mod.cmd_inspect(args)

    out = capsys.readouterr().out
    assert "4.66 GB" in out


def test_cli_pull_logs_real_size_not_zero(monkeypatch, tmp_path):
    """_log_download read chunk.get('total') from the terminal 'success'
    chunk, which Ollama never populates there -- size_gb was always 0."""
    import cli as cli_mod

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    def fake_stream(path, body, timeout=300):
        yield {"status": "pulling manifest"}
        yield {"status": "downloading", "completed": 500_000_000, "total": 1_000_000_000}
        yield {"status": "success"}

    monkeypatch.setattr(cli_mod, "ollama_stream", fake_stream)
    # lac-pro is installed as a real plugin in this venv ("pro", confirmed
    # via backend.plugins.discover()) -- on_model_installed would otherwise
    # fire a REAL autopilot benchmark against live Ollama. Not what this
    # test is checking; stub it out.
    monkeypatch.setattr(cli_mod, "_notify_model_installed", lambda model_name: None)
    args = argparse.Namespace(model="test-model:1b")
    cli_mod.cmd_pull(args)

    history = cli_mod._download_history()
    assert any(
        h["model"] == "test-model:1b" and h["status"] == "completed" and h["size_gb"] > 0
        for h in history
    )


def test_cli_session_import_missing_file_exits_clean(capsys):
    import cli as cli_mod

    args = argparse.Namespace(action="import", path="/definitely/does/not/exist.json")
    with pytest.raises(SystemExit) as e:
        cli_mod.cmd_session(args)
    assert e.value.code == 1
    assert "File not found" in capsys.readouterr().err
```

- [ ] Step 2: Run tests to verify they fail

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_cli_reporting.py`
Expected: `test_cli_inspect_shows_real_size_from_tags` FAILS (`"4.66 GB" in out` is false — the printed size is `"0.0 GB"`). `test_cli_pull_logs_real_size_not_zero` FAILS (`h["size_gb"] > 0` — it's `0`). `test_cli_session_import_missing_file_exits_clean` FAILS — an unhandled `FileNotFoundError` propagates out of `cmd_session` instead of a clean `SystemExit(1)`.

- [ ] Step 3: Fix `cmd_inspect`

Replace lines 539-555:

```python
def cmd_inspect(args):
    model = args.model
    result = ollama("POST", f"/api/show", {"name": model})
    if "error" in result:
        eprint(f"{C['red']}{result['error']}{C['reset']}")
        sys.exit(1)

    print_header(f"Model: {model}")
    details = result.get("details", {})
    info_rows = [
        ["Parameters", details.get("parameter_size", "?")],
        ["Quantization", details.get("quantization_level", "?")],
        ["Family", details.get("family", "?")],
        ["Format", details.get("format", "?")],
        ["Size", f"{round(result.get('size', 0) / (1024**3), 2)} GB"],
        ["Modified", result.get("modified_at", "?")],
    ]
```

with:

```python
def cmd_inspect(args):
    model = args.model
    result = ollama("POST", f"/api/show", {"name": model})
    if "error" in result:
        eprint(f"{C['red']}{result['error']}{C['reset']}")
        sys.exit(1)

    # /api/show has no top-level 'size' field -- only /api/tags does.
    size_bytes = 0
    tags = ollama("GET", "/api/tags")
    if "error" not in tags:
        for m in tags.get("models", []):
            if m.get("name") == model:
                size_bytes = m.get("size", 0)
                break

    print_header(f"Model: {model}")
    details = result.get("details", {})
    info_rows = [
        ["Parameters", details.get("parameter_size", "?")],
        ["Quantization", details.get("quantization_level", "?")],
        ["Family", details.get("family", "?")],
        ["Format", details.get("format", "?")],
        ["Size", f"{round(size_bytes / (1024**3), 2)} GB"],
        ["Modified", result.get("modified_at", "?")],
    ]
```

- [ ] Step 4: Fix `cmd_pull` — track the last real `total` across the stream

Replace lines 472-499:

```python
    success = False
    for chunk in ollama_stream("/api/pull", {"name": model}, timeout=3600):
        if "error" in chunk:
            eprint(f"\n{C['red']}Error: {chunk['error']}{C['reset']}")
            _log_download(model, "failed")
            sys.exit(1)
        status = chunk.get("status", "")
        if status:
            completed = chunk.get("completed", 0)
            total = chunk.get("total", 0)
            if total and completed:
                pct = int(completed / total * 100)
                bar = "█" * (pct // 2) + "░" * (50 - pct // 2)
                print(f"\r{C['cyan']}[{bar}]{C['reset']} {pct}% - {status}", end="", flush=True)
            else:
                print(f"\r  {C['dim']}{status}{C['reset']}", end="", flush=True)
        if chunk.get("status") == "success":
            success = True
            size_gb = 0
            if chunk.get("total"):
                size_gb = round(chunk["total"] / (1024**3), 2)
            print(f"\n\n{C['green']}✓ {model} installed successfully!{C['reset']}")
            _log_download(model, "completed", size_gb)
            _notify_model_installed(model)

    if not success:
        print(f"\n{C['yellow']}Pull may still be in progress. Check 'lac list'.{C['reset']}")
        _log_download(model, "incomplete")
```

with:

```python
    success = False
    last_total = 0
    for chunk in ollama_stream("/api/pull", {"name": model}, timeout=3600):
        if "error" in chunk:
            eprint(f"\n{C['red']}Error: {chunk['error']}{C['reset']}")
            _log_download(model, "failed")
            sys.exit(1)
        status = chunk.get("status", "")
        if status:
            completed = chunk.get("completed", 0)
            total = chunk.get("total", 0)
            if total:
                last_total = total
            if total and completed:
                pct = int(completed / total * 100)
                bar = "█" * (pct // 2) + "░" * (50 - pct // 2)
                print(f"\r{C['cyan']}[{bar}]{C['reset']} {pct}% - {status}", end="", flush=True)
            else:
                print(f"\r  {C['dim']}{status}{C['reset']}", end="", flush=True)
        if chunk.get("status") == "success":
            success = True
            # The terminal "success" chunk never carries 'total' itself --
            # use the last real total seen during the download.
            size_gb = round(last_total / (1024**3), 2) if last_total else 0
            print(f"\n\n{C['green']}✓ {model} installed successfully!{C['reset']}")
            _log_download(model, "completed", size_gb)
            _notify_model_installed(model)

    if not success:
        print(f"\n{C['yellow']}Pull may still be in progress. Check 'lac list'.{C['reset']}")
        _log_download(model, "incomplete")
```

- [ ] Step 5: Fix `cmd_session`'s `import` action

Replace lines 699-707:

```python
    if action == "import":
        if not args.path:
            eprint(f"{C['red']}session import requires <path>{C['reset']}")
            sys.exit(1)
        data = import_session(args.path)
        sid = data.get("id") or create_session(model=data.get("model", ""))
        save_session(sid, model=data.get("model", ""), messages=data.get("messages", []))
        print(f"{C['green']}Imported session {sid[:12]}{C['reset']}  {C['dim']}({len(data.get('messages', []))} messages, model={data.get('model','')}){C['reset']}")
        return
```

with:

```python
    if action == "import":
        if not args.path:
            eprint(f"{C['red']}session import requires <path>{C['reset']}")
            sys.exit(1)
        try:
            data = import_session(args.path)
        except FileNotFoundError:
            eprint(f"{C['red']}File not found: {args.path}{C['reset']}")
            sys.exit(1)
        sid = data.get("id") or create_session(model=data.get("model", ""))
        save_session(sid, model=data.get("model", ""), messages=data.get("messages", []))
        print(f"{C['green']}Imported session {sid[:12]}{C['reset']}  {C['dim']}({len(data.get('messages', []))} messages, model={data.get('model','')}){C['reset']}")
        return
```

- [ ] Step 6: Run tests to verify they pass

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_cli_reporting.py`
Expected: all 3 tests PASS.

Then run the full suite: `.venv\Scripts\python.exe -m pytest -q`
Expected: all tests pass.

This is CLI-only — no server restart consideration applies.

- [ ] Step 7: Commit

```bash
cd "C:\Users\User\repos\model-hub"
git add cli.py tests/test_cli_reporting.py
git commit -m "fix: lac inspect always showed Size: 0.0 GB, lac pull always logged size_gb=0, lac session import crashed on a missing file"
```

---

### Task 16: Frontend dead-code sweep

**Files:**
- Delete: `web/src/components/ui/dialog.tsx`
- Modify: `web/package.json:25` (remove the `@radix-ui/react-dialog` dependency), `web/package-lock.json` (regenerated by `npm install`)
- Modify: `web/src/pages/browse.tsx:15` (remove unused `verdictFromFit` import)
- Modify: `web/src/components/markdown.tsx:57-61` (remove unused `escapeHtml` function)
- Test: none (no component-test harness) — verify via `npm run typecheck && npm run build`

**Interfaces:**
- Consumes: none
- Produces: no runtime behavior change — this removes code confirmed (via grep across `web/src`) to have zero remaining references.

- [ ] Step 1: Delete the unused Dialog wrapper

Run: `rm "C:\Users\User\repos\model-hub\web\src\components\ui\dialog.tsx"` (or delete via your editor)

Confirmed via `grep -rn "from \"@/components/ui/dialog\"\|BenchmarkDialog" web/src` that nothing imports it (it became dead after `BenchmarkDialog`'s removal).

- [ ] Step 2: Remove the now-unused radix dependency

In `web/package.json`, remove line 25:

```json
    "@radix-ui/react-dialog": "^1.1.2",
```

(leaving the surrounding `dependencies` block's other `@radix-ui/*` entries untouched).

- [ ] Step 3: Remove the unused import in `browse.tsx`

Replace lines 13-16:

```tsx
import { useAsync } from "@/lib/hooks";
import { api } from "@/lib/api";
import { verdictFromFit } from "@/components/verdict";
import { pullWithToast } from "@/lib/installer";
```

with:

```tsx
import { useAsync } from "@/lib/hooks";
import { api } from "@/lib/api";
import { pullWithToast } from "@/lib/installer";
```

- [ ] Step 4: Remove the unused function in `markdown.tsx`

Replace lines 56-61:

```tsx
  return <p>{inline(text)}</p>;
}

function escapeHtml(s: string) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** Render inline markdown to React nodes (bold, italic, code, links). */
```

with:

```tsx
  return <p>{inline(text)}</p>;
}

/** Render inline markdown to React nodes (bold, italic, code, links). */
```

- [ ] Step 5: Regenerate the lockfile

Run:

```bash
cd "C:\Users\User\repos\model-hub\web"
npm install
```

Expected: exits 0; `package-lock.json` updates to drop `@radix-ui/react-dialog` and its now-unneeded transitive entries; `node_modules/@radix-ui/react-dialog` is removed.

- [ ] Step 6: Run verification

Run: `cd C:\Users\User\repos\model-hub\web && npm run typecheck && npm run build`
Expected: both exit 0 with no errors (confirms no remaining reference to the deleted file/import/function anywhere in the build graph).

- [ ] Step 7: Commit

```bash
cd "C:\Users\User\repos\model-hub"
git add web/src/components/ui/dialog.tsx web/package.json web/package-lock.json web/src/pages/browse.tsx web/src/components/markdown.tsx
git commit -m "chore: remove dead code — unused Dialog wrapper + @radix-ui/react-dialog dep, unused verdictFromFit import, unused escapeHtml function"
```

---

### Task 17: `lac pro insights` summary can contradict its own regression table

**Files:**
- Modify: `lac_pro/insights.py:57-62` (`cmd_insights`)
- Test: `lac-pro/tests/test_insights.py` (append)

**Interfaces:**
- Consumes: `analyze(rows, window, threshold) -> list[dict]` (existing, unchanged — each entry has `delta_pct: float` and `regression: bool`)
- Produces: the closing summary line now only claims "All models at or above baseline" when that's literally true (every `delta_pct >= 0`); otherwise it reports that nothing crossed the alarm threshold, without implying zero slowdown.

- [ ] Step 1: Write the failing test

Append to `C:\Users\User\repos\lac-pro\tests\test_insights.py`:

```python
def test_cli_insights_summary_reflects_sub_threshold_decline(monkeypatch, capsys, tmp_path):
    """A model that's slower than baseline but under the alarm threshold
    (~-8%, threshold 15%) must not be summarized as 'All models at or
    above baseline' -- that directly contradicts the negative delta shown
    in the table just above it."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LAC_PRO_DEV", "1")

    rows = _rows("m", [100, 100, 100, 100, 100, 92, 91, 93, 92, 91])

    import backend.cookbook.benchmark as bench_mod
    monkeypatch.setattr(bench_mod, "history", lambda: rows)

    import argparse
    from lac_pro.plugin import PLUGIN

    parser = argparse.ArgumentParser(prog="lac")
    sub = parser.add_subparsers(dest="command")
    PLUGIN.register_cli(sub)
    args = parser.parse_args(["pro", "insights"])
    args.func(args)

    out = capsys.readouterr().out
    assert "All models at or above baseline" not in out
```

- [ ] Step 2: Run test to verify it fails

Run: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q tests/test_insights.py::test_cli_insights_summary_reflects_sub_threshold_decline` (from `C:\Users\User\repos\lac-pro`)
Expected: FAIL — the model's `delta_pct` (~-8%) is above the -15% regression threshold, so `regs` is empty and the current code unconditionally prints "All models at or above baseline." even though this model actually declined.

- [ ] Step 3: Fix `lac_pro/insights.py`

Replace lines 57-62 (the end of `cmd_insights`):

```python
    regs = [r for r in results if r["regression"]]
    if regs:
        print(f"\n{len(regs)} model(s) slower than baseline — driver update, background load, "
              f"or a stack change (re-run `lac pro benchmark` to recalibrate).")
    else:
        print("\nAll models at or above baseline.")
```

with:

```python
    regs = [r for r in results if r["regression"]]
    if regs:
        print(f"\n{len(regs)} model(s) slower than baseline — driver update, background load, "
              f"or a stack change (re-run `lac pro benchmark` to recalibrate).")
    elif all(r["delta_pct"] >= 0 for r in results):
        print("\nAll models at or above baseline.")
    else:
        print(f"\nNo regressions past the {args.threshold:.0f}% threshold.")
```

- [ ] Step 4: Run test to verify it passes

Run: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q tests/test_insights.py` (from `C:\Users\User\repos\lac-pro`)
Expected: all tests PASS.

Then run the full lac-pro suite: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q` (from `C:\Users\User\repos\lac-pro`)
Expected: all tests pass.

This is CLI-only — no server restart consideration applies.

- [ ] Step 5: Commit

```bash
cd "C:\Users\User\repos\lac-pro"
git add lac_pro/insights.py tests/test_insights.py
git commit -m "fix: lac pro insights summary could claim 'all models at or above baseline' while a sub-threshold decline sat in the table above it"
```

---

## Self-Review

**Spec coverage** — every one of the 16 Critical/Important findings maps to a task:

| Finding | Task |
|---|---|
| 1. Path traversal (workspace create/delete) | Task 1 |
| 2. Pro license blocked by Cloudflare | Task 2 |
| 3. Unicode crash on CLI write-paths | Task 3 |
| 4. `lac browse` always empty | Task 4 |
| 5. Workspace switching 500 | Task 5 |
| 6. Downloads page empty for web installs | Task 6 |
| 7. `lac pro tune`/`benchmark` raw traceback | Task 7 |
| 8. "Measured" badge never appears | Task 8 |
| 9. Ollama-proxy routes 500 on non-object JSON | Task 9 |
| 10. `/api/ollama/delete` false success | Task 9 |
| 11. Rebrand leaks (export, tools UA, check-update) | Task 11 |
| 12. Hybrid iGPU+dGPU VRAM double-counting | Task 10 |
| 13. `docs.html` fabricated features | Task 14 |
| 14. Downloads page hides errors as empty state | Task 12 |
| 15. `SourceBadge` no defensive fallback | Task 13 |
| 16. Dead Vision/Writing use cases | Task 13 |

Minor findings folded in: `--top-k` validation → Task 4; `ollama/status` version always "unknown" → Task 9; API error responses inconsistent (400/405 HTML) → Task 9; manual VRAM override's stale `combined_vram_gb` → Task 10; unused icon imports in `downloads.tsx` → Task 12; `lac inspect` size, download-history size, `session import` crash → Task 15; dead code (`dialog.tsx` + radix dep, `browse.tsx`/`markdown.tsx` unused imports) → Task 16; `lac pro insights` contradiction → Task 17.

**Not folded in (flagged, not guessed):** none of the 8 minor findings were left unaddressed — every one has a home above. No finding (Critical, Important, or Minor) was left without a concrete file/line.

**Placeholder scan:** no "TBD"/"add appropriate error handling"/"similar to Task N" phrasing anywhere in the 17 tasks above — every step shows real, complete code, real commands, and real expected output. Verified inline while writing; none found on re-read.

**Type/interface consistency:** `enrich_library_models(models, system_vram)` (Task 4) is called identically from both `backend/api.py` and `cli.py`. `log_download`/`download_history` (Task 6) are called identically from `cli.py`'s delegating wrappers and `backend/api.py`'s two call sites (`ollama_pull`'s `generate()` and `api_config_downloads`). `_resolve_within_workspaces` (Task 1) is used by both `create_workspace` and `delete_workspace` with the same raise/no-raise contract each caller expects. `_tier_capacity` (Task 10) is used identically in both the per-tier allocation loop and the `total_capacity` check within `_compute_split_plan`. Task 9's routes and Task 6's `ollama_pull` rewrite both touch the same function — Task 9's "Fix" step in Step 4 is written against the **post-Task-6** version of `ollama_pull` (already includes the `log_download`/`last_total` logic), so applying the tasks in order produces no conflict.

**Files touched per repo:** `model-hub` — `backend/cookbook/config.py`, `backend/cookbook/library.py` (new), `backend/cookbook/downloads.py` (new), `backend/cookbook/calibration.py`, `backend/cookbook/recommend.py`, `backend/cookbook/export.py`, `backend/plugin/builtins/tools.py`, `backend/api.py`, `cli.py`, `frontend/docs.html`, `web/src/pages/downloads.tsx`, `web/src/pages/scan.tsx`, `web/src/pages/browse.tsx`, `web/src/components/markdown.tsx`, `web/src/components/ui/dialog.tsx` (deleted), `web/package.json`, `web/package-lock.json`, plus 9 test files (5 new, 4 appended). `lac-pro` — `lac_pro/ls.py`, `lac_pro/tune.py`, `lac_pro/benchmark_cli.py`, `lac_pro/insights.py`, plus appends to `tests/test_ls.py`, `tests/test_tune.py`, `tests/test_benchmark_cli.py`, `tests/test_insights.py`.

Plan saved to: `C:\Users\User\repos\model-hub\docs\superpowers\plans\2026-07-05-lac-audit-fixes.md`
