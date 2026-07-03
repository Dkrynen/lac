# APT v2 — W4 Security Posture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden APT's real attack surface — supply chain (CI + Dependabot), the local server bind, the plugin trust seam, and the update/download path — with honest, verifiable copy for the unsigned-installer reality.

**Architecture:** Four thin layers, no new services. (1) GitHub-native supply-chain CI: a new `security.yml` workflow (CodeQL python+javascript, pip-audit, npm audit) plus `dependabot.yml`. (2) A pure, testable `bind_warning()` helper in `server.py` that prints a loud banner when the Flask server binds beyond loopback. (3) A `dist` (providing distribution) column threaded through `backend/plugins.py` → `apt plugins` → `GET /api/plugins`, plus trust-rule docs. (4) A `SHA256SUMS` release asset in `build.yml` plus SmartScreen/checksum-verify copy in README and the landing page — and one real bug fix found during exploration: `backend/update.py`'s upgrade command targets PyPI's unrelated `apt` package instead of our dist `apt-hub`.

**Tech Stack:** GitHub Actions (CodeQL v3, pip-audit, `npm audit`), Dependabot v2 config, Python 3.11 (stdlib `importlib.metadata`, Flask, argparse), pytest, PyYAML 6.0.3 (verified installed in the venv) for local YAML validation.

## Global Constraints

