# Design — LAC Shell Hardening (S1)

**Created:** 2026-07-06 · **Status:** approved for planning · **Repo:** `model-hub` (open core, `Dkrynen/lac`)
**Predecessor context:** `docs/superpowers/HANDOFF-lac-desktop-shell-and-pro-expansion.md`

> This is **slice 1 of 4** in the "make LAC a product people feel is worth paying for" effort.
> Sequence (locked with Duan): **S1 shell → S2 Pro self-serve/visible → S3 Pro cockpit (felt value) → S4 chat latency.**
> S1 makes LAC *a real, safe desktop app* instead of a headless server that pops a browser tab.
> The Pro-value work (S2/S3) and latency (S4) are explicitly **out of scope here** — each gets its own spec.

---

## 1. Problem

The shipped app (`LAC-Setup-x.y.z.exe`) is a headless Flask server that shells open the default
browser. Three consequences make it read as "a script," not "a product":

1. **Terminal windows flash on every navigation and on launch.** Root cause confirmed: backend
   `subprocess.run/Popen` calls pass **no** `creationflags=CREATE_NO_WINDOW` / hidden `STARTUPINFO`
   (grep confirms it is used nowhere). On a windowed PyInstaller exe, every shell-out pops a console;
   hardware detection fires on every page → a console per click.
2. **It is a browser tab, not an app.** No native window, no single-instance guard, no taskbar
   identity. Multiple launches stack orphan Flask servers on the same port.
3. **It can act destructively on the host.** `clear_port` → `taskkill` kills whatever holds a port;
   the app shells out, writes under `~/.model-hub`, and (Pro) downloads models. For an
   enterprise-grade tool the rule must be absolute: **never break a user's system.**

Plus one adjacent installer defect: the install-time "Ollama not detected" guardrail false-negatives
(PATH context) and can block otherwise-fine installs.

## 2. Goal & non-goals

**Goal:** LAC launches as a real native window, never flashes a console, runs as a single instance
with proper taskbar identity, and is provably incapable of destructive action against processes or
files it does not own.

**Non-goals (deferred to later slices):**
- GUI "Activate Pro" that writes the license grant (S2).
- Pro status surface / activation celebration modal / hot-load (S2).
- Pro cockpit, model-tweaking UX, calibration insights surfacing (S3).
- Chat latency profiling / fix (S4).
- Bundling the WebView2 *runtime installer* (rely on Win11 Evergreen; see §5).
- macOS/Linux packaging of the native window (Windows-first; the subprocess/safety layer is
  cross-platform but the window ships Windows-first, matching the current release surface).

## 3. Architecture decision — pywebview + Flask coexistence

