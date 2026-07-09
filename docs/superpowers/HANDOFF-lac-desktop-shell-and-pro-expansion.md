# Handoff — LAC: Desktop Shell, Pro That Feels Premium, and the Road to Enterprise

**Created:** 2026-07-06 · **For:** a fresh session to run RESEARCH → BRAINSTORM → SPEC → PLAN → build.
**Paste-in prompt is at the bottom.** Read this whole doc first — it's the full context.

> This is NOT a bug-fix ticket. It's the plan to close the gap between "the crypto works" and
> "a product a paying user *feels* is worth it." Duan's mandate, verbatim in spirit:
> *"I need to feel the Pro — like you feel the difference paying for Claude/Anthropic Pro. You
> physically feel it. Real model tweaking, not toggling a switch. No one pays $3 to flip a switch —
> they'd code it themselves. Make it irresistible. We're thinking enterprise level, and we are very
> far from that."*

---

## 1. What LAC is (one paragraph)

LAC (Local AI Companion) — scans your hardware, recommends the best local LLMs, installs them via
Ollama, and chats. Open-source core (`Dkrynen/lac`, this repo) + a proprietary **Pro** plugin
(`lac-pro`, private, no remote) delivered as a Nuitka-compiled `.pyd` from a Cloudflare Worker gate
to validated Polar license keys. Shipped today as a **Windows installer** (`LAC-Setup-x.y.z.exe`) that
runs a **Flask server + opens a browser tab** (React UI). Pro = $3/mo on Polar.

## 2. What is DONE and must NOT be redone (all committed, verified)

- **Security hardening (2026-07-06) — complete, 5-lens reviewed, proven end-to-end on real hardware.**
  License grant encrypted at rest (AES-256-GCM, HKDF over a machine-bound id), `repo_id` validation at
  the import choke point, dev-override (`LAC_PRO_DEV`) compiled out of release builds. Proven live: real
  key → gate 200 → hardened `.pyd` from R2 → CLI activate → **encrypted `license.json`** (no plaintext
  key). See `specs/2026-07-05-lac-pro-delivery-and-hardening-design.md`,
  `specs/2026-07-06-lac-pro-security-hardening-design.md`, and both repos' `.superpowers/sdd/progress.md`.
- **Delivery gate unlock bug — found + fixed.** `pro_install.py::_http_post` sent urllib's default
  User-Agent; Cloudflare WAF 403'd it, so *every* unlock (web + CLI) died as "invalid_key." Fixed
  (real UA + regression test, commit `4a28616`), re-cut into the v2.5.0 exe.
- **v2.5.0 is BUILT but HELD (unpublished draft).** `master` pushed; R2 serves the hardened `.pyd`
  (sha-verified). Last *published* release is the ancient v2.1.0. **Do not publish until the product
  below is worth it.** Duan's call: hold now (A), publish later (B) as one solid release.
- **Pro capabilities that already exist but are massively undersold** (this is gold to mine, not
  rebuild): **Pro Autopilot** (auto-benchmark + GPU-offload sweep + tune on every model install),
  **custom Hugging Face model import** (paste a repo id → download/quantize/install/register), and
  **calibration insights** (per-machine measured-speed history + regression detection). These are real,
  substantial, and buried. The Pro-value problem is 20% "build more" and 80% "surface + productize what
  exists so it's *felt*."

## 3. The problem inventory (grounded — root causes where known)

### A. The shell is not a product yet
- **Terminal windows flash on every navigation + on launch.** ROOT CAUSE CONFIRMED: backend
  `subprocess.run/Popen` calls (`backend/cookbook/hardware.py:65,291`, `backend/api.py`,
  `backend/plugin/builtins/tools.py`, `backend/update.py`, `server.py`) use **no**
  `creationflags=subprocess.CREATE_NO_WINDOW` / hidden `STARTUPINFO` — `grep` confirms it's used
  nowhere. On a windowed PyInstaller exe every shell-out pops a console; hardware detect fires on every
  page → a terminal per click. Fix = one shared subprocess wrapper that always passes
  `CREATE_NO_WINDOW` on Windows. High-visibility, low-risk, do it early.
- **It's a browser tab, not an app.** The exe is a headless server that (unreliably) opens the default
  browser. No window, no single-instance, no taskbar identity; multiple launches stack orphan servers.
  DIRECTION (already a named LAC workstream): wrap the *existing* React+Flask app in a **native window
  via pywebview** (Windows WebView2 — already on every Win11 box, near-zero footprint, reuse 100% of the
  UI). **Not Electron.** This alone changes the entire perceived quality.

### B. Pro is invisible and undersold
- **No way to see you have Pro.** After activating + restarting, the Settings card still shows the
  "Activate Pro" input — it never checks or reflects licensed state. There is no Pro status surface
  anywhere.
- **Activation has no moment.** Duan wants: the instant you activate, a **celebration modal** — "You're
  Pro. Here's everything that just unlocked" — with a live feature tour, **no restart required** (today
  the plugin only loads on restart, and the web "Activate Pro" only *installs* the plugin — it never
  writes the license grant; that's `lac pro activate`, CLI-only; GUI buyers currently can't self-serve
  licensing at all). Closing this is both a UX and a plumbing task (web activate must install AND
  license, then hot-load the plugin + fire the modal).
- **"Pro = a toggle" is the core product failure.** The offer has to *feel* like new power: deep,
  first-class **model tweaking** (GPU-offload/quant/context as a real cockpit, before/after tok/s proof),
  the Autopilot cockpit made visible, the custom-import flow made delightful. If Pro doesn't feel
  categorically more capable than free, $3 is a hard no.