- Repo: `C:\Users\User\repos\model-hub` (Windows). All paths below are relative to this root unless absolute.
- Python: `.venv\Scripts\python.exe` — always this interpreter, never system python.
- Test suite baseline (verified 2026-07-03): `.venv\Scripts\python.exe -m pytest -q` → **200 tests collected, exit 0** (195 passed + 5 skipped "Ollama not running" — the skips are normal without a live Ollama).
- **Nothing is pushed to origin until Duan's explicit go** (spec §2.1 "Overhaul first, launch once" + §8 cross-cutting). Commit locally on the current branch; never `git push`.
- **No code signing tasks** — deferred post-revenue (spec §2.7, locked). Do not add signing steps, cert handling, or signtool anything.
- No servers of ours: distribution = GitHub CDN, licensing = LemonSqueezy (spec §2.9). No telemetry, no phone-home beyond the existing GitHub Releases version check.
- Repo is **public** → CodeQL and Dependabot are free; no paid GitHub features assumed.
- Dist name is `apt-hub` (`pyproject.toml [project] name`); the Windows installer artifact is `Model-Hub-Setup-x.x.x.exe`; releases live at `https://github.com/Dkrynen/model-hub/releases`.
- Voice for all user-facing copy: sharp, technical, calm, no hype (APT's voice, spec §3).
- Commit style: conventional commits (`ci:`, `feat:`, `fix:`, `docs:`), as in recent history.
- W4 runs **last** (build order W3 → W1 → W5 → W2 → W4, spec §1). W3 re-skins `site/index.html` and README branding — anchor edits to those files by **content** (the download CTA / Install section), not line numbers, and adapt if the surrounding markup changed.

---

## Context for the engineer (read once, saves an hour)

- `.github/workflows/` currently contains exactly three files: `build.yml` (tag-triggered PyInstaller + InnoSetup build, draft release of the Windows installer only), `test.yml` (pytest matrix on 3 OSes + web typecheck/build), `pages.yml` (GitHub Pages deploy of `site/`). There is **no** `security.yml` and **no** `.github/dependabot.yml` yet.
- `server.py:14` — `HOST = "127.0.0.1"` is already the verified default; only an explicit `--host` flag widens the bind (spec §6.2). The gap is purely that widening it is *silent* today.
- `backend/update.py` — verified during planning: the only network endpoint is `GITHUB_API = "https://api.github.com/repos/Dkrynen/model-hub/releases/latest"` (HTTPS, GitHub Releases API). `server.py:30` uses the same URL inline. **No fix needed on endpoints.** But `upgrade_command()` returns `pip install --upgrade apt` / `uv pip install --upgrade apt` — and `do_update()` **executes** that for pip/uv installs. PyPI's `apt` is an unrelated (squattable) name; our dist is `apt-hub`. Task 5 fixes this.
- `backend/plugins.py` — `discover()` loads `apt.plugins` entry points into `LoadedPlugin(name, version, obj, error)` records with per-plugin error isolation. `cli.py:1123` `cmd_plugins()` prints a Name/Version/Status table; `backend/api.py:909` `api_plugins()` serves the same as JSON. None of them show *which installed distribution provides the plugin* — that's the origin column Task 6 adds.
- Tests live in `tests/` (flat), `pytest.ini` sets `testpaths = tests`, `addopts = -ra -q`, asyncio auto mode. `tests/conftest.py` puts the repo root on `sys.path`, so `from server import bind_warning` and `import cli` both work in tests.
- PyYAML is in `requirements.txt` and **verified present in the venv (6.0.3)** — the local YAML validation commands below are real.

---

### Task 1: Supply-chain CI workflow (`security.yml`)

**Files:**
- Create: `.github/workflows/security.yml`

**Interfaces:**
- Consumes: `requirements.txt` (repo root), `web/package-lock.json` (both exist today).
- Produces: three CI job names later referenced by branch protection (Task 9): `codeql (python)`, `codeql (javascript)`, `pip-audit`, `npm-audit`.

- [ ] **Step 1: Write the complete workflow file**

Create `.github/workflows/security.yml` with exactly this content:

```yaml
name: security

on:
  pull_request:
    branches: [main, master]
  schedule:
    # Weekly sweep, Monday 06:00 UTC. Scheduled runs execute on the default
    # branch, which also keeps the CodeQL baseline current.
    - cron: '0 6 * * 1'

permissions:
  contents: read

jobs:
  codeql:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    permissions:
      contents: read
      security-events: write
    strategy:
      fail-fast: false
      matrix:
        language: [python, javascript]
    steps:
      - uses: actions/checkout@v4

      - name: Initialize CodeQL
        uses: github/codeql-action/init@v3
        with:
          languages: ${{ matrix.language }}

      - name: Analyze
        uses: github/codeql-action/analyze@v3
        with:
          category: "/language:${{ matrix.language }}"

  pip-audit:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install pip-audit
        run: pip install pip-audit

      - name: Audit Python dependencies
        run: pip-audit -r requirements.txt

  npm-audit:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4

      - name: Set up Node
        uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: npm
          cache-dependency-path: web/package-lock.json

      - name: Audit npm dependencies (high and above)
        working-directory: web
        run: npm audit --audit-level=high
```

Notes baked into the design (don't second-guess them):
- Repo is public → CodeQL is free; `javascript` covers the TypeScript in `web/` (CodeQL's `javascript` language includes TS).
- CodeQL v3 with no build step is correct for Python and JS — no `pip install` needed for analysis.
- `requirements.txt` uses `>=` floors; `pip-audit -r` resolves them to concrete versions at run time and audits those — that is the intended behavior (audits what a fresh install would get today).
- `npm audit --audit-level=high` exits non-zero only on high/critical advisories — that's the gate; moderate noise doesn't block.

- [ ] **Step 2: Validate the YAML parses**

Run (from `C:\Users\User\repos\model-hub`):

```
.venv\Scripts\python.exe -c "import yaml; yaml.safe_load(open('.github/workflows/security.yml')); print('security.yml OK')"
```

Expected: `security.yml OK` (exit 0). If PyYAML were somehow missing, `pip show pyyaml` first — it was verified at 6.0.3 during planning.

- [ ] **Step 3: Confirm the test suite is untouched**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: 200 collected, exit 0 (195 passed + 5 skipped). A workflow file cannot affect the suite; this is the cheap regression tripwire before committing.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/security.yml
git commit -m "ci: add supply-chain security workflow (CodeQL + pip-audit + npm audit)"
```

---

### Task 2: Dependabot configuration

**Files:**
- Create: `.github/dependabot.yml`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: weekly dependency PRs (pip at `/`, npm at `/web`, github-actions at `/`) once pushed — nothing local depends on it.

- [ ] **Step 1: Write the complete config**

Create `.github/dependabot.yml` (note: lives in `.github/`, NOT in `.github/workflows/`) with exactly this content:

```yaml
version: 2
updates:
  - package-ecosystem: pip
    directory: "/"
    schedule:
      interval: weekly

  - package-ecosystem: npm
    directory: "/web"
    schedule:
      interval: weekly

  - package-ecosystem: github-actions
    directory: "/"
    schedule:
      interval: weekly
```

- [ ] **Step 2: Validate the YAML parses**

```
.venv\Scripts\python.exe -c "import yaml; yaml.safe_load(open('.github/dependabot.yml')); print('dependabot.yml OK')"
```

Expected: `dependabot.yml OK` (exit 0).

- [ ] **Step 3: Commit**

```bash
git add .github/dependabot.yml
git commit -m "ci: add dependabot config (pip, npm in /web, github-actions - weekly)"
```

---

### Task 3: `SHA256SUMS` release asset in `build.yml`

**Files:**
- Modify: `.github/workflows/build.yml` (the `release` job, lines 119–133)

**Interfaces:**
- Consumes: the existing `windows-build` artifact (contains `dist/Model-Hub-Setup-*.exe` and `dist/model-hub.exe`; `actions/download-artifact@v4` with no `name` extracts each artifact into a directory named after it, so the installer lands at `windows-build/Model-Hub-Setup-*.exe` — this is why the existing `files:` glob already uses that prefix).
- Produces: a `SHA256SUMS` release asset whose filename Task 8's README/landing copy references verbatim.

- [ ] **Step 1: Edit the release job**

The `release` job in `.github/workflows/build.yml` currently reads exactly:

```yaml
  release:
    needs: [build-windows, build-linux, build-macos]
    runs-on: ubuntu-latest
    if: startsWith(github.ref, 'refs/tags/v')
    steps:
      - uses: actions/download-artifact@v4

      - name: Create draft release (Windows installer only — review before publish)
        uses: softprops/action-gh-release@v2
        with:
          draft: true
          files: |
            windows-build/Model-Hub-Setup-*.exe
          generate_release_notes: true
          fail_on_unmatched_files: true
```

Replace it with (adds one step + one line in `files:`):

```yaml
  release:
    needs: [build-windows, build-linux, build-macos]
    runs-on: ubuntu-latest
    if: startsWith(github.ref, 'refs/tags/v')
    steps:
      - uses: actions/download-artifact@v4

      - name: Generate SHA256SUMS for release artifacts
        working-directory: windows-build
        run: |
          sha256sum Model-Hub-Setup-*.exe > SHA256SUMS
          echo "--- SHA256SUMS ---"
          cat SHA256SUMS

      - name: Create draft release (Windows installer only — review before publish)
        uses: softprops/action-gh-release@v2
        with:
          draft: true
          files: |
            windows-build/Model-Hub-Setup-*.exe
            windows-build/SHA256SUMS
          generate_release_notes: true
          fail_on_unmatched_files: true
```

Design notes:
- The runner is `ubuntu-latest`, so `sha256sum` (coreutils) is the right tool — no PowerShell here. (Windows *users* verify with `certutil` — that's Task 8's copy.)
- The sums cover exactly what is attached to the release (`Model-Hub-Setup-*.exe`). `model-hub.exe` (the bare console exe, also inside the artifact) is deliberately NOT summed because it is not attached — a checksum file listing files users can't download is noise. If a future change attaches more artifacts, extend the glob then.
- `sha256sum` output format (`<hash>  <filename>`) is the ecosystem standard; the filenames in `SHA256SUMS` will match the release asset names exactly because both come from the same directory.

- [ ] **Step 2: Validate the YAML parses**

```
.venv\Scripts\python.exe -c "import yaml; yaml.safe_load(open('.github/workflows/build.yml')); print('build.yml OK')"
```

Expected: `build.yml OK` (exit 0).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/build.yml
git commit -m "ci: publish SHA256SUMS with release artifacts"
```

---

### Task 4: Non-loopback bind warning in `server.py` (TDD)

**Files:**
- Modify: `server.py` (add helper after the constants at lines 14–15; add call in `main()` after the `clear_port` guard at lines 151–152)
- Test: `tests/test_server.py` (new file — `server.py` has no tests today)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `bind_warning(host: str) -> str | None` and `LOOPBACK_HOSTS: frozenset[str]`, both module-level in `server.py`, importable as `from server import bind_warning, LOOPBACK_HOSTS`. Nothing downstream consumes them besides `main()` and the tests, but W2's `backend/desktop.py` (pywebview) reuses the same loopback server — keep the helper pure so it can be reused there.

Background: `HOST = "127.0.0.1"` at `server.py:14` is already the verified-safe default (spec §6.2). The Flask app has **no authentication** — anyone who can reach the port can read the hardware profile, install/delete models, and drive chat. Binding wide must stay possible (`--host` is a legitimate power feature) but must never be silent. The logic must live in a pure helper — `bind_warning(host) -> str | None` — so it's unit-testable without starting a server.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_server.py` with exactly:

```python
"""Non-loopback bind warning (server.py bind_warning helper)."""
from server import bind_warning


def test_loopback_hosts_produce_no_warning():
    for host in ("127.0.0.1", "localhost", "::1", "LOCALHOST", " 127.0.0.1 "):
        assert bind_warning(host) is None, f"loopback host {host!r} must not warn"


def test_non_loopback_host_produces_prominent_warning():
    msg = bind_warning("0.0.0.0")
    assert msg is not None
    assert "0.0.0.0" in msg                    # names the actual bind
    assert "no authentication" in msg.lower()  # says WHY it matters
    assert msg.count("\n") >= 4                # multi-line = prominent, not a one-liner


def test_lan_ip_produces_warning():
    assert bind_warning("192.168.1.20") is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_server.py -v`
Expected: FAIL — `ImportError: cannot import name 'bind_warning' from 'server'` (collection error counts; the point is red-before-green).

- [ ] **Step 3: Write the implementation**

In `server.py`, directly after the constants (currently lines 14–15):

```python
HOST = "127.0.0.1"
PORT = 5050
```

add:

```python
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def bind_warning(host: str) -> str | None:
    """Return a multi-line warning banner when `host` is not loopback, else None.

    Pure helper (no I/O) so the bind-safety logic is unit-testable without
    starting the server. The default bind (HOST above) never triggers it;
    only an explicit non-loopback --host does.
    """
    if str(host).strip().lower() in LOOPBACK_HOSTS:
        return None
    return "\n".join([
        "  !!! ------------------------------------------------------------- !!!",
        f"  !!!  WARNING: binding to {host} - APT is reachable from the network.",
        "  !!!  The APT server has no authentication. Anyone who can reach",
        "  !!!  this address can read your hardware profile, install or",
        "  !!!  delete models, and drive chat sessions on this machine.",
        "  !!!  Only bind beyond loopback on a network you fully trust.",
        "  !!!  To stay private, omit --host (default: 127.0.0.1).",
        "  !!! ------------------------------------------------------------- !!!",
    ])
```

Then in `main()`, the code currently reads (lines 151–160):

```python
    if not clear_port(port, args.force):
        sys.exit(1)

    if not args.no_browser:
        def open_browser():
            time.sleep(1.5)
            webbrowser.open(f"http://{host}:{port}")

        threading.Thread(target=open_browser, daemon=True).start()
    run_server(host=host, port=port)
```

Change it to (insert the 4-line warning block after the `clear_port` guard, so the banner is the last thing printed before the server starts — maximally visible):

```python
    if not clear_port(port, args.force):
        sys.exit(1)

    warning = bind_warning(host)
    if warning:
        print(warning)
        print()

    if not args.no_browser:
        def open_browser():
            time.sleep(1.5)
            webbrowser.open(f"http://{host}:{port}")

        threading.Thread(target=open_browser, daemon=True).start()
    run_server(host=host, port=port)
```

(Importing `server` in tests is safe: the module top level only inserts the repo dir into `sys.path`; Flask import and `run_server` happen inside `main()`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_server.py -v`
Expected: `3 passed`.

- [ ] **Step 5: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: 203 collected, exit 0 (baseline 200 + 3 new; same 5 Ollama skips).

- [ ] **Step 6: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat(server): warn loudly when binding beyond loopback"
```

---

### Task 5: Update path — verify GitHub-Releases-only + fix the upgrade-command dist name (TDD)

**Files:**
- Modify: `backend/update.py:98-106` (`upgrade_command`)
- Test: `tests/test_update.py` (append two tests)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `upgrade_command(method)` now returns `"pip install --upgrade apt-hub"` / `"uv pip install --upgrade apt-hub"`. `GITHUB_API` unchanged, now pinned by a test.

**Verification result (done during planning — record, don't redo):** `backend/update.py` talks to exactly one endpoint, `GITHUB_API = "https://api.github.com/repos/Dkrynen/model-hub/releases/latest"` (HTTPS, GitHub Releases API), used only in `check_update()`. `server.py:30`'s `check_for_update()` uses the same HTTPS GitHub URL inline. Download URLs surfaced to the user come from the release's own `assets[].browser_download_url` / `html_url`, with `backend/version.py`'s `__download_url__ = "https://github.com/Dkrynen/model-hub/releases"` as fallback. **The update path is GitHub-Releases-over-HTTPS only — spec §6.4 requirement already holds.**

**The one real defect found:** `upgrade_command()` currently reads (backend/update.py:98–106):

```python
def upgrade_command(method: str | None = None) -> str:
    method = method or detect_install_method()
    if method == "pip":
        return "pip install --upgrade apt"
    if method == "uv":
        return "uv pip install --upgrade apt"
    if method == "pyinstaller":
        return f"download latest exe from {__download_url__}"
    return "git pull && uv pip install -r requirements.txt"
```

Our distribution is `apt-hub` (`pyproject.toml [project] name = "apt-hub"`). PyPI's `apt` is a *different, squattable* package name — and `do_update()` (lines 130–140) doesn't just print this string, it **executes** it via `subprocess.run(cmd.split())` for pip/uv installs. That is a self-inflicted supply-chain hole in the update path. Fix: target `apt-hub`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_update.py`, first extend the existing import line at the top of the file. It currently reads:

```python
from backend.update import UpdateMode, _parse_version, check_update, detect_install_method, is_newer
```

Change it to:

```python
from backend.update import (
    GITHUB_API,
    UpdateMode,
    _parse_version,
    check_update,
    detect_install_method,
    is_newer,
    upgrade_command,
)
```

Then append at the end of the file:

```python
def test_upgrade_command_targets_the_real_distribution():
    # The dist is "apt-hub" (pyproject.toml). "pip install --upgrade apt"
    # would fetch PyPI's unrelated "apt" package - and do_update() EXECUTES
    # this string for pip/uv installs, so the wrong name is a supply-chain
    # hazard, not a typo.
    assert upgrade_command("pip") == "pip install --upgrade apt-hub"
    assert upgrade_command("uv") == "uv pip install --upgrade apt-hub"


def test_update_endpoint_is_github_releases_over_https():
    # Locks the W4 invariant: the update check talks ONLY to the GitHub
    # Releases API, over HTTPS. If someone reroutes this, a test goes red.
    assert GITHUB_API == "https://api.github.com/repos/Dkrynen/model-hub/releases/latest"
```

- [ ] **Step 2: Run tests to verify the new one fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update.py -v`
Expected: `test_upgrade_command_targets_the_real_distribution` FAILS with `assert 'pip install --upgrade apt' == 'pip install --upgrade apt-hub'`; the endpoint test PASSES (it documents an already-true invariant); the 7 pre-existing tests stay green.

- [ ] **Step 3: Apply the fix**

In `backend/update.py`, replace the two return lines:

```python
    if method == "pip":
        return "pip install --upgrade apt-hub"
    if method == "uv":
        return "uv pip install --upgrade apt-hub"
```

(Leave the `pyinstaller` and `source` branches untouched.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update.py -v`
Expected: `9 passed` (7 baseline + 2 new).

- [ ] **Step 5: Commit**

```bash
git add backend/update.py tests/test_update.py
git commit -m "fix(update): upgrade command targets apt-hub, not PyPI's unrelated 'apt'"
```

---

### Task 6: Plugin origin — `dist` column in `apt plugins` and `/api/plugins` (TDD)

**Files:**
- Modify: `backend/plugins.py` (dataclass + `discover()`, whole file is 54 lines)
- Modify: `cli.py:1123-1134` (`cmd_plugins`)
- Modify: `backend/api.py:909-914` (`api_plugins`)
- Test: `tests/test_plugins.py`, `tests/test_cli_plugins.py`, `tests/test_api_plugins.py` (append tests)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `LoadedPlugin` gains a final defaulted field `dist: str | None = None` (e.g. `"apt-pro 0.1.0"`) — positional construction `LoadedPlugin(name, version, obj, error)` stays valid everywhere, including apt-pro's tests. New private helper `_dist_of(ep) -> str | None` in `backend/plugins.py`. `GET /api/plugins` items gain a `"dist"` key. Task 7's docs reference the CLI column name **Distribution** — keep it exactly that.

Why: a plugin's *self-reported* `name`/`version` is whatever the plugin object claims. The **providing distribution** (from `importlib.metadata` — the entry point's `.dist`) is what the user actually `pip install`ed and what they'd `pip uninstall` to get rid of it. Showing it is the difference between "a thing called pro is loaded" and "package `apt-pro 0.1.0` provides plugin `pro`". Origin matters *most* for broken/suspicious plugins, so the dist is captured even when `ep.load()` raises.

(Compatibility note: `EntryPoint.dist` and `Distribution.name` are available on Python 3.10+ — the project floor per README. The helper still guards with `getattr` so a missing/misbehaving `.dist` degrades to `None`, never an exception — same isolation philosophy as the rest of the seam.)

- [ ] **Step 1: Write the failing tests — discovery layer**

Append to `tests/test_plugins.py` (it already defines `FakeEntryPoint` and `_patch_eps` at the top — reuse them):

```python
class FakeDist:
    def __init__(self, name, version):
        self.name = name
        self.version = version


def test_discover_captures_providing_distribution(monkeypatch):
    plug = SimpleNamespace(name="pro", version="0.1.0")
    ep = FakeEntryPoint("pro", obj=plug)
    ep.dist = FakeDist("apt-pro", "0.1.0")
    _patch_eps(monkeypatch, [ep])
    out = discover()
    assert out[0].dist == "apt-pro 0.1.0"


def test_discover_dist_is_none_when_unavailable(monkeypatch):
    plug = SimpleNamespace(name="pro", version="0.1.0")
    _patch_eps(monkeypatch, [FakeEntryPoint("pro", obj=plug)])  # no .dist attr
    out = discover()
    assert out[0].dist is None
    assert out[0].ok  # missing metadata must not degrade the plugin itself


def test_discover_broken_plugin_still_reports_distribution(monkeypatch):
    # Origin matters MOST for a plugin that failed to load.
    ep = FakeEntryPoint("broken", exc=ImportError("boom"))
    ep.dist = FakeDist("sketchy-pkg", "6.6.6")
    _patch_eps(monkeypatch, [ep])
    out = discover()
    assert not out[0].ok
    assert out[0].dist == "sketchy-pkg 6.6.6"


def test_discover_isolates_raising_dist_metadata(monkeypatch):
    class ExplodingDist:
        @property
        def name(self):
            raise RuntimeError("dist bomb")

        version = "1.0"

    plug = SimpleNamespace(name="pro", version="0.1.0")
    ep = FakeEntryPoint("pro", obj=plug)
    ep.dist = ExplodingDist()
    _patch_eps(monkeypatch, [ep])
    out = discover()
    assert out[0].ok           # plugin still loads fine
    assert out[0].dist is None  # bad metadata degrades to unknown, never raises
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_plugins.py -v`
Expected: the 4 new tests FAIL (`AttributeError: 'LoadedPlugin' object has no attribute 'dist'` / assertion on `None`); the 5 pre-existing tests stay green.

- [ ] **Step 3: Implement in `backend/plugins.py`**

The file is 54 lines; here is the complete new version (docstring updated, `dist` field added, `_dist_of` helper added, `discover()` threads it through):

```python
"""Open-core plugin seam.

Plugins are Python packages exposing an entry point in the ``apt.plugins``
group. The entry point resolves to a plugin object with:

- ``name: str``            display name (falls back to the entry-point name)
- ``version: str``         plugin version (falls back to "?")
- ``register_cli(subparsers)``  optional — add argparse subcommands
- ``register_api(app)``         optional — add Flask routes

A plugin that raises during load or registration must never break core:
every call is isolated and errors are captured on the LoadedPlugin record.

Each record also carries ``dist`` — the *providing distribution* ("name
version" from importlib.metadata), i.e. what the user actually installed
and what they would ``pip uninstall``. Best-effort: None when unknown.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import entry_points

GROUP = "apt.plugins"


def _entry_points():
    """Indirection so tests can substitute fake entry points."""
    return list(entry_points(group=GROUP))


def _dist_of(ep) -> str | None:
    """Best-effort '<distribution> <version>' for an entry point's provider.

    Metadata problems degrade to None — they must never break discovery.
    """
    try:
        dist = getattr(ep, "dist", None)
        if dist is None:
            return None
        name = getattr(dist, "name", None)
        version = getattr(dist, "version", None)
        if not name:
            return None
        return f"{name} {version}" if version else str(name)
    except Exception:  # noqa: BLE001 — metadata must never break discovery
        return None


@dataclass
class LoadedPlugin:
    name: str
    version: str
    obj: object | None
    error: str | None = None
    dist: str | None = None  # providing distribution, e.g. "apt-pro 0.1.0"

    @property
    def ok(self) -> bool:
        return self.error is None


def discover() -> list[LoadedPlugin]:
    """Load all ``apt.plugins`` entry points, isolating per-plugin failures."""
    out: list[LoadedPlugin] = []
    for ep in _entry_points():
        dist = _dist_of(ep)
        try:
            obj = ep.load()
            # getattr is inside the guard: a raising name/version property
            # must not break core either.
            name = getattr(obj, "name", None) or ep.name
            version = getattr(obj, "version", None) or "?"
        except Exception as exc:  # noqa: BLE001 — a plugin must never break core
            out.append(LoadedPlugin(name=ep.name, version="?", obj=None, error=str(exc), dist=dist))
            continue
        out.append(LoadedPlugin(name=name, version=version, obj=obj, dist=dist))
    return out
```

- [ ] **Step 4: Run discovery tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_plugins.py -v`
Expected: `9 passed` (5 baseline + 4 new).

- [ ] **Step 5: Write the failing test — CLI column**

Append to `tests/test_cli_plugins.py` (reuses its existing `_fake_discover` helper):

```python
def test_cmd_plugins_shows_distribution_column(monkeypatch, capsys):
    plug = SimpleNamespace(name="fake", version="9.9")
    _fake_discover(monkeypatch, [
        LoadedPlugin("fake", "9.9", plug, dist="apt-pro 0.1.0"),
        LoadedPlugin("bare", "1.0", plug),  # dist unknown -> "?"
    ])
    import cli
    cli.cmd_plugins(SimpleNamespace())
    out = capsys.readouterr().out
    assert "Distribution" in out
    assert "apt-pro 0.1.0" in out
```

Run: `.venv\Scripts\python.exe -m pytest tests/test_cli_plugins.py -v`
Expected: the new test FAILS (`assert "Distribution" in out`); the 4 pre-existing tests stay green (they assert substrings only, unaffected by a new column).

- [ ] **Step 6: Implement the CLI column**

`cli.py` `cmd_plugins` currently reads (lines 1123–1134):

```python
def cmd_plugins(args):
    from backend import plugins as _plugins
    found = _plugins.discover()
    print_header("Plugins")
    if not found:
        print("  No plugins installed. Pro and community plugins mount here.")
        return
    rows = []
    for p in found:
        status = "ok" if p.ok else f"error: {p.error}"
        rows.append([p.name, p.version, status])
    print_table(["Name", "Version", "Status"], rows)
```

Change the last four lines so the function becomes:

```python
def cmd_plugins(args):
    from backend import plugins as _plugins
    found = _plugins.discover()
    print_header("Plugins")
    if not found:
        print("  No plugins installed. Pro and community plugins mount here.")
        return
    rows = []
    for p in found:
        status = "ok" if p.ok else f"error: {p.error}"
        rows.append([p.name, p.version, p.dist or "?", status])
    print_table(["Name", "Version", "Distribution", "Status"], rows)
```

Run: `.venv\Scripts\python.exe -m pytest tests/test_cli_plugins.py -v` → Expected: `5 passed`.

- [ ] **Step 7: Write the failing test — API field**

Append to `tests/test_api_plugins.py`:

```python
def test_api_plugins_includes_dist(monkeypatch, flask_app):
    plug = SimpleNamespace(name="fake", version="9.9")
    monkeypatch.setattr(plugins_mod, "discover", lambda: [
        LoadedPlugin("fake", "9.9", plug, dist="apt-pro 0.1.0"),
    ])
    client = flask_app.test_client()
    data = client.get("/api/plugins").get_json()
    assert data[0]["dist"] == "apt-pro 0.1.0"
```

Run: `.venv\Scripts\python.exe -m pytest tests/test_api_plugins.py -v`
Expected: the new test FAILS with `KeyError: 'dist'`; the 3 pre-existing tests stay green.

- [ ] **Step 8: Implement the API field**

`backend/api.py` `api_plugins` currently reads (lines 909–914):

```python
@app.route("/api/plugins")
def api_plugins():
    return jsonify([
        {"name": p.name, "version": p.version, "ok": p.ok, "error": p.error}
        for p in _discover_plugins_safe()
    ])
```

Change the dict line so it becomes:

```python
@app.route("/api/plugins")
def api_plugins():
    return jsonify([
        {"name": p.name, "version": p.version, "dist": p.dist, "ok": p.ok, "error": p.error}
        for p in _discover_plugins_safe()
    ])
```

(Checked during planning: `tests/test_openapi.py` does not pin the `/api/plugins` response shape — adding a key breaks nothing.)

Run: `.venv\Scripts\python.exe -m pytest tests/test_api_plugins.py -v` → Expected: `4 passed`.

- [ ] **Step 9: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected (executing tasks in plan order): **211 collected, exit 0** — baseline 200 + 3 (Task 4) + 2 (Task 5) + 6 (this task: 4 discovery + 1 CLI + 1 API), with the usual 5 "Ollama not running" skips.

- [ ] **Step 10: Commit**

```bash
git add backend/plugins.py cli.py backend/api.py tests/test_plugins.py tests/test_cli_plugins.py tests/test_api_plugins.py
git commit -m "feat(plugins): show providing distribution in apt plugins and /api/plugins"
```

---

### Task 7: Plugin-trust security note (`docs/PLUGINS.md` + README)

**Files:**
- Modify: `docs/PLUGINS.md` (append a section — the file is 21 lines today)
- Modify: `README.md` (the Development section's plugin sentence, line 71 today)

**Interfaces:**
- Consumes: the CLI column name **Distribution** from Task 6 (the copy references it).
- Produces: nothing code-level.

- [ ] **Step 1: Append the security section to `docs/PLUGINS.md`**

The file currently ends with:

```markdown
Errors in a plugin never break APT: load and registration are isolated per
plugin (`backend/plugins.py`), and a failure in discovery itself degrades to
a warning. Inspect with `apt plugins` or `GET /api/plugins`.
```

Append after it:

```markdown
## Security

An entry-point plugin is an ordinary Python package: **installing one executes
arbitrary code with APT's privileges** — at import time, in the CLI process,
and inside the web server. There is no sandbox. That's by design; it's the
same trust model as `pip install` itself.

The rules:

- Install plugins only from distributions you trust — same rule as any pip
  package.
- `apt plugins` shows the providing distribution for every plugin
  (**Distribution** column, also `dist` in `GET /api/plugins`). See something
  you don't recognize? Remove it: `pip uninstall <distribution>`.
- The error isolation described above protects APT from a plugin *crashing* —
  it does not and cannot protect you from a plugin doing something *malicious*.
```

- [ ] **Step 2: Update the README plugin sentence**

`README.md` line 71 currently reads:

```markdown
Plugins mount via the `apt.plugins` entry-point group — see [docs/PLUGINS.md](docs/PLUGINS.md). Contributions welcome: [CONTRIBUTING.md](CONTRIBUTING.md).
```

Replace with:

```markdown
Plugins mount via the `apt.plugins` entry-point group — see [docs/PLUGINS.md](docs/PLUGINS.md). **Security note:** a plugin is a normal Python package and runs with APT's privileges — install plugins you trust, same rule as pip packages. `apt plugins` shows which installed distribution provides each one. Contributions welcome: [CONTRIBUTING.md](CONTRIBUTING.md).
```

(If W3's rebrand moved this sentence, find it by the `apt.plugins` mention — the note attaches to wherever plugins are introduced in the README.)

- [ ] **Step 3: Commit**

```bash
git add docs/PLUGINS.md README.md
git commit -m "docs(plugins): security note - plugins execute arbitrary code, trust rule"
```

---

### Task 8: Honest download story — SmartScreen + checksum verify (README + landing page)

**Files:**
- Modify: `README.md` (Install → Windows subsection, lines 20–22 today)
- Modify: `site/index.html` (the header CTA block, lines 64–68 today)

**Interfaces:**
- Consumes: the `SHA256SUMS` asset name from Task 3 (referenced verbatim in the copy).
- Produces: nothing code-level.

**Ordering caveat:** W3 re-skins `site/index.html` and the README header before W4 runs. Anchor these edits by content — the "Download for Windows" CTA and the `### Windows (recommended)` install subsection — not by the line numbers quoted here. The copy below is skin-agnostic.

- [ ] **Step 1: Add the note to README**

`README.md` currently reads (lines 20–22):

```markdown
### Windows (recommended)

Download the latest `Model-Hub-Setup-x.x.x.exe` from [Releases](https://github.com/Dkrynen/model-hub/releases) and run it.
```

Replace with:

```markdown
### Windows (recommended)

Download the latest `Model-Hub-Setup-x.x.x.exe` from [Releases](https://github.com/Dkrynen/model-hub/releases) and run it.

> **Unsigned build — what Windows will say.** The installer isn't code-signed
> yet (certificates cost real money; signing lands once APT has revenue), so
> SmartScreen will warn on first run: click **More info → Run anyway**.
> Don't take our word for it — verify what you downloaded first:
>
> ```
> certutil -hashfile Model-Hub-Setup-x.x.x.exe SHA256
> ```
>
> Compare the output against the `SHA256SUMS` file attached to the same
> [release](https://github.com/Dkrynen/model-hub/releases/latest).
> Hash matches → run it. Hash doesn't → delete it.
```

- [ ] **Step 2: Add the note to the landing page**

`site/index.html` currently has, in the header (lines 64–68):

```html
  <div class="cta-row">
    <a class="btn btn-primary" href="https://github.com/Dkrynen/model-hub/releases/latest">Download for Windows</a>
    <a class="btn btn-ghost" href="https://github.com/Dkrynen/model-hub">View on GitHub</a>
  </div>
  <p class="hint">CLI on any platform: <code>pipx install git+https://github.com/Dkrynen/model-hub</code> → <code>aptm scan</code></p>
```

Insert one paragraph after the existing `.hint` line (reuse the `hint` class — no new CSS; if W3 renamed the class, use whatever class the adjacent fine-print paragraph uses):

```html
  <p class="hint">Unsigned build — SmartScreen will warn on first run (<em>More info → Run anyway</em>).
     Verify it yourself: <code>certutil -hashfile Model-Hub-Setup-x.x.x.exe SHA256</code>
     against the <code>SHA256SUMS</code> file on the release. Code signing lands post-revenue.</p>
```

- [ ] **Step 3: Eyeball it locally**

Open `site/index.html` in a browser (double-click or `start site\index.html` from PowerShell). Expected: the note renders under the CTA buttons, legible, no layout break. This page has no build step — the file is served as-is by GitHub Pages.

- [ ] **Step 4: Commit**

```bash
git add README.md site/index.html
git commit -m "docs: honest SmartScreen + SHA-256 verify copy on README and landing"
```

---

### Task 9: Branch protection on `master` — Duan-gated GitHub-settings checklist (no code)

**Files:** none. This is a settings change on github.com that only Duan can perform, and parts of it are only possible **after** the first push (spec §6.1 marks it Duan-gated).

- [ ] **Step 1: Record the checklist for Duan (this checklist IS the deliverable — surface it in the final report / handoff verbatim):**

**When:** after Duan pushes the v2 branch/master and the `tests` + `security` workflows have each run at least once (GitHub's status-check picker only lists checks it has already seen run).

1. Go to `https://github.com/Dkrynen/model-hub/settings/branches` → **Add branch protection rule** (or Settings → Rules → Rulesets → New branch ruleset, targeting `master`).
2. Branch name pattern: `master`.
3. Minimum bar (pick at least these):
   - ✅ **Block force pushes**
   - ✅ **Restrict deletions**
   - ✅ **Require status checks to pass before merging**, selecting:
     - `test (ubuntu-latest)`, `test (windows-latest)`, `test (macos-latest)` and `web` (from `test.yml`)
     - `codeql (python)`, `codeql (javascript)`, `pip-audit`, `npm-audit` (from `security.yml`, Task 1)
4. Stronger bar (Duan's call — changes his solo workflow): ✅ **Require a pull request before merging**. Honest trade-off: Duan currently commits straight to `master`; requiring PRs means every change goes through a branch + PR, even solo. The minimum bar above already stops force-push history rewrites and unreviewed red merges.
5. Save. Verify by attempting `git push --force` on a throwaway commit — GitHub must reject it. (Then don't force-push, obviously.)

- [ ] **Step 2: No commit** — nothing in the repo changes for this task. Mark the task complete when the checklist has been delivered to Duan (it is deferred-verifiable only post-push, see Task 10).

---

### Task 10: Final verification + deferred-verification record

**Files:** none created. This task produces evidence, not artifacts.

- [ ] **Step 1: Full test suite**

Run (from `C:\Users\User\repos\model-hub`): `.venv\Scripts\python.exe -m pytest -q`
Expected: **211 tests collected, exit 0** — baseline 200 + 11 new (3 in `tests/test_server.py`, 2 in `tests/test_update.py`, 4 in `tests/test_plugins.py`, 1 in `tests/test_cli_plugins.py`, 1 in `tests/test_api_plugins.py`), with the usual 5 "Ollama not running" skips. Any failure: stop and fix before proceeding — do not rationalize.

- [ ] **Step 2: Validate all three touched/created YAML files parse**

```
.venv\Scripts\python.exe -c "import yaml; [yaml.safe_load(open(p)) for p in ('.github/workflows/security.yml', '.github/workflows/build.yml', '.github/dependabot.yml')]; print('all YAML OK')"
```

Expected: `all YAML OK` (exit 0). (PyYAML 6.0.3 verified present in the venv during planning.)

- [ ] **Step 3: Confirm nothing was pushed**

Run: `git log origin/master..HEAD --oneline` (or `git status` + `git log --oneline -8`).
Expected: the W4 commits exist locally only. **Do not push** — that is Duan's launch-gate call.

- [ ] **Step 4: Write the deferred-verification record into the final report (honesty rail — these CANNOT be proven locally):**

| Item | Proven when |
| --- | --- |
| `security.yml` actually runs green (CodeQL, pip-audit, npm-audit) | first PR or first weekly cron **after Duan pushes** |
| CodeQL results appear under Security → Code scanning | after first run on the default branch |
| Dependabot opens weekly PRs | within a week of push |
| `SHA256SUMS` attached to a release | next `v*` tag build after push |
| Branch protection enforced | after Duan performs Task 9 on github.com |

Local YAML-parse checks prove syntax only, not Actions semantics (job wiring, action versions, permissions). That residual risk is accepted and disclosed — it resolves on the first real CI run.

---

## Self-review (performed against spec §6 before saving)

1. **Spec coverage:** §6.1 supply-chain CI → Tasks 1–2; §6.1 branch protection (Duan-gated, documented not coded) → Task 9; §6.2 localhost bind warning → Task 4; §6.3 plugin trust (dist column + PLUGINS.md + README note) → Tasks 6–7; §6.4 update path (GitHub-Releases-only verified, `SHA256SUMS` asset, checksum-verify docs) → Tasks 3, 5, 8; §6.5 / §2.7 code signing → correctly **absent** (only honest copy in Task 8). No gaps found.
2. **Placeholder scan:** no TBDs/TODOs added by this plan; every code step shows complete code; every command shows expected output. The one `TODO(launch)` visible in `site/index.html` is pre-existing (waitlist form) and out of W4 scope.
3. **Type consistency:** `bind_warning(host: str) -> str | None` used identically in Task 4's code and tests; `LoadedPlugin.dist: str | None` (defaulted last field) constructed positionally-compatible everywhere; `_dist_of(ep) -> str | None` defined and used only in `backend/plugins.py`; CLI header string `"Distribution"` matches Task 7's docs copy; `SHA256SUMS` filename identical across Tasks 3 and 8. Expected suite counts reconciled: 200 → 203 (T4) → 205 (T5) → 211 (T6).
