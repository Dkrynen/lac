# Tweakability 3b — Per-Project Agent Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `lac agent` stops re-rolling the dice on every launch. The first run in a project records what LAC picked (model, context, permission preset) in a project profile; every later run honors it. The user can pin a model explicitly, re-pick on demand, and choose a permission preset — per project, persistently, never clobbered.

**Architecture:** A new `backend/agent_launch/project_profile.py` owns the profile contract (schema, load/save/validate, permission presets with a safety floor). `launch_agent` gains a profile-first flow: profile present → honor it (honest refusals when the pinned model is gone); absent → auto-select as today, then record the selection so the next run is stable. `--model` pins explicitly; `--reselect` re-derives and updates. The phase-3 spec item 3b (`docs/superpowers/specs/2026-08-01-phase3-proven-profiles-tweakability-design.md` §3) is the scope anchor; the no-clobber profile-manifest machinery (`.lac-profiles.json` in config_writer.py) already shipped and is untouched.

**Tech Stack:** Python 3.10+, pytest (`-m "not live"`), existing `backend/agent_launch/` package, argparse CLI (`cli.py`). No new dependencies.

## Global Constraints

- Python 3.10+; core stays Pro-LOGIC-unaware (no `lac_pro` imports in model-hub).
- Test gate: `.venv\Scripts\python.exe -m pytest -m "not live" -p no:cacheprovider -q` stays green.
- TDD every task: red → green → commit. One logical commit per task.
- Dependency-injected seams (house pattern — `launch_agent` already takes `detect_fn`/`recommend_fn`/`ensure_variant_fn`/etc.): profile I/O joins the same style so tests never touch a real project dir they didn't create.
- **Safety floor (non-negotiable):** no profile, preset, or flag may produce a config that allows reading secret-shaped files (`.env*`, `credentials.json`, `token.json`, `*.pem`, `*.key`) or enables `external_directory`/`task`. Presets change the working permissions (edit/bash/grep/web), never the floor. Enforced by a dedicated test.
- **Never a silent pull** (variant.py hard rule): a pinned model that isn't installed is an honest refusal with a `lac pull` hint, never an auto-download and never a silent switch to a different model.
- A malformed/tampered profile is an honest error + exit — never a silent fallback to auto-select (the user's explicit project state is authoritative; surprising them is worse than stopping).
- Subprocess routing stays on `backend.cookbook.proc` (house guard test).
- Run tests with the repo venv: `.venv\Scripts\python.exe -m pytest ...` (Windows).

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `backend/agent_launch/project_profile.py` | `ProjectProfile`, presets, load/save/validate, permission merge with safety floor | Create |
| `backend/agent_launch/config_writer.py` | `write_opencode_config` accepts an explicit permission dict (default unchanged) | Modify |
| `backend/agent_launch/launcher.py` | Profile-first flow in `launch_agent` (honor / record / reselect) | Modify |
| `cli.py` | `lac agent --model <m>`, `lac agent --reselect`; profile status in launch output | Modify |
| `tests/test_project_profile.py` | Profile core + preset floor tests | Create |
| `tests/test_launcher_profiles.py` | Launcher profile flows (injected seams) | Create |
| `tests/test_cli_agent_profiles.py` | CLI parsing + output conventions | Create |

Dependency order: Task 1 (profile core) → Task 2 (presets + safety floor) → Task 3 (config_writer permission passthrough) → Task 4 (launcher profile-first flow) → Task 5 (CLI wiring) → Task 6 (full gate + docs touch).

---

## Task 1: Profile core — schema, load, save, validate

**Files:**
- Create: `backend/agent_launch/project_profile.py`
- Test: `tests/test_project_profile.py`

**Interfaces:**

```python
PROFILE_FILENAME = "lac-profile.json"          # lives at <project>/.opencode/lac-profile.json
PROFILE_SCHEMA_VERSION = 1

class ProfileError(Exception):
    """Malformed or invalid profile; .reason is user-facing."""

@dataclass(frozen=True)
class ProjectProfile:
    model: str                    # pinned base model id, e.g. "qwen3:8b" (pre-variant)
    context: int | None = None    # pinned num_ctx request; launch still clamps to the agent floor
    preset: str = "strict"        # permission preset name (Task 2)
    updated_at: str = ""          # ISO-8601 UTC, set on save

def profile_path(project_dir) -> Path: ...
def load_profile(project_dir) -> ProjectProfile | None:
    """None when no profile exists. Raises ProfileError for malformed JSON,
    wrong schema_version, missing/empty model, or unknown preset."""
def save_profile(project_dir, profile: ProjectProfile) -> Path:
    """Writes atomically-enough (mkdir parents + single write), stamps updated_at,
    returns the path."""
```

- [ ] **Step 1: Write the failing tests** — cover: round-trip (save → load equality, updated_at stamped); load returns None when absent; ProfileError for each of invalid-JSON / bad-schema-version / missing-model / empty-model / unknown-preset; profile lands at `<project>/.opencode/lac-profile.json`. All under `tmp_path`.
- [ ] **Step 2: Red → implement → green.**
- [ ] **Step 3: Commit** (`feat(profiles): per-project profile core — schema, load, save, validate`).

---

## Task 2: Permission presets + the safety floor

**Files:**
- Modify: `backend/agent_launch/project_profile.py`
- Test: `tests/test_project_profile.py`

**Interfaces:**

```python
PRESETS: dict[str, dict]   # name -> permission dict (opencode permission shape)

def preset_permissions(preset: str) -> dict:
    """Return a deep copy of the preset's permission dict; ProfileError on unknown name."""
```

Presets (both start from `config_writer._FAIL_CLOSED_PERMISSIONS` and ONLY relax working tools — the read-deny list for secret shapes, `external_directory: deny`, and `task: deny` are identical in every preset):

- `strict` — the current fail-closed defaults exactly (`*: ask`, edit/bash/grep/webfetch/websearch: ask).
- `dev` — working tools relaxed for flow: `edit: allow`, `bash: allow`, `grep: allow`, `webfetch: allow`, `websearch: ask`, `*: ask`. Secret read-denies, `external_directory: deny`, `task: deny` unchanged.

- [ ] **Step 1: Write the failing tests** — cover: both presets exist; **the floor test** — for EVERY preset, `permission["read"]` denies `*.env`, `*.env.*`, `*credentials.json`, `*token.json`, `*.pem`, `*.key`, and `external_directory`/`task` are `deny` (iterate all PRESETS so a future preset can't skip the floor); preset_permissions returns copies (mutating the result doesn't leak into PRESETS); unknown preset raises ProfileError.
- [ ] **Step 2: Red → implement → green.**
- [ ] **Step 3: Commit** (`feat(profiles): permission presets with a secret-safety floor`).

---

## Task 3: config_writer permission passthrough

**Files:**
- Modify: `backend/agent_launch/config_writer.py` (`write_opencode_config`)
- Test: `tests/test_agent_config_writer.py` (extend — find the existing write_opencode_config tests and mirror them)

**Interfaces:**
- `write_opencode_config(project_dir, model, ollama_host, *, permission=None)` — `None` keeps today's behavior (fail-closed); an explicit dict is used as-is (caller validates via Task 2). The generated JSON's `permission` block must equal the passed dict.

- [ ] **Step 1: Failing test** — write with an explicit permission dict; read the written `.opencode/opencode.json`; assert the permission block matches and the provider/model wiring is unchanged. Plus a default-behavior test (no permission arg → fail-closed, exactly as today).
- [ ] **Step 2: Red → implement → green.**
- [ ] **Step 3: Commit** (`feat(profiles): write_opencode_config accepts explicit permissions`).

---

## Task 4: Launcher profile-first flow

**Files:**
- Modify: `backend/agent_launch/launcher.py`
- Test: `tests/test_launcher_profiles.py`

**Interfaces:** `launch_agent` gains `model_pin: str | None = None` (from `--model`) and `reselect: bool = False` (from `--reselect`), plus `load_profile_fn`/`save_profile_fn` seams (defaults = project_profile.load_profile/save_profile).

Flow (replaces the current select-from-recommendations block):

1. `profile = load_profile_fn(project_dir)` unless `reselect` (reselect forces auto-select).
2. **Profile present:** pinned model must be installed (`is_installed` against the provider list) — else honest refusal: `"<model> (pinned in this project's profile) is not installed. lac pull <model> — or run lac agent --reselect to pick a different model."` return 1. Variant/num_ctx come from the profile (`context` clamped at `AGENT_MIN_CONTEXT` with the same honest note as `--context`); permissions from `preset_permissions(profile.preset)`; skip recommendation entirely.
3. **No profile (or reselect):** today's auto-select path unchanged, then `save_profile_fn(project_dir, ProjectProfile(model=base, context=num_ctx, preset="strict"))` records the selection. Output notes it: `Recorded project profile: <model> (lac agent --reselect to re-pick)`.
4. `model_pin` set: the pinned model must exist in the catalog (`load_models()`) and be installed — honest refusals otherwise (`Unknown model ...` / `not installed ... lac pull ...`); on success it writes the profile with that model (context auto unless `--context` given) and launches from it.
5. Malformed profile → `ProfileError` caught in the CLI layer (Task 5), not here; launcher lets it propagate.

- [ ] **Step 1: Write the failing tests** (all seams injected; tmp_path projects):
  - profile honored: profile with model X installed → launches with X's variant, no recommend call (recommend_fn spy asserts not called).
  - profile model not installed → return 1, message contains `lac pull` and `--reselect`, no launch.
  - profile context below floor → clamped with the honest note.
  - no profile → auto-select path + profile file written with the picked model.
  - reselect=True with existing profile → auto-select runs, profile updated to the new pick.
  - model_pin happy path + unknown-model refusal + not-installed refusal.
  - preset flows into the written `.opencode/opencode.json` permission block (dev preset → edit/bash allow; floor still intact).
- [ ] **Step 2: Red → implement → green.**
- [ ] **Step 3: Full suite** `.venv\Scripts\python.exe -m pytest -m "not live" -q` — green.
- [ ] **Step 4: Commit** (`feat(profiles): launcher honors per-project profiles (pin, record, reselect)`).

---

## Task 5: CLI wiring

**Files:**
- Modify: `cli.py` (`cmd_agent`, `build_parser` agent subparser)
- Test: `tests/test_cli_agent_profiles.py`

**Interfaces:**
- Parser: `p_agent.add_argument("--model", help="Pin this project's agent model (recorded in the project profile)")`; `p_agent.add_argument("--reselect", action="store_true", help="Ignore the project profile and re-pick a model, updating the profile")`.
- `cmd_agent`: passes `model_pin`/`reselect` into `launch_agent`; catches `ProfileError` → red reason on stderr, exit 1. Launch output when a profile is used: `Using project profile: <model> (pinned) — lac agent --reselect to re-pick.`

- [ ] **Step 1: Failing tests** — parser accepts `agent . --model qwen3:8b` and `agent --reselect` (assert fields); ProfileError path prints reason + exits 1 (monkeypatch launch_agent to raise). Mirror `tests/test_cli_quantize.py` conventions.
- [ ] **Step 2: Red → implement → green.**
- [ ] **Step 3: Commit** (`feat(cli): lac agent --model / --reselect + profile status output`).

---

## Task 6: Full gate + docs

- [ ] **Step 1:** `.venv\Scripts\python.exe -m pytest -m "not live" -p no:cacheprovider -q` → exit 0.
- [ ] **Step 2:** README `lac agent .` section gains one line: first run records a project profile; `--model` pins; `--reselect` re-picks. (README is the user-facing truth — keep it honest.)
- [ ] **Step 3: Commit** (`docs: per-project agent profiles in README`).

---

## Out of scope (explicitly)

- **3c remainder** (`lac agent --customize` opening the profile for editing) — separate follow-on plan; the no-clobber half already shipped in v2.7.1.
- **3d** offload config editor (web Pro cockpit) — Pro-gated, later.
- Custom permission dicts in profiles (presets only in v1 — a free-form permission block in a project file is a foot-gun that deserves its own design pass).
- Profile portability/sharing, profile versioning migration machinery (schema_version 1 is the floor; migration logic lands when a v2 exists).

## Risks

| Risk | Mitigation |
|---|---|
| A preset relaxes the secret floor | Task 2's floor test iterates ALL presets; CI keeps it green |
| Pinned model removed from disk → silent weirdness | Honest refusal with pull/reselect hints; never a silent pull, never a silent substitute |
| Profile silently overrides the user's intent on a bad parse | Malformed profile = loud error + exit, never silent fallback |
| Auto-recording a profile surprises a first-time user | Launch output states it was recorded + how to re-pick |
| Launcher test sprawl | All seams injected (house pattern); no real Ollama/provider in tests |