### C. Performance
- **Chat latency is bad even on tiny models** (~500M params that hit ~500 tok/s still take forever to
  *start* responding). Doesn't add up — likely first-token/model-warm-up, streaming not wired through,
  a blocking proxy hop, or the UI not streaming. Needs real profiling (time-to-first-token vs
  tokens/sec), not guessing.

### D. Safety (hard constraint, non-negotiable)
- The app shells out, kills processes on ports (`clear_port` → `taskkill`), writes under
  `~/.model-hub`, and (Pro) downloads/imports models. **It must never break a user's system or do
  anything destructive.** Audit every shell-out, every kill, every filesystem write, every "force" path
  for blast radius; scope kills to our own PIDs, sandbox writes, confirm destructive actions. Enterprise
  buyers will not tolerate a tool that `taskkill`s the wrong thing.

## 4. The Pro thesis — "make it irresistible" (the heart of the new work)

Design the Pro tier so the difference is **felt in the first 60 seconds** and every session after:
1. **The unlock moment** — a real celebration + guided tour of what's now available (Duan's explicit ask).
2. **A visible Pro cockpit** — Autopilot results, per-model tuning, calibration insights, custom import,
   all surfaced as a coherent premium surface, not scattered flags.
3. **Model tweaking as craft, not a switch** — a genuine tuning experience (offload/quant/context/threads),
   with measured before/after speedups the user can see and trust. This is the "I could code it myself —
   but this is better" moat.
4. **Ongoing felt value** — every model install auto-optimized; recommendations that get smarter with the
   user's own measured data; things a free user simply cannot get.
The benchmark is the emotional one Duan named: *the physical "this is clearly better" you feel moving from
free to Claude/Anthropic Pro.* Free must be genuinely useful; Pro must feel like a different class of tool.

## 5. Parking lot (note only — do NOT scope yet)
- **Cloud models as a second, higher subscription tier** — optional access to faster/hosted models (BYO
  key or managed) for users who want speed beyond local hardware. A later, separate story. Just captured
  here so it informs how the Pro/plan architecture is designed (leave room for tiers).

## 6. How the new session should proceed (process)
This is wide, cross-cutting product work — exactly what the superpowers/GSD discipline is for. Do NOT
jump to code.
1. **Research** (`researcher` / `/deep-research`): how premium local-AI / desktop tools create "felt"
   value (Ollama's own app, LM Studio, Jan, Msty); pywebview + WebView2 packaging patterns; the
   subprocess-no-window fix; time-to-first-token profiling for Ollama chat; SaaS activation/"aha-moment"
   UX; safe-process-kill patterns.
2. **Brainstorm** (`superpowers:brainstorming`): decompose into sub-projects and sequence them. Likely
   slices: (S1) shell hardening — pywebview window + subprocess-no-window + single-instance + safety
   audit; (S2) Pro made visible — status surface + activation modal + web activate-that-licenses +
   hot-load; (S3) the Pro cockpit + model-tweaking experience; (S4) chat latency profiling + fix.
   Right-size and order with Duan — do not boil the ocean in one plan.
3. **Spec → Plan → Build** per slice (`superpowers:writing-plans` +
   `superpowers:subagent-driven-development`, TDD, per-task review, ledger in both repos). Only publish
   v2.5.0 (or a later version) once at least S1+S2 make the product one a paying GUI buyer can install,
   launch, see they have Pro, and *feel* it.

## 7. Fast facts the new session needs
- Repos: `C:\Users\User\repos\model-hub` (open core, `Dkrynen/lac`) + `C:\Users\User\repos\lac-pro`
  (private Pro, NO remote, never gets one). Tests via model-hub's venv:
  `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q -m "not live"` (core 286;
  lac-pro 161 non-live/non-slow).
- App: Flask server `127.0.0.1:5050` (`server.py`), React UI in `web/` (`npm run build` → `web/dist`),
  Windows build `pyinstaller build.spec` → `LAC-Setup` via `installer.iss`; release built by CI on tag
  push; installs to `C:\Program Files (x86)\LAC\lac.exe`.
- Pro delivery: Cloudflare Worker gate → private R2 artifact (hardened `.pyd` live); account-specific
  Worker URL, bucket/object names, and Polar organization values are redacted from this public handoff; keys use
  the stale `APT--` prefix (Polar benefit prefix never updated to `LAC-`).
- Standing rules: open every reply with "Duan"; plan before pushing to live systems; never publish/push
  without explicit go; superpowers/plugin-first on all build work.

---

## PASTE THIS INTO THE NEW SESSION

```
Duan here. Read docs/superpowers/HANDOFF-lac-desktop-shell-and-pro-expansion.md in
C:\Users\User\repos\model-hub — it's the full context. LAC's security hardening + delivery are DONE
and proven (don't redo them), but the PRODUCT is far from enterprise-grade and Pro doesn't feel worth
paying for. I want to start a proper research → brainstorm → plan pass to fix the shell (native
pywebview window, kill the terminal-flash-on-every-click bug, safety audit), make Pro VISIBLE and
FELT (activation celebration modal + a real Pro cockpit + genuine model-tweaking, not a toggle), and
profile the chat latency. Pro has to feel like the jump to Claude/Anthropic Pro — you physically feel
it — or no one pays $3 to flip a switch. Cloud models as a future second tier is noted, don't scope it
yet. App must never do anything destructive to a user's system. Start with superpowers:brainstorming to
decompose this into right-sized slices and sequence them WITH me before any code. Both repos' ledgers
are at .superpowers/sdd/progress.md. Don't push or publish anything without my explicit go; lac-pro
never gets a remote.
```
