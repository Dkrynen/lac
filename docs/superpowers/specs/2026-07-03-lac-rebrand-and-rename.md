# LAC Rebrand & Rename — Design Spec

**Date:** 2026-07-03 · **Status:** Approved · **Supersedes:** decisions §2.4 (brand direction)
and §2.8 (name stays apt) of `2026-07-03-apt-v2-overhaul-design.md`. Everything else in that
spec (W1 Deep-Dive, W2 Surfaces, W4 Security, W5 Hardware identity, build order) stands
unchanged — only the brand direction and the product name change.

## 1. Why this supersedes the original W3 decision

The original overhaul spec locked rebrand direction "Forge" (warm graphite + copper,
silicon-and-heat metaphor) after a visual-companion mockup session. Before any of it shipped
(confirmed via repo grep — it only existed in planning docs and a gitignored mockup file),
Duan caught that **"Forge" collides with his other flagship project** (the OSS parallel-agent
IDE, literally named Forge) — a same-portfolio naming collision, not a legal one, but real
enough to redirect on immediately.

Path to the new direction, for the record:
1. "Leaf" (organic motif) explored via a second visual-companion session — three directions
   mocked (Fern, Sprout, Undergrowth). Duan picked **Undergrowth**.
2. Whether "leaf" was a codename or a real product rename was clarified: **real rename**,
   not just a visual codename swap.
3. Bare "leaf" name-cleared badly: taken on PyPI (HTML-parsing lib), GitHub dominated by
   Leaflet (45k stars) and a dead-but-remembered Rust ML framework of the same name, `leaf.ai`
   owned by an active IoT-hardware company, and a filed trademark (Leaf Software Solutions,
   Class 042 — software services, the exact legal category) in the space. Modifier candidates
   (`getleaf`, `useleaf`, `leafhub`) cleared cleanly but Duan didn't want a generic get-/use-
   prefix pattern — wanted something short and ownable, "like Cursor."
4. Single-word alternatives (Fern, Moss, Thicket, etc.) were proposed; Duan redirected again
   before deep-clearing them, toward a short **acronym** instead: **LAC — Local AI Companion**.
5. Quick single check (PyPI only, no full sweep — Duan explicitly asked not to over-spend on
   name research): bare `lac` is taken on PyPI (Baidu's Chinese NLP library, also
   AI-adjacent). Same fix the original "apt" name used — split CLI command from PyPI
   distribution name.

## 2. Final identity

| Element | Value |
|---|---|
| Product name | **LAC** — Local AI Companion |
| Wordmark style | lowercase mono `lac` (matches the terminal/mono aesthetic; "LAC" / "Local AI Companion" used in prose) |
| Tagline | unchanged: **"local AI, sorted."** |
| CLI command | `lac` |
| PyPI distribution name | `lac-ai` |
| GitHub repo | `Dkrynen/model-hub` → renamed to `Dkrynen/lac` (GitHub auto-redirects the old URL) |
| Pro companion repo | `C:\Users\User\repos\apt-pro` → renamed to `lac-pro`; Python package `apt_pro` → `lac_pro`. Still private, still NEVER gets a remote. |
| Licensing provider | Polar.sh — unchanged by this doc, already migrated and committed (`apt-pro@a4494fa`) before this rebrand started |

## 3. Visual direction — Undergrowth

Near-black canvas, a single green accent, a leaf mark whose veins are drawn as circuit-style
right angles — half-plant, half-PCB. Darkest and most "terminal" of the three directions
shown; closest to the product's existing hacker-tool feel. Reference mockup:
`.superpowers/brainstorm/1139-1783113612/content/leaf-directions.html` (direction "Undergrowth" —
gitignored; the W3 plan reproduces the mark as committed SVG assets, which become canonical).

### 3.1 Tokens (replaces the Iris set in `web/tokens.css`)

Dark (default):

| Token | Value | Note |
|---|---|---|
| `--bg` | `#08090A` | near-black canvas |
| `--surface` | `#0F1210` | cards, sidebar |
| `--surface-2` | `#141917` | inputs, raised rows |
| `--surface-3` | `#1B211D` | hover, active well |
| `--overlay` | `rgba(4,5,4,.60)` | |
| `--border` | `rgba(228,232,226,.08)` | hairlines |
| `--border-strong` | `rgba(228,232,226,.14)` | |
| `--text` | `#E4E8E2` | off-white |
| `--text-muted` | `#7C8981` | |
| `--text-faint` | `#545C55` | |
| `--accent` | `#4ADE80` | **green — the one accent** |
| `--accent-hover` | `#6AEB9C` | |
| `--accent-pressed` | `#34C06A` | |
| `--accent-soft` | `rgba(74,222,128,.13)` | tints, selected rows |
| `--accent-fg` | `#06170D` | text on accent (dark, not white) |
| `--success` | `#2DD4BF` | **teal, deliberately not the accent green** — verdicts must not compete with or be confused with the brand accent |
| `--warning` | `#D9A84C` | warm amber, distinct from accent |
| `--danger` | `#E5484D` | |
| `--info` | `#6FA8D8` | desaturated blue |

