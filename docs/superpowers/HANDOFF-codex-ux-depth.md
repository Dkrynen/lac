# LAC — Handoff for Codex: UX-depth pass + v2.6.2

You are picking up **LAC**, a shipped Windows-first local-LLM manager. It's live and public; this handoff
is for the next work: a **UX-depth / polish pass** (settings feel thin, wants real theming, general
beta-shallowness), which then ships as **v2.6.2** carrying some already-committed frontend fixes.

Address the owner as **Duan**. Don't call work "perfect" — report verified vs unverified honestly. Nothing
gets **pushed or published** to the public without Duan's explicit go.

---

## What LAC is
Scans a user's hardware (GPU/VRAM/RAM/CPU), recommends which local LLMs actually fit + run well, installs
them via **Ollama**, lets you chat with them, and (Pro) benchmarks/tunes them on the real hardware. Open-core:
free tier does the whole scan→recommend→install→chat loop; **Pro** ($3/mo) adds the `/pro` cockpit.

## Repos + stack
- **`C:\Users\User\repos\model-hub`** — open core. Public repo `github.com/Dkrynen/lac`. Python 3.11 + **Flask**
  (`127.0.0.1:5050`) serving a **React/Vite** SPA (`web/`) + JSON API; wrapped in a **pywebview/WebView2**
  native window. Packaged as a **PyInstaller ONE-DIR** app (as of v2.6.1) → `dist/lac/lac.exe`.
- **`C:\Users\User\repos\lac-pro`** — private Pro plugin. **NEVER gets a git remote** (by design). Nuitka-
  compiled to `lac_pro.cp311-win_amd64.pyd`, delivered to licensed users via a Cloudflare Worker gate + R2.
- **Open-core boundary (enforced by a test):** `model-hub` must NEVER `import lac_pro`. It drives the plugin
  only through the entry-point seam + license-gated `/api/pro/*` routes that live in `lac-pro`.

## Current state (2026-07-06)
- **v2.6.0 = PUBLISHED** (`github.com/Dkrynen/lac/releases/tag/v2.6.0`). **v2.6.1 = built DRAFT release**
  (`LAC-Setup-2.6.1.exe`, one-dir, held — Duan installed it locally to QA). CI is green.
- Shipped across v2.6.0/v2.6.1: native window + single-instance + no console-flash + kill-safety (S1);
  self-serve web activation + celebration + **safe self-relaunch** (S2); the **`/pro` cockpit** — tune
  (sweep → before/after tok/s → apply), insights, benchmark, autopilot log, HF import (S3); chat **warm-on-
  select** (S4); and a **perf pass** (v2.6.1): one-dir packaging (launch ~4.5s→0.54s), cached hardware probe,
  serialized Autopilot.
- **Git HEADs:** `model-hub` at **`de6e752`** (LOCAL, unpushed — see "pending fixes" below); pushed up to
  `1a8a650` (v2.6.1 tag). `lac-pro` at **`067ad82`** (local, no remote).

## Pending, already-committed frontend fixes (in `de6e752`, NOT yet in any build)
Two fixes are committed locally but the installed app still has the old baked frontend, so they need the next
rebuild to go live:
1. **HF import accepts pasted URLs** — `web/src/lib/installer.ts` `normalizeRepoId()` strips
   `https://huggingface.co/…` / `/tree/main` / query down to `org/model` (users kept pasting the full URL and
   getting "too many '/'").
2. **Warm-on-select in Tune + Benchmark panels** — `web/src/components/pro/{tune-hero,benchmark-panel}.tsx`
   call `api.warm(model)` on select so their tok/s + TTFT reflect warm speed, not the one-time cold load.
Roll these into v2.6.2.

## The task: UX-depth / polish pass
On real use, Duan found the app **felt slow/heavy/thin**. Perf (slow/heavy) is largely handled by v2.6.1;
the **"thin"** is what's left:
- **Settings "don't feel like settings"** — `web/src/pages/settings.tsx` is shallow (Engine host, a
  Dark/Light theme `<Select>`, the `<ProActivation/>` card, About). Make it feel real: more real controls,
  better structure/hierarchy, maybe grouped sections.
