# APT v2 Overhaul — Session Handoff (2026-07-03)

Paste the PICKUP block at the bottom into a fresh session.

## Where everything stands

- **Plans 1–3 ALL SHIPPED** (see `.superpowers/sdd/progress.md` for the full ledger):
  - P1: web technical controls (GPU/spill what-if toggles, split-plan rows, benchmark
    dialog) + open-core plugin seam (`apt.plugins`) + apt-pro Tuning Cockpit
    (`apt pro tune/--apply`, live-smoked on real HW).
  - P2: LemonSqueezy licensing (activate/deactivate, 3-day revalidate / 14-day offline
    grace / hard-lock, contract-frozen `check()/require()`) + `apt pro insights`.
    Final opus review: SHIP.
  - P3: release engineering — tri-OS CI, pyproject (`aptm`, dist `apt-hub`), release
    pipeline FIXED (was shipping the legacy UI in the exe; now bundles web/dist —
    proven by building + booting the exe), APT README rebrand v0, landing page
    `site/` + Pages workflow, full-history secrets sweep (repo is ALREADY PUBLIC;
    56 commits, clean).
- model-hub master @ `7c065cf`+, 200 tests green; apt-pro @ `b8ac406`+, 47 tests,
  NO remote (never gets one). **Nothing pushed to origin.**
- **v1 LAUNCH CHECKLIST (HANDOFF.md) = ON HOLD** by explicit decision.

## v2 overhaul — decisions LOCKED with Duan (do not re-litigate)

1. **Sequencing = A: overhaul first, launch once.** No quiet-publish. Nobody ever
   sees the Model-Hub-flavored version.
2. **Two primary surfaces from ONE codebase:** Web + Desktop.
   **Desktop shell = pywebview** (native window over the existing Flask+React;
   one PyInstaller bundle; Tauri = possible post-revenue graduation, not now).
3. **Admin CLI retired as a product.** The CLI becomes ONE command that opens a
   full-screen **OpenCode-style agentic chat TUI**: model switcher, every APT
   function reachable via chat (agent tools) + slash commands, powered by the
   user's local models. Subcommands demoted to hidden plumbing/scripting.
   **Substrate already exists:** `backend/agent/runner.py` (`run_stream()` yields
   delta/tool_calls/tool_result/done), permission engine, provider abstraction,
   MCP client, `backend/plugin/builtins/tools.py`. The TUI is NOT yet wired to it —
   `TUI_AGENT_WIRING_FINDINGS.md` documents the verified Textual async-worker
   wiring approach (async `@work`, not thread workers).
4. **Rebrand: Cursor-INSPIRED, not cloned.** Near-black, restrained monochrome +
   one sharp accent, dimensional logo mark. Replaces the iris/#6E7BF2 Model-Hub
   look across web app, landing page, TUI banner, installer.
5. **Dev Deep-Dive Mode** (workstream 1): beyond toggles — live per-tier VRAM/RAM
   occupancy, per-model layer/KV-cache/context math, the bandwidth numbers behind
   every speed estimate, "why did this rank here" score breakdowns, raw split-plan
   JSON. Developer-grade transparency.
6. **Security posture** (workstream 4): the REAL attack surface is
   repo supply-chain (CodeQL, Dependabot, pip-audit, npm audit, branch protection),
   the local Flask server (**verify it binds 127.0.0.1 only** — check server.py),
   plugin-seam trust, and the update path. There are NO servers of ours — a
   1000-user spike hits GitHub's CDN and LemonSqueezy, both their problem. Keep
   this framing honest with Duan (he asked about DDoS/SSH — answered by architecture).

## Resume point

Brainstorming (superpowers:brainstorming) is MID-FLIGHT at the **brand/visual fork**.
Duan **accepted the visual companion offer** — the skill's browser mockup tool.
Next actions, in order:
1. Re-invoke superpowers:brainstorming for the v2 overhaul (context above).
2. Launch the visual companion (see the skill's `visual-companion.md` guide; start
   its server with `--open`) and present 2–3 brand directions side by side:
   palettes, logo-mark concepts, a mocked APT screen in each direction.
3. After brand locks → finish design questions for the 4 workstreams (deep-dive
   scope, TUI UX, security checklist) → present design → spec doc
   (`docs/superpowers/specs/2026-07-XX-apt-v2-overhaul-design.md`) → per-workstream
   decomposition → writing-plans → build (subagent-driven default; inline fallback
   proved fine this session).

## Working facts

- Repos: core `C:\Users\User\repos\model-hub` (venv `.venv\Scripts\python.exe`);
  Pro `C:\Users\User\repos\apt-pro` (tests run with core's venv; repo-local git
  identity already set; NEVER push it anywhere).
- Suite commands: core `.venv\Scripts\python.exe -m pytest -q` (200 green);
  web `cd web && npm run typecheck && npm run build` (capture TRUE exit codes —
  piping into tail/grep masks tsc failures); pro: run pytest from apt-pro with
  core's venv python.
- A Flask dev server may still be running on :5050 from this session (started for
  Duan's walkthrough), and Ollama is up with falcon3:3b + qwen3:30b-a3b-q8_0.
- Subagent dispatch rule learned the hard way: every dispatch must say
  "work in the foreground, do NOT spawn agents" (delegation-loop pathology).
- Duan's standing prefs: subagent-driven for builds; fix-don't-redesign on tweaks;
  grill-me on strategy/finance; plan-before-push to live systems; he'll keep
  bringing web-UI ideas — treat the web app as the fast-iteration surface.

---

## PICKUP (paste into fresh session)

Duan here — continue APT. Read FIRST: `C:\Users\User\repos\model-hub\docs\superpowers\HANDOFF-v2-overhaul.md`
(full state + locked decisions) and memory `project-apt-model-hub`. Plans 1–3 are
SHIPPED (open-core + Pro cockpit + LS licensing + release engineering; 200 core /
47 pro tests green; repo already public but NOTHING pushed; v1 launch checklist ON
HOLD). We decided the **v2 overhaul: overhaul first, launch once** — 4 workstreams:
dev deep-dive mode · web+desktop (pywebview) from one codebase with the CLI reborn
as an OpenCode-style agentic chat TUI (AgentRunner substrate exists, see
TUI_AGENT_WIRING_FINDINGS.md) · Cursor-inspired rebrand · security posture
(supply-chain + localhost bind, no own servers). RESUME the brainstorming skill at
the brand/visual fork: I accepted the visual companion — launch it and show me 2–3
brand directions (palette + logo concept + one mocked screen each), then finish the
design questions for the four workstreams, write the spec, and take it through
writing-plans. Don't re-litigate the locked decisions in the handoff doc.