**Chosen (approach #1):** Flask runs on `127.0.0.1:5050` in a **daemon thread**; **pywebview owns the
main thread**. Entry point boots Flask, waits until it is serving, then
`webview.create_window("LAC", "http://127.0.0.1:5050", ...)` + `webview.start()`. When the window
closes, the process exits and the daemon server dies with it — **orphan-free by construction**.
pywebview auto-selects the EdgeChromium (WebView2) backend, present on every Win11 box. Reuses 100%
of the existing React+Flask UI — zero frontend rewrite.

**Rejected:**
- **#2 pywebview serving assets directly via `js_api` bridge** — would require rewriting the entire
  frontend API layer from `fetch('/api/...')` to bridge calls; throws away the working Flask surface.
  High risk, large rewrite.
- **#3 keep the browser tab, only fix orphans** — cheapest, but never becomes "an app": no window
  identity, no taskbar. Fails the goal.

## 4. Design

Two **independent** workstreams (share no code; can be planned/built in parallel).

### Workstream A — subprocess & safety layer (backend, fully unit-testable)

**A1. Shared subprocess wrapper.** New module `backend/cookbook/proc.py` exposing `run(...)` and
`popen(...)` that every shell-out routes through. On Windows it *always* sets
`creationflags=subprocess.CREATE_NO_WINDOW` **and** a hidden `STARTUPINFO`
(`STARTF_USESHOWWINDOW`, `SW_HIDE`) — belt-and-suspenders. Centralizes timeout/encoding handling so
call sites stay uniform. This is the single, structural fix for the terminal-flash bug.

**A2. Migrate all shell-out call sites.** Route every `subprocess.*` call through the wrapper. Known
sites: `backend/cookbook/hardware.py:65,291`, `backend/api.py`, `backend/plugin/builtins/tools.py`,
`backend/update.py`, `server.py`. The audit (A4) is partly **discovery** — any site it finds beyond
this list is migrated too. Acceptance: `grep` finds no raw `subprocess.run/Popen/check_output/call`
outside `proc.py` (documented allow-list exceptions permitted only with rationale in the audit).

**A3. Blast-radius policy for process kills.** The wrapper maintains a registry of PIDs **we
spawned**. Kill logic (`clear_port` / any `taskkill`) becomes:
- **Only ever kill a PID in our own spawn registry.**
- If our port (5050) is held by a **foreign** PID, **surface a clear error** ("port 5050 is in use by
  another application") and refuse — never force-kill it.
- No `taskkill /F` by image name or by port-owner lookup anywhere.

**A4. Blast-radius audit (written deliverable).** Enumerate every shell-out, every process kill, and
every filesystem write/`force`/overwrite path. For each: what it does, its blast radius, the
mitigation applied. Confirm every FS write stays under `~/.model-hub` (sandbox); flag any that does
not. Committed as `docs/superpowers/specs/2026-07-06-lac-shell-safety-audit.md`. Residual/accepted
risks listed explicitly.

**A5. Ollama guardrail fix.** Remove the install-time "Ollama not detected" hard check (false-negatives
on PATH context; can block a good install). Replace with an **in-app "Ollama required" banner + install
link**, shown when the app cannot reach a local Ollama at runtime. Install never blocks over Ollama.

### Workstream B — native window (packaging + lifecycle, verified by smoke)

**B1. Entry point.** Boot Flask (daemon thread) on `127.0.0.1:5050`; poll until it answers; then create
the pywebview window pointed at that URL and `webview.start()` on the main thread. Window close → clean
process exit → daemon Flask dies. No explicit server-shutdown dance needed (daemon thread), but the
readiness poll must have a bounded timeout with a clear error if Flask never comes up.

**B2. Single-instance guard.** On launch, detect an already-running instance (Windows named mutex, with
a port-in-use check as corroboration). If one exists: **focus the existing window and exit**; if focus
is not feasible, exit cleanly with a brief "LAC is already running" notice. Never stack a second server.

**B3. Taskbar identity.** Set the window title ("LAC"), the app icon, and a Windows `AppUserModelID` so
the taskbar labels/groups it as LAC rather than a generic "python" process.

**B4. WebView2 absence handling.** Assume Evergreen WebView2 (present on all Win11). If the backend fails
to initialize, show a clear dialog with the runtime download link **and** offer "open in browser" as a
graceful fallback, so the app never simply fails to open. (The browser fallback is an error path only —
not the default — so it does not reintroduce the tab-as-primary-UX problem.)

**B5. Packaging.** Update `build.spec` to bundle pywebview and its WebView2 loader
(`collect_all("webview")`), **proven on a real local build** the same way the `cryptography` bundling
ship-blocker was closed: a packaged exe that boots the actual native window with **zero missing-import
warnings**. Do **not** bundle the WebView2 runtime installer (weight) — rely on Evergreen + B4.

## 5. Verification strategy

**Workstream A — automated (RED→GREEN):**
- Wrapper always sets `CREATE_NO_WINDOW` (+ hidden `STARTUPINFO`) on Windows; asserts `creationflags`.
- Kill logic refuses any PID not in the spawn registry; foreign-PID-on-our-port → clear error, no kill.
- FS-write helper rejects paths resolving outside `~/.model-hub`.
- Grep gate: no raw `subprocess.*` outside `proc.py` (minus documented, justified exceptions).

**Workstream B — manual smoke checklist** (a GUI window is not unit-testable), plus one build test:
1. App opens as a real native window (not a browser tab).
2. **Zero console flashes** — launch, then navigate through every page; no console window ever appears.
3. Second launch focuses the existing window / exits cleanly; no second server (check `netstat`/`tasklist`).
4. Taskbar shows the LAC icon + title (not "python").
5. Close the window → `tasklist` shows no lingering `lac`/python process (no orphan).
6. The **packaged exe** (not just `python server.py`) boots the window.
7. Build-graph test asserts `build.spec` bundles `webview` (mirrors the `cryptography` collect_all check).

## 6. Risks & mitigations

- **Packaging miss (highest risk).** pywebview's native WebView2 loader not bundled → exe opens no
  window. Mitigation: B5's real-build proof with zero missing-import warnings, before this slice is
  called done (same discipline that caught the `cryptography` omission).
- **Flask readiness race.** Window created before Flask is serving → blank/error page. Mitigation: B1's
  bounded readiness poll before `create_window`.
- **Audit finds more than the known list.** Expected — A4 is discovery. Mitigation: the grep gate is the
  backstop that proves completeness, not the hand-listed sites.
- **Single-instance focus is platform-fiddly.** Focusing another process's window can be unreliable.
  Mitigation: B2 degrades to "exit cleanly with a notice" if focus fails — correctness (no orphan) does
  not depend on focus succeeding.

## 7. Out-of-scope confirmations (so planning does not drift)

- No Pro plumbing, no license-grant writes, no Pro UI (S2/S3).
- No latency work (S4).
- No macOS/Linux native window this slice.
- `lac-pro` repo is untouched by S1 (shell + safety are open-core only).

## 8. Definition of done

- All shell-outs route through `proc.py`; no console flashes anywhere (observed + asserted).
- Kill logic provably scoped to our own PIDs; foreign port-owner is never killed.
- FS writes sandboxed to `~/.model-hub`; audit doc committed with residual risks named.
- Ollama install-time guardrail removed; runtime banner in its place.
- LAC boots as a single-instance native window with taskbar identity; close leaves no orphan.
- Packaged exe boots the window with zero missing-import warnings.
- Full existing suite green; new A-layer tests green; B smoke checklist passed and recorded in the ledger.
- Nothing pushed/published without Duan's explicit go (standing rule).
