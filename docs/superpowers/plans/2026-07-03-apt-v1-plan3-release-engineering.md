# APT v1 — Plan 3: Release Engineering + Public-Ready

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the already-public repo launch-grade: tri-OS CI, a tag-triggered Windows-installer release pipeline, pip/pipx installability, the APT rebrand, repo hygiene, and a landing page with the macOS/Linux waitlist — ending in a Duan-gated release checklist.

**Architecture:** No code-behavior changes to core. CI = GitHub Actions matrix (free: repo is public). Packaging keeps the internal `backend` package name for v1 (pipx venvs are isolated, so the generic name harms nobody; the PyPI publish + rename is deferred to the name-freeze). Release = tag push → PyInstaller + InnoSetup on a windows runner → draft GitHub Release. Landing = static `site/` deployed by a Pages workflow.

**Tech Stack:** GitHub Actions · PyInstaller/InnoSetup (already proven locally — dist/ + installer.iss exist) · pyproject/setuptools · plain HTML/CSS landing.

## Global Constraints

- Spec §6–7 (`docs/superpowers/specs/2026-07-02-apt-v1-public-launch-design.md`). Repo `Dkrynen/model-hub` is ALREADY PUBLIC — treat every commit as public the moment it's pushed; **nothing is pushed by this plan** (push + tag + Pages enable = Duan-gated checklist at the end).
- Secrets posture: full-history sweep PASSED 2026-07-03 (56 commits; zero credential patterns; only secret-ish filename ever = web/tokens.css, harmless). Keep it that way: .gitignore must cover `.env`, `credentials.json`, `token.json`, `license.json`.
- **No Pro references leak into public docs beyond marketing copy** — apt-pro repo/paths never appear in README/site (the Pro pitch does; the private repo location does not).
- PyPI names checked 2026-07-03: `apt` TAKEN; `apt-hub`, `aptcli`, `localapt` free. v1 dist name = `apt-hub` (installable now from git; PyPI upload deferred). Console script = `aptm` everywhere (never literally `apt` on POSIX — Debian collision; the Windows installer may add an `apt` alias since Windows has no system apt).
- Suite gates unchanged: core pytest green (195/5), web typecheck+build exit 0, after every task.
- macOS CI runners: fine now that the repo is public (free minutes). If visibility ever flips private, drop the macos leg (10× minute burn).

---

### Task 1: Ignore-hardening + version truth

**Files:** Modify: `.gitignore`, read `backend/version.py`.

- [ ] Append to `.gitignore` (idempotent — only if missing): `.env`, `credentials.json`, `token.json`, `license.json`, `.superpowers/`.
- [ ] Read `backend/version.py`; note the current version for the release checklist (do not bump — Duan picks the launch version at tag time).
- [ ] Commit: `chore: harden .gitignore (env/credential/license patterns)`

### Task 2: CI — tri-OS matrix + web gates

**Files:** Modify: `.github/workflows/test.yml`.