- **Theming** — there's a dark/light toggle (`web/src/components/theme.tsx`, tokens in `tailwind.config.ts` +
  CSS vars). Duan wants richer theming (more than a binary toggle — accent options? more polish?). Confirm
  scope with him before building.
- **General beta-shallowness** — tighten the surfaces that feel unfinished.
**Recommended approach:** treat this as design-first. Brainstorm the settings/theming scope WITH Duan (what
"real settings" means to him, what theming he wants) BEFORE coding — it's a design decision, not a bug. Then
build it, keep tests green, and batch into one v2.6.2.

## How to build, test, ship
- **Python tests (model-hub):** `.venv\Scripts\python.exe -m pytest -q -m "not live"` (from `model-hub`).
- **Pro tests (lac-pro):** from `lac-pro`: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q -m "not live and not slow"`.
- **Web:** `cd web && npm run typecheck && npm run build` (both must exit 0; no web test runner configured).
- **Build the exe:** `cd web && npm run build && cd .. && .venv\Scripts\pyinstaller build.spec` → one-dir at
  `dist\lac\lac.exe` (`build.spec` uses `collect_all("cryptography")`+`collect_all("webview")` + `"cli"` in
  hiddenimports — don't drop those).
- **Version bump spots (keep in sync):** `backend/version.py`, `backend/tui/app.py`, `installer.iss`
  (`MyAppVersion`), `web/package.json` (use `npm version X.Y.Z --no-git-tag-version` in `web/`), `CHANGELOG.md`.
- **Release:** bump → commit → `git push origin master` → `git tag vX.Y.Z && git push origin vX.Y.Z` → the
  `Build and Release` workflow (`.github/workflows/build.yml`) builds `LAC-Setup-X.Y.Z.exe` as a **draft**
  release. **Publishing is Duan's call** (`gh release edit vX.Y.Z --draft=false --latest`).
- **Ship a `lac-pro` change (Pro delivery):** in `lac-pro`: `<model-hub-venv-python> build/build_artifact.py`
  → produces `build/dist/lac-pro-0.1.0-cp311-win_amd64.zip` (+ sha). Upload to R2 from `model-hub/worker`:
  Upload the artifact through the private Cloudflare operator checklist (**remote account-backed action is
  mandatory**). Verify the private artifact hash through the private operator checklist. Duan's local install uses
  `~/.model-hub/plugins/lac_pro.*` — swap that file to test locally without re-activating. Gate URL is redacted from this public handoff.

## Launch state (so you don't accidentally undo it)
- **v2.6.0 is public; the Reddit launch post is HELD** until the app feels good. The ready-to-paste
  r/LocalLLaMA post + the full launch runbook are in **`docs/superpowers/LAUNCH-v2.6.0-runbook.md`**.
- Landing page: `site/index.html` → `dkrynen.github.io/lac/` (auto-deploys via Pages on push). Waitlist is a
  **Tally** form (`https://tally.so/r/GxyBx2`) — already wired.
- Polar (checkout / license keys / prices) = Duan's dashboard.

## Known non-goals / honest limits (don't chase these)
- **Heavy RAM is inherent** to shipping a Python interpreter + a Chromium (WebView2) instance. A prior
  research pass concluded: **Pake is the WRONG tool** (it only swaps the window shell; WebView2 is already
  the light part; it won't run the Flask backend). Truly going light = a bigger rewrite (Tauri + Python
  sidecar, or porting hot paths) — **deferred, don't start it in this pass.**
- A **big model that doesn't fit the GPU** (e.g. a 24 GB model on a 16 GB card) will always be slow (spills to
  RAM) — that's physics, not a bug; LAC's job is to warn the user (the "fits my GPU" filter).

## Where the full history lives
- **Ledger** (task-by-task, both repos): `model-hub/.superpowers/sdd/progress.md` — read the tail for the
  latest.
- Specs/plans: `docs/superpowers/specs/` and `docs/superpowers/plans/` (S1–S4 designs + the cockpit/activation
  designs). Original mandate: `docs/superpowers/HANDOFF-lac-desktop-shell-and-pro-expansion.md`.

**First move:** confirm the UX-depth scope with Duan (what "real settings" + theming means to him), then
design → build → keep tests green → batch with the pending `de6e752` fixes into v2.6.2 → hand the build back
to Duan to install + decide on publishing.