Light theme kept as secondary: soft green-tinted paper `#F6F8F4` canvas, surfaces
white/near-white with the same border logic, accent deepened to `#1FA157` for contrast.
Radius/elevation/motion/type scale unchanged from the current design system (Geist Sans +
Geist Mono stay).

**Accent discipline (unchanged principle from the original spec):** green appears only where
the instrument speaks — primary actions, focus rings, live/measured numbers, the deep-dive
toggle and its revealed math, the TUI prompt/cursor. Never for decoration, never for large
fills.

### 3.2 Logo

**The vein-leaf** — a single-contour leaf outline whose veins are drawn as right-angle
traces instead of organic curves, in accent green on dark surfaces. Meaning: your machine,
mapped — same "your silicon, mapped" idea the original die-mark carried, now expressed as
circuitry hiding inside a leaf rather than a chip package. Scales favicon → app icon →
README header → installer. In the TUI (no SVG), the mark degrades to a green `❋`-style glyph
+ `lac` wordmark rendered in truecolor `#4ADE80` (exact glyph choice is a W3 plan task, not
locked here).

### 3.3 Scope of application

Unchanged from the original W3 scope, just re-themed and re-named:
- `web/tokens.css` + `web/DESIGN_SYSTEM.md` (rewrite brand section; component rules unchanged)
- Web app: no layout changes — tokens flow through Tailwind/shadcn automatically; audit for
  hardcoded iris hexes
- `site/index.html` landing page re-skin — the Polar.sh checkout link and pricing copy
  (added 2026-07-03, commit `eab536c`) stay as-is, only the visual theme and product name
  change
- TUI banner + theme (W2 consumes these tokens)
- `installer.iss` branding + app icon, README header, GitHub social preview
- New `assets/` dir: vein-leaf mark SVG (mono + color), favicon.ico, app icon .ico/.png sizes

## 4. Rename blast radius (beyond the visual scope above)

This is bigger than a normal rebrand because the name itself changed, not just the palette:

- `pyproject.toml` — command `aptm` → `lac`, dist name `apt-hub` → `lac-ai`
- `build.spec`, `installer.iss`, `.github/workflows/build.yml`, `.github/workflows/pages.yml` —
  any hardcoded `apt`/`aptm`/`apt-hub` strings
- `README.md`, `CONTRIBUTING.md`, issue templates, `CHANGELOG.md` — already APT-branded from
  Plan 3, need a find-and-rename pass
- CLI source: `backend/cli.py` and anywhere the `apt` verb is printed in help text /
  banners / error messages
- `apt-pro` repo rename + `apt_pro` package rename (`lac_pro`), its imports in `backend/`
  (the open-core plugin seam that loads it), its own README/HANDOFF
- `docs/PLUGINS.md`, `docs/CLI.md`, `TUI_AGENT_WIRING_FINDINGS.md` — any `apt` command
  examples
- GitHub repo rename (`model-hub` → `lac`) — do this **early** in execution so all
  subsequent commits/PRs/CI runs land under the new URL, not before/after inconsistently
- `HANDOFF.md` and other **current-state** docs get updated; historical plan/spec docs
  (Plan 1-3, the original overhaul spec) are left as-is — they're the historical record of
  what was decided and why, not live documentation

## 5. Out of scope (unchanged from the original overhaul spec, still true)

Tauri shell · macOS/Linux polished installers · in-app auto-update · crowd-benchmark cloud ·
light-theme-first design · TUI feature parity with web · code signing · multi-runtime
execution · vendor-fitment scoring influence. Also out of scope **here**: any changes to W1
(Deep-Dive), W2 (Surfaces), W4 (Security posture), or W5 (Hardware identity) content — those
workstreams proceed exactly as specced, just under the new name/palette. Build order is
unchanged: **W3 → W1 → W5 → W2 → W4.**

## Changelog

- 2026-07-03: Initial spec — LAC rebrand + rename supersedes the "Forge" brand direction and
  the "name stays apt" decision from the original v2 overhaul spec. Undergrowth visual
  direction locked via visual-companion mockups; LAC/lac/lac-ai naming locked via targeted
  PyPI/GitHub/prior-art/domain clearance (two workflow sweeps + one manual PyPI check).