- [ ] Extend the existing job to `strategy.matrix.os: [ubuntu-latest, windows-latest, macos-latest]` with `runs-on: ${{ matrix.os }}`; keep the uv-based install steps (they're OS-agnostic); pytest step unchanged (live tests already self-skip when Ollama is absent).
- [ ] Add a second job `web`: ubuntu, `actions/setup-node@v4` node 20, `npm ci` + `npm run typecheck` + `npm run build` in `web/`.
- [ ] Validate YAML locally: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/test.yml'))"` (pyyaml is in the venv; if not, `ruamel` or skip with a careful read).
- [ ] Commit: `ci: tri-OS pytest matrix + web typecheck/build job`
- Verification note: workflows can only truly run after Duan pushes — listed in the final checklist.

### Task 3: pip/pipx installability (pyproject for core)

**Files:** Create: `pyproject.toml` (repo root). Modify: `.gitignore` already covers egg-info.

- [ ] `pyproject.toml`: setuptools backend; `name = "apt-hub"`; version read dynamically from `backend/version.py` (`[tool.setuptools.dynamic]` attr) or pinned to its current value; `requires-python = ">=3.10"`; dependencies copied from `requirements.txt` (read it; keep runtime-only deps — flask etc., NOT pytest); `[project.scripts] aptm = "cli:main"`; packages = `backend*` + py-modules `cli`, `server`.
- [ ] Prove it in a THROWAWAY venv (not .venv):
  `python -m venv %TEMP%\aptpkg && %TEMP%\aptpkg\Scripts\pip install C:\Users\User\repos\model-hub` then `%TEMP%\aptpkg\Scripts\aptm.exe scan` → prints hardware scan, exit 0. Delete the venv after.
- [ ] Core suite still green (pyproject must not confuse pytest/rootdir).
- [ ] Commit: `feat(packaging): pip/pipx-installable core — aptm console script (dist name apt-hub)`

### Task 4: Release workflow (tag → Windows installer → draft Release)

**Files:** Create: `.github/workflows/release.yml`. Read first: `build.spec`, `installer.iss`, `.github/workflows/build.yml` (an old build workflow may exist — fold/replace it; delete if superseded).

- [ ] Workflow `on: push: tags: ['v*']`, windows-latest: checkout → setup-python 3.11 → `pip install -r requirements.txt pyinstaller` → `pyinstaller build.spec` → InnoSetup (preinstalled on windows runners as `iscc`; else choco install innosetup) → `iscc installer.iss` → upload both artifacts → `softprops/action-gh-release@v2` with `draft: true` attaching the installer + exe.
- [ ] Web build must be baked in BEFORE pyinstaller if the exe serves `web/dist` (read build.spec datas to confirm; add a node setup + `npm ci && npm run build` step if web/dist is bundled).
- [ ] Local proof (fast, no exe build): `pyinstaller build.spec --noconfirm` IS the slow real proof — run it once locally if it completes in a few minutes and `dist/` output launches (`dist\model-hub.exe --help` or the server binary responds); if the local build exceeds ~10 min, skip and let the first tag run prove it (note in checklist).
- [ ] Validate YAML as in Task 2. Commit: `ci(release): tag-triggered Windows installer + draft GitHub Release`

### Task 5: Rebrand + repo hygiene

**Files:** Modify: `README.md`. Create: `CONTRIBUTING.md`, `.github/ISSUE_TEMPLATE/bug_report.md`, `.github/ISSUE_TEMPLATE/feature_request.md`, `CHANGELOG.md` (exists — add Unreleased section).

- [ ] README rewrite: brand **APT — local AI, sorted** (matches the CLI banner). Sections: hero one-liner, features (scan/recommend/calibrate/benchmark/tune-teaser), install (Windows installer from Releases · `pipx install git+https://github.com/Dkrynen/model-hub` → command `aptm`), quickstart, hardware table (keep), **APT Pro** section (Tuning Cockpit pitch + "coming soon" pricing line, no private-repo mentions), macOS/Linux "coming soon — join the waitlist" link (site URL), screenshots/GIF placeholder markers, license (core MIT).
- [ ] CONTRIBUTING.md: dev setup (venv, pytest, web dev), PR expectations (suite green, typecheck), plugin authoring pointer to docs/PLUGINS.md.
- [ ] Issue templates: minimal bug (repro/expected/actual/`aptm scan` output) + feature.
- [ ] CHANGELOG.md: add `## [Unreleased]` summarizing Plans 1–2 highlights (web controls, plugin seam, Pro cockpit, licensing).
- [ ] Commit: `docs: APT rebrand — README, CONTRIBUTING, issue templates, changelog`

### Task 6: Landing page + waitlist

**Files:** Create: `site/index.html` (self-contained: inline CSS, no CDNs), `.github/workflows/pages.yml`.

- [ ] Single-page dark landing matching the web app's aesthetic (panel/iris tokens from `web/tokens.css`): hero ("APT — local AI, sorted" + one-liner), 3 feature cards (Fit: will it run · Dyno: real tok/s not guesses · Tune: Pro cockpit), download CTA (links to GitHub Releases latest), **waitlist block** for macOS/Linux (placeholder form action + a `mailto:` fallback; swap-in note for Tally/Buttondown at launch), Pro section (subscription "under $5/mo" positioning line + LemonSqueezy checkout placeholder), footer (GitHub, MIT).
- [ ] `pages.yml`: deploy `site/` via `actions/upload-pages-artifact` + `actions/deploy-pages` on push to master (Pages must be enabled by Duan — checklist).
- [ ] Render check: open the file locally (headless fetch of file:// not needed — visual check is Duan's; structural check = valid HTML via python `html.parser` round-trip).
- [ ] Commit: `feat(site): landing page + waitlist, Pages deploy workflow`

### Task 7: Wrap — HANDOFF + ledger + Duan-gated release checklist

- [ ] HANDOFF.md: Plan 3 done; the release checklist (below) is the only remaining path to launch.
- [ ] Ledger entry; final verification (suite, typecheck/build, `git status` clean).
- [ ] Commit: `docs: HANDOFF — Plan 3 done + launch checklist`

**Duan-gated launch checklist (goes in HANDOFF):**
1. Review README/site copy in his voice; add real screenshots/GIF.
2. `git push origin master` (publishes everything — history is sweep-clean).
3. Watch CI go green on the tri-OS matrix (first real run).
4. Enable GitHub Pages (Actions source) → landing goes live.
5. LemonSqueezy store (Plan 2 checklist) → product id → real checkout link into README + site.
6. Swap waitlist placeholder for a real form (Tally/Buttondown).
7. Pick launch version → update `backend/version.py` → `git tag vX.Y.Z && git push --tags` → release workflow builds the installer → publish the draft Release.
8. Announce (the three-launch-moments play starts here).

## Final verification (whole plan)

- [ ] Core suite green; web typecheck+build exit 0; both YAML workflows parse.
- [ ] Throwaway-venv `aptm scan` proof done (Task 3).
- [ ] `git status` clean; nothing pushed; apt-pro untouched & remote-free.
- [ ] No apt-pro paths in README/site (grep).

## Deferred (explicit)

- PyPI upload + `backend`→real-name rename → at name-freeze (fitment/rigfit decision or APT-final).
- macOS/Linux binaries → post-launch, waitlist-driven (spec).
- Signed Windows builds / Apple notarization → when certs exist.
