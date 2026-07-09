# Session Handoff — Build LAC Pro Delivery + Hardening

Paste-prompt for the new session is at the bottom. This doc is the full context.

## The task

Execute the implementation plan at **`docs/superpowers/plans/2026-07-05-lac-pro-delivery-and-hardening.md`**, **subagent-driven** (superpowers:subagent-driven-development), **starting at Task 1 (a spike)**. The approved design spec it implements is `docs/superpowers/specs/2026-07-05-lac-pro-delivery-and-hardening-design.md` — read both first.

**What it builds:** the missing LAC Pro delivery pipeline + casual-piracy hardening. A compiled/obfuscated `lac-pro` plugin (Nuitka, spike-gated; PyArmor fallback) stored in a private Cloudflare R2 bucket, handed out only to validated Polar license keys by a stateless free-tier Cloudflare Worker, installed by a bootstrap command (`lac unlock <key>` + a web "Activate Pro" button) that lives in the **open-source core** (a free user doesn't have the plugin yet, so the fetcher can't be inside it). Delivery (once) and activation (the existing license check, unchanged) are separate.

## Two repos

- **`C:\Users\User\repos\model-hub`** — the open-source core (GitHub `Dkrynen/lac`; local dir still named `model-hub`, package still `backend` — a rename is a *separate* pending task, see below). Venv: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe`. Full suite: `.venv\Scripts\python.exe -m pytest -q -m "not live"` (currently 259 passing).
- **`C:\Users\User\repos\lac-pro`** — the private Pro plugin. **No git remote, and must never get one.** Editable-installed into model-hub's venv; run its commands with model-hub's venv from the lac-pro dir. Suite currently 112 passing (`... -m pytest -q -m "not live"`) + 1 live test.

Both ledgers (append-per-task, they're your recovery map after any compaction): `<repo>/.superpowers/sdd/progress.md`.

## How we got here (state as of 2026-07-05)

- **The LAC Pro Custom Model Import feature is DONE and live-verified** (9 tasks + 5 fix rounds + a real end-to-end run against the live Ollama daemon). model-hub `ef70fc8`, lac-pro `a795d33` (later commits are the spec/plan for THIS build). It lets a Pro user paste a Hugging Face repo ID → LAC downloads/converts/quantizes/installs it. **Nothing is pushed to origin.**
- **This build was chosen after a deliberate strategy call:** Duan wanted "capture people's info / accounts / DB / login." The decided direction (correct-order): the Pro gate is currently trivially bypassable (client-side Python), AND there's no Pro delivery mechanism at all — so build **hardened delivery now** (necessary to sell Pro), and defer the **full account system + the server-side "crowd-benchmark" moat** to post-traction (the moat is the only structurally-un-pirateable value, but it needs users to exist). Free-tier infra only (Cloudflare Workers + R2, $0) by capital constraint. **Honest boundary, agreed and written into the spec: this raises the bar hard against casual bypass, it is NOT uncrackable.**

## Conventions (non-negotiable, carried all session)

- **Open every reply with "Duan"** (standing rule + context-health canary — if it drops, that's the signal to `/clear`).
- **Subagent-driven:** fresh implementer + fresh reviewer subagent per task; a targeted fix subagent for Critical/Important review findings, then re-review; append each task's outcome to the ledger. **Every subagent dispatch MUST say "work in the foreground, do NOT spawn agents"** (a delegation-loop bug bit this project earlier).
- **TDD** per task (except Task 1, a spike). Hand each subagent its task brief via the skill's `scripts/task-brief`, and each reviewer a `scripts/review-package BASE HEAD` diff.
- **Commits land on `master` in both repos, per task. NEVER push to origin without Duan's separate explicit go-ahead each time.**
- lac-pro never gets a public remote; its compiled artifact is stored privately (R2), never public, never in the open-source release.
- Core stays **Pro-LOGIC-unaware** — the bootstrap is generic licensed-plugin delivery, it must not import `lac_pro`.

## Known risks / what to expect

- **Task 1 is a spike and the linchpin.** It must prove hands-on that a compiled/obfuscated `lac-pro` is still discovered via `importlib.metadata.entry_points(group="lac.plugins")` and runs, AND settle the frozen-app install mechanism (likely a plugin dir on `sys.path`, not `pip install`). **Nuitka may need a C compiler that isn't on this machine** — if so, the spike falls back to **PyArmor** (keeps normal package structure, easier discovery, weaker protection). Whichever wins, Tasks 2 & 4 consume that recipe; do NOT force Nuitka. If neither works, that's a BLOCKED escalation to Duan, not a faked green.
- **Task 3 is a new non-Python surface** (Cloudflare Worker in JS/TS + local Worker tests), built + unit-tested with mocked license validation and private artifact storage. Account-specific Polar API details, organization identifiers, Worker URLs, and storage object recipes are intentionally redacted from this public handoff; keep them in private operator notes.
- **The Cloudflare-account steps are Duan-gated** (create account + R2 bucket, `wrangler deploy`, upload the artifact, the real test-mode-Polar-key end-to-end). They're the marked tail of the plan — NOT subagent tasks, NOT blockers for the code work.

## Explicitly NOT part of this plan (separate pending tasks)

- **`model-hub` → `lac` rename** (local dir + the `backend` Python package that lac-pro imports from). Confirmed by Duan, but it's a distinct, wide, careful migration with its own spec/plan. Blocks nothing here.
- **Leads-capture form** — fixing the landing page's broken `mailto:` waitlist into real email capture via a hosted service (Tally/Buttondown). Small, mostly needs Duan's own account. Relevant to the Reddit launch.
- **The deferred server-side moat + full accounts** — post-traction.

## Also open (not this build)

- **Reddit launch** — a separate handoff exists at `docs/superpowers/HANDOFF-reddit-launch.md` (plan the first r/LocalLLaMA post; the one real launch blocker is the waitlist form above). A draft `v2.3.0` GitHub release exists with a built Windows installer, still a draft, unpushed/unpublished (Duan-gated).

---

## Paste this into the new session

```
Duan here. Read docs/superpowers/HANDOFF-pro-delivery.md in C:\Users\User\repos\model-hub — it's the full handoff. Then execute the plan it points to (docs/superpowers/plans/2026-07-05-lac-pro-delivery-and-hardening.md) subagent-driven, starting at Task 1 (the spike). Both repos' ledgers are at .superpowers/sdd/progress.md. Don't push anything to origin.
```
