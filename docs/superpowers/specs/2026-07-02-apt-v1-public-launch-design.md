# APT v1 Public Launch — Design

**Date:** 2026-07-02
**Status:** Approved by Duan (brainstorm session 2026-07-02)
**Repo:** `C:\Users\User\repos\model-hub` (current state: calibration loop merged; `feat/web-technical-controls` in flight)

## 1. Goal

Take APT from a local personal tool to a public, revenue-capable product:

1. **Public-ready release** — packaged, documented, CI-backed, installable by strangers.
2. **Monetization layer** — open-core with a paid Pro tier, delivered via license keys, cheap-subscription pricing.
3. **Feature expansion** — the first Pro pillar (Tuning Cockpit: GPU overflow/offload controls + auto-tuner) built on the existing split-plan and calibration engines.

Strategy decided: monetization phases ship in order **A → B → C** (see §3); distribution is **Windows-first with teased macOS/Linux releases** (see §6).

## 2. Naming

- **Brand: APT** (working name; Duan may still rename pre-launch, e.g. an "LMT / Local Model Tweaks" direction — the rename window closes at launch when the PyPI package, domain, and installer branding bake it in).
- **Guardrail:** the installed Linux/WSL command must not be literally `apt` (collides with Debian/Ubuntu's package manager). Windows keeps `apt`. The Linux binary name is a packaging-time decision (e.g. `aptm`).
- Name-collision sweep run 2026-07-02: `fitment` and `rigfit` verified free on PyPI/npm/GitHub (rigfit fully free incl. domains) and remain the vetted rename candidates if Duan pulls the rename trigger.

## 3. Product & tier line

### APT Free (public repo, MIT)
Everything that exists today, free forever: hardware scan, compute tiers/split plans, recommendations, calibration engine, benchmark (`apt benchmark`), model install/manage, chat, TUI, web UI, workspaces, full CLI. **Nothing currently free ever moves behind the paywall.**

### APT Pro (license key, paid) — Phase 1 pillar: the Tuning Cockpit
- **Offload/overflow controls** — per-model overrides turning the split-plan engine from *descriptive* to *prescriptive*: `num_gpu` layer splits, iGPU on/off, KV-cache quantization, flash-attention toggle, context-length presets. Applied via generated Modelfiles / Ollama API options.
- **`apt tune <model>`** — auto-tuner: sweeps N offload configs, benchmark-runs each (reusing the shared benchmark module), picks the fastest for this rig. Composes calibration + benchmarking into the flagship feature.
- **Calibration insights** — history, regression detection (e.g. "tok/s dropped 12% since driver update").
- The in-flight `feat/web-technical-controls` branch is the seed of the Cockpit's web surface; it gets finished and folded in during planning, not abandoned.

### Later phases (roadmap, not in this spec's build scope)
- **Phase 2:** Pro dashboard views (CLI stays free forever; the polished dashboard experience becomes a Pro surface).
- **Phase 3:** multi-machine sync, team/fleet profiles, crowd-benchmark cloud ("real tok/s for your exact GPU from real users") — this is when a hosted service (VPS + Coolify) and subscription-justifying infra enter.

## 4. Open-core architecture

- **One public codebase** for core. Core gains a small **plugin seam** (Python entry-points): at startup APT discovers installed plugin packages and mounts their CLI subcommands, API routes, and web-UI panels.
- **`apt-pro` is a separate closed-source pip package** delivered to license holders. No Pro code in the public repo; the seam is the only contract between them.
- **Licensing via LemonSqueezy** (merchant of record — handles global VAT/sales tax for the SA Pty Ltd; built-in license-key API for issue/activate/validate). **No self-built license server in v1.** Pro validates the key on activation, caches a signed grant locally, and works offline within a grace window.
- **Lapse behavior (dev-fairness rule):** when a subscription lapses, Pro features lock but (a) the free core is untouched and (b) any Modelfiles/configs already applied by the tuner keep working — no rug-pulls on the user's own machine state.

## 5. Pricing

- **Shape: cheap subscription** — deliberately undercutting incumbent pricing; "come in below the big companies" is the positioning.
- **Annual billing is the default presentation** (e.g. "$3/mo billed annually"): on micro-priced subscriptions, per-transaction fees (~5% + $0.50) eat ~20%+ of a monthly charge but ~7% of an annual one. Monthly offered alongside.
- Exact price point is Duan's launch-day call; spec constraint: monthly-equivalent stays in the "impulse-buy" band (≈ $3–5/mo).

## 6. Distribution & release engineering

- **v1 ships:** polished **Windows installer** (existing PyInstaller + InnoSetup pipeline, moved into a GitHub Actions release workflow on tag push) + **`pipx install` from PyPI** for all three platforms (CLI-comfortable Mac/Linux devs are served from day one).
- **Teased releases:** landing page advertises "macOS & Linux apps coming soon" with an **email waitlist**. Each platform release is its own launch/announcement moment; waitlist size decides which platform ships second.
- **CI from day one:** pytest matrix on Windows + Ubuntu + macOS runners on every PR, so the from-source path stays honest and later binary releases are packaging work, not porting work.
- **macOS signing (open item):** unsigned .app hits Gatekeeper friction; needs a $99/yr Apple Developer account (possibly via Morne, as with the mobile-IDE plan). Blocker only for the macOS binary release, not for v1.

## 7. Launch checklist (public-ready definition)

1. Repo rebrand to APT throughout (README currently says "Model Hub"); final repo slug decided at launch.
2. **Secrets/PII sweep of full git history before the repo flips public** (hard gate).
3. README rewrite + demo GIF; docs: install, quickstart, hardware-support table, tuning guide.
4. CHANGELOG discipline + semver + tagged releases via Actions.
5. Issue templates + CONTRIBUTING + license files (core MIT; Pro proprietary EULA).
6. Landing page: value prop, download, waitlist, Pro checkout (LemonSqueezy).
7. Pro skeleton wired end-to-end at launch: checkout → key issued → `apt license activate` → Cockpit unlocks. Launch = revenue-capable.

## 8. Out of scope for v1

Crowd-benchmark cloud · account system · auto-update · Pro dashboard views · team features · custom GGUF building · self-built license server · Coolify deployment (parked until Phase 3).

## 9. Testing strategy

- Existing pytest suite (178+) stays green throughout; tri-OS CI matrix added.
- Plugin seam gets contract tests in core (a fixture fake-pro plugin exercises discovery, mount, and graceful absence).
- `apt-pro` ships with its own test suite (offload-config generation, tuner sweep logic with a mocked Ollama, license grant caching/expiry/grace).
- License flow tested against LemonSqueezy's test mode before launch.

## 10. Open items & risks

| Item | Owner | When |
|---|---|---|
| Final name (keep APT vs rename; vetted alternates: fitment/rigfit) | Duan | Before launch assets are made |
| Exact price point + early-bird offer | Duan | Launch day |
| Apple Developer account for macOS signing | Duan (Morne?) | Before macOS binary release |
| Trademark sanity-check on final name | Claude (research) | After name freeze |
| LemonSqueezy account + product setup | Duan (Claude assists) | During Pro-skeleton build |
| `feat/web-technical-controls` branch: finish + fold into Cockpit | Claude | First plan phase |
